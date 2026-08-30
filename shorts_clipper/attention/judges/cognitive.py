"""Cognitive Psychology Judges for the Attention Engine."""

from shorts_clipper.attention.judges.base import AttentionJudge, JudgeRegistry
from shorts_clipper.attention.models import AttentionImpact, FeatureSet, JudgeResult


@JudgeRegistry.register("curiosity")
class CuriosityJudge(AttentionJudge):
    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        signals = []

        if features.questions > 0:
            score += min(0.4, features.questions * 0.2)
            signals.append("Contains questions")

        if features.story_arc_markers > 0:
            score += min(0.3, features.story_arc_markers * 0.1)
            signals.append("Story progression markers detected")

        if "secret" in features.raw_words or "truth" in features.raw_words:
            score += 0.3
            signals.append("Information gap triggers found")

        confidence = 0.85

        impact = AttentionImpact.ADD if score > 0.4 else AttentionImpact.PRESERVE

        return JudgeResult(
            score=min(1.0, score),
            confidence=confidence,
            reason="Assessed curiosity gaps based on questions and information withholding.",
            signals=signals,
            evidence=[features.text[:50] + "..."] if score > 0 else [],
            impact=impact,
        )


@JudgeRegistry.register("information_density")
class InformationDensityJudge(AttentionJudge):
    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        signals = []

        wps_norm = min(1.0, features.words_per_second / 3.0)
        score += wps_norm * 0.4

        if features.numbers > 0 or features.money_references > 0:
            score += 0.4
            signals.append(
                f"Contains quantitative data ({features.numbers} numbers, {features.money_references} money refs)"
            )

        if features.time_references > 0:
            score += 0.2
            signals.append(f"Contains time references ({features.time_references})")

        if wps_norm > 0.8:
            signals.append("High speech rate")

        confidence = 0.85

        # If density is too high, it might spend attention (cognitive overload)
        impact = AttentionImpact.SPEND if score > 0.8 else AttentionImpact.ADD

        return JudgeResult(
            score=min(1.0, score),
            confidence=confidence,
            reason="Measured cognitive load and information density via speech rate and quantitative entities.",
            signals=signals,
            evidence=[features.text[:50] + "..."] if score > 0 else [],
            impact=impact,
        )


@JudgeRegistry.register("prediction_error")
class PredictionErrorJudge(AttentionJudge):
    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        signals = []

        if features.contradictions > 0:
            score += min(0.8, features.contradictions * 0.4)
            signals.append(f"Found {features.contradictions} contradiction/subversion markers")

        if "but" in features.raw_words or "however" in features.raw_words:
            score += 0.2
            signals.append("Expectation subversion words present")

        confidence = 0.85

        impact = AttentionImpact.ADD if score > 0.2 else AttentionImpact.PRESERVE

        return JudgeResult(
            score=min(1.0, score),
            confidence=confidence,
            reason="Evaluated expectation subversion and surprise elements.",
            signals=signals,
            evidence=[features.text[:50] + "..."] if score > 0 else [],
            impact=impact,
        )


@JudgeRegistry.register("novelty")
class NoveltyJudge(AttentionJudge):
    """Detects uniqueness and lack of repetition."""

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        score = 0.0
        signals = []

        # A clip that repeats heavily is not novel
        rep_ratio = features.repetition / max(1, features.word_count)

        score = max(0.0, 1.0 - (rep_ratio * 2.0))
        if rep_ratio > 0.2:
            signals.append(f"High repetition detected ({rep_ratio * 100:.1f}%)")
        else:
            signals.append("Unique vocabulary usage")

        impact = AttentionImpact.SPEND if rep_ratio > 0.2 else AttentionImpact.PRESERVE

        return JudgeResult(
            score=min(1.0, score),
            confidence="UNKNOWN",
            reason="Evaluated novelty based on semantic repetition and predictability.",
            signals=signals,
            evidence=[],
            impact=impact,
        )
