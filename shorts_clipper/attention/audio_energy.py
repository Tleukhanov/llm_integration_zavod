"""Cheap audio-energy analysis for stream VOD highlight selection.

Decodes audio to mono 16-bit PCM at 16 kHz via ffmpeg (no heavy audio ML, no
GPU) and computes per-window RMS energy in pure Python / optional numpy. Used
to favor clips that contain energetic crowd-reaction moments and to penalize
dead air. Every function is defensive: any failure returns an empty result so
the caller's behavior is never broken.
"""

from __future__ import annotations

import logging
import subprocess

from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes per int16 sample

try:
    import numpy as _np
except Exception:  # pragma: no cover - depends on environment
    _np = None


def _rms_pure(buf: bytes) -> float:
    """Compute RMS of a raw s16le buffer using struct.iter_unpack."""
    import struct
    from math import sqrt

    total = 0.0
    count = 0
    for (s,) in struct.iter_unpack("<h", buf):
        total += s * s
        count += 1
    if not count:
        return 0.0
    return sqrt(total / count)


def _rms(buf: bytes) -> float:
    if _np is not None:  # pragma: no cover - fast path when numpy present
        a = _np.frombuffer(buf, dtype=_np.int16).astype(_np.float64)
        return float(_np.sqrt(_np.mean(a * a))) if a.size else 0.0
    return _rms_pure(buf)


def extract_audio_energy(
    video_path, window_seconds: float = 1.0, fft_or_rms: str = "rms", max_seconds: int = 0
) -> list[float]:
    """Return normalized (0..1) RMS audio energy per fixed-size window.

    Args:
        video_path: Path to the video file.
        window_seconds: Length in seconds of each energy window.
        fft_or_rms: Analysis mode. Only "rms" is implemented; anything else is
            ignored and treated as rms (kept in the signature for future use).
        max_seconds: If >0, only the first N seconds are decoded. 0 means the
            whole file. Pass a bounded value for long VODs to stay cheap.

    Returns:
        A list of floats in [0, 1], one per window (normalized by the max
        energy). Returns [] on any error or if the file has no audio.
    """
    try:
        if window_seconds <= 0:
            return []

        window_samples = max(1, int(window_seconds * SAMPLE_RATE))
        window_bytes = window_samples * SAMPLE_WIDTH

        cmd = [
            ffmpeg_path(),
            "-nostdin",
            "-i",
            str(video_path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
        ]
        if max_seconds and max_seconds > 0:
            cmd.extend(["-t", str(max_seconds)])
        cmd.append("-")

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return []

        raw = proc.stdout
        energies = []
        for start in range(0, len(raw) - window_bytes + 1, window_bytes):
            energies.append(_rms(raw[start : start + window_bytes]))

        if not energies:
            return []

        peak = max(energies)
        if peak <= 0:
            return energies  # all silent; already normalized to 0

        return [e / peak for e in energies]
    except Exception as exc:
        log.warning("Audio energy extraction failed for %s: %s", video_path, exc)
        return []


def _window_index(t: float, window_seconds: float, n: int) -> int:
    return min(n - 1, max(0, int(t / window_seconds)))


def bias_toward_energetic(
    segments,
    energy: list[float],
    fallback=None,
    threshold: float = 0.15,
    window_seconds: float = 1.0,
):
    """Return a sub-window of segments that carries energetic audio.

    Args:
        segments: List of objects with `.start` and `.end` (seconds, relative to
            the source the `energy` array was extracted from).
        energy: Normalized (0..1) per-window energy; index = window index of
            `window_seconds` duration.
        fallback: Value to return when none of the input can be used or no
            energetic window is found.
        threshold: Minimum average energy to consider a sub-window "energetic".
        window_seconds: Window size (s) the `energy` array was computed with.

    Returns:
        If `energy` is empty or `segments` is empty, returns `fallback` unchanged.
        If the current span already averages at/above `threshold`, returns the
        original `segments`. Otherwise returns the contiguous subset of segments
        that falls inside the highest-energy run that clears `threshold`, or
        `fallback` if no such run exists.
    """
    if fallback is None:
        fallback = segments
    if not energy or not segments:
        return fallback

    if window_seconds <= 0:
        return fallback

    span = (segments[0].start, segments[-1].end)
    if span[1] <= span[0]:
        return fallback

    lo = _window_index(span[0], window_seconds, len(energy))
    hi = _window_index(span[1], window_seconds, len(energy))
    if lo > hi:
        lo, hi = hi, lo

    current = energy[lo : hi + 1]
    if current and (sum(current) / len(current)) >= threshold:
        return segments

    best_start = best_end = -1
    cur_start = None
    for i in range(lo, hi + 1):
        if energy[i] >= threshold:
            if cur_start is None:
                cur_start = i
        else:
            if cur_start is not None and (i - cur_start) > (best_end - best_start):
                best_start, best_end = cur_start, i - 1
            cur_start = None
    if cur_start is not None and (hi - cur_start) > (best_end - best_start):
        best_start, best_end = cur_start, hi

    if best_start < 0:
        return fallback

    t_start = best_start * window_seconds
    t_end = (best_end + 1) * window_seconds

    chosen = [s for s in segments if s.end > t_start and s.start < t_end]
    if len(chosen) < 2:
        return fallback
    return chosen
