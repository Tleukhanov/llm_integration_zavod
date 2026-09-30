"""Optional TTS voiceover generation via edge-tts.

The voiceover layer adds an original audio track to each clip, making it
unique and boosting engagement.  edge-tts is treated as an optional
dependency — if the CLI / package is not installed the module degrades
gracefully (returns ``None``, logs a warning, never crashes the pipeline).

A module-level lock serialises concurrent synthesisation calls so that
multiple clip renders sharing the same process do not collide on
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


def synthesize_voiceover(
    text: str,
    out_path: Path,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
) -> Path | None:
    """Synthesise *text* into a WAV file via edge-tts.

    Uses a module-level lock to serialise concurrent calls.  ``voice`` may be
    ``None`` to auto-select by detected text language.  ``rate`` (speed,
    ``"+8%"``) and ``pitch`` (e.g. ``"+4Hz"``) tune expressiveness — a slight
    positive pitch makes Neural voices sound less robotic.  Returns the
    output ``Path`` on success or ``None`` if edge-tts is unavailable /
    any error occurs.
    """
    if not text or not text.strip():
        return None

    if not _edge_tts_available():
        log.warning("edge-tts CLI not found — skipping voiceover synthesis")
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

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

            from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

            convert = subprocess.run(
                [
                    ffmpeg_path(),
                    "-y",
                    "-i",
                    str(tmp_media),
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if convert.returncode != 0 or not out_path.is_file():
                log.warning(
                    "TTS transcode to WAV failed (exit %d): %s",
                    convert.returncode,
                    convert.stderr[-500:],
                )
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


def synthesize_voiceover_boundaries(
    text: str,
    out_path: Path,
    voice: str | None = None,
    rate: str = "+8%",
    pitch: str | None = None,
):
    """Synthesise *text* to WAV and return spoken word boundaries.

    Returns ``(wav_path, [(word, start_s, end_s), ...])``.  Falls back to the
    plain CLI synthesis returning ``(wav_path, [])`` when the library is
    missing or the stream yields no boundaries; returns ``(None, [])`` on
    failure.
    """
    if not text or not text.strip():
        return None, []

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    audio, boundaries = (None, [])
    if _edge_tts_library_available():
        audio, boundaries = _synthesize_audio_bytes(text, voice, rate, pitch)

    if audio is None or not audio:
        synth = synthesize_voiceover(text, out_path, voice=voice, rate=rate, pitch=pitch)
        return (synth, []) if synth else (None, [])

    import os
    import subprocess
    import tempfile

    fd, tmp_name = tempfile.mkstemp(suffix=".mp3", prefix="edgetts_")
    os.close(fd)
    tmp_media = Path(tmp_name)
    try:
        tmp_media.write_bytes(audio)
        from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

        convert = subprocess.run(
            [
                ffmpeg_path(),
                "-y",
                "-i",
                str(tmp_media),
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if convert.returncode != 0 or not out_path.is_file():
            log.warning("edge_tts transcode to WAV failed (exit %d)", convert.returncode)
            synth = synthesize_voiceover(text, out_path, voice=voice, rate=rate, pitch=pitch)
            return (synth, []) if synth else (None, [])
        return out_path, boundaries
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
