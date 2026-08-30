from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class EmotionJudge(EditorialJudge):
    """Detects emotional intensity based on keyword density and punctuation."""

    EMOTION_WORDS = {
        "amazing",
        "insane",
        "crazy",
        "shocking",
        "unbelievable",
        "love",
        "hate",
        "angry",
        "terrified",
        "best",
        "worst",
        "changed",
        "everything",
    }

    @property
    def name(self) -> str:
        return "EmotionJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        text = features.text_content.lower()

        emotion_hits = sum(1 for word in text.split() if word in self.EMOTION_WORDS)

        score = min(100.0, (emotion_hits * 15.0) + (features.exclamation_count * 10.0))

        if score == 0.0:
            reasoning = "Static emotion. No strong emotion words or emphasis detected."
        else:
            reasoning = f"Detected {emotion_hits} emotion words and {features.exclamation_count} exclamations."

        return JudgeResult(
            score=score,
            confidence=0.7,  # Lower confidence because keyword matching is crude
            reasoning=reasoning,
        )
