"""Core Attention Optimization and Simulation Engine for Shorts Clipper V3.3."""

import dataclasses
import statistics

from shorts_clipper.attention.features import FeatureExtractor
from shorts_clipper.attention.judges import JudgeRegistry
from shorts_clipper.attention.models import (
    AttentionReport,
    AttentionState,
    AttentionTimeline,
    CounterfactualVariant,
    EditorialRecommendation,
    FeatureSet,
    NarrativeState,
    SemanticSegment,
    SimulationResult,
)
from shorts_clipper.core.models import TranscriptSegment


class AttentionEngine:
    """Orchestrates feature extraction and modular judges to predict human attention."""

    def __init__(self):
        self.judges = JudgeRegistry.get_all_judges()

    def evaluate_variant(self, variant: CounterfactualVariant) -> AttentionReport:
        """Evaluates a potential clip window and returns a detailed AttentionReport."""
        if not variant.modified_segments:
            raise ValueError("Empty segments provided to AttentionEngine.")

        # Extract features ONCE (Agent G: Performance Engineer)
        features = FeatureExtractor.extract(variant.modified_segments)

        # Generate Attention Timeline first, so we can use it for probabilities (Agent D)
        timeline = self._generate_timeline(features.semantic_segments)

        # Run all modular judges (Agent F: Software Architect)
        results = {}
        for name, judge in self.judges.items():
            results[name] = judge.evaluate(features)

        curiosity = results.get("curiosity").score if "curiosity" in results else 0.5
        info_density = (
            results.get("information_density").score if "information_density" in results else 0.5
        )
        prediction_error = (
            results.get("prediction_error").score if "prediction_error" in results else 0.5
        )
        novelty = results.get("novelty").score if "novelty" in results else 0.5
        judge_scroll_stop = results.get("scroll_stop").score if "scroll_stop" in results else 0.5
        judge_payoff = results.get("payoff").score if "payoff" in results else 0.5

        # Probability Calibration (Agent A & D)
        # We now derive probabilities from the dynamic timeline!
        if timeline.states:
            final_attention = timeline.states[-1].attention_level
            min_attention = min(st.attention_level for st in timeline.states)
            max_fatigue = max(st.fatigue for st in timeline.states)

            timeline_scroll = max(0.01, min(0.99, timeline.states[0].attention_level))
            scroll_stop_prob = (timeline_scroll * 0.6) + (judge_scroll_stop * 0.4)

            timeline_completion = max(0.01, min(0.99, min_attention * (1.0 - max_fatigue)))

            payoff_strength = (final_attention * 0.5) + (judge_payoff * 0.5)
        else:
            scroll_stop_prob = judge_scroll_stop
            timeline_completion = 0.5
            payoff_strength = judge_payoff

        # Synthesize remaining probabilities (Agent E)
        retention_3s_prob = min(1.0, (scroll_stop_prob * 0.7) + (curiosity * 0.3))
        completion_prob = min(
            1.0, (timeline_completion * 0.5) + (retention_3s_prob * 0.2) + (payoff_strength * 0.3)
        )
        shareability = min(1.0, (prediction_error * 0.6) + (info_density * 0.4))
        subscribe_potential = min(1.0, (completion_prob * 0.5) + (payoff_strength * 0.5))
        comment_prob = min(1.0, (prediction_error * 0.7) + (curiosity * 0.3))
        memorability = min(
            1.0, (payoff_strength * 0.4) + (novelty * 0.4) + (prediction_error * 0.2)
        )

        # Average confidence of all judges
        confidences = [
            r.confidence for r in results.values() if isinstance(r.confidence, (int, float))
        ]
        overall_confidence = statistics.mean(confidences) if confidences else 0.5

        # Editorial Recommendations (Agent C)
        recommendations = self._generate_recommendations(features, results, timeline)

        return AttentionReport(
            start_time=features.start_time,
            end_time=features.end_time,
            scroll_stop_prob=scroll_stop_prob,
            retention_3s_prob=retention_3s_prob,
            completion_prob=completion_prob,
            payoff_strength=payoff_strength,
            shareability=shareability,
            subscribe_potential=subscribe_potential,
            comment_prob=comment_prob,
            memorability=memorability,
            novelty=novelty,
            curiosity=curiosity,
            overall_confidence=overall_confidence,
            judge_results=results,
            recommendations=recommendations,
            timeline=timeline,
        )

    def _generate_timeline(self, semantic_segments: list[SemanticSegment]) -> AttentionTimeline:
        """Generates a dynamic attention state over time based on semantic state."""
        states = []
        high_risk_moments = []
        boring_regions = []

        current_attention = 1.0  # starts at 100% and decays/gains
        curiosity = 0.0
        reward_expectation = 0.0
        confusion = 0.0
        fatigue = 0.0

        for i, s_seg in enumerate(semantic_segments):
            s = s_seg.segment
            state_val = s_seg.state
            words = len(s.words) if s.words else len(s.text.split())
            dur = max(s.end - s.start, 0.1)
            wps = words / dur

            gain = 0.0
            loss = 0.0

            # Narrative State Effects
            if s_seg.is_hook:
                gain += 0.3
                curiosity = min(1.0, curiosity + 0.5)
            if s_seg.is_dead_narrative:
                loss += 0.4
                fatigue += 0.2

            if state_val == NarrativeState.QUESTION:
                curiosity = min(1.0, curiosity + 0.3)
            elif state_val == NarrativeState.CONFLICT:
                gain += 0.2
            elif state_val == NarrativeState.ESCALATION:
                gain += 0.1
                reward_expectation += 0.2
            elif state_val == NarrativeState.DISCOVERY:
                gain += 0.15
            elif state_val == NarrativeState.PAYOFF:
                gain += 0.4
                reward_expectation = 0.0  # Fulfilled
                curiosity = max(0.0, curiosity - 0.5)

            # Wps effects
            if wps > 4.5:
                confusion += 0.1
                loss += 0.1
            elif wps < 1.0:
                fatigue += 0.1
                loss += 0.1

            if i > 0:
                pause = s.start - semantic_segments[i - 1].segment.end
                if pause > 0.5:
                    loss += min(pause * 0.2, 0.6)
                    fatigue += 0.1

            # Update attention
            current_attention = max(
                0.01, min(1.0, current_attention + gain - loss - (fatigue * 0.1))
            )

            swipe_risk = 1.0 - current_attention
            if swipe_risk > 0.6:
                high_risk_moments.append(s.start)

            is_valley = current_attention < 0.3
            is_spike = gain > 0.15

            states.append(
                AttentionState(
                    time=s.start,
                    attention_level=current_attention,
                    gain=gain,
                    loss=loss,
                    predicted_swipe_risk=swipe_risk,
                    information_overload=wps > 4.0,
                    emotional_intensity=0.5,  # Placeholder
                    curiosity=curiosity,
                    reward_expectation=reward_expectation,
                    confusion=confusion,
                    fatigue=fatigue,
                    is_spike=is_spike,
                    is_valley=is_valley,
                )
            )

        in_boring = False
        start_boring = 0.0
        for st in states:
            if st.attention_level < 0.4:
                if not in_boring:
                    in_boring = True
                    start_boring = st.time
            else:
                if in_boring:
                    boring_regions.append((start_boring, st.time))
                    in_boring = False
        if in_boring:
            boring_regions.append((start_boring, states[-1].time))

        peak_interest = max(states, key=lambda x: x.attention_level).time if states else 0.0

        return AttentionTimeline(
            states=states,
            peak_interest_time=peak_interest,
            high_risk_moments=high_risk_moments,
            boring_regions=boring_regions,
        )

    def _generate_recommendations(
        self, features: FeatureSet, results: dict, timeline: AttentionTimeline
    ) -> list[EditorialRecommendation]:
        recs = []
        if features.pause_density > 0.1:
            recs.append(
                EditorialRecommendation(
                    action="Trim Pauses",
                    reason="High pause density detected, causing swipe risk.",
                    expected_retention_improvement=0.08,
                    expected_scroll_stop_improvement=0.0,
                    confidence=0.92,
                )
            )

        if timeline.states and timeline.states[0].attention_level < 0.5:
            recs.append(
                EditorialRecommendation(
                    action="Start at Hook",
                    reason="The current hook is buried or missing, causing an immediate attention drop.",
                    expected_retention_improvement=0.15,
                    expected_scroll_stop_improvement=0.20,
                    confidence=0.88,
                )
            )

        return recs


