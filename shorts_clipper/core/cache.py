"""
SQLite-backed metadata cache for scout.
Caches: duration, views, upload_date, language, subtitle_langs.
Never caches stream URLs (they expire in hours).
TTL default: 6 hours. Configurable per call.
"""

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

_lock = threading.Lock()
_DB_PATH = Path("outputs/jobs.db")


def _ensure_table(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata_cache (
            video_id     TEXT PRIMARY KEY,
            metadata_json TEXT NOT NULL,
            cached_at    TEXT NOT NULL,
            ttl_hours    INTEGER NOT NULL DEFAULT 6
        )
    """)


def get_cached(video_id: str) -> dict | None:
    with _lock:
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            import contextlib

            with contextlib.closing(sqlite3.connect(_DB_PATH, check_same_thread=False)) as con:
                _ensure_table(con)
                row = con.execute(
                    "SELECT metadata_json, cached_at, ttl_hours FROM metadata_cache WHERE video_id = ?",
                    (video_id,),
                ).fetchone()
                if not row:
                    return None
                metadata_json, cached_at, ttl_hours = row
                cached_dt = datetime.fromisoformat(cached_at)
                if datetime.now() > cached_dt + timedelta(hours=ttl_hours):
                    con.execute("DELETE FROM metadata_cache WHERE video_id = ?", (video_id,))
                    con.commit()
                    return None
                return json.loads(metadata_json)
        except Exception:
            return None


def set_cached(video_id: str, metadata: dict, ttl_hours: int = 6) -> None:
    # Strip stream URLs before caching — they expire
    safe = {
        k: v
        for k, v in metadata.items()
        if k not in ("url", "formats", "requested_formats", "requested_downloads")
    }
    with _lock:
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            import contextlib

            with contextlib.closing(sqlite3.connect(_DB_PATH, check_same_thread=False)) as con:
                _ensure_table(con)
                con.execute(
                    """INSERT OR REPLACE INTO metadata_cache
                       (video_id, metadata_json, cached_at, ttl_hours)
                       VALUES (?, ?, ?, ?)""",
                    (video_id, json.dumps(safe), datetime.now().isoformat(), ttl_hours),
                )
                con.commit()
        except Exception:
            pass


def purge_expired() -> int:
    """Delete expired cache entries. Returns count deleted."""
    with _lock:
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            import contextlib

            with contextlib.closing(sqlite3.connect(_DB_PATH, check_same_thread=False)) as con:
                _ensure_table(con)
                cur = con.execute(
                    """DELETE FROM metadata_cache
                       WHERE datetime(cached_at, '+' || ttl_hours || ' hours') < datetime('now')"""
                )
                con.commit()
                count = cur.rowcount
                return count
        except Exception:
            return 0
