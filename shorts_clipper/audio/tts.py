"""Optional TTS voiceover generation.

The voiceover layer adds an original audio track to each clip, making it
unique and boosting engagement.

Two engines, picked by ``SHORTS_VO_ENGINE``:

* ``piper`` (default) — local VITS synthesis via ``shorts_clipper.audio.piper_tts``.
  Licence-free, offline once the model is cached and noticeably more natural
  than the two Russian edge-tts voices.  Piper emits no word-boundary
  metadata, so timings are recovered by aligning the generated audio with the
  already-installed faster-whisper, degrading to a proportional split.
* ``edge`` (forced) — edge-tts, which streams real ``WordBoundary`` events.

Both engines are optional dependencies: when neither is usable the module
degrades gracefully (returns ``None``, logs a warning, never crashes the
pipeline).  Every Piper failure path logs a line and falls back to edge-tts,
exactly like ``captions.music.ensure_synthesized_track`` degrades, so a TTS
problem can never take down a render.

A module-level lock serialises concurrent edge-tts synthesisation calls so
that multiple clip renders sharing the same process do not collide on
edge-tts internals.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_tts_lock = threading.Lock()

# Maps a detected language to an edge-tts voice. Keep the default English
# voice as the reserved fallback so existing configs keep working.
VOICE_BY_LANG: dict[str, str] = {
    "en": "en-US-GuyNeural",
    "ru": "ru-RU-DmitryNeural",
    "de": "de-DE-ConradNeural",
    "fr": "fr-FR-HenriNeural",
    "es": "es-ES-AlvaroNeural",
    "it": "it-IT-DiegoNeural",
    "pt": "pt-BR-AntonioNeural",
}

DEFAULT_VOICE = "en-US-GuyNeural"


def _detect_language(text: str) -> str:
    """Rough cyrillic/latin language hint for voice selection."""
    if not text:
        return "en"
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04FF")
    if cyrillic > latin:
        return "ru"
    return "en"


def pick_voice(text: str, configured: str | None = None) -> str:
    """Choose an edge-tts voice.

    Honors an explicit ``configured`` voice when it differs from the default,
    otherwise auto-picks by detected language.
    """
    lang = _detect_language(text)
    auto = VOICE_BY_LANG.get(lang, VOICE_BY_LANG["en"])
    if not configured or configured == DEFAULT_VOICE:
        return auto
    return configured


def voiceover_engine() -> str:
    """Return the configured voiceover engine: ``"piper"`` or ``"edge"``.

    ``SHORTS_VO_ENGINE`` (``Settings.vo_engine``) defaults to ``piper``.  Any
    failure to read settings falls back to the documented default; the Piper
    path degrades to edge-tts on its own, so the default is always safe.
    """
    try:
        from shorts_clipper.core.settings import Settings

        return Settings.from_env().vo_engine
    except Exception:
        return "piper"


def transcode_to_wav(src: Path, dest: Path) -> bool:
    """Normalise *src* into a 48 kHz mono PCM WAV at *dest*.

    Both engines produce audio at different sample rates (Piper medium voices
    are 22.05 kHz, edge-tts MP3 is decoded at whatever ffmpeg picks), while
    everything downstream — duration probing, speech-window detection, AMIX —
    assumes the edge-tts shape.  Returns ``False`` instead of raising so a
    failed transcode can be treated like any other TTS failure.
    """
    try:
        from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

        convert = subprocess.run(
            [
                ffmpeg_path(),
                "-y",
                "-i",
                str(src),
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        log.warning("TTS transcode to WAV raised an exception", exc_info=True)
        return False
    if convert.returncode != 0 or not Path(dest).is_file():
        log.warning(
            "TTS transcode to WAV failed (exit %d): %s",
            convert.returncode,
            convert.stderr[-500:],
        )
        return False
    return True


def _edge_tts_command() -> list[str]:
    """Return the invokable edge-tts command.

    Prefers the ``edge-tts`` console script when it is on PATH, otherwise
    falls back to ``python -m edge_tts`` against the current interpreter
    (independent of whether the venv Scripts dir is on PATH).
    """
    if shutil.which("edge-tts") is not None:
        return ["edge-tts"]
    if importlib.util.find_spec("edge_tts") is not None:
        return [sys.executable, "-m", "edge_tts"]
    return []


def _edge_tts_available() -> bool:
    return bool(_edge_tts_command())


def build_voiceover_text(
    segments: list,
    clip_duration: float,
    language: str = "en",
    max_chars: int = 220,
) -> str | None:
    """Build a punchy short voiceover text from transcript segments.

    Joins the most relevant segment texts, trimmed to *max_chars* so the
    resulting audio fits a short-form clip.  Returns ``None`` when there
    are no usable segments.
    """
    if not segments:
        return None

    parts: list[str] = []
    total = 0
    for seg in segments:
        text = getattr(seg, "text", None) or (seg.get("text") if isinstance(seg, dict) else None)
        if not text or not text.strip():
            continue
        text = text.strip()
        if total + len(text) + 1 > max_chars:
            remaining = max_chars - total
            if remaining > 20:
                text = text[: remaining].rsplit(" ", 1)[0]
                parts.append(text)
            break
        parts.append(text)
        total += len(text) + 1

    if not parts:
        return None
    return " ".join(parts)


def _synthesize_edge(
    text: str,
    out_path: Path,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
) -> Path | None:
    """Synthesise *text* into a WAV file via the edge-tts CLI.

    Uses a module-level lock to serialise concurrent calls.  ``voice`` may be
    ``None`` to auto-select by detected text language.  ``rate`` (speed,
    ``"+8%"``) and ``pitch`` (e.g. ``"+4Hz"``) tune expressiveness — a slight
    positive pitch makes Neural voices sound less robotic.  Returns the
    output ``Path`` on success or ``None`` if edge-tts is unavailable /
    any error occurs.
    """
    if not _edge_tts_available():
        log.warning("edge-tts CLI not found — skipping voiceover synthesis")
        return None

    # edge-tts always streams MP3 regardless of the file extension, so write
    # to a temp path first and transcode into a real WAV (downstream duration
    # probing, speech windows and AMIX all assume a valid wave container).
    import os
    import tempfile

    fd, tmp_name = tempfile.mkstemp(suffix=".mp3", prefix="edgetts_")
    os.close(fd)
    tmp_media = Path(tmp_name)
    effective_voice = voice or pick_voice(text)

    cmd = [
        *_edge_tts_command(),
        "--voice",
        effective_voice,
        "--rate",
        rate,
        "--text",
        text.strip(),
        "--write-media",
        str(tmp_media),
    ]
    if pitch:
        cmd += ["--pitch", pitch]

    try:
        with _tts_lock:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0 or not tmp_media.is_file():
                log.warning(
                    "edge-tts failed (exit %d): %s",
                    result.returncode,
                    result.stderr[:500],
                )
                return None
            if not transcode_to_wav(tmp_media, out_path):
                return None
            return out_path
    except Exception:
        log.warning("edge-tts synthesis raised an exception", exc_info=True)
        return None
    finally:
        try:
            tmp_media.unlink(missing_ok=True)
        except Exception:
            pass


def synthesize_voiceover(
    text: str,
    out_path: Path,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
) -> Path | None:
    """Synthesise *text* into a WAV file, returning the path or ``None``.

    Routes to Piper when ``SHORTS_VO_ENGINE`` is ``piper`` (the default) and
    silently falls back to edge-tts when Piper is unusable.  ``SHORTS_VO_ENGINE=edge``
    forces edge-tts.  ``rate``/``pitch`` stay meaningful for both engines:
    edge-tts takes them verbatim, Piper maps ``rate`` onto its (inverted)
    ``length_scale`` and ignores ``pitch`` because VITS has no pitch control.
    Returns ``None`` — never raises — when no engine could produce audio.
    """
    if not text or not text.strip():
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if voiceover_engine() == "piper":
        path, _boundaries = _synthesize_piper(text, out_path, voice, rate, pitch)
        if path is not None:
            return path

    return _synthesize_edge(text, out_path, voice, rate, pitch)


def _synthesize_piper(
    text: str,
    out_path: Path,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
) -> tuple:
    """Try Piper.  Returns ``(wav_path, word_boundaries)``.

    ``wav_path`` is ``None`` on every failure path (package missing, model not
    cached, download failed, synthesis raised); ``audio.piper_tts`` logs the
    specific reason before returning, and this wrapper logs the engine switch
    so the fallback is visible in the render log.
    """
    try:
        from shorts_clipper.audio.piper_tts import synthesize as piper_synthesize

        path, boundaries = piper_synthesize(
            text,
            out_path,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
    except Exception:
        # A bug in the Piper path must never take down a render.
        log.warning("Piper voiceover raised an exception — falling back to edge-tts", exc_info=True)
        return None, []
    if path is None:
        log.info("Falling back to edge-tts for this voiceover")
        return None, []
    return path, boundaries


def _edge_tts_library_available() -> bool:
    """True when the edge_tts *library* (not just the CLI) is importable."""
    try:
        import edge_tts  # noqa: F401

        return True
    except Exception:
        return False


def _synthesize_audio_bytes(
    text: str,
    voice: str | None,
    rate: str,
    pitch: str | None,
) -> tuple[bytes | None, list]:
    """Synthesise *text* via the edge_tts library, capturing word boundaries.

    Returns ``(mp3_bytes, word_boundaries)`` where each boundary item is
    ``(word, start_seconds, end_seconds)`` matched to the audible speech,
    rather than a uniform grid.  ``offset``/``duration`` delivered by
    edge-tts are in 100ns ticks, so they are converted to seconds.
    """

    async def _run():
        try:
            import edge_tts
        except Exception:
            return None, []

        last_exc: Exception | None = None
        for _attempt in range(3):
            try:
                kwargs = {"voice": voice or pick_voice(text), "rate": rate}
                if pitch:
                    kwargs["pitch"] = pitch
                communicate = edge_tts.Communicate(text=text, boundary="WordBoundary", **kwargs)
            except TypeError:
                communicate = edge_tts.Communicate(text=text, voice=voice or pick_voice(text), rate=rate)
            audio = bytearray()
            boundaries: list = []
            try:
                async for chunk in communicate.stream():
                    ctype = chunk.get("type")
                    if ctype == "audio":
                        data = chunk.get("data") or chunk.get("raw") or b""
                        if data:
                            audio += data
                    elif ctype in ("WordBoundary", "word_boundary"):
                        start = secs(chunk.get("offset"))
                        end = secs(chunk.get("duration")) + start
                        word = chunk.get("text")
                        if word is not None and end > start:
                            boundaries.append((word, start, end))
            except Exception as exc:
                last_exc = exc
                continue
            if audio:
                return bytes(audio), boundaries
        log.warning("edge_tts streaming failed after retries", exc_info=last_exc)
        return None, []

    def secs(ticks) -> float:
        try:
            return float(ticks) / 10_000_000.0
        except Exception:
            return 0.0

    return asyncio_run(_run())


def asyncio_run(coro):
    import asyncio

    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def normalize_word_boundaries(boundaries, *, duration: float | None = None) -> list:
    """Coerce a raw boundary list into the shape the caption path relies on.

    The subtitle builder assumes ``(word, start, end)`` tuples that are sorted
    by ``start`` with a strictly positive span, all inside the audio —
    see ``pipeline.stock_runner._segments_from_word_bounds`` and
    ``tests/test_stock_sync.py::test_boundaries_monotonic``.  Piper alignments,
    edge-tts ticks and proportional fallbacks can each violate that in
    different ways, so every engine's output goes through here.

    Returns ``[]`` for an empty/blank input (no synthesised words to time).
    """
    cleaned: list = []
    for item in boundaries or []:
        try:
            word, start, end = item
        except (TypeError, ValueError):
            continue
        token = str(word or "").strip()
        if not token:
            continue
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            continue
        cleaned.append((token, start, end))
    if not cleaned:
        return []

    cleaned.sort(key=lambda item: item[1])

    ceiling = float(duration) if duration is not None and duration > 0.0 else None
    normalized: list = []
    floor = 0.0
    for token, start, end in cleaned:
        start = max(0.0, start)
        end = max(start, end)
        if ceiling is not None:
            start = min(start, ceiling)
            end = min(end, ceiling)
        # Keep starts monotonic and guarantee a non-zero span so a zero-length
        # or overlapping entry can never produce an inverted ASS \k interval.
        if start < floor:
            start = floor
        if end <= start:
            end = start + 0.01
        normalized.append((token, start, end))
        floor = start
    return normalized


def finalize_word_boundaries(text: str, boundaries, audio_path) -> list:
    """Return a usable boundary list for *audio_path*, or ``[]`` if impossible.

    Guarantee relied upon by the caption path: when audio exists, the returned
    list is non-empty, ordered and bounded by the audio length.  *boundaries*
    is whatever the engine reported (real edge-tts word events, a whisper
    alignment, or a proportional split); when it is missing or unusable the
    timings are distributed proportionally over the detected speech window so
    subtitles still follow the voice.

    Those proportional timings DRIFT: word character count is only a proxy for
    duration, so punctuation pauses and syllable structure are ignored.
    """
    duration = _tts_wav_duration(audio_path) if audio_path is not None else None
    normalized = normalize_word_boundaries(boundaries, duration=duration)
    if normalized:
        return normalized
    if not duration or duration <= 0.0:
        return []
    try:
        from shorts_clipper.audio.piper_tts import proportional_word_boundaries

        window = speech_window(audio_path)
        start, end = window if window is not None else (0.0, duration)
        fallback = proportional_word_boundaries(text, start, end)
    except Exception:
        log.warning("Could not derive fallback word timings", exc_info=True)
        return []
    normalized = normalize_word_boundaries(fallback, duration=duration)
    if normalized:
        log.info("Using proportional word timings for %d words (no engine boundaries)", len(normalized))
    return normalized


def synthesize_voiceover_boundaries(
    text: str,
    out_path: Path,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
):
    """Synthesise *text* to WAV and return spoken word boundaries.

    Returns ``(wav_path, [(word, start_s, end_s), ...])``.

    Piper is tried first unless ``SHORTS_VO_ENGINE=edge``.  edge-tts supplies
    real ``WordBoundary`` events; Piper does not, so its timings come from
    aligning the generated audio with faster-whisper and, failing that, from a
    proportional split over the speech window.  Whenever audio was produced
    the boundary list is non-empty and ordered (see
    :func:`finalize_word_boundaries`) — subtitle sync depends on it.

    Returns ``(None, [])`` only when no engine could produce audio at all.
    """
    if not text or not text.strip():
        return None, []

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if voiceover_engine() == "piper":
        path, boundaries = _synthesize_piper(text, out_path, voice, rate, pitch)
        if path is not None:
            return path, finalize_word_boundaries(text, boundaries, path)

    return _synthesize_edge_boundaries(text, out_path, voice, rate, pitch)


def _synthesize_edge_boundaries(
    text: str,
    out_path: Path,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
) -> tuple:
    """edge-tts synthesis that captures streamed ``WordBoundary`` metadata."""
    audio, boundaries = (None, [])
    if _edge_tts_library_available():
        audio, boundaries = _synthesize_audio_bytes(text, voice, rate, pitch)

    if audio is None or not audio:
        synth = _synthesize_edge(text, out_path, voice=voice, rate=rate, pitch=pitch)
        return (synth, finalize_word_boundaries(text, [], synth)) if synth else (None, [])

    import os
    import tempfile

    fd, tmp_name = tempfile.mkstemp(suffix=".mp3", prefix="edgetts_")
    os.close(fd)
    tmp_media = Path(tmp_name)
    try:
        tmp_media.write_bytes(audio)
        if not transcode_to_wav(tmp_media, out_path):
            synth = _synthesize_edge(text, out_path, voice=voice, rate=rate, pitch=pitch)
            return (synth, finalize_word_boundaries(text, [], synth)) if synth else (None, [])
        return out_path, finalize_word_boundaries(text, boundaries, out_path)
    finally:
        try:
            tmp_media.unlink(missing_ok=True)
        except Exception:
            pass


def resolve_speech_end(intervals, duration, epsilon=0.05):
    """Return corrected speech end with trailing-silence awareness."""
    if not intervals:
        if duration is not None and float(duration) > 0.0:
            return max(0.0, float(duration) - float(epsilon))
        return 0.0
    last_start = float(intervals[-1][0])
    last_end = float(intervals[-1][1])
    if duration is None or float(duration) <= 0.0:
        return last_start
    if float(last_end) >= float(duration) - 0.2:
        return last_start
    return max(float(last_end), float(duration) - float(epsilon))


def _tts_wav_duration(audio_path):
    """Return WAV duration in seconds or None when unreadable."""
    try:
        import wave

        with wave.open(str(audio_path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            if rate and rate > 0 and frames >= 0:
                return float(frames) / float(rate)
            return None
    except Exception:
        return None


def speech_window(audio_path, threshold_db=-35.0, min_silence=0.4):
    """Locate actual speech window with trailing-silence aware end."""
    from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return None
    silence_starts = []
    silence_ends = []
    for line in result.stderr.splitlines():
        text = line.strip()
        try:
            if "silence_start" in text:
                silence_starts.append(float(text.rsplit(":", 1)[1].strip()))
            elif "silence_end" in text:
                silence_ends.append(float(text.rsplit("|", 1)[0].split(":", 1)[1].strip()))
        except ValueError:
            continue
    if not silence_starts and not silence_ends:
        return None
    intervals = list(zip(silence_starts, silence_ends, strict=False))
    if not intervals:
        return None
    if intervals[0][0] <= 0.05:
        speech_start = float(intervals[0][1])
    else:
        speech_start = 0.0
    duration = _tts_wav_duration(audio_path)
    if duration is None:
        speech_end = float(intervals[-1][0])
    else:
        speech_end = float(resolve_speech_end(intervals, duration))
    if speech_end <= speech_start + 0.3:
        return None
    return float(speech_start), float(speech_end)