class SimulationEngine:
    """Agent D: Evaluates counterfactuals to optimize the clip."""

    def __init__(self):
        self.engine = AttentionEngine()

    def _generate_counterfactuals(
        self, base_segments: list[TranscriptSegment]
    ) -> list[CounterfactualVariant]:
        """Generates semantic alternatives to compare against."""
        variants = []

        if not base_segments:
            return variants

        # Extract features ONCE for the base variant to identify semantic bounds
        features = FeatureExtractor.extract(base_segments)

        # V0: Base
        variants.append(
            CounterfactualVariant(
                variant_id="base",
                description="Original Unmodified Clip",
                start_time=base_segments[0].start,
                end_time=base_segments[-1].end,
                modified_segments=base_segments,
            )
        )

        # Find first hook
        hook_idx = -1
        for i, s_seg in enumerate(features.semantic_segments):
            if s_seg.is_hook:
                hook_idx = i
                break

        # V1: Start at Hook
        if hook_idx > 0 and hook_idx < len(base_segments):
            trimmed = base_segments[hook_idx:]
            variants.append(
                CounterfactualVariant(
                    variant_id="start_at_hook",
                    description="Starts directly at the strongest identified hook.",
                    start_time=trimmed[0].start,
                    end_time=trimmed[-1].end,
                    modified_segments=trimmed,
                )
            )

        # Find dead narrative at start
        dead_idx = -1
        for i, s_seg in enumerate(features.semantic_segments):
            if not s_seg.is_dead_narrative:
                break
            dead_idx = i

        # V2: Remove Dead Setup
        if dead_idx >= 0 and dead_idx + 1 < len(base_segments) and (dead_idx + 1) != hook_idx:
            trimmed = base_segments[dead_idx + 1 :]
            variants.append(
                CounterfactualVariant(
                    variant_id="remove_dead_setup",
                    description="Removes unnecessary rambling at the start.",
                    start_time=trimmed[0].start,
                    end_time=trimmed[-1].end,
                    modified_segments=trimmed,
                )
            )

        # V3: Remove Dead Air (Shift timestamps)
        compressed = list(base_segments)
        shifted = False
        for i in range(1, len(compressed)):
            gap = compressed[i].start - compressed[i - 1].end
            if gap > 0.5:
                shifted = True
                shift = gap - 0.1
                for j in range(i, len(compressed)):
                    new_start = compressed[j].start - shift
                    new_end = compressed[j].end - shift
                    new_words = []
                    if hasattr(compressed[j], "words") and compressed[j].words:
                        for w in compressed[j].words:
                            new_words.append(
                                dataclasses.replace(w, start=w.start - shift, end=w.end - shift)
                            )
                    else:
                        new_words = compressed[j].words if hasattr(compressed[j], "words") else []
                    compressed[j] = dataclasses.replace(
                        compressed[j], start=new_start, end=new_end, words=new_words
                    )

        if shifted:
            variants.append(
                CounterfactualVariant(
                    variant_id="remove_dead_air",
                    description="Trimmed pauses > 0.5s",
                    start_time=compressed[0].start,
                    end_time=compressed[-1].end,
                    modified_segments=compressed,
                )
            )

        return variants

    def optimize_clip(self, base_segments: list[TranscriptSegment]) -> SimulationResult:
        """Run simulation over counterfactuals and return the strongest."""
        variants = self._generate_counterfactuals(base_segments)
        reports = {}

        best_score = -1.0
        winner_id = "base"
        reason = "Base variant remained optimal."

        variant_scores = []
        for v in variants:
            report = self.engine.evaluate_variant(v)
            reports[v.variant_id] = report

            # Ranking objective (Agent E)
            # Completion prob is now heavily weighted as it directly factors in fatigue and minimum attention.
            overall_perf = (report.scroll_stop_prob * 0.4) + (report.completion_prob * 0.6)
            variant_scores.append((v.variant_id, overall_perf))

            if overall_perf > best_score:
                best_score = overall_perf
                winner_id = v.variant_id
                if v.variant_id != "base":
                    reason = f"Variant '{v.variant_id}' outperformed base due to higher completion probability ({report.completion_prob:.2f})."

        variant_scores.sort(key=lambda x: x[1], reverse=True)
        runner_up_id = variant_scores[1][0] if len(variant_scores) > 1 else None

        base_score = next((score for vid, score in variant_scores if vid == "base"), 0.0)
        improvement_percentage = (
            ((best_score - base_score) / base_score * 100.0) if base_score > 0 else 0.0
        )

        return SimulationResult(
            base_variant=variants[0] if variants else None,
            variants=variants,
            reports=reports,
            winner_id=winner_id,
            reason=reason,
            runner_up_id=runner_up_id,
            improvement_percentage=improvement_percentage,
        )
