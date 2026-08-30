"""Thumbnail generator — extract a representative frame from a rendered clip.

Uses a single FFmpeg command to extract one frame at the most visually
interesting point (25% into the clip, where the hook should be landing).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from shorts_clipper.utils.ffmpeg_path import ffmpeg_path
from shorts_clipper.utils.video import get_video_metadata

log = logging.getLogger(__name__)


def extract_thumbnail(
    video_path: str | Path,
    output_path: str | Path | None = None,
    *,
    position: float | None = None,
    quality: int = 2,
) -> Path:
    """
    Extract a single frame as a JPEG thumbnail.

    Args:
        video_path: Path to the source video.
        output_path: Where to save the thumbnail. If None, saves next to video
                     with .jpg extension.
        position: Timestamp in seconds to extract. If None, uses 25% of
                  video duration (where the hook should be landing).
        quality: JPEG quality (2 = best, 31 = worst). Default 2.

    Returns:
        Path to the generated thumbnail.
    """
    video_path = Path(video_path)

    if output_path is None:
        output_path = video_path.with_suffix(".jpg")
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if position is None:
        try:
            meta = get_video_metadata(str(video_path))
            position = meta.duration * 0.25 if meta.duration > 0 else 1.0
        except Exception as err:
            log.warning("Probing video duration failed: %s. Using default 1.0s position.", err)
            position = 1.0

    cmd = [
        ffmpeg_path(),
        "-y",
        "-ss",
        f"{position:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        str(quality),
        str(output_path),
    ]

    log.info("📸 Extracting thumbnail at %.1fs → %s", position, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.warning(
            "FFmpeg thumbnail primary failed, attempting solid fallback: %s",
            result.stderr,
        )
        # Safe fallback: extract frame at 1.0s
        fallback_cmd = [
            ffmpeg_path(),
            "-y",
            "-i",
            str(video_path),
            "-ss",
            "00:00:01.000",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
        fallback_result = subprocess.run(fallback_cmd, capture_output=True, text=True)
        if fallback_result.returncode != 0:
            log.error("FFmpeg thumbnail fallback failed: %s", fallback_result.stderr)
            raise RuntimeError("Thumbnail extraction failed completely.")

    log.info("✅ Thumbnail saved: %s", output_path)
    return output_path
