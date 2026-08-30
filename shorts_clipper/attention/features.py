"""Feature extraction pipeline for the Semantic Attention Engine."""

import re

from shorts_clipper.attention.models import FeatureSet, NarrativeState, SemanticSegment
from shorts_clipper.core.models import TranscriptSegment

# Constants for naive parsing
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
}
VIRAL_WORDS = {"secret", "hack", "trick", "mistake", "truth", "viral", "million", "money"}
MONEY_PATTERN = re.compile(r"\$?\d+(?:\.\d+)?(?:k|m|b| million| billion| thousand)?", re.IGNORECASE)
TIME_PATTERN = re.compile(
    r"\b(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|today|yesterday|tomorrow)\b",
    re.IGNORECASE,
)
CONTRADICTION_WORDS = {"but", "however", "although", "except", "yet", "unless", "instead"}
STORY_ARC_WORDS = {"first", "then", "suddenly", "finally", "because", "therefore", "so"}
VISUAL_DEP_WORDS = {"this", "that", "here", "look", "see", "watch", "show", "pointing"}


class FeatureExtractor:
    """Extracts semantic features from segments exactly once (Agent G)."""

    @classmethod
    def extract(cls, segments: list[TranscriptSegment]) -> FeatureSet:
        if not segments:
            return cls._empty_feature_set()

        text_parts = [s.text.strip() for s in segments]
        text = " ".join(text_parts)
        lower_text = text.lower()
        raw_words = re.findall(r"[a-zA-Z']+", lower_text)
        word_count = len(raw_words)

        start_time = segments[0].start
        end_time = segments[-1].end
        duration = max(end_time - start_time, 0.001)

        questions = text.count("?")
        exclamations = text.count("!")
        emotion_hits = sum(1 for word in raw_words if word in EMOTION_WORDS)
        viral_hits = sum(1 for word in raw_words if word in VIRAL_WORDS)
        contradictions = sum(1 for word in raw_words if word in CONTRADICTION_WORDS)
        story_arc_markers = sum(1 for word in raw_words if word in STORY_ARC_WORDS)
        visual_dependency_markers = sum(1 for word in raw_words if word in VISUAL_DEP_WORDS)

        numbers = len(re.findall(r"\b\d+\b", text))
        money_references = len(MONEY_PATTERN.findall(text))
        time_references = len(TIME_PATTERN.findall(text))

        semantic_segments = []
        hook_hits = 0

        for s in segments:
            s_text = s.text.lower()
            state = NarrativeState.SETUP
            is_hook = False
            is_dead_narrative = False

            # Semantic extraction
            if any(
                w in s_text for w in ["um", "uh", "hello guys", "welcome back", "so yeah", "anyway"]
            ):
                is_dead_narrative = True
                state = NarrativeState.SETUP
            elif (
                "you won't believe" in s_text
                or "secret" in s_text
                or "mysterious" in s_text
                or "insane" in s_text
            ):
                is_hook = True
                hook_hits += 1
                state = NarrativeState.QUESTION
            elif any(w in s_text for w in ["but", "however", "suddenly"]):
                state = NarrativeState.CONFLICT
            elif any(w in s_text for w in ["then", "because", "so", "more"]):
                state = NarrativeState.ESCALATION
            elif any(w in s_text for w in ["found", "realized", "discovered", "turns out"]):
                state = NarrativeState.DISCOVERY
            elif "!" in s_text or any(w in s_text for w in ["finally", "truth", "$"]):
                state = NarrativeState.PAYOFF
            elif "?" in s_text:
                state = NarrativeState.QUESTION

            semantic_segments.append(
                SemanticSegment(
                    segment=s, state=state, is_hook=is_hook, is_dead_narrative=is_dead_narrative
                )
            )

        # Pause density
        pauses = 0
        for i in range(1, len(segments)):
            if segments[i].start - segments[i - 1].end > 0.5:
                pauses += 1
        pause_density = pauses / duration

        speaker_changes = 0
        sentiment = min(max((emotion_hits * 0.1) - (contradictions * 0.05), -1.0), 1.0)
        unique_words = len(set(raw_words))
        repetition = max(0, word_count - unique_words)

        return FeatureSet(
            text=text,
            word_count=word_count,
            words_per_second=word_count / duration,
            questions=questions,
            exclamations=exclamations,
            emotion_hits=emotion_hits,
            viral_hits=viral_hits,
            hook_hits=hook_hits,
            story_arc_markers=story_arc_markers,
            contradictions=contradictions,
            numbers=numbers,
            money_references=money_references,
            time_references=time_references,
            pause_density=pause_density,
            speaker_changes=speaker_changes,
            sentiment=sentiment,
            repetition=repetition,
            visual_dependency_markers=visual_dependency_markers,
            raw_words=raw_words,
            duration=duration,
            start_time=start_time,
            end_time=end_time,
            semantic_segments=semantic_segments,
        )

    @classmethod
    def _empty_feature_set(cls) -> FeatureSet:
        return FeatureSet(
            text="",
            word_count=0,
            words_per_second=0.0,
            questions=0,
            exclamations=0,
            emotion_hits=0,
            viral_hits=0,
            hook_hits=0,
            story_arc_markers=0,
            contradictions=0,
            numbers=0,
            money_references=0,
            time_references=0,
            pause_density=0.0,
            speaker_changes=0,
            sentiment=0.0,
            repetition=0,
            visual_dependency_markers=0,
            raw_words=[],
            duration=0.0,
            start_time=0.0,
            end_time=0.0,
            semantic_segments=[],
        )
