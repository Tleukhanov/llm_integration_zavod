from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class NarrativeArcJudge(EditorialJudge):
    """Evaluates if the clip forms a complete thought."""

    @property
    def name(self) -> str:
        return "NarrativeArcJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 50.0
        reasoning = []

        if features.starts_with_conjunction:
            score -= 20.0
            reasoning.append("Starts mid-thought (conjunction).")
        else:
            score += 20.0
            reasoning.append("Starts with a fresh thought.")

        if features.ends_with_punctuation:
            score += 30.0
            reasoning.append("Ends on a complete sentence.")
        else:
            score -= 20.0
            reasoning.append("Ends abruptly (no punctuation).")

        if features.has_hanging_pronoun:
            score -= 20.0
            reasoning.append("Ends with a hanging pronoun.")

        return JudgeResult(
            score=max(0.0, min(100.0, score)), confidence=0.85, reasoning=" | ".join(reasoning)
        )
