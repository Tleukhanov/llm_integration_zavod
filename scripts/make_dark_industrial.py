#!/usr/bin/env python3
"""Procedurally synthesize a ~90s Gesaffelstein-style industrial techno loop.

This is a sibling of ``scripts/make_phonk.py``: it produces an original,
licence-free dark-industrial / EBM track (no external samples, no license
required) that can be mixed under shorts clips.  Uses only the Python standard
library (``wave``, ``math``, ``argparse``, ``sys``) plus numpy (already a project
dependency).

Style target: relentless driving 4/4 at 128-140 BPM, massive saturated kick,
sustained distorted sub bass in A minor / F# minor, austere and cold -- long
space between hits, no trap hats and no cowbells, only metallic machine
percussion.  Everything is synthesized so the output is an original work:
"no license needed".

Layered ingredients:
  * Modal-synthesis metal clangs (inharmonic bar partials) + resonant body
    excited by a noise transient -- struck-metal percussion, not a hi-hat.
  * Machine-gun noise bursts: short band-resonant clicks on a rigid grid.
  * Reverse/industrial impacts and rising noise risers for tension.
  * Hard-clipped kick (pitch-swept sine -> tanh -> hard ceiling) 4-on-the-floor.
  * Sustained saturated sub bass (A1 / F#1 roots) plus a sparse EBM-style
    square blip sequence an octave or two up.
  * Dark drone/pad bed with a slowly wandering resonant low-pass, and a long
    synthesized reverb (decaying-noise impulse response) for the metal sends.
  * A sidechain duck (``_duck_env``) on the sub and air beds so every kick carves
    a hole for itself -- that pumping is what makes the kick read as massive.
  * A time-varying master low-pass whose cutoff tracks the arrangement, which
    is what opens and closes the "filter sweep" tension across sections.

Mix bus: the four stems are robust-normalized (``_stem_norm``, 99.7th percentile
rather than true peak so one kick/sub coincidence cannot drag the whole stem
down) and only then summed and gently saturated.  Combined with the per-section
``level`` weight this keeps the arrangement's ~35 dB dynamic shape intact instead
of squashing the whole track into one wall of sound.

Arrangement (fractions of the track, so it scales to any duration):
  intro -> build -> main_a -> breakdown -> main_b -> build_b -> final

DSP helpers used below: ``_tv_biquad`` performs offline *time-varying* biquad
filtering via STFT overlap-add (needed for the filter sweeps), ``_decay_ir`` +
``_fft_convolve`` build the reverb/resonator tails from synthesized noise
impulse responses.  No scipy.
"""

from __future__ import annotations

import argparse
import math
import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
BPM = 134.0
BEAT = 60.0 / BPM  # seconds per beat
BAR = 4 * BEAT     # seconds per bar (4 beats / bar)
SEED = 20240617    # fixed seed -> deterministic renders
PEAK = 0.92        # master peak target (matches the phonk fallback)

# Arrangement map: (name, start fraction, end fraction).  Fractions (not bar
# counts) so a 20s render and a 120s render both keep the same shape.
_SECTIONS: tuple[tuple[str, float, float], ...] = (
    ("intro", 0.00, 0.09),
    ("build", 0.09, 0.18),
    ("main_a", 0.18, 0.46),
    ("breakdown", 0.46, 0.57),
    ("main_b", 0.57, 0.79),
    ("build_b", 0.79, 0.86),
    ("final", 0.86, 1.00),
)

# Per-section mix.  The six 0..1 keys are per-layer crossfade weights (smoothed
# across bars by ``_smooth_bars``); ``bright`` is the master low-pass cutoff in Hz.
# ``level`` is the section's own weight and is what gives the track its dynamic
# shape: without it the arrangement reads as one flat wall, because every layer is
# loudest exactly when it is turned fully up.
_SECTION_CFG: dict[str, dict[str, float]] = {
    #                     drone  pad   sub   perc  kick  bright  level
    "intro":     {"drone": 1.00, "pad": 0.95, "sub": 0.10, "perc": 0.22,
                  "kick": 0.00, "bright": 420.0, "level": 0.20},
    "build":     {"drone": 1.00, "pad": 0.80, "sub": 0.45, "perc": 0.35,
                  "kick": 0.30, "bright": 1100.0, "level": 0.52},
    "main_a":    {"drone": 0.70, "pad": 0.55, "sub": 1.00, "perc": 0.85,
                  "kick": 1.00, "bright": 3000.0, "level": 1.00},
    "breakdown": {"drone": 1.00, "pad": 1.00, "sub": 0.25, "perc": 0.45,
                  "kick": 0.00, "bright": 300.0, "level": 0.34},
    "main_b":    {"drone": 0.75, "pad": 0.50, "sub": 1.00, "perc": 1.00,
                  "kick": 1.00, "bright": 3600.0, "level": 1.06},
    "build_b":   {"drone": 1.00, "pad": 0.85, "sub": 0.35, "perc": 0.40,
                  "kick": 0.25, "bright": 1400.0, "level": 0.56},
    "final":     {"drone": 0.70, "pad": 0.55, "sub": 1.00, "perc": 1.00,
                  "kick": 1.00, "bright": 4600.0, "level": 1.12},
}

