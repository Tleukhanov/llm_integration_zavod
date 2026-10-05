"""Local Piper (VITS) text-to-speech with word-boundary recovery.

Piper is the *primary* voiceover engine: it runs offline on CPU, needs no API
key, has no rate limit and sounds markedly more natural than the two Russian
edge-tts Neural voices (``ru-RU-DmitryNeural`` / ``ru-RU-SvetlanaNeural``),
which are all the same Microsoft engine and have no headroom left.

Everything here is optional and lazy:

* ``piper-tts`` is imported inside :func:`piper_installed` / :func:`_load_voice`,
  never at module import time, so a machine without the package behaves
  exactly like before.
* The ONNX voice is cached under ``SHORTS_MODELS_DIR/piper`` (never the repo
  root) and is downloaded from the public ``rhasspy/piper-voices`` HuggingFace
  repo *only when it is missing*. Once cached, synthesis makes no network
  calls at all.

Word boundaries
---------------
edge-tts streams ``WordBoundary`` metadata; Piper does not.  Since the
voiceover text is what we just synthesised, the audio and the script are known
to match, so timings are recovered in this order:

1. **faster-whisper alignment** (:func:`align_word_boundaries`) — the already
   installed transcription stack re-reads the ~13s WAV with
   ``word_timestamps=True``.  This is exact and adds no new dependency.
2. **Proportional distribution** (:func:`proportional_word_boundaries`) — each
   word gets a slice of the speech window proportional to its character count.
   This is an *approximation that drifts*: it ignores punctuation, inter-word
   pauses and syllable structure, so long words get over-served and
   punctuation beats get none.  Good enough to keep subtitles roughly in sync,
   not good enough to be trusted for karaoke-style highlighting.

Every failure path returns ``(None, [])`` and the caller (``audio.tts``)
degrades to edge-tts; a TTS problem must never take down a render.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

log = logging.getLogger(__name__)

# Model cache / synthesis are serialised per process: ONNX sessions are heavy
# and the same voice must not be loaded twice from two clip renders.
_piper_lock = threading.Lock()
_voice_lock = threading.Lock()
_voice_cache: dict[str, object] = {}

# HuggingFace repo holding the licence-free Piper voices.
VOICES_REPO = "rhasspy/piper-voices"
VOICES_REVISION = "main"
_DOWNLOAD_TIMEOUT = 300

# Piper voice per detected language.  Both are "medium" quality: noticeably
# more natural than edge-tts and still ~2x realtime on a single CPU core.
# ru_RU-irina-medium is the female alternative to the default male voice.
PIPER_MODEL_BY_LANG: dict[str, str] = {
    "ru": "ru_RU-denis-medium",
    "en": "en_US-amy-medium",
}

DEFAULT_PIPER_MODEL = PIPER_MODEL_BY_LANG["ru"]

# edge-tts ``rate`` is a percentage speed delta ("+8%" == 1.08x).  Piper has no
# such percentage; it exposes ``length_scale``, which is INVERTED: VITS scales
# the *predicted* duration by it, so a SMALLER value means SHORTER (faster)
# audio.  Verified on ru_RU-denis-medium:
#   length_scale 0.6 -> 3.00s | 0.8 -> 3.85s | 1.0 -> 4.34s | 1.25 -> 4.90s
# We therefore convert with ``1 / (1 + pct/100)``.
#
# The conversion is only sane over a modest range: edge-tts happily accepts
# "+200%" (3x speed), which would become length_scale 0.33 — a chipmunk.  The
# percentage is therefore clamped to +-30% before inverting, so a Piper speed-up
# can never exceed ~1.3x no matter how aggressive SHORTS_VO_RATE is.
RATE_PERCENT_MIN = -30.0
RATE_PERCENT_MAX = 30.0
LENGTH_SCALE_MIN = 1.0 / (1.0 + RATE_PERCENT_MAX / 100.0)  # 0.769 (~1.3x faster)
LENGTH_SCALE_MAX = 1.0 / (1.0 + RATE_PERCENT_MIN / 100.0)  # 1.429 (~1.4x slower)

# Piper's own inference defaults; pinned so the engine does not pick up a
# different noise level if a model ships other values.
#
# NOTE ON DETERMINISM: the samples themselves are NOT bit-reproducible across
# processes.  VITS samples its duration predictor and the flow noise, and
# onnxruntime exposes no seed hook here (SessionOptions has no ``random_seed``
# in ORT 1.29), so re-rendering the same line lands within ~1.3% on duration
# (measured 4.284-4.342s over three fresh processes) and never identical byte
# for byte.  Within one process the output is stable, which is what a factory
# run actually needs.  Everything that feeds the render — engine choice, voice,
# length_scale, cache path, boundary normalisation — is a pure function and IS
# reproducible, and boundaries are always measured off the audio that was just
# written, so captions stay in sync with what you hear.
PIPER_NOISE_SCALE = 0.667
PIPER_NOISE_W_SCALE = 0.8

_PIPER_MODEL_RE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-.+$")
_EDGE_VOICE_LANG_RE = re.compile(r"^([a-z]{2})-[A-Z]{2}-")
_PERCENT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%?\s*$")

_QUALITIES = ("x_low", "low", "medium", "high")


def parse_rate_percent(rate: str | None) -> float:
    """Parse an edge-tts ``rate`` string into a signed speed percentage.

    ``"+8%"`` -> ``8.0``, ``"-15%"`` -> ``-15.0``, ``""``/``None``/junk -> ``0.0``.
    The result is clamped to :data:`RATE_PERCENT_MIN`..:data:`RATE_PERCENT_MAX`.
    """
    if rate is None:
        return 0.0
    match = _PERCENT_RE.match(str(rate))
    if not match:
        return 0.0
    try:
        percent = float(match.group(1))
    except ValueError:
        return 0.0
    return max(RATE_PERCENT_MIN, min(RATE_PERCENT_MAX, percent))


def rate_to_length_scale(rate: str | None) -> float:
    """Map an edge-tts ``rate`` to Piper's ``length_scale``.

    Inverted on purpose: edge ``"+8%"`` (1.08x faster) becomes a length_scale
    *below* 1.0, which makes Piper's audio shorter.  Monotonic: a faster
    request always yields a smaller length_scale.  Clamped to
    :data:`LENGTH_SCALE_MIN`..:data:`LENGTH_SCALE_MAX` so an extreme
    ``SHORTS_VO_RATE`` cannot produce a 2x chipmunk.
    """
    percent = parse_rate_percent(rate)
    scale = 1.0 / (1.0 + percent / 100.0)
    return max(LENGTH_SCALE_MIN, min(LENGTH_SCALE_MAX, scale))


def _settings():
    from shorts_clipper.core.settings import Settings

    return Settings.from_env()


def model_dir() -> Path:
    """Directory holding cached Piper ONNX voices.

    Always ``SHORTS_MODELS_DIR/piper`` so the multi-hundred-megabyte models
    land in the gitignored models dir and never in the repo root.
    """
    return Path(_settings().models_dir).expanduser() / "piper"


def model_paths(model_name: str) -> tuple[Path, Path]:
    """Return ``(onnx_path, config_path)`` for *model_name* inside the cache."""
    base = model_dir()
    return base / f"{model_name}.onnx", base / f"{model_name}.onnx.json"


def model_present(model_name: str) -> bool:
    """True when both the ONNX weights and its JSON config are already cached."""
    onnx_path, config_path = model_paths(model_name)
    try:
        return (
            onnx_path.is_file()
            and config_path.is_file()
            and onnx_path.stat().st_size > 0
            and config_path.stat().st_size > 0
        )
    except OSError:
        return False


def piper_installed() -> bool:
    """True when the ``piper`` package is importable (checked lazily)."""
    try:
        import importlib.util

        return importlib.util.find_spec("piper") is not None
    except Exception:
        return False


def _hf_relative_path(model_name: str, suffix: str) -> str:
    """Map ``ru_RU-denis-medium`` to its path inside the voices repo.

    ``ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx``
    """
    parts = model_name.split("-")
    if len(parts) < 3 or parts[-1] not in _QUALITIES:
        raise ValueError(f"unrecognised Piper model name: {model_name!r}")
    locale = parts[0]
    quality = parts[-1]
    name = "-".join(parts[1:-1])
    language = locale.split("_")[0].lower()
    filename = f"{model_name}.onnx{suffix}"
    return f"{language}/{locale}/{name}/{quality}/{filename}"


def _download(url: str, dest: Path) -> None:
    """Stream *url* into *dest* atomically (``.part`` then rename)."""
    request = urllib.request.Request(url, headers={"User-Agent": "shorts-clipper/piper"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response:
            with tmp.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 256)
        if tmp.stat().st_size == 0:
            raise OSError("downloaded file is empty")
        tmp.replace(dest)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_model(model_name: str | None = None) -> bool:
    """Make sure *model_name* is cached locally, downloading it if needed.

    This is the ONLY place in the TTS layer that touches the network, and it is
    reached at most once per model.  Returns ``False`` (never raises) when the
    model cannot be fetched, so the caller can fall back to edge-tts.
    """
    try:
        settings = _settings()
    except Exception:
        settings = None
    model_name = model_name or (getattr(settings, "vo_piper_model", "") or None)
    if not model_name:
        model_name = DEFAULT_PIPER_MODEL
    try:
        if model_present(model_name):
            return True
        onnx_path, config_path = model_paths(model_name)
        base = f"https://huggingface.co/{VOICES_REPO}/resolve/{VOICES_REVISION}"
        started = time.time()
        log.info(
            "Piper voice %r not cached — downloading into %s (first run only)",
            model_name,
            onnx_path.parent,
        )
        _download(f"{base}/{_hf_relative_path(model_name, '')}", onnx_path)
        _download(f"{base}/{_hf_relative_path(model_name, '.json')}", config_path)
        log.info("Piper voice %r cached in %.1fs", model_name, time.time() - started)
        return model_present(model_name)
    except Exception as exc:
        log.warning("Could not fetch Piper voice %r: %s", model_name, exc)
        return False


def _looks_like_piper_model(name: str) -> bool:
    """True for a Piper model id such as ``ru_RU-denis-medium``."""
    return bool(_PIPER_MODEL_RE.match(name.strip())) and name.strip().split("-")[-1] in _QUALITIES


def resolve_model_name(text: str, configured: str | None = None) -> str:
    """Pick the Piper voice for *text*.

    Mirrors :func:`shorts_clipper.audio.tts.pick_voice` so the Russian
    auto-selection keeps working: an explicitly configured Piper model id wins,
    an explicitly configured *edge* voice is mapped to the Piper voice of the
    same language, and otherwise the language is detected from *text*.
    """
    from shorts_clipper.audio.tts import DEFAULT_VOICE, _detect_language

    override = (getattr(_settings(), "vo_piper_model", "") or "").strip()
    if override:
        return override

    configured = (configured or "").strip()
    if configured and configured != DEFAULT_VOICE:
        if _looks_like_piper_model(configured):
            return configured
        match = _EDGE_VOICE_LANG_RE.match(configured)
        if match:
            lang = match.group(1)
            log.info(
                "Piper: honouring configured voice %r as language %r -> %s",
                configured,
                lang,
                PIPER_MODEL_BY_LANG.get(lang, PIPER_MODEL_BY_LANG["en"]),
            )
            return PIPER_MODEL_BY_LANG.get(lang, PIPER_MODEL_BY_LANG["en"])

    lang = _detect_language(text or "")
    return PIPER_MODEL_BY_LANG.get(lang, PIPER_MODEL_BY_LANG["en"])


def _synthesis_config(length_scale: float):
    """Build Piper's inference config.  ``length_scale`` is the speed knob."""
    from piper import SynthesisConfig

    return SynthesisConfig(
        length_scale=length_scale,
        noise_scale=PIPER_NOISE_SCALE,
        noise_w_scale=PIPER_NOISE_W_SCALE,
    )


