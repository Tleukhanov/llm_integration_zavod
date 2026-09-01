#!/usr/bin/env python3
"""Procedurally synthesize a ~90s phonk-style loop into the shorts music dir.

This is the fallback for the background-music feature: it produces an original,
licence-free phonk-style beat (no external samples, no license required) that can
be mixed under shorts clips.  Uses only the Python standard library (``wave``,
``math``, ``struct``) plus numpy (already a project dependency).

Layered ingredients:
  * 4/4 beat at a fast BPM (~132) with a trap-style hi-hat roll (16ths + extra 32nds).
  * Distorted 808-style sine kick on beats 1 and 3 (double-time feel on the second bar).
  * Sliding, saturated (tanh) 808-style bass following the kick, with mid-bass pattern.
  * Cowbell pattern on the classic phonk E / A pitch figure (Mi / La).

Everything is synthesized so the output is an original work: "no license needed".
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
BPM = 132.0
BEAT = 60.0 / BPM  # seconds per beat
BAR = 4 * BEAT     # seconds per bar (4 beats / bar)


def _adsr(n: int, a: float, d: float, s: float, r: float, sr: int) -> np.ndarray:
    """Return an ADSR gain envelope over *n* samples."""
    a_n = max(1, int(a * sr))
    d_n = max(1, int(d * sr))
    r_n = max(1, int(r * sr))
    s_n = max(1, n - a_n - d_n - r_n)
    att = np.linspace(0.0, 1.0, a_n)
    dec = np.linspace(1.0, s, d_n)
    sus = np.full(s_n, s)
    rel = np.linspace(s, 0.0, r_n)
    env = np.concatenate([att, dec, sus, rel])
    if len(env) < n:
        env = np.concatenate([env, np.full(n - len(env), s)])
    return env[:n]


def _kick(dur: float, sr: int) -> np.ndarray:
    """808-style kick: low sine pitch-sweep + click with saturation."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    freq = 150.0 * np.exp(-t * 18.0) + 42.0
    phase = 2 * np.pi * np.cumsum(freq) / sr
    body = np.sin(phase)
    click = np.sin(2 * np.pi * 900.0 * t) * np.exp(-t * 220.0) * 0.4
    env = _adsr(n, 0.001, 0.05, 0.0, 0.30, sr)
    sig = (body + click) * env
    return np.tanh(sig * 3.0) * 0.95


def _hat(dur: float, sr: int, freq: float = 7800.0) -> np.ndarray:
    """Trap hi-hat: band-passed noise click."""
    n = int(dur * sr)
    rng = np.random.default_rng(1234)
    noise = rng.standard_normal(n)
    # crude one-pole band emphasis near *freq*
    b = np.exp(-2.0 * np.pi * 40.0 / sr)  # fast decay
    filtered = np.zeros(n)
    prev = 0.0
    for i in range(n):
        prev = b * prev + (1 - b) * noise[i]
        filtered[i] = prev
    residual = noise - filtered
    env = np.exp(-np.arange(n) / (0.03 * sr))
    return (residual * 0.5 + filtered * 0.5) * env * 0.5


def _cowbell(dur: float, sr: int, freq: float, bw: float = 600.0) -> np.ndarray:
    """Phonk cowbell: two detuned saw-ish partials for that metallic 'tick', panned
    by hard-panning the two partials to opposite sides (crude mono is fine here)."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    p1 = np.sin(2 * np.pi * freq * t)
    p2 = np.sin(2 * np.pi * (freq * 2.6) * t + math.pi / 3)
    sig = p1 * 0.6 + p2 * 0.4
    env = _adsr(n, 0.001, 0.03, 0.0, 0.12, sr)
    return np.tanh(sig * 1.5) * env * 0.55


def _bass(freq: float, dur: float, sr: int, slide_to: float | None = None) -> np.ndarray:
    """Deep saturated 808 bass line.  Optionally slides from *freq* to *slide_to*."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    if slide_to is not None:
        f = freq * (slide_to / freq) ** (t / dur)
    else:
        f = np.full(n, freq)
    phase = 2 * np.pi * np.cumsum(f) / sr
    sig = np.sin(phase)
    env = _adsr(n, 0.005, 0.03, 0.9, 0.15, sr)
    sig = sig * env
    return np.tanh(sig * 2.2) * 0.85


