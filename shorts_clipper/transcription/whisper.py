"""Local Whisper transcription using faster-whisper with Gemini Flash acceleration."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)


def _get_audio_bytes(media_path: Path) -> tuple[bytes, str]:
    """Ensure we have audio bytes and the correct mime type."""
    suffix = media_path.suffix.lower()
    if suffix in {".m4a", ".mp3", ".wav", ".ogg", ".aac"}:
        mime = f"audio/{suffix[1:]}"
        if suffix == ".m4a":
            mime = "audio/m4a"
        return media_path.read_bytes(), mime

    # If it is a video or other format, extract audio to a temporary m4a file
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        cmd = [
            ffmpeg_path(),
            "-y",
            "-i",
            str(media_path),
            "-vn",
            "-c:a",
            "aac",
            str(tmp_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        return tmp_path.read_bytes(), "audio/m4a"
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _transcribe_with_gemini(
    media_path: Path,
    api_key: str | None = None,
) -> list[TranscriptSegment] | None:
    """Attempt to transcribe the media file using Gemini 2.5 Flash."""
    try:
        from google.genai import types

        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            log.warning("Gemini API key not found. Skipping Gemini transcription.")
            return None

        log.info("🚀 Attempting fast transcription with Gemini 2.5 Flash...")
        audio_bytes, mime_type = _get_audio_bytes(media_path)

        from shorts_clipper.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key=api_key)

        prompt = (
            "Transcribe this audio file. Format the response as a JSON array of objects, "
            "where each object represents a segment:\n"
            '- "start": float representing segment start in seconds\n'
            '- "end": float representing segment end in seconds\n'
            '- "text": string representing segment text\n'
            '- "words": list of objects representing each word in the segment, '
            "where each object has:\n"
            '  - "word": string representing the word\n'
            '  - "start": float representing when the word starts in seconds\n'
            '  - "end": float representing when the word ends in seconds\n\n'
            "Do NOT include any markdown code blocks, introductory, or concluding text. "
            "Return ONLY the raw valid JSON."
        )

        response = provider.generate_content(
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

        raw_text = response.text.strip()
        json_match = re.search(r"```(?:json)?(.*?)```", raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(1).strip()

        # Fix trailing commas that cause json.loads to fail
        raw_text = re.sub(r",\s*([\]}])", r"\1", raw_text)

        data = json.loads(raw_text)
        segments: list[TranscriptSegment] = []
        for item in data:
            words = []
            for w in item.get("words", []):
                words.append(
                    TranscriptWord(
                        start=float(w["start"]),
                        end=float(w["end"]),
                        word=str(w["word"]).strip(),
                    )
                )
            segments.append(
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item["text"]).strip(),
                    words=words,
                )
            )

        log.info("✅ Gemini transcription successful: %d segments", len(segments))
        return segments
    except Exception as exc:
        log.warning("⚠️  Gemini transcription failed (%s). Falling back to local Whisper.", exc)
        return None


_global_model = None
_model_lock = threading.Lock()
_transcription_semaphore = threading.Semaphore(1)


def get_whisper_model():
    """Load the Whisper model (singleton pattern)."""
    global _global_model
    if _global_model is not None:
        log.info("[WHISPER] reusing existing model")
        return _global_model

    with _model_lock:
        if _global_model is not None:
            log.info("[WHISPER] reusing existing model")
            return _global_model

        from faster_whisper import WhisperModel

        from shorts_clipper.core.settings import Settings

        settings = Settings.from_env()
        t_start = time.time()
        log.info(
            "Loading Whisper model '%s' on %s...", settings.whisper_model, settings.whisper_device
        )

        # Explicitly configure cpu_threads=4 to match typical VPS sizing.
        # This prevents runaway CPU oversubscription and OpenMP thread contention.
        _global_model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            cpu_threads=4,
        )
        t_end = time.time()
        log.info(f"[WHISPER] model_load_time: {t_end - t_start:.1f}s")
        log.info("[WHISPER] global model initialized")
        return _global_model


def transcribe_clip(
    video_path: str | Path,
    *,
    beam_size: int = 1,
) -> list[TranscriptSegment]:
    """
    Transcribe a video/audio file locally with faster-whisper.

    Args:
        video_path: Path to the video or audio file.
        beam_size: Beam search width (higher = more accurate, slower).

    Returns:
        List of TranscriptSegment with word-level timestamps where available.
    """
    video_path = Path(video_path)

    # Try Gemini first for ultra-fast, premium transcription
    # gemini_segments = _transcribe_with_gemini(video_path)
    # if gemini_segments is not None:
    #     return gemini_segments

    log.info("🎙 Transcribing %s with Whisper...", video_path.name)

    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()

    log.info("[WHISPER] waiting for transcription slot")
    with _transcription_semaphore:
        log.info("[WHISPER] transcription slot acquired")
        model = get_whisper_model()
        t_inference_start = time.time()
        raw_segments, info = model.transcribe(
            str(video_path),
            beam_size=beam_size,
            word_timestamps=True,
            language=settings.whisper_language or None,
        )
        # Convert generator to list to force execution
        raw_segments = list(raw_segments)
        t_inference_end = time.time()

    log.info("[WHISPER] transcription complete")
    log.info(f"[WHISPER] inference_time: {t_inference_end - t_inference_start:.1f}s")

    if settings.whisper_language:
        if info.language != settings.whisper_language:
            log.warning(
                "⚠️  Whisper detected language '%s' (requested '%s'). "
                "Transcript may be inaccurate — continuing anyway.",
                info.language,
                settings.whisper_language,
            )
    else:
        log.info(
            "[WHISPER] auto-detected language: '%s' (confidence %.2f)",
            info.language,
            info.language_probability,
        )

    segments: list[TranscriptSegment] = []
    for seg in raw_segments:
        words = [
            TranscriptWord(
                start=float(w.start),
                end=float(w.end),
                word=w.word.strip(),
                probability=float(w.probability) if w.probability is not None else None,
            )
            for w in (seg.words or [])
        ]
        segments.append(
            TranscriptSegment(
                start=float(seg.start),
                end=float(seg.end),
                text=seg.text.strip(),
                words=words,
            )
        )

    log.info("✅ Transcription done: %d segments (lang=%s)", len(segments), info.language)
    return segments
