from __future__ import annotations

from shorts_clipper.editorial.interfaces import EditorialJudge
from shorts_clipper.editorial.models import FeatureSet, JudgeResult


class HookJudge(EditorialJudge):
    """Evaluates if the first few seconds create attention."""

    HOOK_PATTERNS = [
        "you won't believe",
        "secret",
        "mistake",
        "watch this",
        "here's why",
        "this is why",
        "the truth",
        "wait",
        "look",
        "listen",
    ]

    @property
    def name(self) -> str:
        return "HookJudge"

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        if not features.raw_segments:
            return JudgeResult(score=0.0, confidence=1.0, reasoning="No audio.")

        # Get the first 5 seconds of transcript text
        start_time = features.raw_segments[0].start
        hook_text_parts = []
        for s in features.raw_segments:
            if s.end <= start_time + 5.0:
                hook_text_parts.append(s.text.strip())
            else:
                break

        hook_text = " ".join(hook_text_parts).lower()

        score = 0.0
        reasoning = []

        # Check for strong hook keywords
        if any(p in hook_text for p in self.HOOK_PATTERNS):
            score += 50.0
            reasoning.append("Strong hook keyword detected.")

        # Check for immediate question
        if "?" in hook_text:
            score += 30.0
            reasoning.append("Poses an immediate question.")

        # Check energy (words per second in the first 5 seconds)
        if features.words_per_second > 2.5:
            score += 20.0
            reasoning.append("High energy start.")

        if score == 0.0:
            reasoning.append("No strong hook detected.")

        return JudgeResult(
            score=min(100.0, score), confidence=0.85, reasoning=" | ".join(reasoning)
        )