def make_phonk(duration: float = 90.0, bpm: float = BPM) -> np.ndarray:
    """Synthesize a *duration*-second phonk loop as a mono float array in [-1, 1].

    Returns a numpy array of shape (n_samples,) at ``SAMPLE_RATE`` Hz.
    """
    sr = SAMPLE_RATE
    beat = 60.0 / bpm
    bar = 4 * beat
    total = int(duration * sr)
    bus = np.zeros(total)

    n_bars = int(math.ceil(duration / bar))
    for bar_idx in range(n_bars):
        start = bar_idx * bar
        # --- Kick: beats 1 & 3 (with double-time on alternate bars) ---
        kick_times = [0.0, 2.0 * beat]
        if bar_idx % 2 == 1:
            kick_times += [1.0 * beat, 3.5 * beat]
        for kt in kick_times:
            pos = start + kt
            i0 = int(pos * sr)
            if i0 >= total:
                continue
            seg = _kick(0.45, sr)
            i1 = min(total, i0 + len(seg))
            bus[i0:i1] += seg[: i1 - i0]

        # --- Hi-hat: 16ths with an extra 32nd triplet feel on the offbeat ---
        for step in range(16):
            ht = start + step * (beat / 4)
            i0 = int(ht * sr)
            if i0 >= total:
                continue
            seg = _hat(0.06, sr)
            i1 = min(total, i0 + len(seg))
            vol = 1.0 if step % 4 == 2 else 0.7
            bus[i0:i1] += seg[: i1 - i0] * vol

        # --- Cowbell: E / A pitch figure (phonk signature), swung ---
        cowbell_pitches = [329.63, 440.0, 329.63, 440.0]  # E4 / A4 (Mi / La)
        for step in range(4):
            ct = start + step * beat + 0.21 * beat  # swung off the beat
            i0 = int(ct * sr)
            if i0 >= total:
                continue
            seg = _cowbell(0.18, sr, freq=cowbell_pitches[bar_idx % 4])
            i1 = min(total, i0 + len(seg))
            bus[i0:i1] += seg[: i1 - i0]

        # --- 808 bass: sustained low root on beats 1 & 3, with slides ---
        root = 55.0  # A1
        for kt in [0.0, 2.0 * beat]:
            bt = start + kt
            i0 = int(bt * sr)
            if i0 >= total:
                continue
            seg = _bass(root, 1.8 * beat, sr, slide_to=root * 1.5)
            i1 = min(total, i0 + len(seg))
            bus[i0:i1] += seg[: i1 - i0]

    # --- Master: normalize + soften clips accumulation ---
    peak = np.max(np.abs(bus)) or 1.0
    bus = bus / peak * 0.92
    return bus.astype(np.float64)


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
    parser = argparse.ArgumentParser(description="Generate a procedural phonk loop.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("D:/shorts_music/generated_phonk_loop.wav"),
        help="Output WAV path (default: D:/shorts_music/generated_phonk_loop.wav)",
    )
    parser.add_argument("--duration", type=float, default=90.0, help="Length in seconds")
    parser.add_argument("--bpm", type=float, default=BPM, help="Tempo in BPM")
    args = parser.parse_args(argv)

    print(f"Rendering {args.duration}s phonk loop at {args.bpm:.1f} BPM ...")
    samples = make_phonk(duration=args.duration, bpm=args.bpm)
    write_wav(args.out, samples)
    n = len(samples)
    print(
        f"Wrote {args.out} ({n} samples, ~{n / SAMPLE_RATE:.1f}s, "
        f"{n / SAMPLE_RATE * SAMPLE_RATE / 1000 / 1000:.1f} MB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
