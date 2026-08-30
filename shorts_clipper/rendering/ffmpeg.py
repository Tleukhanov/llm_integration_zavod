"""Safe ffmpeg command builders.

These helpers only build argv lists. Callers should pass the result to
`subprocess.run(command, check=True)` without `shell=True`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shorts_clipper.cropping.geometry import compute_center_crop
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path


@dataclass(frozen=True, slots=True)
class FfmpegRenderOptions:
    target_width: int = 1080
    target_height: int = 1920
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 28
    preset: str = "medium"
    overwrite: bool = True
    extra_input_args: tuple[str, ...] = ()
    extra_output_args: tuple[str, ...] = ()
    font_name: str = "DejaVu Sans"
    font_size: int = 24
    zoom_effect: bool = False  # Disabled by default to prevent over-editing


def build_vertical_render_command(
    *,
    input_path: str | Path,
    output_path: str | Path,
    start: float,
    end: float,
    source_width: int,
    source_height: int,
    subtitles_path: str | Path | None = None,
    options: FfmpegRenderOptions | None = None,
) -> list[str]:
    if start < 0:
        raise ValueError("start must be non-negative")
    if end <= start:
        raise ValueError("end must be greater than start")

    opts = options or FfmpegRenderOptions()
    crop = compute_center_crop(
        width=source_width,
        height=source_height,
        target_width=opts.target_width,
        target_height=opts.target_height,
    )

    filters = [
        crop.as_ffmpeg_filter(),
        f"scale={opts.target_width}:{opts.target_height}",
    ]

    if opts.zoom_effect:
        # Subtle slow zoom in for emotional intensity
        filters.append(
            f"zoompan=z='min(zoom+0.001,1.15)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={opts.target_width}x{opts.target_height}"
        )

    if subtitles_path:
        # ffmpeg subtitles filter is notoriously difficult with paths.
        # We use a double-escaped path for the filter.
        # On Linux, we just need to escape the colon if it's there (rare)
        # and wrap it in single quotes if it has spaces.
        path_str = str(subtitles_path).replace("\\", "/").replace(":", "\\:")

        subtitle_style = (
            f"force_style='FontName={opts.font_name},FontSize={opts.font_size},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
            "Alignment=2,MarginV=140'"
        )
        filters.append(f"subtitles='{path_str}':{subtitle_style}")

    video_filter = ",".join(filters)

    command = [ffmpeg_path()]
    if opts.overwrite:
        command.append("-y")
    command.extend(
        [
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
        ]
    )
    command.extend(opts.extra_input_args)
    command.extend(
        [
            "-i",
            str(input_path),
            "-vf",
            video_filter,
            "-c:v",
            opts.video_codec,
        ]
    )
    if opts.video_codec == "libx264":
        command.extend(["-crf", str(opts.crf), "-preset", opts.preset])
    else:
        command.extend(["-preset", opts.preset])
    command.extend(
        [
            "-c:a",
            opts.audio_codec,
            "-movflags",
            "+faststart",
        ]
    )
    command.extend(opts.extra_output_args)
    command.append(str(output_path))
    return command
