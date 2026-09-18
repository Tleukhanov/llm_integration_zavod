import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path("outputs/subtitle_cache.db")
_conn_pool = None
_db_lock = threading.Lock()


def _get_db():
    global _conn_pool
    with _db_lock:
        if _conn_pool is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Use isolation_level=None for autocommit and avoid transaction deadlocks
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            try:
                # Performance and concurrency optimizations
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=5000")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS subtitle_cache (
                        video_id      TEXT PRIMARY KEY,
                        status        TEXT,
                        language      TEXT,
                        checked_at    REAL,
                        expires_at    REAL
                    )
                """)

                # Create an index on expires_at to speed up purge_expired
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_expires_at ON subtitle_cache(expires_at)"
                )
            except Exception:
                # A half-initialized pool must not be kept nor leaked: close
                # it and let the next caller retry from scratch.
                conn.close()
                raise
            _conn_pool = conn
    return _conn_pool


def get_status(video_id: str) -> str | None:
    conn = _get_db()
    with _db_lock:
        cur = conn.execute(
            "SELECT status FROM subtitle_cache WHERE video_id = ? AND expires_at > ?",
            (video_id, time.time()),
        )
        row = cur.fetchone()
    return row[0] if row else None


def set_status(video_id: str, status: str, language: str = "en"):
    conn = _get_db()
    now = time.time()

    if status == "AVAILABLE":
        ttl = 7 * 24 * 3600
    elif status == "MISSING":
        ttl = 24 * 3600
    elif status == "RATE_LIMITED":
        ttl = 15 * 60
    else:
        ttl = 0

    expires_at = now + ttl

    with _db_lock:
        conn.execute(
            """
            INSERT INTO subtitle_cache (video_id, status, language, checked_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                status=excluded.status,
                language=excluded.language,
                checked_at=excluded.checked_at,
                expires_at=excluded.expires_at
            """,
            (video_id, status, language, now, expires_at),
        )


def purge_expired() -> int:
    """Removes expired subtitle cache entries to prevent infinite database growth."""
    conn = _get_db()
    now = time.time()
    with _db_lock:
        cur = conn.execute("DELETE FROM subtitle_cache WHERE expires_at <= ?", (now,))
        deleted_count = cur.rowcount

    return deleted_count
