import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shorts_clipper.core.cleanup import run_cleanup, should_move_to_archive
from shorts_clipper.core.scheduler import (
    enqueue_publish,
    get_due_publish_entries,
    update_publish_entry,
)
from shorts_clipper.core.settings import Settings


def _make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = {
        "output_dir": tmp_path / "outputs",
        "cache_dir": tmp_path / "cache",
        "archive_dir": str(tmp_path / "outputs" / "archive"),
        "clip_retention_days": 30,
        "max_keep_clips": 200,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _write(path: Path) -> None:
    path.write_bytes(b"x")


def _age(path: Path, days: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))


def test_should_move_to_archive():
    assert should_move_to_archive({"publish_status": "success"})
    assert should_move_to_archive({"publish_status": "partial_success"})
    assert not should_move_to_archive({"publish_status": "failed"})
    assert not should_move_to_archive({"publish_status": "scheduled"})
    assert not should_move_to_archive({})


def test_run_cleanup_archives_success_and_deletes_old(tmp_path):
    settings = _make_settings(tmp_path)
    out = Path(settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    success_clip = out / "rendered_clip_1.mp4"
    success_thumb = out / "thumbnail_1.jpg"
    success_meta = out / "final_metadata_1.json"
    _write(success_clip)
    _write(success_thumb)
    success_meta.write_text(
        json.dumps({"title": "t", "publish_status": "success"}), encoding="utf-8"
    )

    failed_clip = out / "rendered_clip_2.mp4"
    failed_meta = out / "final_metadata_2.json"
    _write(failed_clip)
    failed_meta.write_text(json.dumps({"publish_status": "failed"}), encoding="utf-8")
    _age(failed_clip, 60)
    _age(failed_meta, 60)

    _write(out / "stray.part")
    _write(out / "stray.tmp")

    counts = run_cleanup(settings)

    archive = Path(settings.archive_dir)
    assert counts["archived"] == 2
    assert (archive / "rendered_clip_1.mp4").exists()
    assert (archive / "thumbnail_1.jpg").exists()
    assert (archive / "final_metadata_1.json").exists()
    assert not success_clip.exists()

    assert counts["deleted_old"] == 1
    assert not failed_clip.exists()

    assert counts["deleted_stray"] == 2
    assert not (out / "stray.part").exists()
    assert not (out / "stray.tmp").exists()


def test_run_cleanup_archives_on_manifest_only(tmp_path):
    settings = _make_settings(tmp_path)
    out = Path(settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    clip = out / "rendered_clip_9.mp4"
    _write(clip)
    (out / "rendered_clip_9_publish_manifest.json").write_text(
        json.dumps({"overall_status": "SUCCESS"}), encoding="utf-8"
    )

    counts = run_cleanup(settings)

    assert counts["archived"] == 1
    assert not clip.exists()
    assert (Path(settings.archive_dir) / "rendered_clip_9.mp4").exists()


def test_run_cleanup_deletes_excess_oldest_first(tmp_path):
    settings = _make_settings(tmp_path, max_keep_clips=2)
    out = Path(settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    base = datetime.now(timezone.utc) - timedelta(seconds=1000)
    for i in range(4):
        clip = out / f"rendered_clip_{i}.mp4"
        _write(clip)
        ts = (base + timedelta(seconds=i)).timestamp()
        os.utime(clip, (ts, ts))

    counts = run_cleanup(settings)

    assert counts["deleted_excess"] == 2
    remaining = sorted(p.name for p in out.glob("rendered_clip_*.mp4"))
    assert remaining == ["rendered_clip_2.mp4", "rendered_clip_3.mp4"]


def test_run_cleanup_missing_output_dir(tmp_path):
    counts = run_cleanup(_make_settings(tmp_path))
    assert counts == {"archived": 0, "deleted_old": 0, "deleted_excess": 0, "deleted_stray": 0}


def test_scheduler_queue_pending_until_due(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_CACHE_DIR", str(tmp_path / "cache"))

    future_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    entry_path = enqueue_publish(
        str(tmp_path / "clip.mp4"), {"title": "t"}, ["youtube"], future_iso
    )
    assert entry_path.exists()
    data = json.loads(entry_path.read_text(encoding="utf-8"))
    assert data["status"] == "queued"
    assert data["platforms"] == ["youtube"]
    assert data["publish_at"] == future_iso

    assert get_due_publish_entries() == []

    later = datetime.now(timezone.utc) + timedelta(hours=2)
    due = get_due_publish_entries(now=later)
    assert len(due) == 1
    assert due[0]["status"] == "queued"

    update_publish_entry(entry_path, status="done")
    assert get_due_publish_entries(now=later) == []


def test_enqueue_immediate_is_due(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_CACHE_DIR", str(tmp_path / "cache"))

    entry_path = enqueue_publish(str(tmp_path / "clip.mp4"), {}, ["youtube"], None)
    data = json.loads(entry_path.read_text(encoding="utf-8"))
    assert data["status"] == "queued"
    assert data["publish_at"]

    due = get_due_publish_entries(now=datetime.now(timezone.utc))
    assert [d["queue_file"] for d in due] == [str(entry_path)]


def test_update_publish_entry_mutates_json(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_CACHE_DIR", str(tmp_path / "cache"))

    entry_path = enqueue_publish(str(tmp_path / "clip.mp4"), {}, ["youtube"], None)
    update_publish_entry(entry_path, status="failed", error_message="boom")

    data = json.loads(entry_path.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["error_message"] == "boom"
    assert not entry_path.with_name(f"{entry_path.stem}.tmp").exists()