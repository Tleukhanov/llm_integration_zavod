from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shorts_clipper.core.models import ClipWindow, TranscriptSegment


@dataclass
class FeatureSet:
    """Computed features for a specific transcript window.

    Computed once by FeatureStore and shared across all Judges.
    """

    total_duration: float
    word_count: int
    words_per_second: float
    longest_pause: float
    starts_with_conjunction: bool
    ends_with_punctuation: bool
    question_count: int
    exclamation_count: int
    has_hanging_pronoun: bool
    text_content: str
    raw_segments: list[TranscriptSegment] = field(default_factory=list)


@dataclass
class JudgeResult:
    """The result returned by a single EditorialJudge."""

    score: float  # 0.0 to 100.0
    confidence: float  # 0.0 to 1.0
    reasoning: str
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reject_hard: bool = False  # If True, the clip is completely rejected in Stage 1


@dataclass
class EditorialDecision:
    """The final decision for a candidate clip after all pipeline stages."""

    clip_window: ClipWindow
    final_score: float
    confidence: float
    reasoning: str
    judge_results: dict[str, JudgeResult] = field(default_factory=dict)
    rejected: bool = False
