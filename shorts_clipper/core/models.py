"""Typed domain models for transcripts, clip windows, scores, and jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    start: float
    end: float
    word: str
    probability: float | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = field(default_factory=list)
    speaker: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class ClipWindow:
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("clip start must be non-negative")
        if self.end <= self.start:
            raise ValueError("clip end must be greater than start")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class HighlightScore:
    hook: float = 0.0
    emotion: float = 0.0
    silence: float = 0.0
    retention: float = 0.0
    virality: float = 0.0
    topic: float = 0.0
    speaker_emphasis: float = 0.0
    caption_density: float = 0.0
    # New AI Metrics
    emotional_category: str = "unknown"
    hook_strength: float = 0.0
    pacing_score: float = 0.0
    replayability_score: float = 0.0
    strongest_caption_phrase: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return (
            self.hook
            + self.emotion
            + self.silence
            + self.retention
            + self.virality
            + self.topic
            + self.speaker_emphasis
            + self.caption_density
        )


@dataclass(frozen=True, slots=True)
class RenderPreset:
    name: str = "reels_default"
    width: int = 1080
    height: int = 1920
    fps: int | None = None
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 18


@dataclass(frozen=True, slots=True)
class ClipJob:
    source_url: str
    work_dir: Path
    output_path: Path
    dry_run: bool = False