# A minor / F# minor pitch pool.  Root is what the sub and drone sit on.
_ROOT_A = 55.00   # A1
_ROOT_FS = 46.25  # F#1
_ROOT_D = 73.42   # D2
_MINOR = (220.00, 261.63, 329.63, 392.00)  # A3 C4 E4 G4 -- blip sequence pool


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------
def _mix(bus: np.ndarray, seg: np.ndarray, at: float, gain: float = 1.0) -> None:
    """Add *seg* into *bus* at time *at* seconds (clipped to the bus length)."""
    i0 = int(round(at * SAMPLE_RATE))
    if i0 >= len(bus) or i0 + len(seg) <= 0:
        return
    i1 = min(len(bus), i0 + len(seg))
    j0 = max(0, -i0)
    bus[i0 + j0 : i1] += seg[j0 : i1 - i0] * gain


def _exp_env(n: int, tau: float) -> np.ndarray:
    """Simple exponential decay envelope over *n* samples with time-constant *tau*."""
    return np.exp(-np.arange(n) / max(1.0, tau))


def _peak_norm(x: np.ndarray, peak: float = 1.0) -> np.ndarray:
    """Scale *x* to a known peak so the arrangement gains below read as a mix balance."""
    top = float(np.max(np.abs(x)))
    return x / top * peak if top > 1e-9 else x


def _stem_norm(x: np.ndarray, target: float, pct: float = 99.7) -> np.ndarray:
    """Scale *x* so its *pct* percentile of |x| lands on *target*.

    Percentile rather than true peak, because one kick/sub coincidence must not
    dictate the level of a whole stem -- that drags every quiet bar down with it.
    The top *pct* is allowed to overshoot: those are the transients the saturator
    downstream is there to glue.
    """
    ref = float(np.percentile(np.abs(x), pct))
    return x / ref * target if ref > 1e-9 else x


def _duck_env(
    triggers: list[float],
    total: int,
    sr: int,
    depth: float = 0.45,
    hold: float = 0.045,
    rel: float = 0.16,
) -> np.ndarray:
    """Sidechain-style gain track: dips at each trigger time in seconds, recovers.

    Combined with ``np.minimum`` so overlapping triggers deepen the dip but never
    multiply down to silence.
    """
    env = np.ones(total)
    for at in triggers:
        i0 = int(at * sr)
        if i0 < 0 or i0 >= total:
            continue
        i1 = min(total, i0 + max(1, int(hold * sr)))
        i2 = min(total, i1 + max(1, int(rel * sr)))
        shape = np.full(i2 - i0, depth)
        shape[i1 - i0 :] = depth + (1.0 - depth) * np.linspace(0.0, 1.0, i2 - i1)
        env[i0:i2] = np.minimum(env[i0:i2], shape)
    return env


def _soft_clip(x: np.ndarray, drive: float = 1.6) -> np.ndarray:
    """Symmetric saturation with a hard ceiling (keeps the summed bus musical)."""
    y = np.tanh(x * drive)
    return np.clip(y * 1.08, -1.15, 1.15)


