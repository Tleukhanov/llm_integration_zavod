from dataclasses import dataclass

import av


@dataclass
class VideoMetadata:
    width: int
    height: int
    duration: float


def get_video_metadata(path: str) -> VideoMetadata:
    try:
        container = av.open(path)
    except av.AVError as exc:
        raise RuntimeError(f"Could not open video file {path}: {exc}") from exc

    try:
        video_stream = next(
            (s for s in container.streams if s.type == "video"), None
        )
        if video_stream is None:
            raise RuntimeError(f"No video stream found in {path}")

        width = video_stream.width
        height = video_stream.height

        if container.duration:
            duration = float(container.duration) / av.time_base
        else:
            duration = float(video_stream.duration * video_stream.time_base)
    finally:
        container.close()

    return VideoMetadata(width=width, height=height, duration=duration)
