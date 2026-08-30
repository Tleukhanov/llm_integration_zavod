"""Editorial Judges for the Attention Engine."""

from shorts_clipper.attention.judges.base import AttentionJudge, JudgeRegistry
from shorts_clipper.attention.models import AttentionImpact, FeatureSet, JudgeResult


@JudgeRegistry.register("scroll_stop")
class ScrollStopJudge(AttentionJudge):
    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        signals = []

        if features.emotion_hits > 0:
            score += min(0.4, features.emotion_hits * 0.2)
            signals.append("High emotional hook")

        if getattr(features, "hook_hits", 0) > 0:
            score += 0.4
            signals.append("Strong verbal hook detected")

        if features.viral_hits > 0:
            score += min(0.3, features.viral_hits * 0.15)
            signals.append("Viral trigger words detected")

        if features.words_per_second > 2.0:
            score += 0.3
            signals.append("Fast pacing")

        confidence = 0.85

        impact = AttentionImpact.ADD if score > 0.6 else AttentionImpact.SPEND

        return JudgeResult(
            score=min(1.0, score),
            confidence=confidence,
            reason="Calculated scroll stopping probability based on initial emotional intensity and pacing.",
            signals=signals,
            evidence=[features.text[:50] + "..."] if score > 0 else [],
            impact=impact,
        )


@JudgeRegistry.register("payoff")
class PayoffJudge(AttentionJudge):
    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        signals = []

        if features.sentiment > 0.5 or features.sentiment < -0.5:
            score += 0.4
            signals.append("Strong emotional polarity")

        if features.money_references > 0 or features.numbers > 0:
            score += 0.4
            signals.append("Concrete quantitative payoff")

        if features.exclamations > 0:
            score += 0.2
            signals.append("High energy delivery")

        confidence = 0.85
        impact = AttentionImpact.ADD if score > 0.5 else AttentionImpact.PRESERVE

        return JudgeResult(
            score=min(1.0, score),
            confidence=confidence,
            reason="Assessed strength of the climax/payoff via sentiment and concrete facts.",
            signals=signals,
            evidence=[features.text[-50:] + "..."] if score > 0 else [],
            impact=impact,
        )
