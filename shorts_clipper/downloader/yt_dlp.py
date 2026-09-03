"""yt-dlp video downloader with subtitle fetching."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from shorts_clipper.core.exceptions import SUBTITLE_NOT_AVAILABLE, YOUTUBE_RATE_LIMIT_429
from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

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


def _ffmpegwrapper_dir(src_ffmpeg: Path) -> Path:
    """Stage the bundled ffmpeg under a generic ``ffmpeg.exe`` name.

    yt-dlp's partial-download support looks for a binary literally named
    ``ffmpeg``/``ffmpeg.exe`` in the ``--ffmpeg-location`` directory. The
    imageio-ffmpeg binary has a versioned filename, so we expose it under the
    expected name via a hardlink (same volume) or a copy.
    """
    import tempfile

    wrapper_dir = Path(tempfile.gettempdir()) / "shorts_clipper_ffmpeg"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    target = wrapper_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not target.exists():
        try:
            os.link(src_ffmpeg, target)
        except OSError:
            import shutil

            shutil.copy2(src_ffmpeg, target)
    return wrapper_dir


# ---------------------------------------------------------------------------
# Subtitle fetching + SRT parsing
# ---------------------------------------------------------------------------


def _subtitle_langs() -> list[str]:
    """Configured subtitle languages (default: ru,en) from Settings/env."""
    from shorts_clipper.core.settings import Settings

    langs = Settings.from_env().subtitle_langs
    return langs or ["ru", "en"]


def _sub_lang_arg(langs: list[str]) -> str:
    """Build the --sub-lang value, expanding each lang with common variants."""
    variants: list[str] = []
    for lang in langs:
        variants.append(lang)
        variants.append(f"{lang}-orig")
        variants.append(f"{lang}-US")
        variants.append(f"{lang}-GB")
    return ",".join(dict.fromkeys(variants))


def _srt_masks(langs: list[str]) -> list[str]:
    """SRT glob masks for the configured languages and their variants."""
    masks: list[str] = []
    for lang in langs:
        masks.append(f"subs.{lang}*.srt")
    return masks


def _srt_time_to_seconds(t: str) -> float:
    h, m, s_ms = t.split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def fetch_subtitles(url: str, work_dir: Path, max_retries: int = 3) -> list[TranscriptSegment]:
    """
    Download subtitles (auto or manual) from YouTube for the configured languages.

    Retries with exponential backoff on rate-limit (429) errors.
    Returns parsed TranscriptSegment list, or empty list if unavailable.
    """
    log.info("\n--- FETCHING NATIVE SUBTITLES ---")
    output_base = work_dir / "subs"
    langs = _subtitle_langs()

    last_err_str = ""
    for attempt in range(1, max_retries + 1):
        # Clean previous subtitle files for retry
        for mask in _srt_masks(langs):
            for old_srt in work_dir.glob(mask):
                old_srt.unlink(missing_ok=True)

        cmd = get_base_yt_dlp_cmd()
        cmd.extend(
            [
                "--write-auto-subs",
                "--write-subs",
                "--sub-lang",
                _sub_lang_arg(langs),
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
        srt_files = []
        for mask in _srt_masks(langs):
            srt_files.extend(work_dir.glob(mask))
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

        log.info("✅ Loaded %d subtitle segments.", len(segments))
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
        # Partial downloads require ffmpeg to cut and merge segments. Point
        # yt-dlp at the bundled imageio-ffmpeg binary so it does not fail with
        # "ffmpeg is not installed" in environments without a system ffmpeg.
        # The bundled binary has a versioned name, so stage it under the
        # generic `ffmpeg.exe` name that yt-dlp looks for.
        try:
            src_ffmpeg = Path(ffmpeg_path()).resolve()
            ffmpeg_dir = _ffmpegwrapper_dir(src_ffmpeg)
            cmd.extend(["--ffmpeg-location", str(ffmpeg_dir)])
        except RuntimeError as exc:
            log.warning("ffmpeg not available for partial download: %s", exc)
        # ffmpeg doesn't support curl_cffi impersonation, which causes 403s
        if "--impersonate" in cmd:
            idx = cmd.index("--impersonate")
            del cmd[idx : idx + 2]

    cmd.extend(["--", url])

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired:
        log.error("Audio download timed out after 30 minutes: %s", url)
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
        # Partial downloads require ffmpeg to cut and merge segments. Point
        # yt-dlp at the bundled imageio-ffmpeg binary so it does not fail with
        # "ffmpeg is not installed" in environments without a system ffmpeg.
        # The bundled binary has a versioned name, so stage it under the
        # generic `ffmpeg.exe` name that yt-dlp looks for.
        try:
            src_ffmpeg = Path(ffmpeg_path()).resolve()
            ffmpeg_dir = _ffmpegwrapper_dir(src_ffmpeg)
            cmd.extend(["--ffmpeg-location", str(ffmpeg_dir)])
        except RuntimeError as exc:
            log.warning("ffmpeg not available for partial download: %s", exc)
        # ffmpeg doesn't support curl_cffi impersonation, which causes 403s
        if "--impersonate" in cmd:
            idx = cmd.index("--impersonate")
            del cmd[idx : idx + 2]

    cmd.extend(["--", url])
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        log.error("Video clip download timed out after 15 minutes: %s", url)
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


_CC_LICENSE_FILTER = (
    "license='Creative Commons Attribution license (reuse allowed)'"
)


def search_cc_videos(
    query: str,
    max_results: int = 20,
    *,
    channel_url: str | None = None,
    dateafter: str | None = None,
    timeout: int = 120,
) -> list[dict]:
    """Search YouTube for Creative-Commons-licensed gaming VODs.

    Finds videos whose uploader opted into the Creative Commons license, so the
    footage can be reused (with attribution) in a short-form content pipeline.
    The ``license`` field is set by the uploader, so treat results as
    candidates to verify, not guarantees.

    Args:
        query: Free-text search, e.g. ``"CS2 gameplay"``.
        max_results: Number of results to fetch.
        channel_url: If given, restrict to a channel's uploads
            (e.g. ``https://www.youtube.com/@Channel/videos``).
        dateafter: Only results newer than this (``YYYYMMDD``); skips when None.
        timeout: Subprocess timeout in seconds.

    Returns:
        List of flat metadata dicts (``id``, ``title``, ``url``, ``upload_date``)
        for CC-licensed matches.  Empty on failure timeouts / 429.
    """
    cmd = get_base_yt_dlp_cmd()
    cmd.extend(
        [
            "--match-filters",
            _CC_LICENSE_FILTER,
            "--flat-playlist",
            "--print-json",
            "--playlist-items",
            f"1:{max_results}",
        ]
    )
    if dateafter:
        cmd.extend(["--dateafter", dateafter])

    if channel_url:
        cmd.extend(["--", channel_url])
    else:
        cmd.extend(["--", f"ytsearch{max_results}:{query}"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("CC video search timed out after %ds: %s", timeout, query)
        return []
    if proc.returncode != 0:
        err_str = proc.stderr[-2000:]
        log.warning("CC video search failed for %r: %s", query, err_str)
        return []

    results: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        _id = item.get("id")
        if not _id:
            continue
        results.append(
            {
                "id": _id,
                "title": item.get("title"),
                "url": f"https://www.youtube.com/watch?v={_id}",
                "upload_date": item.get("upload_date"),
                "channel": item.get("channel") or item.get("uploader"),
            }
        )
    log.info("CC video search %r returned %d result(s)", query, len(results))
    return results
