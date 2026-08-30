from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class LengthJudge(EditorialJudge):
    """Rejects clips that are too short or too long."""

    MIN_DURATION = 15.0
    MAX_DURATION = 65.0

    @property
    def name(self) -> str:
        return "LengthJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        dur = features.total_duration

        if dur < self.MIN_DURATION:
            return JudgeResult(
                score=0.0,
                confidence=1.0,
                reasoning=f"Clip too short ({dur:.1f}s < {self.MIN_DURATION}s).",
                reject_hard=True,
            )

        if dur > self.MAX_DURATION:
            return JudgeResult(
                score=0.0,
                confidence=1.0,
                reasoning=f"Clip too long ({dur:.1f}s > {self.MAX_DURATION}s).",
                reject_hard=True,
            )

        # Perfect duration (30-45s) gets max score
        score = 100.0
        if dur < 30.0:
            score = 100.0 - (30.0 - dur) * 2
        elif dur > 45.0:
            score = 100.0 - (dur - 45.0) * 2

        return JudgeResult(
            score=max(0.0, score), confidence=1.0, reasoning=f"Acceptable duration ({dur:.1f}s)."
        )
