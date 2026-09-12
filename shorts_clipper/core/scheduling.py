"""Publish-window scheduling helpers for the factory autopilot."""

from __future__ import annotations


def in_publish_window(hour: int, start: int | None, end: int | None) -> bool:
    """Return whether *hour* falls inside the active [start, end) window.

    The window is end-exclusive: start is inclusive, end is not. A missing
    boundary leaves that side open (None/None => always open). When
    start >= end the window wraps across midnight, e.g. (22, 6) covers
    hours 22, 23, 0, 1, 2, 3, 4 and 5. Pure function, no side effects.
    """
    if start is None and end is None:
        return True
    if start is None:
        return hour < end
    if end is None:
        return hour >= start
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end