"""Gameplay mode helpers: select clip windows by audio energy.

For CS2/CS:GO and other gaming VODs there are usually no subtitles, so the
subtitle-driven semantic pipeline cannot pick a highlight. These helpers select
the most energetic (frag/hype) windows from `extract_audio_energy` output and
turn them into `ClipWindow`s, replacing the semantic pass entirely.
"""

from __future__ import annotations

import math
from pathlib import Path

from shorts_clipper.attention.audio_energy import extract_audio_energy
from shorts_clipper.core.models import ClipWindow


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


def windows_from_audio(
    audio_path: Path,
    total_seconds: float,
    settings,
) -> list[ClipWindow]:
    """Extract energy from an audio file and turn the top windows into clips.

    Args:
        audio_path: Path to a downloaded audio file (decodable by ffmpeg).
        total_seconds: Total duration of the source (s); used as upper bound on
            how many windows can be produced when the scan cap exceeds it.
        settings: A Settings instance with the gameplay_* fields.

    Returns:
        Top energy `ClipWindow`s sorted by aggregate energy descending.
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
    return windows
