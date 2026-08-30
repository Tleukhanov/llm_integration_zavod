from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class SilenceJudge(EditorialJudge):
    """Penalizes clips with excessive silence/dead air."""

    @property
    def name(self) -> str:
        return "SilenceJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        if features.longest_pause > 3.0:
            return JudgeResult(
                score=0.0,
                confidence=0.9,
                reasoning=f"Excessive silence detected ({features.longest_pause:.1f}s pause).",
                reject_hard=True,
            )

        if features.longest_pause > 1.5:
            score = max(0.0, 100.0 - (features.longest_pause * 30))
            return JudgeResult(
                score=score,
                confidence=0.8,
                reasoning=f"Moderate silence penalized ({features.longest_pause:.1f}s pause).",
            )

        return JudgeResult(
            score=100.0, confidence=0.8, reasoning="Good pacing with no long silences."
        )
