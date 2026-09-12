"""Disk cache for completed yt-dlp media + parsed subtitles, keyed by video_id.

Re-processing the same VOD currently re-downloads audio/video and re-fetches
subtitles every run, which is the main trigger of YouTube 403/429 throttling.
We cache only *fully completed* artifacts (guarded by a sidecar marker), so a
retry of a known video skips the network entirely.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

_CACHE_SUBDIR = "downloads"
_SUBTITLE_TTL_DAYS = 30


def cache_root() -> Path:
    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()
    base = Path(settings.cache_dir) if settings.cache_dir else Path(".cache/shorts-clipper")
    return base / _CACHE_SUBDIR


def _section_key(start: float | None, end: float | None) -> str:
    if start is None and end is None:
        return "full"
    return f"{round(start, 2)}-{round(end, 2)}"


def media_cache_path(video_id: str, kind: str, start, end, ext: str) -> Path:
    return cache_root() / video_id / f"{kind}_{_section_key(start, end)}{ext}"


def _sidecar_path(media_path: Path) -> Path:
    return media_path.with_suffix(media_path.suffix + ".json")


def cache_hit(video_id: str, kind: str, start, end, ext: str) -> Path | None:
    """Return the cached media path if a *complete* artifact exists."""
    if not video_id:
        return None
    path = media_cache_path(video_id, kind, start, end, ext)
    sc = _sidecar_path(path)
    if not path.exists() or not sc.exists():
        return None
    try:
        data = json.loads(sc.read_text(encoding="utf-8"))
        if data.get("complete"):
            return path
    except Exception:
        return None
    return None


def store_media(video_id: str, kind: str, start, end, src: Path) -> Path | None:
    """Copy a just-downloaded file into the cache (best-effort)."""
    if not video_id or not src.exists():
        return None
    src = Path(src)
    dst = media_cache_path(video_id, kind, start, end, src.suffix)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.resolve() != src.resolve():
            shutil.copy2(src, dst)
        _sidecar_path(dst).write_text(
            json.dumps(
                {
                    "video_id": video_id,
                    "kind": kind,
                    "start": start,
                    "end": end,
                    "complete": True,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
            ),
            encoding="utf-8",
        )
        return dst
    except Exception:
        # Caching must never break a run.
        return None


def restore_media(dst: Path, cached: Path) -> bool:
    """Copy a cached artifact out to the requested destination."""
    if not cached.exists():
        return False
    try:
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, dst)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Subtitle cache (parsed TranscriptSegment payloads)
# ---------------------------------------------------------------------------


def subtitle_cache_path(video_id: str, langs: list[str]) -> Path:
    tag = "_".join(langs) or "none"
    return cache_root() / video_id / f"subs_{tag}.json"


def subtitle_cache_hit(video_id: str, langs: list[str]):
    """Return cached segment dicts, or None when absent/expired."""
    if not video_id:
        return None
    path = subtitle_cache_path(video_id, langs)
    if not path.exists():
        return None
    try:
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        if age > timedelta(days=_SUBTITLE_TTL_DAYS):
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("complete") and data.get("segments") is not None:
            return data["segments"]
    except Exception:
        return None
    return None


def store_subtitles(video_id: str, langs: list[str], segments) -> None:
    if not video_id:
        return
    path = subtitle_cache_path(video_id, langs)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "complete": True,
                    "langs": list(langs),
                    "segments": [
                        {"start": s.start, "end": s.end, "text": s.text} for s in segments
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass