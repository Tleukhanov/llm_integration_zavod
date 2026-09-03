"""Caster-emotion / clutch-moment detection via audio signal analysis.

Detects commentator excitement (screams, hype) by combining:
  - Short-time loudness (RMS) bursts
  - High-frequency-band energy (spectral flatness proxy via zero-crossing rate)
  - Zero-crossing-rate spikes (harsh vocal onset)
  - Spectral centroid shifts (voice pitch/scream signature)

All math is pure numpy; audio is read via ffmpeg subprocess (same pattern as
`audio_energy.py`).  Every public function is defensive: failures return empty
results so the pipeline is never broken.
"""

from __future__ import annotations

import logging
import math
import subprocess

import numpy as np

from shorts_clipper.core.models import ClipWindow
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes per int16 sample


# ---------------------------------------------------------------------------
# Audio decode helper
# ---------------------------------------------------------------------------


def _decode_mono_pcm(
    audio_path, max_seconds: int = 0
) -> np.ndarray | None:
    """Decode audio to mono 16-bit PCM via ffmpeg. Returns int16 ndarray or None."""
    try:
        cmd = [
            ffmpeg_path(),
            "-nostdin",
            "-i",
            str(audio_path),
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
            return None
        return np.frombuffer(proc.stdout, dtype=np.int16)
    except Exception as exc:
        log.warning("Audio decode failed for %s: %s", audio_path, exc)
        return None


# ---------------------------------------------------------------------------
# Feature extraction helpers (all pure numpy, 1-D)
# ---------------------------------------------------------------------------


def _frame_signal(signal: np.ndarray, frame_size: int, hop_size: int) -> np.ndarray:
    """Reshape a 1-D signal into overlapping frames → 2-D (n_frames, frame_size)."""
    n_frames = max(0, (len(signal) - frame_size) // hop_size + 1)
    if n_frames == 0:
        return np.empty((0, frame_size), dtype=signal.dtype)
    indices = np.arange(frame_size)[None, :] + (np.arange(n_frames) * hop_size)[:, None]
    return signal[indices].astype(np.float64)


def _rms_per_frame(frames: np.ndarray) -> np.ndarray:
    """Root-mean-square energy per frame."""
    if frames.size == 0:
        return np.array([], dtype=np.float64)
    return np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))


def _zero_crossing_rate_per_frame(frames: np.ndarray) -> np.ndarray:
    """Fraction of sign changes per frame (high for harsh/screaming audio)."""
    if frames.size == 0:
        return np.array([], dtype=np.float64)
    f = frames.astype(np.float64)
    signs = np.sign(f)
    crossings = np.sum(np.abs(np.diff(signs, axis=1)), axis=1) / (2.0 * frames.shape[1])
    return crossings


def _spectral_centroid_per_frame(frames: np.ndarray) -> np.ndarray:
    """Weighted mean frequency per frame via DFT magnitude (proxy for pitch)."""
    if frames.size == 0:
        return np.array([], dtype=np.float64)
    f = frames.astype(np.float64)
    # Hann window
    win = np.hanning(f.shape[1])[None, :]
    windowed = f * win
    fft = np.fft.rfft(windowed, axis=1)
    mag = np.abs(fft)
    freqs = np.fft.rfftfreq(f.shape[1], d=1.0 / SAMPLE_RATE)[None, :]
    denom = np.sum(mag, axis=1)
    denom[denom == 0] = 1.0
    return np.sum(mag * freqs, axis=1) / denom


def _high_frequency_energy_ratio(frames: np.ndarray) -> np.ndarray:
    """Fraction of energy above 3 kHz (high for screams/hype)."""
    if frames.size == 0:
        return np.array([], dtype=np.float64)
    f = frames.astype(np.float64)
    win = np.hanning(f.shape[1])[None, :]
    windowed = f * win
    fft = np.fft.rfft(windowed, axis=1)
    mag = np.abs(fft) ** 2
    freqs = np.fft.rfftfreq(f.shape[1], d=1.0 / SAMPLE_RATE)
    hf_mask = freqs >= 3000.0
    total_energy = np.sum(mag, axis=1)
    hf_energy = np.sum(mag[:, hf_mask], axis=1)
    total_energy[total_energy == 0] = 1.0
    return hf_energy / total_energy


# ---------------------------------------------------------------------------
# Public: compute excitement signal
# ---------------------------------------------------------------------------


def compute_excitement(
    audio_path,
    *,
    window_seconds: float = 1.0,
    max_seconds: int = 0,
) -> list[float]:
    """Compute a normalised (0..1) caster-excitement signal from an audio file.

    Algorithm per 1-second window:
      1. RMS loudness (normalised within file)
      2. Zero-crossing rate spikes (harsh vocal onset)
      3. Spectral centroid (high pitch = scream)
      4. High-frequency energy ratio (3 kHz+)
      5. Final = weighted blend; higher = more excited.

    Returns:
        A list of floats in [0, 1], one per window.  Empty list on failure.
    """
    try:
        pcm = _decode_mono_pcm(audio_path, max_seconds=max_seconds)
        if pcm is None or pcm.size == 0:
            return []

        frame_size = max(1, int(SAMPLE_RATE * window_seconds))
        hop_size = frame_size  # non-overlapping
        frames = _frame_signal(pcm, frame_size, hop_size)
        n_frames = frames.shape[0]
        if n_frames == 0:
            return []

        rms = _rms_per_frame(frames)
        zcr = _zero_crossing_rate_per_frame(frames)
        centroid = _spectral_centroid_per_frame(frames)
        hf_ratio = _high_frequency_energy_ratio(frames)

        # Normalise each feature to [0, 1] within this file.
        def _norm01(arr: np.ndarray) -> np.ndarray:
            lo, hi = float(arr.min()), float(arr.max())
            if hi - lo < 1e-9:
                return np.zeros_like(arr, dtype=np.float64)
            return (arr - lo) / (hi - lo)

        rms_n = _norm01(rms)
        zcr_n = _norm01(zcr)
        cent_n = _norm01(centroid)
        hf_n = _norm01(hf_ratio)

        # Blended excitement score:
        #   RMS is primary (loud = hype), ZCR captures harsh screams,
        #   centroid + HF pick up high-pitched caster excitement.
        excitement = (
            0.35 * rms_n
            + 0.25 * zcr_n
            + 0.20 * cent_n
            + 0.20 * hf_n
        )

        # Clamp to [0, 1]
        excitement = np.clip(excitement, 0.0, 1.0)
        return excitement.tolist()

    except Exception as exc:
        log.warning("Excitement extraction failed for %s: %s", audio_path, exc)
        return []


# ---------------------------------------------------------------------------
# Public: select windows by excitement (mirrors select_energy_windows)
# ---------------------------------------------------------------------------


def select_emotion_windows(
    emotion: list[float],
    window_seconds: float,
    *,
    min_length: float,
    max_length: float,
    top_n: int,
    threshold: float = 0.35,
) -> list[ClipWindow]:
    """Pick the top `top_n` clutch/emotion windows from a normalised excitement array.

    Deterministic, pure function — same clustering/expansion logic as
    `select_energy_windows` but over the excitement signal.

    Args:
        emotion: Normalised (0..1) per-window excitement, one value per
            `window_seconds`.
        window_seconds: Length in seconds each value represents.
        min_length: Minimum cluster duration (s). Shorter clusters dropped.
        max_length: Maximum clip duration (s) to expand a cluster toward.
        top_n: Maximum number of windows to return.
        threshold: A window counts as "exciting" when its value >= threshold.

    Returns:
        Up to `top_n` non-overlapping ClipWindows sorted by aggregate excitement
        descending (best first).
    """
    if window_seconds <= 0 or min_length <= 0 or max_length < min_length or not emotion:
        return []

    import math

    above = [e >= threshold for e in emotion]

    clusters: list[list[int]] = []
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
        clusters.append([run_start, len(emotion) - 1])

    min_windows = math.ceil(min_length / window_seconds)
    target_windows = max(1, math.ceil(max_length / window_seconds))

    kept: list[tuple[float, ClipWindow]] = []
    n = len(emotion)
    for i, (lo, hi) in enumerate(clusters):
        span = hi - lo + 1
        if span < min_windows:
            continue

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
                left_add = min(left_add + (add - left_add - right_add), lo - left_bound)
            lo = max(lo - left_add, left_bound)
            hi = min(hi + right_add, right_bound)

        emo_total = sum(emotion[lo : hi + 1])
        start = lo * window_seconds
        end = (hi + 1) * window_seconds
        kept.append((emo_total, ClipWindow(start=start, end=end)))

    kept.sort(key=lambda t: t[0], reverse=True)
    return [w for _, w in kept[:top_n]]


