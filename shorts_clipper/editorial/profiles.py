from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EditorialProfile:
    """Defines weights and minimum thresholds for judges."""

    name: str
    weights: dict[str, float] = field(default_factory=dict)
    default_weight: float = 1.0


# Define base profiles
DEFAULT_PROFILE = EditorialProfile(
    name="Default",
    weights={
        "HookJudge": 1.5,
        "CuriosityJudge": 1.2,
        "SilenceJudge": 1.0,
        "ContextJudge": 1.5,
        "PacingJudge": 1.0,
        "EmotionJudge": 1.2,
    },
    default_weight=1.0,
)

PODCAST_PROFILE = EditorialProfile(
    name="Podcast",
    weights={
        "HookJudge": 1.2,
        "CuriosityJudge": 1.5,  # High curiosity is important for podcasts
        "SilenceJudge": 0.8,  # Silence is more acceptable
        "ContextJudge": 2.0,  # Context is critical
        "PacingJudge": 0.8,
        "EmotionJudge": 1.0,
    },
    default_weight=1.0,
)


def get_profile(name: str) -> EditorialProfile:
    profiles = {
        "default": DEFAULT_PROFILE,
        "podcast": PODCAST_PROFILE,
    }
    return profiles.get(name.lower(), DEFAULT_PROFILE)
