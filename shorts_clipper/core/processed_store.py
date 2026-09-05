"""Persistent store tracking which YouTube videos have already been clipped."""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def extract_video_id(url: str) -> str:
    """Extract a YouTube video ID from a URL, falling back to the raw string."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return url

    # youtu.be/<id>
    if parsed.hostname in ("youtu.be", "www.youtu.be"):
        path = parsed.path.strip("/")
        if path:
            return path.split("/")[0]

    # youtube.com/watch?v=<id>
    qs = urllib.parse.parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]

    # youtube.com/shorts/<id>
    parts = [p for p in parsed.path.split("/") if p]
    for i, part in enumerate(parts):
        if part == "shorts" and i + 1 < len(parts):
            return parts[i + 1]

    return url


class ProcessedStore:
    """JSON-backed set of already-processed video IDs."""

    _VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            entries = raw.get("videos", raw.get("entries", {}))
            self._data = {k: v for k, v in entries.items() if isinstance(v, dict)}
        except Exception as exc:
            log.warning("Failed to read processed-videos store %s: %s", self._path, exc)
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": self._VERSION,
                "videos": self._data,
            }
            self._path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("Failed to persist processed-videos store %s: %s", self._path, exc)

    def is_processed(self, video_id: str) -> bool:
        with self._lock:
            return video_id in self._data

    def mark_processed(
        self,
        video_id: str,
        url: str,
        title: str | None = None,
        when: datetime | None = None,
    ) -> bool:
        with self._lock:
            self._data[video_id] = {
                "url": url,
                "title": title,
                "processed_at": (when or datetime.now(timezone.utc)).isoformat(),
            }
            self._save()
            return True

    def all_ids(self) -> set[str]:
        with self._lock:
            return set(self._data.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    @classmethod
    def from_path(cls, path: str | Path) -> ProcessedStore:
        return cls(Path(path))