def _load_voice(model_name: str):
    """Load (and cache) the ONNX voice.  Returns ``None`` on any failure."""
    onnx_path, config_path = model_paths(model_name)
    key = str(onnx_path)
    cached = _voice_cache.get(key)
    if cached is not None:
        return cached
    with _voice_lock:
        cached = _voice_cache.get(key)
        if cached is not None:
            return cached
        try:
            from piper import PiperVoice

            started = time.time()
            voice = PiperVoice.load(str(onnx_path), config_path=str(config_path))
            log.info(
                "Loaded Piper voice %r in %.1fs (%d Hz)",
                model_name,
                time.time() - started,
                voice.config.sample_rate,
            )
        except Exception:
            log.warning("Piper voice %r failed to load", model_name, exc_info=True)
            return None
        _voice_cache[key] = voice
        return voice


def reset_voice_cache() -> None:
    """Drop cached ONNX sessions (used by tests)."""
    with _voice_lock:
        _voice_cache.clear()


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate > 0:
                return float(handle.getnframes()) / float(rate)
    except Exception:
        pass
    return 0.0


def proportional_word_boundaries(
    text: str,
    start: float = 0.0,
    end: float | None = None,
) -> list:
    """Distribute *text*'s word timings proportionally over ``[start, end]``.

    DRIFTS: word length is a crude proxy for duration.  Punctuation, inter-word
    pauses and syllable structure are all ignored, so captions can sit a few
    hundred milliseconds off the voice.  Only used when whisper alignment is
    unavailable (no ``faster-whisper``, no model, or a failed transcription).
    """
    words = [w for w in (text or "").split() if w.strip()]
    if not words:
        return []
    total_weight = float(sum(max(1, len(w)) for w in words))
    span = float(end if end is not None else start) - float(start)
    if span <= 0.0:
        span = 0.05 * len(words)
    cursor = float(max(0.0, start))
    bounds: list = []
    for word in words:
        weight = max(1, len(word))
        length = span * (weight / total_weight)
        bounds.append((word, cursor, cursor + length))
        cursor += length
    return bounds


