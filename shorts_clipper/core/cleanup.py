"""Disk auto-cleanup for rendered clips and stray artifacts.

Archives successfully published clips and prunes old/excess files so the
pipeline can run unattended on machines with limited disk space.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from shorts_clipper.core.settings import Settings

log = logging.getLogger(__name__)

_PUBLISHED_STATUSES = {"success", "partial_success"}

_CLIP_RE = re.compile(r"^rendered_clip_(\d+)\.mp4$")
_THUMB_RE = re.compile(r"^thumbnail_(\d+)\.jpg$")


def should_move_to_archive(metadata: dict) -> bool:
    """Return True if the clip metadata marks it as successfully published."""
    return metadata.get("publish_status") in _PUBLISHED_STATUSES


def _load_sidecar_metadata(media_path: Path) -> dict:
    """Read publish status from the sidecar JSON next to a media file."""
    out_dir = media_path.parent
    candidates: list[Path] = [media_path.with_suffix(".json")]

    m = _CLIP_RE.match(media_path.name)
    if m:
        candidates.append(out_dir / f"final_metadata_{m.group(1)}.json")
    m = _THUMB_RE.match(media_path.name)
    if m:
        candidates.append(out_dir / f"final_metadata_{m.group(1)}.json")
        candidates.append(out_dir / f"rendered_clip_{m.group(1)}.json")

    for cand in candidates:
        try:
            if cand.exists():
                return json.loads(cand.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Failed to read sidecar metadata %s: %s", cand, e)
    return {}


def _has_manifest(media_path: Path) -> bool:
    return media_path.with_name(f"{media_path.stem}_publish_manifest.json").exists()


def _companion_files(media_path: Path) -> list[Path]:
    """Sidecar JSON + manifest + matching thumbnail files for a media file."""
    out_dir = media_path.parent
    companions: list[Path] = [media_path.with_suffix(".json")]
    m = _CLIP_RE.match(media_path.name)
    if m:
        companions.append(out_dir / f"thumbnail_{m.group(1)}.jpg")
        companions.append(out_dir / f"final_metadata_{m.group(1)}.json")
    m = _THUMB_RE.match(media_path.name)
    if m:
        companions.append(out_dir / f"final_metadata_{m.group(1)}.json")
        companions.append(out_dir / f"rendered_clip_{m.group(1)}.json")
    companions.append(media_path.with_name(f"{media_path.stem}_publish_manifest.json"))
    return companions


def _mtime_age_seconds(path: Path, now: datetime) -> float:
    try:
        return now.timestamp() - os.path.getmtime(path)
    except OSError as e:
        log.warning("Failed to stat %s: %s", path, e)
        return 0.0


def _move_to_archive(path: Path, archive_dir: Path) -> bool:
    try:
        shutil.move(str(path), str(archive_dir / path.name))
        return True
    except Exception as e:
        log.error("Failed to archive %s: %s", path, e)
        return False


def _delete(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except Exception as e:
        log.error("Failed to delete %s: %s", path, e)
        return False


def run_cleanup(settings: Settings) -> dict[str, int]:
    """Archive published clips and prune old/excess/stray files.

    Returns a dict of counts for archived / deleted_old / deleted_excess /
    deleted_stray. Every file operation is guarded so a single failure never
    crashes the pipeline.
    """
    counts = {"archived": 0, "deleted_old": 0, "deleted_excess": 0, "deleted_stray": 0}

    output_dir = Path(settings.output_dir)
    if not output_dir.is_dir():
        return counts

    archive_dir = Path(settings.archive_dir)
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.error("Failed to create archive dir %s: %s", archive_dir, e)
        return counts

    now = datetime.now(timezone.utc)
    retention_seconds = settings.clip_retention_days * 86400

    # (a) Move successfully published media to the archive, grouped per clip.
    archived_names: set[str] = set()
    for clip in sorted(output_dir.glob("rendered_clip_*.mp4")):
        if should_move_to_archive(_load_sidecar_metadata(clip)) or _has_manifest(clip):
            for item in [clip] + _companion_files(clip):
                if item.name in archived_names or not item.exists():
                    continue
                if _move_to_archive(item, archive_dir):
                    archived_names.add(item.name)
                    if item.suffix in (".mp4", ".jpg"):
                        counts["archived"] += 1
    # Orphan thumbnails marked published (no matching mp4 in the output dir).
    for thumb in sorted(output_dir.glob("thumbnail_*.jpg")):
        if thumb.name in archived_names:
            continue
        if should_move_to_archive(_load_sidecar_metadata(thumb)):
            if _move_to_archive(thumb, archive_dir):
                counts["archived"] += 1
                for companion in _companion_files(thumb):
                    if companion.exists():
                        _move_to_archive(companion, archive_dir)

    # (b) Delete media older than the retention window that is not archived.
    remaining = list(output_dir.glob("rendered_clip_*.mp4")) + list(
        output_dir.glob("thumbnail_*.jpg")
    )
    for media in remaining:
        if _mtime_age_seconds(media, now) > retention_seconds:
            if _delete(media):
                counts["deleted_old"] += 1
                if media.with_suffix(".json").exists():
                    _delete(media.with_suffix(".json"))

    # (c) Enforce max_keep_clips on remaining mp4 files (oldest first).
    remaining_mp4 = list(output_dir.glob("rendered_clip_*.mp4"))
    excess = len(remaining_mp4) - settings.max_keep_clips
    if excess > 0:
        remaining_mp4.sort(key=lambda p: os.path.getmtime(p))
        for media in remaining_mp4[:excess]:
            if _delete(media):
                counts["deleted_excess"] += 1
                thumb = media.with_name(f"thumbnail_{_CLIP_RE.match(media.name).group(1)}.jpg")
                if thumb.exists():
                    _delete(thumb)
                if media.with_suffix(".json").exists():
                    _delete(media.with_suffix(".json"))

    # (d) Stray partial/sidecar download files.
    for pattern in ("*.part", "*.tmp"):
        for stray in output_dir.glob(pattern):
            if _delete(stray):
                counts["deleted_stray"] += 1

    # (e) Old scout metrics (older than the clip retention window).
    for scout in output_dir.glob("scout_metrics_*"):
        if _mtime_age_seconds(scout, now) > retention_seconds:
            if _delete(scout):
                counts["deleted_stray"] += 1

    return counts


def cleanup_worker(
    settings: Settings,
    interval_seconds: int = 86400,
    stop_event: threading.Event | None = None,
) -> None:
    """Background loop that runs cleanup on an interval, stopping promptly."""
    if stop_event is None:
        stop_event = threading.Event()
    log.info("Cleanup worker started (interval %ss)", interval_seconds)
    while not stop_event.is_set():
        try:
            counts = run_cleanup(settings)
            log.info(
                "Cleanup done: archived=%s deleted_old=%s deleted_excess=%s deleted_stray=%s",
                counts["archived"],
                counts["deleted_old"],
                counts["deleted_excess"],
                counts["deleted_stray"],
            )
        except Exception as e:
            log.error("Cleanup worker failed: %s", e)
        for _ in range(int(interval_seconds)):
            if stop_event.wait(1):
                return