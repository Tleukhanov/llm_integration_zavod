"""Resolve ffmpeg/ffprobe binaries.

Priority:
1. $FFMPEG_PATH / $FFPROBE_PATH (explicit overrides, handy on VPS)
2. `ffmpeg` / `ffprobe` on PATH
3. Binary bundled with imageio-ffmpeg (local dev, no system install needed)

ffprobe is now optional: video metadata is read via PyAV, so `ffprobe_path()`
is kept as a compat shim that warns and falls back to the ffmpeg binary when a
bundled ffprobe is unavailable (imageio-ffmpeg ships ffmpeg.exe but not
ffprobe.exe).
"""

from __future__ import annotations

import logging
import os
import shutil

log = logging.getLogger(__name__)

_cache: dict[str, str | None] = {}


def _resolve(bin_name: str, env_name: str, imageio_attr: str) -> str:
    cached = _cache.get(bin_name)
    if cached is not None:
        return cached

    explicit = os.environ.get(env_name)
    if explicit and os.path.isfile(explicit):
        _cache[bin_name] = explicit
        return explicit

    found = shutil.which(bin_name)
    if found:
        _cache[bin_name] = found
        return found

    try:
        import imageio_ffmpeg

        exe = getattr(imageio_ffmpeg, imageio_attr)()
        if exe and os.path.isfile(exe):
            log.info("Using bundled %s from imageio-ffmpeg: %s", bin_name, exe)
            _cache[bin_name] = exe
            return exe
    except Exception as exc:
        log.debug("imageio-ffmpeg lookup failed for %s: %s", bin_name, exc)

    raise RuntimeError(
        f"{bin_name} not found. Install it (apt install ffmpeg, or set {env_name}) "
        "or run: pip install imageio-ffmpeg"
    )


def ffmpeg_path() -> str:
    return _resolve("ffmpeg", "FFMPEG_PATH", "get_ffmpeg_exe")


def ffprobe_path() -> str:
    try:
        return _resolve("ffprobe", "FFPROBE_PATH", "get_ffprobe_exe")
    except RuntimeError:
        log.warning(
            "ffprobe not available; falling back to ffmpeg binary. "
            "Note: imageio-ffmpeg does not bundle ffprobe, and video "
            "metadata is read via PyAV, so ffprobe is optional."
        )
        return ffmpeg_path()