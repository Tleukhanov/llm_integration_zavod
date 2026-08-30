from __future__ import annotations

import logging

from shorts_clipper.core.models import ClipWindow, TranscriptSegment
from shorts_clipper.editorial.confidence import aggregate_scores
from shorts_clipper.editorial.feature_store import FeatureStore
from shorts_clipper.editorial.models import EditorialDecision, JudgeResult
from shorts_clipper.editorial.profiles import get_profile
from shorts_clipper.editorial.registry import PluginRegistry

log = logging.getLogger(__name__)


class EditorialEngine:
    """The central decision-making brain of Shorts Clipper."""

    def __init__(self, profile_name: str = "default"):
        self.registry = PluginRegistry()
        self.profile = get_profile(profile_name)

    def select_clips_detailed(
        self,
        transcript: list[TranscriptSegment],
        count: int = 1,
        window_duration: float = 60.0,
        step: float = 5.0,
    ) -> list[EditorialDecision]:
        """Sliding window evaluation to find the best clips. Returns detailed decisions."""
        if not transcript:
            return []

        candidates = self._generate_windows(transcript, window_duration, step)
        decisions = []

        for window in candidates:
            decision = self.evaluate_window(window)
            if not decision.rejected:
                decisions.append(decision)

        # Sort by final score descending
        decisions.sort(key=lambda d: d.final_score, reverse=True)

        # Deduplicate overlapping clips
        final_decisions = []
        for d in decisions:
            if len(final_decisions) >= count:
                break

            # Check overlap
            is_overlapping = False
            for fd in final_decisions:
                overlap = max(
                    0,
                    min(d.clip_window.end, fd.clip_window.end)
                    - max(d.clip_window.start, fd.clip_window.start),
                )
                if overlap > 10.0:  # More than 10s overlap
                    is_overlapping = True
                    log.info(
                        f"Clip [{d.clip_window.start:.2f}-{d.clip_window.end:.2f}] rejected due to overlap with a higher scoring clip."
                    )
                    break

            if not is_overlapping:
                final_decisions.append(d)

        # Explainability
        for i, d in enumerate(final_decisions):
            log.info(
                f"🏆 Selected Clip {i + 1} [{d.clip_window.start:.2f}-{d.clip_window.end:.2f}] (Score: {d.final_score:.2f}, Confidence: {d.confidence:.2f})"
            )
            log.info(f"   Reasoning: {d.reasoning}")
            for j_name, j_res in d.judge_results.items():
                log.info(f"   - {j_name}: {j_res.score:.2f} ({j_res.reasoning})")

        return final_decisions

    def select_clips(
        self,
        transcript: list[TranscriptSegment],
        count: int = 1,
        window_duration: float = 60.0,
        step: float = 5.0,
    ) -> list[ClipWindow]:
        """Sliding window evaluation to find the best clips."""
        decisions = self.select_clips_detailed(transcript, count, window_duration, step)
        return [d.clip_window for d in decisions]

    def evaluate_window(self, segments: list[TranscriptSegment]) -> EditorialDecision:
        """Evaluates a single window across all pipeline stages."""
        if not segments:
            return EditorialDecision(
                clip_window=ClipWindow(start=0.0, end=0.0),
                final_score=0.0,
                confidence=0.0,
                reasoning="Empty window",
                rejected=True,
            )

        start_time = segments[0].start
        end_time = segments[-1].end
        clip_window = ClipWindow(start=start_time, end=end_time)

        # Stage 1: Feature Extraction
        features = FeatureStore.compute(segments)

        # Stage 2: Scoring
        results: dict[str, JudgeResult] = {}
        judges = self.registry.get_all_judges()

        for judge in judges:
            try:
                res = judge.evaluate(features)
                if res.reject_hard:
                    return EditorialDecision(
                        clip_window=clip_window,
                        final_score=0.0,
                        confidence=res.confidence,
                        reasoning=f"Hard rejected by {judge.name}: {res.reasoning}",
                        rejected=True,
                        judge_results={judge.name: res},
                    )
                results[judge.name] = res
            except Exception as e:
                log.error(f"Judge {judge.name} failed: {e}", exc_info=True)

        # Stage 3: Confidence Aggregation
        final_score, avg_confidence, reasoning = aggregate_scores(results, self.profile)

        return EditorialDecision(
            clip_window=clip_window,
            final_score=final_score,
            confidence=avg_confidence,
            reasoning=reasoning,
            judge_results=results,
            rejected=False,
        )

    def _generate_windows(
        self, segments: list[TranscriptSegment], max_dur: float, step: float
    ) -> list[list[TranscriptSegment]]:
        """Generates sliding windows of transcript segments."""
        windows = []
        if not segments:
            return windows

        total_time = segments[-1].end
        current_start = segments[0].start

        while current_start < total_time - 15.0:  # require at least 15s remaining
            window_segs = []
            for s in segments:
                if s.start >= current_start and s.end <= current_start + max_dur:
                    window_segs.append(s)
                elif s.start > current_start + max_dur:
                    break

            if window_segs:
                windows.append(window_segs)

            current_start += step

        return windows
