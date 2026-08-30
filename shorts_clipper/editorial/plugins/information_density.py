from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class InformationDensityJudge(EditorialJudge):
    """Evaluates if the clip is information dense."""

    @property
    def name(self) -> str:
        return "InformationDensityJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        reasoning = []

        if features.words_per_second > 2.8:
            score += 50.0
            reasoning.append("Very high information density.")
        elif features.words_per_second > 2.0:
            score += 25.0
            reasoning.append("Good information density.")
        else:
            reasoning.append("Low information density (slow pacing).")

        return JudgeResult(
            score=score, confidence=0.9, reasoning=" | ".join(reasoning) or "Average density."
        )
