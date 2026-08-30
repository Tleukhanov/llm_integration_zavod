"""Provider-neutral contracts and response parsing."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

from shorts_clipper.core.models import ClipWindow, TranscriptSegment

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_clip_window(
    text: str, *, min_start: float = 0.0, max_end: float | None = None
) -> ClipWindow:
    numbers = [float(match.group(0)) for match in _NUMBER_RE.finditer(text)]
    if len(numbers) < 2:
        raise ValueError(f"expected at least two timestamps, got: {text!r}")

    window = ClipWindow(start=numbers[0], end=numbers[1])
    if window.start < min_start:
        raise ValueError(f"clip start {window.start} is before minimum {min_start}")
    if max_end is not None and window.end > max_end:
        raise ValueError(f"clip end {window.end} exceeds maximum {max_end}")
    return window


class HighlightProvider(ABC):
    @abstractmethod
    def select_clip(self, segments: Sequence[TranscriptSegment]) -> ClipWindow:
        """Return the best clip window for a transcript."""

    def select_multiple_clips(
        self, segments: Sequence[TranscriptSegment], count: int = 1
    ) -> list[tuple[ClipWindow, str]]:
        """Return multiple clip windows with layouts for a transcript."""
        # By default, falls back to select_clip wrapped in a list
        res = self.select_clip(segments)
        if isinstance(res, tuple):
            return [res]
        return [(res, "crop_center")]
