"""Transcript formatting helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shorts_clipper.core.models import TranscriptSegment, TranscriptWord


def segment_from_any(segment: Any) -> TranscriptSegment:
    words = []
    for word in getattr(segment, "words", None) or []:
        words.append(
            TranscriptWord(
                start=float(word.start),
                end=float(word.end),
                word=str(word.word),
                probability=getattr(word, "probability", None),
            )
        )
    return TranscriptSegment(
        start=float(segment.start),
        end=float(segment.end),
        text=str(getattr(segment, "text", "")).strip(),
        words=words,
    )


def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_transcript(segments: Iterable[TranscriptSegment | Any]) -> str:
    formatted = []
    for raw_segment in segments:
        segment = (
            raw_segment
            if isinstance(raw_segment, TranscriptSegment)
            else segment_from_any(raw_segment)
        )
        formatted.append(f"[{segment.start:.2f}s -> {segment.end:.2f}s]: {segment.text}")
    return "\n".join(formatted)


def to_srt(segments: Iterable[TranscriptSegment | Any], start_offset: float = 0.0) -> str:
    lines = []
    for i, raw_segment in enumerate(segments, 1):
        segment = (
            raw_segment
            if isinstance(raw_segment, TranscriptSegment)
            else segment_from_any(raw_segment)
        )
        # Shift timestamps relative to the clip start
        start = max(0, segment.start - start_offset)
        end = max(0, segment.end - start_offset)

        if end <= start:
            continue

        lines.append(str(i))
        lines.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        lines.append(segment.text)
        lines.append("")
    return "\n".join(lines)
