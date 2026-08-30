"""Audio-energy judge for stream VOD highlight selection."""

from shorts_clipper.attention.judges.base import AttentionJudge, JudgeRegistry
from shorts_clipper.attention.models import AttentionImpact, FeatureSet, JudgeResult


@JudgeRegistry.register("stream_audio_energy")
class StreamAudioEnergyJudge(AttentionJudge):
    """Rewards windows with high normalized audio energy and penalizes dead air.

    Reads `features.audio_energy` (normalized 0..1 per second). When it is empty
    the judge is neutral so it never changes existing behavior.
    """

    THRESHOLD = 0.15

    def evaluate(self, features: FeatureSet) -> JudgeResult:
        energy = features.audio_energy or []
        if not energy:
            return JudgeResult(
                score=0.0,
                confidence="UNKNOWN",
                reason="No audio energy available; judge is neutral.",
                signals=[],
                evidence=[],
                impact=AttentionImpact.PRESERVE,
            )

        peak = max(energy)
        avg = sum(energy) / len(energy)
        above = sum(1 for e in energy if e >= self.THRESHOLD) / len(energy)

        score = 0.0
        signals = []
        if peak >= self.THRESHOLD:
            score += min(0.5, peak * 0.5)
            signals.append(f"Peak audio energy {peak:.2f} detected")
        if above >= 0.5:
            score += 0.3
            signals.append(f"{above * 100:.0f}% of windows are energetic")
        if avg < 0.05:
            score = max(0.0, score - 0.4)
            signals.append("Predominantly dead air detected")

        score = min(1.0, max(0.0, score))
        impact = AttentionImpact.ADD if score >= 0.4 else AttentionImpact.PRESERVE

        return JudgeResult(
            score=score,
            confidence="UNKNOWN",
            reason="Evaluated raw audio energy to favor crowd-reaction moments.",
            signals=signals,
            evidence=[f"peak={peak:.2f} avg={avg:.2f}"],
            impact=impact,
        )
