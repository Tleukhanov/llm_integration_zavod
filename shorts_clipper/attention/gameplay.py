"""Gameplay mode helpers: select clip windows by audio energy.

For CS2/CS:GO and other gaming VODs there are usually no subtitles, so the
subtitle-driven semantic pipeline cannot pick a highlight. These helpers select
the most energetic (frag/hype) windows from `extract_audio_energy` output and
turn them into `ClipWindow`s, replacing the semantic pass entirely.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from shorts_clipper.attention.audio_energy import extract_audio_energy
from shorts_clipper.attention.emotion import cluster_peaks
from shorts_clipper.core.models import ClipWindow


def _cluster_peaks_with_magnitudes(
    signal: list[float],
    windows: list[ClipWindow],
    window_seconds: float = 1.0,
) -> list[tuple[float, float]]:
    """Peak position (seconds) and magnitude inside each window.

    Like :func:`cluster_peaks` but also returns the signal value at the peak
    position, enabling ranking windows by hype strength.

    Returns:
        One (peak_second, peak_magnitude) tuple per window, same order as
        ``windows``.  Falls back to the window center and 0.0 magnitude when
        the signal is empty or the window maps outside the signal.
    """
    peaks: list[tuple[float, float]] = []
    n = len(signal)
    for w in windows:
        lo = min(max(int(w.start / window_seconds), 0), n - 1)
        hi = min(max(int(math.ceil(w.end / window_seconds)) - 1, 0), n - 1)
        if n == 0 or hi < lo:
            peaks.append(((w.start + w.end) / 2.0, 0.0))
        else:
            segment = signal[lo : hi + 1]
            offset = int(np.argmax(segment))
            peaks.append(((lo + offset + 0.5) * window_seconds, float(segment[offset])))
    return peaks


def select_energy_windows(
    energy: list[float],
    window_seconds: float,
    *,
    min_length: float,
    max_length: float,
    top_n: int,
    threshold: float = 0.35,
) -> list[ClipWindow]:
    """Pick the top `top_n` energetic clip windows from a normalized energy array.

    Pure / deterministic — no I/O.

    Args:
        energy: Normalized (0..1) per-window energy, one value per `window_seconds`.
        window_seconds: Length in seconds each energy value represents.
        min_length: Minimum cluster duration (s). Shorter clusters are dropped.
        max_length: Maximum clip duration (s) to expand a cluster toward.
        top_n: Maximum number of windows to return.
        threshold: A window counts as "energetic" when its value >= threshold.

    Returns:
        Up to `top_n` non-overlapping ClipWindows sorted by aggregate energy
        descending (best first).
    """
    if window_seconds <= 0 or min_length <= 0 or max_length < min_length or not energy:
        return []

    above = [e >= threshold for e in energy]

    clusters: list[list[int]] = []  # each cluster is [lo, hi] inclusive indices
    run_start = None
    for i, flag in enumerate(above):
        if flag:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                clusters.append([run_start, i - 1])
                run_start = None
    if run_start is not None:
        clusters.append([run_start, len(energy) - 1])

    min_windows = math.ceil(min_length / window_seconds)
    target_windows = max(1, math.ceil(max_length / window_seconds))

    kept: list[ClipWindow] = []
    n = len(energy)
    for i, (lo, hi) in enumerate(clusters):
        span = hi - lo + 1
        if span < min_windows:
            continue

        # Exclusive expansion regions keep windows non-overlapping: each cluster
        # may grow up to the midpoint of the gap to its neighbors (reserving the
        # gap windows), and the array edges bound the first/last cluster.
        if i == 0:
            left_bound = 0
        else:
            left_bound = (clusters[i - 1][1] + lo) // 2 + 1
        if i == len(clusters) - 1:
            right_bound = n - 1
        else:
            right_bound = (hi + clusters[i + 1][0]) // 2 - 1

        add = target_windows - span
        if add > 0:
            left_add = min(add // 2, lo - left_bound)
            right_add = min(add - left_add, right_bound - hi)
            if right_add < add - left_add:
                # Push leftover budget to the left if right is blocked.
                left_add = min(left_add + (add - left_add - right_add), lo - left_bound)
            lo = max(lo - left_add, left_bound)
            hi = min(hi + right_add, right_bound)

        energy_total = sum(energy[lo : hi + 1])
        start = lo * window_seconds
        end = (hi + 1) * window_seconds
        kept.append((energy_total, ClipWindow(start=start, end=end)))

    kept.sort(key=lambda t: t[0], reverse=True)
    return [window for _, window in kept[:top_n]]


def windows_and_peaks_from_audio(
    audio_path: Path,
    total_seconds: float,
    settings,
) -> list[tuple[ClipWindow, float, float]]:
    """Extract energy from audio; return the top windows, the absolute peak
    energy second within each window, and the peak energy magnitude (anchor for
    a short "juice" slice and for ranking windows by hype strength).

    Args:
        audio_path: Path to a downloaded audio file (decodable by ffmpeg).
        total_seconds: Total duration of the source (s); used as upper bound on
            how many windows can be produced when the scan cap exceeds it.
        settings: A Settings instance with the gameplay_* fields.

    Returns:
        Triples of (ClipWindow, peak_absolute_seconds, peak_magnitude), sorted
        by aggregate energy descending.  Empty when nothing energetic is found.
    """
    energy = extract_audio_energy(
        audio_path,
        window_seconds=1.0,
        max_seconds=settings.gameplay_scan_max_seconds,
    )
    windows = select_energy_windows(
        energy,
        1.0,
        min_length=settings.gameplay_min_length,
        max_length=settings.gameplay_max_length,
        top_n=settings.gameplay_top_windows,
    )
    peak_data = _cluster_peaks_with_magnitudes(energy, windows)
    return [(w, pd[0], pd[1]) for w, pd in zip(windows, peak_data)]


def windows_from_audio(
    audio_path: Path,
    total_seconds: float,
    settings,
) -> list[ClipWindow]:
    """Top energy ``ClipWindow``s (windows only; see windows_and_peaks_from_audio)."""
    return [w for w, _, _ in windows_and_peaks_from_audio(audio_path, total_seconds, settings)]


def select_non_overlapping(
    windows: list[ClipWindow],
    min_gap: float = 15.0,
    *,
    score: object | None = None,
) -> list[ClipWindow]:
    """Greedily select non-overlapping windows with minimum temporal separation.

    Two windows are considered "too close" when the gap between them (i.e.
    ``min(b.end, a.end) - max(b.start, a.start) + min_gap`` on overlap, or
    ``b.start - a.end`` on gap) is less than *min_gap*.  More precisely, a
    candidate is dropped when the temporal distance between its **centre** and
    the centre of any already-kept window is less than ``min_gap`` plus half of
    each window's duration — equivalently, when the nearest edges of the two
    windows are within ``min_gap`` seconds of each other.

    Algorithm:
        1. If ``score`` is provided, sort windows descending by score (ties
           broken by timestamp) so highest-priority windows are considered
           first.  Otherwise preserve the original list order as priority.
        2. Greedily keep a window if no previously-kept window is within
           ``min_gap`` seconds of it (edge-to-edge distance).
        3. Return the kept windows in their **original** list order.

    This function is pure — it never mutates *windows* or *score*.

    Args:
        windows: Candidate clip windows.
        min_gap: Minimum seconds between the end of one kept window and the
            start of the next (or vice-versa).  Windows closer than this are
            de-duplicated, keeping the higher-priority one.
        score: Optional callable ``score(window) -> float`` for ranking.  If
            ``None``, input order is the priority.

    Returns:
        A new list of ClipWindows in the original order with no pair closer
        than *min_gap* seconds (edge-to-edge).
    """
    if not windows or min_gap <= 0:
        return list(windows)

    if score is not None:
        ranked = sorted(windows, key=lambda w: score(w), reverse=True)
    else:
        ranked = list(windows)

    kept: list[ClipWindow] = []
    kept_set: set[int] = set()  # indices into `windows` (via id)

    # Build an index lookup: (start, end) → original index (for small lists this is fine)
    index_of = {id(w): i for i, w in enumerate(windows)}

    for candidate in ranked:
        ci = index_of[id(candidate)]
        c_start = candidate.start
        c_end = candidate.end
        dominated = False
        for kw in kept:
            # Edge-to-edge gap: positive means a gap, negative means overlap.
            # max of the two directional edge distances gives the true nearest-edge gap.
            gap = max(c_start - kw.end, kw.start - c_end)
            # gap < 0 → overlap → too close.
            # gap >= 0 and gap < min_gap → too close.
            if gap < min_gap:
                dominated = True
                break
        if not dominated:
            kept.append(candidate)
            kept_set.add(ci)

    # Return in original list order
    return [w for i, w in enumerate(windows) if i in kept_set]


def cap_to_target(
    final_window: ClipWindow,
    center_seconds: float,
    target_seconds: float,
    peak_ratio: float = 0.5,
) -> ClipWindow:
    """Cap a window to at most ``target_seconds`` around ``center_seconds``.

    The sentence-boundary finisher can balloon a gameplay clip far past the
    selected energy/emotion window (e.g. a 49s selection -> 98s). This keeps
    Shorts-friendly, attention-grabbing clips: take a ``target_seconds``-wide
    slice around the peak moment, then clamp inside ``final_window``.

    ``peak_ratio`` (0..1) controls where the peak falls inside the clip:
      - 0.5 (default): peak centered — symmetric slice.
      - 0.75: peak at 3/4 of the way through — most of the clip is
        the buildup BEFORE the hype moment, then a short payoff.
        Ideal for clutch/emotion clips where the tension matters.

    Args:
        final_window: Window the finisher produced (potentially oversized).
        center_seconds: Peak / center to anchor the slice on (same local
            coordinates as ``final_window``).
        target_seconds: Maximum finished length.  <= 0 disables the cap.
        peak_ratio: Where inside the clip the peak should land (0..1).

    Returns:
        A copy of ``final_window`` when it already fits within target_seconds,
        otherwise a window of at most ``target_seconds`` anchored around
        ``center_seconds`` with the given ``peak_ratio``.
    """
    duration = final_window.end - final_window.start
    if target_seconds <= 0 or duration <= target_seconds:
        return final_window

    ratio = max(0.0, min(1.0, peak_ratio))
    lo = max(final_window.start, center_seconds - target_seconds * ratio)
    hi = lo + target_seconds
    if hi > final_window.end:
        hi = final_window.end
        lo = max(final_window.start, hi - target_seconds)
    return ClipWindow(start=lo, end=hi)
