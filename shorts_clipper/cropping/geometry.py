"""Crop geometry helpers for vertical shorts rendering."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CropBox:
    x: int | str
    y: int | str
    width: int
    height: int

    def as_ffmpeg_filter(self) -> str:
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


def compute_center_crop(
    *,
    width: int,
    height: int,
    target_width: int = 1080,
    target_height: int = 1920,
) -> CropBox:
    if width <= 0 or height <= 0:
        raise ValueError("source dimensions must be positive")
    if target_width <= 0 or target_height <= 0:
        raise ValueError("target dimensions must be positive")

    target_ratio = target_width / target_height
    source_ratio = width / height

    if source_ratio > target_ratio:
        crop_height = height
        crop_width = max(1, round(height * target_ratio))
        x = round((width - crop_width) / 2)
        y = 0
    else:
        crop_width = width
        crop_height = max(1, round(width / target_ratio))
        x = 0
        y = round((height - crop_height) / 2)

    return CropBox(x=max(0, x), y=max(0, y), width=crop_width, height=crop_height)