def cluster_peaks(
    signal: list[float],
    windows: list[ClipWindow],
    window_seconds: float = 1.0,
) -> list[float]:
    """Absolute (seconds) peak position inside each window of an exploration signal.

    Anchors a Shorts-length cap on the single most hype/energetic moment of a
    clutch window instead of its geometric center — keeps the "juice".

    Args:
        signal: Per-window score values (e.g. blended excitement), one per
            ``window_seconds``.
        windows: Selected clip windows (absolute seconds).
        window_seconds: Seconds each ``signal`` value covers.

    Returns:
        One absolute peak second per window, in the same order as ``windows``.
        Falls back to the window geometric center when the signal is empty or
        the window maps outside the signal.
    """
    peaks: list[float] = []
    n = len(signal)
    for w in windows:
        lo = min(max(int(w.start / window_seconds), 0), n - 1)
        hi = min(max(int(math.ceil(w.end / window_seconds)) - 1, 0), n - 1)
        if n == 0 or hi < lo:
            peaks.append((w.start + w.end) / 2.0)
        else:
            segment = signal[lo : hi + 1]
            offset = int(np.argmax(segment))
            peaks.append((lo + offset + 0.5) * window_seconds)
    return peaks


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


# ---------------------------------------------------------------------------
# Public: top-level audio → windows (+ peak anchors) helpers
# ---------------------------------------------------------------------------


def windows_and_peaks_from_audio(
    audio_path,
    total_seconds: float,
    settings,
) -> list[tuple[ClipWindow, float, float]]:
    """Extract excitement from audio; return the top clutch windows, the
    absolute peak-excitement second within each window, and the peak magnitude.

    Scoring: 0.6 × normalised_emotion + 0.4 × normalised_energy (blended for
    explainability and determinism).  Windows are then selected by this blended
    score.

    Args:
        audio_path: Path to a downloaded audio file (decodable by ffmpeg).
        total_seconds: Total duration of the source (s).
        settings: A Settings instance with the gameplay_* fields.

    Returns:
        Triples of (ClipWindow, peak_absolute_seconds, peak_magnitude), sorted
        by blended score descending.  Empty on failure.
    """
    try:
        from shorts_clipper.attention.audio_energy import extract_audio_energy

        emotion = compute_excitement(
            audio_path,
            window_seconds=1.0,
            max_seconds=settings.gameplay_scan_max_seconds,
        )
        energy = extract_audio_energy(
            audio_path,
            window_seconds=1.0,
            max_seconds=settings.gameplay_scan_max_seconds,
        )

        if not emotion and not energy:
            return []

        # Align lengths (shorter list determines size).
        n = min(len(emotion), len(energy)) if emotion and energy else max(len(emotion), len(energy))
        e_list = emotion[:n] if emotion else [0.0] * n
        k_list = energy[:n] if energy else [0.0] * n

        # Blend: emotion-weighted score for clutch detection.
        blended = [0.6 * e + 0.4 * k for e, k in zip(e_list, k_list)]

        # Normalise blended to [0, 1].
        mx = max(blended) if blended else 0.0
        if mx > 0:
            blended = [b / mx for b in blended]

        windows = select_emotion_windows(
            blended,
            1.0,
            min_length=settings.gameplay_min_length,
            max_length=settings.gameplay_max_length,
            top_n=settings.gameplay_top_windows,
        )
        peak_data = _cluster_peaks_with_magnitudes(blended, windows)
        return [(w, pd[0], pd[1]) for w, pd in zip(windows, peak_data)]
    except Exception as exc:
        log.warning("Emotion window selection failed for %s: %s", audio_path, exc)
        return []


def windows_from_audio_emotion(
    audio_path,
    total_seconds: float,
    settings,
) -> list[ClipWindow]:
    """Top clutch/emotion ClipWindows (windows only; see windows_and_peaks_from_audio)."""
    return [w for w, _, _ in windows_and_peaks_from_audio(audio_path, total_seconds, settings)]
