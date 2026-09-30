"""Read-only quality report for a rendered stock short ("inspect" subcommand).

Prints media metadata (via PyAV, falling back to the ffmpeg binary), ASS
subtitle stats, the metadata sidecar, a compliance-gate verdict on the
sidecar title/description, and a set of quick platform-fit checks. Never
modifies the inspected file.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from shorts_clipper.compliance.gate import ComplianceGate, ComplianceVerdict
from shorts_clipper.core.settings import Settings
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

_FFMPEG_DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_FFMPEG_VIDEO_RE = re.compile(r"Video: (\w+).*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps")
_FFMPEG_AUDIO_RE = re.compile(r"Audio: (\w+).*?(\d+) Hz.*?(mono|stereo)")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")


@dataclass
class MediaInfo:
    """Technical metadata extracted from a media file."""

    duration: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    fps: float | None = None
    audio_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bitrate: int | None = None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None

    @property
    def resolution(self) -> str | None:
        if self.width is None or self.height is None:
            return None
        return f"{self.width}x{self.height}"


def _probe_with_av(path: Path) -> MediaInfo:
    import av

    container = av.open(str(path))
    try:
        info = MediaInfo(bitrate=container.bit_rate)
        if container.duration:
            info.duration = container.duration / 1_000_000
        video = next((s for s in container.streams if s.type == "video"), None)
        if video is not None:
            info.width = video.width
            info.height = video.height
            info.video_codec = video.codec_context.name
            if video.average_rate:
                info.fps = float(video.average_rate)
            if info.duration is None and video.duration is not None:
                info.duration = float(video.duration * video.time_base)
        audio = next((s for s in container.streams if s.type == "audio"), None)
        if audio is not None:
            info.audio_codec = audio.codec_context.name
            info.sample_rate = audio.sample_rate
            info.channels = audio.channels
        return info
    finally:
        container.close()


def _probe_with_ffmpeg(path: Path) -> MediaInfo:
    proc = subprocess.run(
        [ffmpeg_path(), "-i", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    text = proc.stderr
    info = MediaInfo()
    match = _FFMPEG_DURATION_RE.search(text)
    if match:
        info.duration = (
            int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
        )
    match = _FFMPEG_VIDEO_RE.search(text)
    if match:
        info.video_codec = match.group(1)
        info.width = int(match.group(2))
        info.height = int(match.group(3))
        info.fps = float(match.group(4))
    match = _FFMPEG_AUDIO_RE.search(text)
    if match:
        info.audio_codec = match.group(1)
        info.sample_rate = int(match.group(2))
        info.channels = 1 if match.group(3) == "mono" else 2
    return info


def probe_media(path: Path) -> MediaInfo:
    """Probe media metadata via PyAV, falling back to the ffmpeg binary."""
    try:
        return _probe_with_av(path)
    except Exception:
        return _probe_with_ffmpeg(path)


def _strip_ass_tags(text: str) -> str:
    text = _ASS_TAG_RE.sub("", text)
    return text.replace("\\N", " ").replace("\\n", " ").strip()


def read_ass(path: Path) -> tuple[int, list[str]]:
    """Return (style count, first 3 dialogue lines) from an ASS file."""
    styles = 0
    dialogues: list[str] = []
    section = ""
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        if section == "[V4+ Styles]" and line.startswith("Style:"):
            styles += 1
        elif section == "[Events]" and line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) == 10:
                dialogues.append(_strip_ass_tags(parts[9]))
    return styles, dialogues[:3]


def read_sidecar(path: Path) -> dict | None:
    """Load the metadata sidecar JSON, or None when absent/unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def run_compliance(settings: Settings, sidecar: dict | None) -> ComplianceVerdict | None:
    """Run the compliance gate on the sidecar title/description."""
    if not sidecar:
        return None
    gate = ComplianceGate(settings)
    return gate.check(sidecar.get("title", ""), sidecar.get("description", ""))


def quick_checks(media: MediaInfo, ass_path: Path) -> list[tuple[str, bool]]:
    """Platform-fit quick checks as (label, ok) pairs."""
    duration_ok = media.duration is not None and 3.0 <= media.duration <= 65.0
    resolution_ok = media.width == 1080 and media.height == 1920
    return [
        ("duration 3-65s", duration_ok),
        ("resolution 1080x1920", resolution_ok),
        ("has audio", media.has_audio),
        ("has subtitles", ass_path.is_file()),
    ]


def _fmt_duration(value: float | None) -> str:
    return f"{value:.2f}s" if value is not None else "unknown"


def _fmt_fps(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "unknown"


def _fmt_sample_rate(value: int | None) -> str:
    return f"{value} Hz" if value is not None else "unknown"


def _fmt_bitrate(value: int | None) -> str:
    return f"{value} bps" if value is not None else "unknown"


def _print_media(media: MediaInfo) -> None:
    print("── Media ──")
    print(f"  duration: {_fmt_duration(media.duration)}")
    print(f"  resolution: {media.resolution or 'unknown'}")
    print(f"  video codec: {media.video_codec or 'unknown'}")
    print(f"  fps: {_fmt_fps(media.fps)}")
    print(f"  audio codec: {media.audio_codec or 'unknown'}")
    print(f"  sample rate: {_fmt_sample_rate(media.sample_rate)}")
    print(f"  channels: {media.channels if media.channels is not None else 'unknown'}")
    print(f"  bitrate: {_fmt_bitrate(media.bitrate)}")


def _print_subtitles(ass_path: Path) -> None:
    print("── Subtitles ──")
    if not ass_path.is_file():
        print("  no ASS")
        return
    styles, dialogues = read_ass(ass_path)
    print(f"  styles: {styles}")
    print("  dialogue:")
    for i, line in enumerate(dialogues, start=1):
        print(f"    {i}. {line}")


def _print_sidecar(sidecar: dict | None) -> None:
    print("── Sidecar ──")
    if sidecar is None:
        print("  no sidecar")
        return
    print(f"  title: {sidecar.get('title', '')}")
    print(f"  niche: {sidecar.get('niche', '')}")
    print(f"  script: {sidecar.get('script', '')}")
    print(f"  publish_status: {sidecar.get('publish_status', '')}")


def _print_compliance(verdict: ComplianceVerdict | None) -> None:
    print("── Compliance ──")
    if verdict is None:
        print("  skipped (no sidecar)")
        return
    outcome = "PASS" if verdict.passed else "FAIL"
    print(f"  verdict: {outcome} (level={verdict.level})")
    for reason in verdict.reasons:
        print(f"    - {reason}")


def _print_checks(checks: list[tuple[str, bool]]) -> None:
    print("── Quick checks ──")
    for label, ok in checks:
        print(f"  {label}: {'PASS' if ok else 'FAIL'}")


def run_inspect(args, settings: Settings) -> int:
    """Print the quality report for ``args.file``; 0 on success, 1 if missing."""
    path = Path(args.file)
    if not path.is_file():
        print(f"❌ File not found: {path}", file=sys.stderr)
        return 1

    print(f"🔍 Inspect: {path}")
    print()

    media = probe_media(path)
    ass_path = path.with_suffix(".ass")
    sidecar = read_sidecar(path.with_suffix(".json"))
    verdict = run_compliance(settings, sidecar)

    _print_media(media)
    print()
    _print_subtitles(ass_path)
    print()
    _print_sidecar(sidecar)
    print()
    _print_compliance(verdict)
    print()
    _print_checks(quick_checks(media, ass_path))
    return 0