def _fade_edges(x: np.ndarray, sr: int, ms: float = 12.0) -> np.ndarray:
    """Fade the very start/end in so the file never ends on a click."""
    n = max(1, min(len(x) // 2, int(ms * 1e-3 * sr)))
    ramp = np.linspace(0.0, 1.0, n)
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def _section_at(frac: float) -> str:
    """Return the arrangement section name covering timeline fraction *frac*."""
    for name, _lo, hi in _SECTIONS:
        if frac < hi:
            return name
    return _SECTIONS[-1][0]


# --------------------------------------------------------------------------
# Filters: offline time-varying biquad (STFT overlap-add)
# --------------------------------------------------------------------------
def _svf_coeffs(cutoff: np.ndarray, q: np.ndarray, sr: int, mode: str = "lp") -> np.ndarray:
    """Chamberlin state-variable-filter biquad coefficients, shape (n, 5).

    Returns ``[b0, b1, b2, a1, a2]`` per entry.  Derived analytically so the
    filter's magnitude response can be evaluated directly per FFT frame, which
    is what makes the time-varying (swept) filtering possible.
    """
    fc = np.clip(np.asarray(cutoff, dtype=float), 20.0, sr * 0.20)
    f = np.clip(2.0 * np.sin(np.pi * fc / sr), 1e-4, 0.90)
    qq = np.clip(np.asarray(q, dtype=float), 0.4, 1.15)
    a1 = -(2.0 + qq * f)
    a2 = np.ones_like(a1)
    one = np.ones_like(f)
    if mode == "bp":
        b = np.stack([f, -f, np.zeros_like(f)], axis=-1)
    elif mode == "hp":
        b = np.stack([one, -2.0 * one, one], axis=-1)
    else:  # "lp"
        b = np.stack([f * f, 2.0 * f * f, f * f], axis=-1)
    return np.concatenate([b, a1[:, None], a2[:, None]], axis=-1)


def _frame_ola(frames: np.ndarray, hop: int, win: np.ndarray, length: int) -> np.ndarray:
    """Overlap-add *frames* (n, n_fft) back to *length* samples, window-normalized."""
    n_fft = len(win)
    n_frames = frames.shape[0]
    out = np.zeros((n_frames - 1) * hop + n_fft)
    wsum = np.zeros_like(out)
    w2 = win * win
    for i in range(n_frames):
        s = i * hop
        out[s : s + n_fft] += frames[i] * win
        wsum[s : s + n_fft] += w2
    out = out[:length]
    wsum = wsum[:length]
    return out / np.where(wsum > 1e-3, wsum, 1.0)


def _as_track(value: np.ndarray | float, n_frames: int) -> np.ndarray:
    """Return *value* as a per-frame control track of length *n_frames*.

    Scalars are held flat; anything else is resampled across the frame axis, so a
    sweep can be described with as few or as many control points as it needs.
    """
    a = np.asarray(value, dtype=float)
    if a.ndim == 0 or a.size == 1:
        return np.full(n_frames, float(a.reshape(-1)[0]))
    if a.size == n_frames:
        return a
    return np.interp(np.linspace(0.0, 1.0, n_frames), np.linspace(0.0, 1.0, a.size), a)


def _tv_biquad(
    x: np.ndarray,
    cutoff: np.ndarray | float,
    q: np.ndarray | float,
    sr: int,
    mode: str = "lp",
    n_fft: int = 1024,
    hop: int | None = None,
) -> np.ndarray:
    """Filter *x* with a biquad whose cutoff/q vary over time.

    *cutoff* and *q* are scalars or control tracks (a rising linspace gives a
    filter sweep).  Implemented as STFT -> per-frame analytic magnitude response
    -> overlap-add, so it stays vectorized.
    """
    if hop is None:
        hop = n_fft // 4
    n = len(x)
    pad = n_fft
    xp = np.concatenate([np.zeros(pad), x, np.zeros(pad + n_fft)])
    n_frames = 1 + (len(xp) - n_fft) // hop
    win = np.hanning(n_fft + 1)[:n_fft]  # periodic Hann
    blocks = np.lib.stride_tricks.sliding_window_view(xp, n_fft)[::hop][:n_frames]
    blocks = blocks * win

    fc_track = _as_track(cutoff, n_frames)
    q_track = _as_track(q, n_frames)
    coeffs = _svf_coeffs(fc_track, q_track, sr, mode)
    k = np.arange(n_fft // 2 + 1)
    w = np.exp(-2j * np.pi * k / n_fft)[None, :]
    b0, b1, b2, a1, a2 = (coeffs[:, i, None] for i in range(5))
    resp = (b0 + b1 * w + b2 * w * w) / (1.0 + a1 * w + a2 * w * w)
    if mode == "bp":
        resp = resp * (1.0 + q_track[:, None])  # compensate the SVF band-pass loss

    spec = np.fft.rfft(blocks, axis=1) * resp
    y = _frame_ola(np.fft.irfft(spec, n=n_fft, axis=1), hop, win, len(xp))
    return y[pad : pad + n]


# --------------------------------------------------------------------------
# Synthesized impulse responses: reverb + resonant bodies
# --------------------------------------------------------------------------
def _decay_ir(
    sr: int,
    dur: float,
    tau: float,
    tilt_hz: float = 2200.0,
    peak_hz: float | None = None,
    peak_q: float = 4.0,
    seed: int = 0,
) -> np.ndarray:
    """Synthesize a dark decaying-noise impulse response (reverb / resonator).

    The spectral tilt darkens the tail and an optional resonant peak carves a
    ringing metallic body out of it.  Normalized to unit energy.
    """
    rng = np.random.default_rng(seed)
    n = max(8, int(dur * sr))
    noise = rng.standard_normal(n)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    shape = 1.0 / np.sqrt(1.0 + (f / max(tilt_hz, 50.0)) ** 2)
    if peak_hz is not None:
        bw = max(peak_hz / (2.0 * peak_q), 20.0)
        shape = shape / np.sqrt(1.0 + ((f - peak_hz) / bw) ** 2)
    ir = np.fft.irfft(np.fft.rfft(noise) * shape, n=n)
    ir *= _exp_env(n, tau * sr)
    ir[0] = 0.0
    return ir / (np.sqrt(np.sum(ir * ir)) + 1e-9)


def _fft_convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """FFT convolution of *x* with a short IR, returned at len(x)."""
    n = len(x) + len(ir) - 1
    n_fft = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, n_fft) * np.fft.rfft(ir, n_fft), n=n_fft)
    return y[: len(x)]


# --------------------------------------------------------------------------
# Voices
# --------------------------------------------------------------------------
def _kick(sr: int, tune: float = 1.0, dur: float = 0.42, seed: int = 0) -> np.ndarray:
    """Massive industrial kick: pitch-swept sine, hard clip, tanh, then LP.

    The body sweeps 165 Hz -> 44 Hz; a very short noise transient gives the
    beater attack.  Two saturation stages in series (hard ceiling + tanh) is
    what makes it sound like a driven analogue drum machine rather than a sine.
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    freq = (168.0 * np.exp(-t * 26.0) + 44.0) * tune
    phase = 2 * np.pi * np.cumsum(freq) / sr
    body = np.sin(phase) + 0.30 * np.sin(2.0 * phase) + 0.12 * np.sin(3.0 * phase)
    rng = np.random.default_rng(seed)
    click = rng.standard_normal(n) * _exp_env(n, 0.004 * sr)
    click = _tv_biquad(click, np.full(4, 2600.0), np.full(4, 1.1), sr, mode="bp", n_fft=256)
    env = np.minimum(1.0, np.arange(n) / (0.0015 * sr)) * _exp_env(n, 0.30 * sr)
    sig = (body * env) + click * 0.55
    sig = np.clip(sig * 4.2, -1.2, 1.2)  # analog-ish hard ceiling
    sig = np.tanh(sig * 2.1) * _exp_env(n, 0.11 * sr)
    sig = _tv_biquad(sig, 5200.0, 0.7, sr, mode="lp", n_fft=512)
    return sig * 0.9


def _sub(sr: int, freq: float, dur: float, drive: float = 1.7, seed: int = 0) -> np.ndarray:
    """Sustained sub bass (the low bed of the whole track).

    Kept deliberately *sub*: a light tanh for harmonic grit, then a fixed
    low-pass at 170 Hz so the 2nd/3rd harmonics stop fighting the kick for the
    low end, and a real release so notes leave a sliver of space instead of one
    unbroken tone.  Sub-heavy means high crest -- that headroom is what lets the
    kick punch through instead of being pinned by the bass.
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    drift = 1.0 + 0.004 * np.sin(2 * np.pi * rng.uniform(0.3, 0.9) * t)
    phase = 2 * np.pi * np.cumsum(freq * drift) / sr
    sig = np.sin(phase) + 0.10 * np.sin(2 * phase + 0.4) + 0.03 * np.sin(3 * phase)
    att = np.minimum(1.0, np.arange(n) / (0.025 * sr))
    rel = np.clip((n - np.arange(n)) / (0.16 * sr), 0.0, 1.0) ** 1.5
    trem = 1.0 - 0.10 * np.sin(2 * np.pi * 0.5 * t + rng.uniform(0, 6.28))
    sig = np.tanh(sig * drive) / np.tanh(drive)
    sig = sig * att * rel * trem
    return _tv_biquad(sig, 170.0, 0.7, sr, mode="lp", n_fft=512)


def _metal_hit(
    sr: int,
    base: float = 330.0,
    dur: float = 0.85,
    tau: float = 0.16,
    seed: int = 0,
) -> np.ndarray:
    """Struck-metal clang: modal synthesis + noise-transient resonator.

    Seven inharmonic partial ratios (bar/plate-like, not harmonic) each with
    its own decay, summed with a short resonant body excited by filtered noise.
    """
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    ratios = np.array([1.0, 1.41, 1.97, 2.63, 3.31, 4.12, 5.49])
    sig = np.zeros(n)
    for i, ratio in enumerate(ratios):
        detune = 1.0 + rng.uniform(-0.012, 0.012)
        decay = tau * (1.0 - 0.11 * i)
        sig += (1.0 / (1.0 + 1.6 * i)) * np.sin(
            2 * np.pi * base * ratio * detune * t + rng.uniform(0, 6.28)
        ) * _exp_env(n, decay * sr)
    transient = rng.standard_normal(n) * _exp_env(n, 0.006 * sr)
    body_ir = _decay_ir(sr, 0.35, 0.09, tilt_hz=5000.0, peak_hz=base * 2.0, peak_q=6.0,
                        seed=seed + 11)
    sig += _fft_convolve(transient, body_ir) * 1.6
    return np.tanh(sig * 1.5) * 0.85


def _machine_gun(
    sr: int,
    shots: int = 7,
    step: float = 0.022,
    tone: float = 3100.0,
    dur: float = 0.9,
    seed: int = 0,
) -> np.ndarray:
    """Machine-gun burst: a rigid grid of very short band-resonant clicks."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    burst = np.zeros(n)
    for s in range(shots):
        at = int(s * step * sr)
        ln = int(0.05 * sr)
        if at + ln >= n:
            break
        env = _exp_env(ln, 0.0035 * sr)
        click = rng.standard_normal(ln) * env
        burst[at : at + ln] += click * rng.uniform(0.7, 1.0)
    ir = _decay_ir(sr, 0.5, 0.07, tilt_hz=6000.0, peak_hz=tone, peak_q=9.0, seed=seed + 3)
    out = _fft_convolve(burst, ir)
    tail = rng.standard_normal(n) * _exp_env(n, 0.05 * sr)
    out += _tv_biquad(tail, tone * 1.3, 1.1, sr, mode="bp") * 0.25
    return out * 0.8


def _reverse_impact(sr: int, dur: float = 1.3, seed: int = 0) -> np.ndarray:
    """Reverse/industrial impact: rising swell that lands hard, then decays."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    u = t / max(dur, 1e-6)
    swell = u**3
    noise = rng.standard_normal(n) * swell
    freq = np.linspace(28.0, 130.0, n)
    boom = np.sin(2 * np.pi * np.cumsum(freq) / sr) * swell
    metal = np.sin(2 * np.pi * (740.0 * np.linspace(0.6, 1.0, n)) * t) * (u**6)
    sig = noise * 1.1 + boom * 1.3 + metal * 0.5
    ir = _decay_ir(sr, 0.7, 0.12, tilt_hz=3000.0, seed=seed + 5)
    return np.tanh(_fft_convolve(sig, ir) * 1.2) * 0.9


def _riser(sr: int, dur: float, seed: int = 0) -> np.ndarray:
    """Noise riser: band-pass sweep with rising Q and a rising sine cluster."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    u = np.linspace(0.0, 1.0, n)
    noise = rng.standard_normal(n)
    cutoff = 260.0 + (9000.0 - 260.0) * u**2
    q = np.full(n, 0.9) + 0.25 * u**2
    body = _tv_biquad(noise, cutoff, q, sr, mode="bp")
    cluster = np.zeros(n)
    for ratio in (1.0, 1.5, 2.0):
        f = 110.0 * ratio * np.linspace(1.0, 2.4, n)
        cluster += np.sin(2 * np.pi * np.cumsum(f) / sr) / ratio
    env = u**2.2
    out = body * env * 0.9 + cluster * env * 0.18
    return np.tanh(out * 1.4) * 0.85


def _blip(sr: int, freq: float, dur: float = 0.09, seed: int = 0) -> np.ndarray:
    """Short EBM-style square blip an octave or two above the sub."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    ph = 2 * np.pi * freq * t
    square = np.sign(np.sin(ph)) * 0.7 + np.sin(ph) * 0.3
    env = np.minimum(1.0, np.arange(n) / (0.002 * sr)) * np.clip((n - np.arange(n)) / (0.02 * sr), 0, 1)
    noise = rng.standard_normal(n) * _exp_env(n, 0.003 * sr) * 0.25
    return np.tanh((square * env + noise) * 1.6) * 0.7


def _drone(sr: int, freqs: tuple[float, ...], dur: float, seed: int = 0) -> np.ndarray:
    """Cold drone bed: detuned sines + faint air noise, low-passed and resonant."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    t = np.arange(n) / sr
    sig = np.zeros(n)
    for i, f0 in enumerate(freqs):
        lfo = 1.0 + rng.uniform(0.002, 0.008) * np.sin(
            2 * np.pi * rng.uniform(0.07, 0.31) * t + rng.uniform(0, 6.28)
        )
        sig += (1.0 / (1.0 + i)) * np.sin(2 * np.pi * f0 * np.cumsum(lfo) / sr + rng.uniform(0, 6.28))
    air = rng.standard_normal(n) * 0.05
    air = _tv_biquad(air, np.linspace(400.0, 1500.0, 24), np.full(24, 1.0), sr, mode="bp",
                    n_fft=1024)
    sig = _tv_biquad(sig + air, np.linspace(1500.0, 900.0, 32), np.full(32, 1.05), sr,
                     mode="lp", n_fft=2048)
    att = np.minimum(1.0, np.arange(n) / (0.35 * sr))
    return sig * att * 0.12


# --------------------------------------------------------------------------
# Arrangement
# --------------------------------------------------------------------------
def _smooth_bars(values: list[float], width: int = 3) -> np.ndarray:
    """Crossfade per-bar section levels over *width* bars so gates never click."""
    v = np.asarray(values, dtype=float)
    pad = width // 2
    padded = np.concatenate([np.full(pad, v[0]), v, np.full(pad, v[-1])])
    return np.convolve(padded, np.ones(width) / width, mode="valid")


def make_dark_industrial(duration: float = 90.0, bpm: float = BPM) -> np.ndarray:
    """Synthesize a *duration*-second industrial techno loop as a mono float array.

    Returns a numpy array of shape (n_samples,) at ``SAMPLE_RATE`` Hz, peak-normalized
    to ``PEAK`` so it can be dropped straight into the music pipeline.
    """
    sr = SAMPLE_RATE
    beat = 60.0 / bpm
    bar = 4.0 * beat
    total = max(1, int(duration * sr))
    n_bars = max(1, int(math.ceil(duration / bar)))
    rng = np.random.default_rng(SEED)

    # --- 1. Section map for every bar, plus its smoothed level curves. ---
    bar_sections = [_section_at((b + 0.5) / n_bars) for b in range(n_bars)]
    cfg = [_SECTION_CFG[s] for s in bar_sections]
    drone_lvl = _smooth_bars([c["drone"] for c in cfg])
    pad_lvl = _smooth_bars([c["pad"] for c in cfg])
    sub_lvl = _smooth_bars([c["sub"] for c in cfg])
    perc_curve = _smooth_bars([c["perc"] for c in cfg])
    level = _smooth_bars([c["level"] for c in cfg])
    bright = np.array([c["bright"] for c in cfg], dtype=float)

    kick_bus = np.zeros(total)  # kick only, so it can punch through the ducked sub
    sub_bus = np.zeros(total)   # sustained low bed
    perc = np.zeros(total)      # metal / machine gun / impacts / blips
    air = np.zeros(total)       # drone + pad + risers
    kick_times: list[float] = []  # sidechain triggers

    # --- 2. Pre-render the voice variations (deterministic, reused per hit). ---
    # Every voice is peak-normalized here so the mix gains below are a real balance.
    kicks = [_peak_norm(_kick(sr, tune=t, seed=SEED + i), 1.0)
             for i, t in enumerate((1.0, 0.994, 1.007, 1.0))]
    subs = {
        f: _peak_norm(_sub(sr, f, bar * 1.02, drive=d, seed=SEED + int(f)), 1.0)
        for f, d in ((_ROOT_A, 1.7), (_ROOT_FS, 1.9), (_ROOT_D, 1.6))
    }
    clang = [
        _peak_norm(_metal_hit(sr, base=b, seed=SEED + i), 0.9)
        for i, b in enumerate((196.0, 262.0, 330.0, 415.0))
    ]
    guns = [
        _peak_norm(_machine_gun(sr, shots=s, step=st, tone=tone, seed=SEED + i), 0.8)
        for i, (s, st, tone) in enumerate(
            ((6, 0.022, 3100.0), (9, 0.017, 2400.0), (4, 0.026, 3900.0))
        )
    ]
    impacts = [_peak_norm(_reverse_impact(sr, seed=SEED + i), 1.0) for i in range(2)]
    blips = [_peak_norm(_blip(sr, f, seed=SEED + i), 0.7) for i, f in enumerate(_MINOR)]
    pads = [
        _peak_norm(_drone(sr, (f, f * 1.5, f * 2.0), bar * 1.02, seed=SEED + i), 0.5)
        for i, f in enumerate((110.0, 146.83, 174.61, 220.0))
    ]

    # --- 3. Per-bar arrangement. ---
    for b in range(n_bars):
        sec = bar_sections[b]
        t0 = b * bar
        lvl = float(level[b])
        kick_lvl = float(cfg[b]["kick"])
        perc_lvl = float(perc_curve[b])
        is_main = sec in ("main_a", "main_b", "final")
        is_final = sec == "final"
        is_break = sec in ("breakdown",)

        # --- Drone + pad: sparse, cold bed, always present in some form. ---
        if drone_lvl[b] > 0.02:
            root = _ROOT_A if (b // 4) % 2 == 0 else _ROOT_FS
            _mix(air, _drone(sr, (root, root * 1.5, root * 2.0), bar * 1.02,
                             seed=SEED + b), t0, float(drone_lvl[b]) * 0.9 * lvl)
        if pad_lvl[b] > 0.02 and b % 2 == 0:  # pad moves every other bar, so it breathes
            _mix(air, pads[(b // 2) % len(pads)], t0, float(pad_lvl[b]) * 0.8 * lvl)

        # --- Sub bass: sustained root for the whole bar (1 and 3 reinforced). ---
        # Sits well under the kick on purpose: the low end is a sustained bed, so
        # the kick transients have to stay the loudest thing on the bus.
        if sub_lvl[b] > 0.05:
            root = _ROOT_FS if sec in ("breakdown", "build_b") else (
                _ROOT_D if is_final and b % 4 == 3 else _ROOT_A
            )
            _mix(sub_bus, subs[root], t0, float(sub_lvl[b]) * 0.42 * lvl)
            if kick_lvl > 0.5:  # re-assert the low end on 1 and 3
                _mix(sub_bus, subs[root], t0 + 2.0 * beat, float(sub_lvl[b]) * 0.16 * lvl)

        # --- Kick: relentless 4-on-the-floor. ---
        if kick_lvl > 0.5:
            for k in range(4):
                _mix(kick_bus, kicks[int(rng.integers(len(kicks)))], t0 + k * beat,
                     0.85 * lvl)
                kick_times.append(t0 + k * beat)
            if is_final and b % 4 == 3:  # extra drive on the last bar of the phrase
                _mix(kick_bus, kicks[0], t0 + 3.5 * beat, 0.5 * lvl)
                kick_times.append(t0 + 3.5 * beat)
        elif kick_lvl > 0.1:  # roll-in during builds
            for k in range(int(kick_lvl * 4)):
                at = t0 + (3.0 + 0.5 * k) * beat
                _mix(kick_bus, kicks[int(rng.integers(len(kicks)))], at,
                     (0.35 + 0.2 * k) * lvl)
                kick_times.append(at)

        if perc_lvl < 0.05 and not is_break:
            continue

        # --- Metallic percussion: sparse clangs on the back-beats. ---
        if perc_lvl > 0.2:
            for k, off in ((1, 1.0), (3, 3.0), (5, 4.5)):
                if is_main and rng.random() < (0.75 if k < 5 else 0.45):
                    idx = int(rng.integers(len(clang)))
                    _mix(perc, clang[idx], t0 + off * beat + rng.uniform(-0.008, 0.008),
                         perc_lvl * 0.55 * lvl)
            if b % 4 == 0 and is_main:  # heavier plate hit once a phrase
                _mix(perc, clang[int(rng.integers(len(clang)))], t0 + 2.5 * beat,
                     perc_lvl * 0.5 * lvl)

        # --- Machine-gun bursts: rigid grid, every other bar in the big sections. ---
        if is_main and (sec != "main_a" or b % 2 == 1):
            idx = 1 if is_final else int(rng.integers(len(guns)))
            _mix(perc, guns[idx], t0 + 3.5 * beat, perc_lvl * 0.5 * lvl)

        # --- Sparse EBM blip sequence, only in the last two sections. ---
        if sec in ("main_b", "final") and b % 2 == 0:
            for k, off in enumerate((1.5, 2.5, 3.25)):
                _mix(perc, blips[(b + k) % len(blips)], t0 + off * beat,
                     perc_lvl * 0.22 * lvl)

        # --- Reverse / industrial impacts: at drops and once a phrase. ---
        drops = b == 0 or (
            bar_sections[b] != bar_sections[b - 1] and sec in ("main_a", "main_b", "final")
        )
        if drops:
            _mix(perc, impacts[b % 2], t0, 0.75 * lvl)
        elif b % 8 == 7 and perc_lvl > 0.5:
            _mix(perc, impacts[(b // 8) % 2], t0 + 3.0 * beat, 0.5 * lvl)

    # --- 4. Tension: 2-bar noise risers into every intensity increase. ---
    order = ("intro", "build", "main_a", "breakdown", "main_b", "build_b", "final")
    weight = {name: _SECTION_CFG[name]["kick"] + 0.3 * _SECTION_CFG[name]["perc"] for name in order}
    for b in range(n_bars):
        nxt = bar_sections[min(n_bars - 1, b + 1)]
        if b == n_bars - 1 or weight[nxt] <= weight[bar_sections[b]] + 0.05:
            continue
        _mix(air, _riser(sr, bar * 2.0, seed=SEED + b), b * bar, 0.9)
        # The master filter opens *toward the destination section's* brightness
        # instead of jumping to full open, so the sweep never washes out the dark
        # section the riser starts in.
        dest = _SECTION_CFG[nxt]["bright"]
        if b < n_bars:
            bright[b] = min(9000.0, max(bright[b], math.sqrt(bright[b] * dest)))
        if b + 1 < n_bars:
            bright[b + 1] = max(bright[b + 1], dest)

    # --- 5. Sidechain: the sub and air beds step aside for each kick. ---
    duck = _duck_env(kick_times, total, sr)
    low = kick_bus + sub_bus * duck
    air = air * (0.62 + 0.38 * duck)  # shallow pump, just enough to feel the kick

    # --- 6. Bus sum: drone/pad/percussion go through reverb sends, low end stays dry. ---
    # Wet levels stay well under the dry ones: the IRs are unit-energy, so a send
    # at 1.0 would be as loud as the source and the track would lose all its space.
    verb_ir = _decay_ir(sr, 2.6, 0.7, tilt_hz=1500.0, seed=SEED + 77)
    room_ir = _decay_ir(sr, 0.8, 0.11, tilt_hz=4200.0, seed=SEED + 78)
    air = air * 0.85 + _fft_convolve(air * 0.45, verb_ir) * 0.30
    perc = perc * 0.9 + _fft_convolve(perc * 0.7, verb_ir) * 0.28
    perc = perc + _fft_convolve(perc * 0.4, room_ir) * 0.22

    # --- 7. Master: stem balance, arrangement-tracked low-pass, glue, normalize. ---
    # Each stem gets a robust-normalized target first, so the saturator below only
    # ever sees kick/sub transients in its knee and the bar-to-bar arrangement
    # shape survives instead of being flattened into one wall of sound.
    low = _stem_norm(low, 1.15)
    perc = _stem_norm(perc, 0.60)
    air = _stem_norm(air, 0.55)
    bus = low + perc + air

    n_fft = 2048
    hop = n_fft // 4
    frame_t = (np.arange(1 + (total + 2 * n_fft - n_fft) // hop) * hop) / sr
    bar_centers = (np.arange(n_bars) + 0.5) * bar
    cutoff = np.interp(frame_t, bar_centers, np.clip(bright, 120.0, 12000.0))
    bus = _tv_biquad(bus, cutoff, np.full(len(cutoff), 1.05), sr, mode="lp",
                     n_fft=n_fft, hop=hop)
    bus = _soft_clip(bus, 0.75)
    bus = bus - float(np.mean(bus))  # keep the LFOs from leaving any DC behind

    peak = float(np.max(np.abs(bus))) or 1.0
    bus = bus / peak * PEAK
    return _fade_edges(bus.astype(np.float64), sr)


def write_wav(path: Path, samples: np.ndarray, sr: int = SAMPLE_RATE) -> Path:
    """Write *samples* (float array) to a 16-bit mono WAV file at *path*."""
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a procedural dark industrial loop.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/music/dark_industrial_loop.wav"),
        help="Output WAV path (default: data/music/dark_industrial_loop.wav)",
    )
    parser.add_argument("--duration", type=float, default=90.0, help="Length in seconds")
    parser.add_argument("--bpm", type=float, default=BPM, help="Tempo in BPM")
    args = parser.parse_args(argv)

    print(f"Rendering {args.duration}s dark industrial loop at {args.bpm:.1f} BPM ...")
    samples = make_dark_industrial(duration=args.duration, bpm=args.bpm)
    write_wav(args.out, samples)
    n = len(samples)
    print(
        f"Wrote {args.out} ({n} samples, ~{n / SAMPLE_RATE:.1f}s, "
        f"{n / SAMPLE_RATE * SAMPLE_RATE / 1000 / 1000:.1f} MB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())