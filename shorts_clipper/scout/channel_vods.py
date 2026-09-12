"""YouTube channel-vod provider for auto-sourcing fresh uploads.

Flat-lists the latest uploads of a pool of channels (e.g. CS2 creators)
via yt-dlp and exposes them as ``SourceVideo`` instances. The channel pool
is read from ``config/vod_sources.json`` unless ``channel_urls`` is given.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from shorts_clipper.downloader.yt_dlp import get_base_yt_dlp_cmd
from shorts_clipper.scout.source_scout import SourceVideo, _BaseProvider

log = logging.getLogger(__name__)

_PLAYLIST_TIMEOUT_SECONDS = 60


def _config_path() -> Path:
    """Absolute path to ``config/vod_sources.json`` (module-path based, not CWD)."""
    return Path(__file__).resolve().parent.parent.parent / "config" / "vod_sources.json"


def _load_channel_pool(channel_urls: list[str] | None) -> list[dict]:
    """Return the channel pool as entry dicts (``uri``, ``tags``, ``title``).

    Uses *channel_urls* directly when non-empty; otherwise loads the ``sources``
    array from ``config/vod_sources.json`` and keeps every enabled YouTube entry.
    Returns ``[]`` on any read/parse error so callers degrade to no results.
    """
    if channel_urls:
        return [
            {"uri": uri.strip(), "tags": [], "title": ""}
            for uri in channel_urls
            if uri and uri.strip()
        ]

    try:
        raw = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Could not read vod_sources.json: %s", exc)
        return []

    entries = raw if isinstance(raw, list) else raw.get("sources", [])
    pool: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("platform") != "youtube" or not entry.get("enabled", False):
            continue
        uri = str(entry.get("uri") or "").strip()
        if not uri:
            continue
        pool.append(
            {
                "uri": uri,
                "tags": list(entry.get("tags") or []),
                "title": entry.get("title") or "",
            }
        )
    return pool


def _fetch_channel_uploads(channel_uri: str, limit: int) -> list[dict]:
    """Flat-list the ``limit`` most recent uploads from a channel videos page.

    Runs yt-dlp as a subprocess with ``--flat-playlist --print-json``; metadata
    per item is intentionally limited (title, id, optional duration/views).
    Returns ``[]`` on timeout or non-zero exit so one bad channel never breaks
    the whole scout.
    """
    cmd = get_base_yt_dlp_cmd()
    cmd.extend(
        [
            "--flat-playlist",
            "--print-json",
            "--playlist-items",
            f"1:{limit}",
            "--socket-timeout",
            "15",
            "--",
            channel_uri,
        ]
    )
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_PLAYLIST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log.warning("Channel VOD listing timed out for %s", channel_uri)
        return []
    if proc.returncode != 0:
        log.warning("Channel VOD listing failed for %s: %s", channel_uri, proc.stderr[-500:])
        return []

    uploads: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("id"):
            uploads.append(item)
    return uploads


class YouTubeChannelVodsProvider(_BaseProvider):
    """Provide the latest uploads from a configured pool of YouTube channels."""

    def _search(self, query: str, limit: int, **kw) -> list[SourceVideo]:
        channel_urls: list[str] = list(kw.pop("channel_urls", []) or [])
        exclude_ids: set[str] = set(kw.pop("exclude_ids", set()) or set())

        pool = _load_channel_pool(channel_urls)
        if not pool:
            log.warning("No configured YouTube channel URLs to scout")
            return []

        out: list[SourceVideo] = []
        seen: set[str] = set(exclude_ids)
        for entry in pool:
            if len(out) >= limit:
                break
            per_channel = limit - len(out)
            for item in _fetch_channel_uploads(entry["uri"], per_channel):
                vid = item.get("id") or item.get("video_id") or ""
                if not vid or vid in seen or len(out) >= limit:
                    continue
                seen.add(vid)
                duration = item.get("duration")
                try:
                    duration_seconds = float(duration) if duration is not None else None
                except (TypeError, ValueError):
                    duration_seconds = None
                out.append(
                    SourceVideo(
                        url=f"https://www.youtube.com/watch?v={vid}",
                        video_id=vid,
                        title=item.get("title") or "",
                        duration_seconds=duration_seconds,
                        platform="youtube",
                        license="",
                        extra={
                            "channel": item.get("channel")
                            or item.get("uploader")
                            or entry["title"],
                            "view_count": item.get("view_count"),
                            "upload_date": item.get("upload_date"),
                            "tags": list(entry["tags"]),
                        },
                    )
                )
        return out[:limit]
