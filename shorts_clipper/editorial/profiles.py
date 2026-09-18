from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EditorialProfile:
    """Defines weights and minimum thresholds for judges.

    ``retention_floor`` (optional, 0..1): when set, a niche+platform pair is
    only worth publishing if the current retention-grade value (0..1) for
    that pair is >= floor.
    """

    name: str
    weights: dict[str, float] = field(default_factory=dict)
    default_weight: float = 1.0
    retention_floor: float | None = None


# Define base profiles
DEFAULT_PROFILE = EditorialProfile(
    name="Default",
    weights={
        "HookJudge": 1.5,
        "SilenceJudge": 1.0,
        "ContextJudge": 1.5,
        "EmotionJudge": 1.2,
        "LengthJudge": 1.0,
        "NarrativeArcJudge": 1.0,
        "InformationDensityJudge": 1.0,
        "QuestionAnswerJudge": 1.0,
    },
    default_weight=1.0,
)

PODCAST_PROFILE = EditorialProfile(
    name="Podcast",
    weights={
        "HookJudge": 1.2,
        "SilenceJudge": 0.8,  # Silence is more acceptable
        "ContextJudge": 2.0,  # Context is critical
        "EmotionJudge": 1.0,
        "LengthJudge": 0.9,
        "NarrativeArcJudge": 1.4,  # Narrative flow matters in podcasts
        "InformationDensityJudge": 1.1,
        "QuestionAnswerJudge": 1.2,
    },
    default_weight=1.0,
)


def get_profile(name: str) -> EditorialProfile:
    profiles = {
        "default": DEFAULT_PROFILE,
        "podcast": PODCAST_PROFILE,
    }
    return profiles.get(name.lower(), DEFAULT_PROFILE)
