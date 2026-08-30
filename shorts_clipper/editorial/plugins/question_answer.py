from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class QuestionAnswerJudge(EditorialJudge):
    """Evaluates if the clip poses a question and provides an answer."""

    @property
    def name(self) -> str:
        return "QuestionAnswerJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        reasoning = []

        if features.question_count > 0:
            score += 60.0
            reasoning.append(
                f"Contains {features.question_count} question(s), suggesting an engaging Q&A format."
            )
        else:
            reasoning.append("No explicit questions posed.")

        return JudgeResult(score=score, confidence=0.8, reasoning=" | ".join(reasoning))
