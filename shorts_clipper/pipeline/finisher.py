from __future__ import annotations

import logging

from shorts_clipper.core.models import ClipWindow, TranscriptSegment

log = logging.getLogger(__name__)


class EditorialFinisher:
    """
    Decides whether a generated Short is editorially complete before it is rendered.
    If it's not, it modifies the boundaries to snap to nearest sentences.
    """

    def snap_boundaries(
        self, target_start: float, target_end: float, segments: list[TranscriptSegment]
    ) -> ClipWindow:
        all_words = []
        for s in segments:
            if s.words:
                all_words.extend(s.words)
            else:
                all_words.append(s)

        if not all_words:
            return ClipWindow(start=target_start, end=target_end)

        # Find the word indices closest to the target_start and target_end
        initial_start_idx = 0
        for i, w in enumerate(all_words):
            if (getattr(w, "end", w.start) > target_start) or (w.start >= target_start):
                initial_start_idx = i
                break

        initial_end_idx = len(all_words) - 1
        for i in range(initial_start_idx, len(all_words)):
            if all_words[i].start >= target_end:
                initial_end_idx = max(initial_start_idx, i - 1)
                break

        def is_sentence_start(idx: int) -> bool:
            if idx == 0:
                return True
            prev_word = getattr(
                all_words[idx - 1], "word", getattr(all_words[idx - 1], "text", "")
            ).strip()
            if not prev_word:
                return False
            # Check if prev word ends with sentence-ending punctuation
            if prev_word[-1] in {".", "!", "?"}:
                curr_word = (
                    getattr(all_words[idx], "word", getattr(all_words[idx], "text", ""))
                    .strip()
                    .lower()
                )
                if curr_word not in {"and", "but", "so", "because", "or"}:
                    return True
            return False

        def is_sentence_end(idx: int) -> bool:
            curr_word = getattr(all_words[idx], "word", getattr(all_words[idx], "text", "")).strip()
            if not curr_word:
                return False
            return curr_word[-1] in {".", "!", "?"}

        # Find closest sentence start going backwards
        start_id = initial_start_idx
        for i in range(initial_start_idx, -1, -1):
            if is_sentence_start(i):
                start_id = i
                break

        # Find closest sentence end going forwards
        end_id = initial_end_idx
        for i in range(initial_end_idx, len(all_words)):
            if is_sentence_end(i):
                end_id = i
                break

        final_start = all_words[start_id].start
        final_end = getattr(all_words[end_id], "end", all_words[end_id].start)

        log.info(
            f"EditorialFinisher adjusted timestamps deterministically: start {target_start:.2f} -> {final_start:.2f}, end {target_end:.2f} -> {final_end:.2f}"
        )

        # Return exact bounds; do not add arbitrary padding, or it will bleed into the next word's audio
        return ClipWindow(start=final_start, end=final_end)
