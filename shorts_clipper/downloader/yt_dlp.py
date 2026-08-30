"""yt-dlp video downloader with subtitle fetching."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path

from shorts_clipper.core.exceptions import SUBTITLE_NOT_AVAILABLE, YOUTUBE_RATE_LIMIT_429
from shorts_clipper.core.models import TranscriptSegment

log = logging.getLogger(__name__)

# Subtitle fetch metrics (module-level counters)
_subtitle_metrics = {
    "fetch_success": 0,
    "fetch_failure": 0,
    "rate_limit_429": 0,
    "forbidden_403": 0,
    "timeout": 0,
}


def get_subtitle_metrics() -> dict:
    """Return a copy of subtitle fetch metrics."""
    total = _subtitle_metrics["fetch_success"] + _subtitle_metrics["fetch_failure"]
    return {
        **_subtitle_metrics,
        "total": total,
        "success_pct": round(_subtitle_metrics["fetch_success"] / total * 100, 1)
        if total > 0
        else 0.0,
        "failure_pct": round(_subtitle_metrics["fetch_failure"] / total * 100, 1)
        if total > 0
        else 0.0,
    }


def get_base_yt_dlp_cmd() -> list[str]:
    import random
    import sys

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--extractor-args",
        "youtube:player_client=default,-android_sdkless",
    ]
    # Check if curl-cffi is available for impersonation
    try:
        import curl_cffi  # noqa: F401

        cmd.extend(["--impersonate", "Chrome"])
    except ImportError:
        pass

    proxy_str = os.environ.get("SHORTS_PROXY")
    if proxy_str:
        proxies = [p.strip() for p in proxy_str.split(",") if p.strip()]
        if proxies:
            cmd.extend(["--proxy", random.choice(proxies)])
    return cmd


# ---------------------------------------------------------------------------
# Subtitle fetching + SRT parsing
# ---------------------------------------------------------------------------


def _srt_time_to_seconds(t: str) -> float:
    h, m, s_ms = t.split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def fetch_subtitles(url: str, work_dir: Path, max_retries: int = 3) -> list[TranscriptSegment]:
    """
    Download English subtitles (auto or manual) from YouTube.

    Retries with exponential backoff on rate-limit (429) errors.
    Returns parsed TranscriptSegment list, or empty list if unavailable.
    """
    log.info("\n--- FETCHING NATIVE ENGLISH SUBTITLES ---")
    output_base = work_dir / "subs"

    last_err_str = ""
    for attempt in range(1, max_retries + 1):
        # Clean previous subtitle files for retry
        for old_srt in work_dir.glob("subs.en*.srt"):
            old_srt.unlink(missing_ok=True)

        cmd = get_base_yt_dlp_cmd()
        cmd.extend(
            [
                "--write-auto-subs",
                "--write-subs",
                "--sub-lang",
                "en,en-orig,en-US,en-GB,en-CA,en-AU,en-NZ,en-IE,en-ZA",
                "--sub-format",
                "srt/best",
                "--convert-subs",
                "srt",
                "--skip-download",
                "--socket-timeout",
                "15",
                "--retries",
                "3",
                "-o",
                str(output_base),
                "--",
                url,
            ]
        )
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            log.warning(
                "Subtitle fetch timed out for %s (attempt %d/%d)", url, attempt, max_retries
            )
            _subtitle_metrics["timeout"] += 1
            if attempt < max_retries:
                time.sleep(2**attempt)
                continue
            _subtitle_metrics["fetch_failure"] += 1
            raise SUBTITLE_NOT_AVAILABLE("Timeout fetching subtitles") from None
        except subprocess.CalledProcessError as err:
            last_err_str = err.stderr.decode(errors="ignore") if err.stderr else ""
            is_rate_limit = "429" in last_err_str or "too many requests" in last_err_str.lower()
            is_forbidden = "403" in last_err_str

            if is_rate_limit:
                _subtitle_metrics["rate_limit_429"] += 1
                log.warning("YouTube 429 rate limit during subtitle fetch for %s", url)
                # Fail fast on 429, it is an IP-level block. Retrying is pointless.
                _subtitle_metrics["fetch_failure"] += 1
                raise YOUTUBE_RATE_LIMIT_429("Rate limited by YouTube") from None
            elif is_forbidden:
                _subtitle_metrics["forbidden_403"] += 1
                log.warning("YouTube 403 forbidden during subtitle fetch for %s", url)

            log.error(
                "Subtitle fetch failed for %s (attempt %d/%d): %s",
                url,
                attempt,
                max_retries,
                last_err_str[:200],
            )
            if attempt < max_retries:
                continue
            _subtitle_metrics["fetch_failure"] += 1
            raise SUBTITLE_NOT_AVAILABLE(f"Fetch failed: {last_err_str[:100]}") from None

        # Success — parse the SRT
        srt_files = list(work_dir.glob("subs.en*.srt"))
        if not srt_files:
            _subtitle_metrics["fetch_failure"] += 1
            raise SUBTITLE_NOT_AVAILABLE("No SRT files found after successful download")

        srt_path = srt_files[0]
        content = srt_path.read_text(encoding="utf-8")

        blocks = re.split(r"\n\s*\n", content.strip())
        segments: list[TranscriptSegment] = []
        for block in blocks:
            lines = block.split("\n")
            if len(lines) >= 3:
                times = re.findall(r"(\d+:\d+:\d+,\d+)", lines[1])
                if len(times) == 2:
                    start = _srt_time_to_seconds(times[0])
                    end = _srt_time_to_seconds(times[1])
                    text = " ".join(lines[2:]).strip()
                    segments.append(TranscriptSegment(start=start, end=end, text=text))

        log.info("✅ Loaded %d English subtitle segments.", len(segments))
        _subtitle_metrics["fetch_success"] += 1
        return segments

    # Exhausted retries
    _subtitle_metrics["fetch_failure"] += 1
    raise SUBTITLE_NOT_AVAILABLE("Exhausted retries fetching subtitles")


def download_audio(
    url: str,
    output_path: str | Path,
    *,
    start_time: float | None = None,
    end_time: float | None = None,
) -> Path:
    """Download best audio only for transcription."""
    output_path = Path(output_path)

    # Clean up leftovers from previous partial downloads
    part_path = Path(str(output_path) + ".part")
    for p in (output_path, part_path):
        if p.exists():
            p.unlink()

    if start_time is not None and end_time is not None:
        log.info("⬇ Downloading audio section %.1fs–%.1fs from %s", start_time, end_time, url)
    else:
        log.info("⬇ Downloading full audio from %s", url)

    cmd = get_base_yt_dlp_cmd()
    cmd.extend(
        [
            "--retries",
            "5",
            "--socket-timeout",
            "15",
            "--extract-audio",
            "-f",
            "ba[ext=m4a]/ba[ext=mp3]/ba",
            "-o",
            str(output_path),
        ]
    )

    if start_time is not None and end_time is not None:
        cmd.extend(["--download-sections", f"*{start_time}-{end_time}"])

    cmd.extend(["--", url])

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        log.error("Audio download timed out after 10 minutes: %s", url)
        raise
    except subprocess.CalledProcessError as err:
        err_str = err.stderr.decode(errors="ignore") if err.stderr else ""
        log.error("Audio download failed via yt-dlp: %s. Stderr: %s", err, err_str)
        if "429" in err_str or "too many requests" in err_str.lower():
            log.warning("YouTube THROTTLING/RATE LIMIT (429) detected during audio download!")
        elif "403" in err_str:
            log.warning("YouTube Access Forbidden (403) detected during audio download!")
        raise
    log.info("✅ Audio download complete: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Video download
# ---------------------------------------------------------------------------


def download_clip(
    url: str,
    output_path: str | Path,
    *,
    start_time: float | None = None,
    end_time: float | None = None,
    max_height: int = 1080,
) -> Path:
    """
    Download a video (or section) via yt-dlp.

    Uses --download-sections for pre-snipped clips, avoiding a full
    download when only a 30-45s window is needed.

    Args:
        url: YouTube or other yt-dlp-compatible URL.
        output_path: Destination path for the downloaded file.
        start_time: Section start in seconds (optional).
        end_time: Section end in seconds (optional).
        max_height: Max vertical resolution to request.

    Returns:
        Path to the downloaded file.
    """
    output_path = Path(output_path)

    # Clean up leftovers from previous partial downloads
    part_path = Path(str(output_path) + ".part")
    for p in (output_path, part_path):
        if p.exists():
            p.unlink()

    if start_time is not None and end_time is not None:
        log.info("⬇ Downloading section %.1fs–%.1fs from %s", start_time, end_time, url)
    else:
        log.info("⬇ Downloading full video from %s", url)

    cmd = get_base_yt_dlp_cmd()
    cmd.extend(
        [
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--socket-timeout",
            "15",
            "--no-part",
            "-f",
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b",
            "-o",
            str(output_path),
        ]
    )

    if start_time is not None and end_time is not None:
        cmd.extend(["--download-sections", f"*{start_time}-{end_time}"])
        # ffmpeg doesn't support curl_cffi impersonation, which causes 403s
        if "--impersonate" in cmd:
            idx = cmd.index("--impersonate")
            del cmd[idx : idx + 2]

    cmd.extend(["--", url])
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        log.error("Video clip download timed out after 10 minutes: %s", url)
        raise
    except subprocess.CalledProcessError as err:
        err_str = err.stderr.decode(errors="ignore") if err.stderr else ""
        log.error("Video clip download failed via yt-dlp: %s. Stderr: %s", err, err_str)
        if "429" in err_str or "too many requests" in err_str.lower():
            log.warning("YouTube THROTTLING/RATE LIMIT (429) detected during video download!")
        elif "403" in err_str:
            log.warning("YouTube Access Forbidden (403) detected during video download!")
        raise
    log.info("✅ Download complete: %s", output_path)
    return output_path
