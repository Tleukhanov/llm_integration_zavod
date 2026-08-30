"""Optional publish scheduler backed by a JSON queue on disk.

Used to delay publishing to a future time; when unused (no `publish_at`
set on the pipeline) nothing is queued and behavior is identical to
immediate publishing.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from shorts_clipper.core.settings import Settings

log = logging.getLogger(__name__)


def _queue_dir(settings: Settings) -> Path:
    qdir = Path(settings.cache_dir) / "publish_queue"
    qdir.mkdir(parents=True, exist_ok=True)
    return qdir


def enqueue_publish(
    path_s: str,
    metadata: dict,
    platforms: list[str],
    publish_at_iso: str | None,
) -> Path:
    """Write a publish job to the queue and return the queue file path."""
    settings = Settings.from_env()
    publish_at = publish_at_iso or datetime.now(timezone.utc).isoformat()
    entry = {
        "path": str(path_s),
        "metadata": metadata,
        "platforms": platforms,
        "publish_at": publish_at,
        "status": "queued",
        "attempts": 0,
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    path = _queue_dir(settings) / f"queued_publish_{ts}.json"
    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Enqueued scheduled publish for %s at %s", path_s, publish_at)
    return path


def _parse_publish_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def get_due_publish_entries(now: datetime | None = None) -> list[dict]:
    """Return queued entries whose publish time has arrived."""
    if now is None:
        now = datetime.now(timezone.utc)
    settings = Settings.from_env()
    due: list[dict] = []
    for queue_file in sorted(_queue_dir(settings).glob("queued_publish_*.json")):
        try:
            entry = json.loads(queue_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.error("Failed to read publish queue entry %s: %s", queue_file, e)
            continue
        if entry.get("status") not in {"queued", "retry"}:
            continue
        if _parse_publish_at(entry.get("publish_at", "")) <= now:
            entry["queue_file"] = str(queue_file)
            due.append(entry)
    return due


def update_publish_entry(path: str | Path, **fields) -> None:
    """Atomically update a queue entry with the given fields."""
    queue_file = Path(path)
    entry = json.loads(queue_file.read_text(encoding="utf-8"))
    entry.update(fields)
    tmp = queue_file.with_name(f"{queue_file.stem}.tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, queue_file)


def _publish_due_once(settings: Settings) -> None:
    from shorts_clipper.publishers.manager import PublishingEngine
    from shorts_clipper.publishers.models import ClipMetadata

    for entry in get_due_publish_entries():
        queue_file = Path(entry["queue_file"])
        video_path = Path(entry["path"])
        if not video_path.exists():
            update_publish_entry(queue_file, status="failed", error_message="Video file missing")
            log.error("Scheduled publish skipped: %s (video missing)", video_path)
            continue

        meta = entry.get("metadata") or {}
        platforms = entry.get("platforms") or settings.publish_platforms
        clip_metadata = ClipMetadata(
            title=meta.get("title") or "",
            description=meta.get("description") or "",
            tags=meta.get("tags") or ["shorts"],
            privacy_status=meta.get("privacy_status") or "private",
        )
        try:
            engine = PublishingEngine()
            results = engine.publish(
                video_path=video_path,
                metadata=clip_metadata,
                platforms=platforms,
            )
            successes = [r for r in results.values() if r.success]
            if successes:
                update_publish_entry(queue_file, status="done")
                log.info("Scheduled publish done: %s (%d/%d platforms)", video_path, len(successes), len(platforms))
            else:
                errors = [r.error_message for r in results.values() if r.error_message]
                update_publish_entry(
                    queue_file,
                    status="failed",
                    error_message="; ".join(errors) or "All platforms failed",
                )
                log.error("Scheduled publish failed for %s: %s", video_path, errors)
        except Exception as e:
            update_publish_entry(queue_file, status="failed", error_message=str(e))
            log.error("Scheduled publish failed for %s: %s", video_path, e)


def publish_scheduler_loop(
    settings: Settings,
    interval_seconds: int = 30,
    stop_event: threading.Event | None = None,
) -> None:
    """Background loop polling the publish queue on an interval."""
    if stop_event is None:
        stop_event = threading.Event()
    log.info("Publish scheduler started (interval %ss)", interval_seconds)
    while not stop_event.is_set():
        try:
            _publish_due_once(settings)
        except Exception as e:
            log.error("Publish scheduler loop failed: %s", e)
        for _ in range(int(interval_seconds)):
            if stop_event.wait(1):
                return