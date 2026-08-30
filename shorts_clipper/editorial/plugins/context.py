from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class ContextJudge(EditorialJudge):
    """Evaluates if the clip is context-independent."""

    @property
    def name(self) -> str:
        return "ContextJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 100.0
        reasoning = []

        if features.starts_with_conjunction:
            score -= 30.0
            reasoning.append("Starts mid-thought (conjunction).")

        if features.has_hanging_pronoun:
            score -= 40.0
            reasoning.append("Ends with unresolved pronoun.")

        if not features.ends_with_punctuation:
            score -= 30.0
            reasoning.append("Does not end with terminal punctuation.")

        if score == 100.0:
            reasoning.append("Strong semantic boundaries.")

        return JudgeResult(score=max(0.0, score), confidence=0.9, reasoning=" | ".join(reasoning))
