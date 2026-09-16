from __future__ import annotations

import logging

from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.editorial.models import FeatureSet

log = logging.getLogger(__name__)


class FeatureStore:
    """Computes transcript/audio features once per window."""

    @classmethod
    def compute(cls, segments: list[TranscriptSegment]) -> FeatureSet:
        """Computes all features for a given list of transcript segments."""
        if not segments:
            return FeatureSet(
                total_duration=0.0,
                word_count=0,
                words_per_second=0.0,
                longest_pause=0.0,
                starts_with_conjunction=False,
                ends_with_punctuation=False,
                question_count=0,
                exclamation_count=0,
                has_hanging_pronoun=False,
                text_content="",
                raw_segments=[],
            )

        total_duration = segments[-1].end - segments[0].start

        words = []
        longest_pause = 0.0
        text_parts = []
        has_word_timestamps = False

        for i, segment in enumerate(segments):
            text_parts.append(segment.text.strip())
            if segment.words:
                words.extend(segment.words)
                has_word_timestamps = True

            # Calculate pause between this segment and the next
            if i < len(segments) - 1:
                pause = segments[i + 1].start - segment.end
                if pause > longest_pause:
                    longest_pause = pause

        text_content = " ".join(text_parts).strip()

        # Word-level features: prefer word-level timestamps, falling back to
        # a simple text split so judges do not silently degrade to zero scores.
        if not has_word_timestamps:
            fallback_words = text_content.split()
            if fallback_words:
                log.warning(
                    "Segments lack word-level timestamps; falling back to text-based word count (%d words).",
                    len(fallback_words),
                )
            word_count = len(fallback_words)
        else:
            word_count = len(words)
        words_per_second = word_count / total_duration if total_duration > 0 else 0.0

        # Punctuation
        question_count = text_content.count("?")
        exclamation_count = text_content.count("!")

        # Semantic boundaries
        lower_text = text_content.lower()
        starts_with_conjunction = lower_text.startswith(("and ", "but ", "so ", "because ", "or "))
        ends_with_punctuation = text_content.endswith((".", "!", "?"))

        # Hanging pronouns (very basic heuristic - e.g., ending with "he", "it", "they", "this", "that")
        # without further context
        has_hanging_pronoun = False
        if words and has_word_timestamps:
            last_word = words[-1].word.strip().lower()
            # Remove punctuation from last word for check
            last_word_clean = last_word.rstrip(".!?,")
            if last_word_clean in {
                "he",
                "she",
                "it",
                "they",
                "this",
                "that",
                "those",
                "these",
                "which",
                "who",
            }:
                has_hanging_pronoun = True
        elif not has_word_timestamps:
            tokens = [t.strip().rstrip(".!?,").lower() for t in text_content.split()]
            if tokens and tokens[-1] in {
                "he",
                "she",
                "it",
                "they",
                "this",
                "that",
                "those",
                "these",
                "which",
                "who",
            }:
                has_hanging_pronoun = True

        return FeatureSet(
            total_duration=total_duration,
            word_count=word_count,
            words_per_second=words_per_second,
            longest_pause=longest_pause,
            starts_with_conjunction=starts_with_conjunction,
            ends_with_punctuation=ends_with_punctuation,
            question_count=question_count,
            exclamation_count=exclamation_count,
            has_hanging_pronoun=has_hanging_pronoun,
            text_content=text_content,
            raw_segments=segments,
        )
