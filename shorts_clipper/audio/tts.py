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

import logging
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_tts_lock = threading.Lock()


def _edge_tts_available() -> bool:
    return shutil.which("edge-tts") is not None


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
    voice: str = "en-US-GuyNeural",
    rate: str = "+8%",
) -> Path | None:
    """Synthesise *text* into a WAV file via edge-tts.

    Uses a module-level lock to serialise concurrent calls.  Returns the
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

    try:
        with _tts_lock:
            result = subprocess.run(
                [
                    "edge-tts",
                    "--voice",
                    voice,
                    "--rate",
                    rate,
                    "--text",
                    text.strip(),
                    "--write-media",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                log.warning("edge-tts failed (exit %d): %s", result.returncode, result.stderr[:500])
                return None
            if not out_path.is_file():
                log.warning("edge-tts did not produce output file: %s", out_path)
                return None
            return out_path
    except Exception:
        log.warning("edge-tts synthesis raised an exception", exc_info=True)
        return None