_align_model_cache: dict[str, object] = {}


def _alignment_model(lang: str | None):
    """Return a faster-whisper model able to transcribe *lang*.

    Reuses the model already configured for transcription.  The stock default
    is ``tiny.en`` (English-only), which cannot transcribe Russian, so for a
    non-English line the ``.en`` suffix is dropped to get the multilingual
    sibling of the same size.  Cached per model name so a batch of shorts pays
    the load once.
    """
    from faster_whisper import WhisperModel

    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()
    name = (settings.whisper_model or "tiny").strip()
    if lang and lang != "en" and name.endswith(".en"):
        name = name[: -len(".en")]
    cached = _align_model_cache.get(name)
    if cached is not None:
        return cached
    started = time.time()
    model = WhisperModel(
        name,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        cpu_threads=4,
        download_root=str(Path(settings.models_dir).expanduser().resolve()),
    )
    log.info("Loaded Whisper '%s' for Piper word alignment in %.1fs", name, time.time() - started)
    _align_model_cache[name] = model
    return model


def _char_spans(words: list) -> list:
    """Normalised cumulative character positions, e.g. ``[(0.0, 0.14), ...]``."""
    total = float(sum(max(1, len(w)) for w in words)) or 1.0
    spans = []
    cursor = 0.0
    for word in words:
        width = max(1, len(word)) / total
        spans.append((cursor, min(1.0, cursor + width)))
        cursor += width
    return spans


