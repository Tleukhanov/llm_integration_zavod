from __future__ import annotations

from shorts_clipper.editorial.models import JudgeResult
from shorts_clipper.editorial.profiles import EditorialProfile


def aggregate_scores(
    results: dict[str, JudgeResult], profile: EditorialProfile
) -> tuple[float, float, str]:
    """
    Aggregates JudgeResults into a final score and confidence.

    Low confidence scores influence the final score less.
    """
    if not results:
        return 0.0, 0.0, "No results to aggregate."

    total_weighted_score = 0.0
    total_effective_weight = 0.0
    total_confidence = 0.0

    reasons = []

    for judge_name, result in results.items():
        base_weight = profile.weights.get(judge_name, profile.default_weight)

        # Effective weight is scaled by the judge's confidence
        effective_weight = base_weight * result.confidence

        total_weighted_score += result.score * effective_weight
        total_effective_weight += effective_weight
        total_confidence += result.confidence

        reasons.append(
            f"{judge_name}: {result.score:.1f} (conf: {result.confidence:.2f}) - {result.reasoning}"
        )

    if total_effective_weight > 0:
        final_score = total_weighted_score / total_effective_weight
    else:
        final_score = 0.0

    avg_confidence = total_confidence / len(results)
    reasoning_summary = " | ".join(reasons)

    return final_score, avg_confidence, reasoning_summary
