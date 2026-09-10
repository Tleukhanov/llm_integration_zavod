"""Seeded visual transform filters for unique clip presentation.

Generates deterministic FFmpeg video filter strings from a seed, applying
subtle zoom/pan/color-grade deltas so each rendered short looks slightly
different (less likely flagged as a duplicate) without hurting watchability.

Return contract for ``render_transform``:
    Returns a list of individual FFmpeg ``-vf`` filter strings (e.g.
    ``["scale=...", "crop=...", "eq=..."]``) to be joined with ``,`` by
    the caller and appended after the crop filter chain.  Empty list when
    effects are disabled or no seed is given.

Env vars:
    SHORTS_RENDER_TRANSFORM  – "1"/"true"/"on" to enable (default OFF).
    SHORTS_RENDER_TRANSFORM_STRENGTH – float 0..1, default 0.5.
"""

from __future__ import annotations

import hashlib
import os


def _seed_to_float(seed: int, idx: int) -> float:
    """Derive a stable pseudo-random float in [0, 1) from a seed + index."""
    h = hashlib.sha256(f"{seed}:{idx}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _is_transform_enabled() -> bool:
    val = os.getenv("SHORTS_RENDER_TRANSFORM", "").lower()
    return val in ("1", "true", "on")


def _strength() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("SHORTS_RENDER_TRANSFORM_STRENGTH", "0.5"))))
    except (ValueError, TypeError):
        return 0.5


def render_transform(seed: int, width: int, height: int) -> list[str]:
    """Return a list of FFmpeg video filter strings for seeded visual uniqueness.

    Return contract: list of individual filter strings (e.g.
    ``["scale=...", "crop=...", "eq=..."]``) to be joined with ``,`` by
    the caller and appended after the crop filter chain.  Empty list when
    disabled.
    """
    if not _is_transform_enabled():
        return []

    s = _strength()

    zoom = 1.0 + _seed_to_float(seed, 0) * 0.08 * s
    zw = round(width * zoom) // 2 * 2
    zh = round(height * zoom) // 2 * 2

    max_x = max(0, zw - width)
    max_y = max(0, zh - height)
    ox = int(_seed_to_float(seed, 1) * max_x) if max_x > 0 else 0
    oy = int(_seed_to_float(seed, 2) * max_y) if max_y > 0 else 0

    brightness = (_seed_to_float(seed, 3) - 0.5) * 0.04 * s
    contrast = 1.0 + (_seed_to_float(seed, 4) - 0.5) * 0.06 * s
    saturation = 1.0 + (_seed_to_float(seed, 5) - 0.5) * 0.08 * s

    return [
        f"scale={zw}:{zh}",
        f"crop={width}:{height}:{ox}:{oy}",
        f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}",
    ]


def apply_effects_filter(
    prev_label: str,
    seed: int,
    width: int,
    height: int,
    py: str = "[v0]",
) -> str:
    """Build a complex-filter snippet that applies effects after *prev_label*.

    ``py`` is the label of the preceding video stream in the filter graph.
    Returns a ``-filter_complex`` string segment, or ``py`` unchanged when
    effects are disabled.
    """
    effects = render_transform(seed, width, height)
    if not effects:
        return py
    label = f"[transformed_{seed & 0xFFFF:04x}]"
    chain = py + "," + ",".join(effects)
    return f"{chain}{label}"