def _pair_with_source_words(aligned: list, source_words: list) -> list:
    """Keep the SCRIPT's words, borrow only whisper's TIMINGS.

    Whisper transcribes the audio we just generated, so it is close to — but
    not guaranteed to be — the original text.  A real capture of this line
    came back as ``... пользуются по | в поменутье.`` instead of
    ``... пользуются | по минуте.``: same word count, wrong tail.  Emitting
    whisper's tokens directly would burn that onto the screen, so timings and
    text are kept apart.

    Equal counts pair up 1:1 (no approximation at all).  When whisper merged or
    split a word, each source word adopts the time span of the aligned items
    its proportional character position overlaps.
    """
    if not aligned or not source_words:
        return []
    if len(aligned) == len(source_words):
        return [
            (word, float(start), float(end))
            for word, (_token, start, end) in zip(source_words, aligned, strict=True)
        ]

    source_spans = _char_spans(source_words)
    aligned_spans = _char_spans([token for token, _s, _e in aligned])
    out: list = []
    for word, (lo, hi) in zip(source_words, source_spans, strict=True):
        overlapping = [
            (start, end)
            for (_token, start, end), (alo, ahi) in zip(aligned, aligned_spans, strict=True)
            if alo < hi and lo < ahi
        ]
        if not overlapping:
            continue
        out.append((word, min(s for s, _e in overlapping), max(e for _s, e in overlapping)))
    return out


def align_word_boundaries(audio_path: Path, text: str, lang: str | None = None) -> list:
    """Recover word timings for *audio_path* by re-transcribing it.

    The audio was synthesised from *text*, so the transcription is expected to
    reproduce it; ``initial_prompt=text`` biases whisper towards the exact
    vocabulary and ``language`` is pinned so a short Russian line is not
    mis-detected as English.

    The returned words are always the ORIGINAL script words — see
    :func:`_pair_with_source_words`.  Returns ``[]`` — never raises — when
    faster-whisper is missing, the model cannot be loaded, or the recovered
    words are too few to trust.
    """
    source_words = [w for w in (text or "").split() if w.strip()]
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        log.info(
            "faster-whisper is not installed — falling back to proportional word "
            "timing for the Piper voiceover (timings will drift)"
        )
        return []

    try:
        model = _alignment_model(lang)
        segments, _info = model.transcribe(
            str(audio_path),
            language=(lang or None),
            word_timestamps=True,
            initial_prompt=text,
            beam_size=1,
            condition_on_previous_text=False,
        )
        aligned: list = []
        for segment in segments:
            for word in segment.words or []:
                start = getattr(word, "start", None)
                end = getattr(word, "end", None)
                token = (getattr(word, "word", "") or "").strip()
                if not token or start is None or end is None:
                    continue
                start = float(start)
                end = float(end)
                if end > start:
                    aligned.append((token, start, end))
    except Exception:
        log.warning(
            "Piper word alignment failed — falling back to proportional timings",
            exc_info=True,
        )
        return []

    if source_words and len(aligned) < max(2, int(len(source_words) * 0.5)):
        log.warning(
            "Piper word alignment recovered only %d/%d words — using proportional timings",
            len(aligned),
            len(source_words),
        )
        return []
    if not aligned:
        return []
    bounds = _pair_with_source_words(aligned, source_words)
    log.info("Piper word alignment recovered %d word timings", len(bounds))
    return bounds


def _speech_span(audio_path: Path) -> tuple[float, float]:
    """Return ``(start, end)`` of the audible speech inside *audio_path*."""
    duration = _wav_duration(audio_path)
    try:
        from shorts_clipper.audio.tts import speech_window

        window = speech_window(audio_path)
    except Exception:
        window = None
    if window is not None:
        return float(window[0]), float(window[1])
    if duration > 0.0:
        return 0.0, duration
    return 0.0, 0.0


def synthesize(
    text: str,
    out_path: Path,
    *,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
) -> tuple:
    """Synthesise *text* with Piper into a 48 kHz mono WAV at *out_path*.

    Returns ``(wav_path, word_boundaries)``.  ``wav_path`` is ``None`` when
    Piper cannot be used at all (not installed, model unavailable, synthesis
    error) so the caller can fall back to edge-tts; ``word_boundaries`` is
    ``[]`` only if even the proportional fallback could not be built.
    """
    text = (text or "").strip()
    if not text:
        return None, []

    if not piper_installed():
        log.warning(
            "Piper TTS is not installed (pip install piper-tts) — falling back to edge-tts"
        )
        return None, []

    model_name = resolve_model_name(text, voice)
    if not ensure_model(model_name):
        log.warning(
            "Piper voice %r is unavailable (no cache, download failed) — "
            "falling back to edge-tts",
            model_name,
        )
        return None, []

    voice_obj = _load_voice(model_name)
    if voice_obj is None:
        log.warning("Piper voice %r could not be loaded — falling back to edge-tts", model_name)
        return None, []

    length_scale = rate_to_length_scale(rate)
    if pitch:
        # Piper is VITS: F0 is baked into the trained checkpoint, so there is no
        # pitch knob.  Accepted and ignored rather than treated as an error.
        log.debug("Piper ignores pitch=%r (VITS has no pitch control)", pitch)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix="piper_")
    os.close(fd)
    tmp_wav = Path(tmp_name)
    try:
        syn_config = _synthesis_config(length_scale)
        with _piper_lock:
            with wave.open(str(tmp_wav), "wb") as handle:
                voice_obj.synthesize_wav(text, handle, syn_config)
        if not tmp_wav.is_file() or tmp_wav.stat().st_size <= 44:
            raise OSError("piper produced no audio")

        from shorts_clipper.audio.tts import transcode_to_wav

        if not transcode_to_wav(tmp_wav, out_path):
            return None, []
    except Exception:
        log.warning("Piper synthesis failed — falling back to edge-tts", exc_info=True)
        return None, []
    finally:
        try:
            tmp_wav.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        lang = _detect_lang_for(text)
        bounds = align_word_boundaries(out_path, text, lang)
        if not bounds:
            start, end = _speech_span(out_path)
            bounds = proportional_word_boundaries(text, start, end)
    except Exception:
        log.warning("Piper word-timing recovery failed", exc_info=True)
        bounds = []

    return out_path, bounds


def _detect_lang_for(text: str) -> str:
    from shorts_clipper.audio.tts import _detect_language

    try:
        return _detect_language(text)
    except Exception:
        return "en"