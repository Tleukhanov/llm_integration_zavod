"""
Scout learning system.
Tracks what has worked before.
Future scouts prioritize historically successful patterns.
On first run, all tables are empty — system falls through to API/yt-dlp discovery normally.
Learning accumulates over time automatically.
"""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_DB_PATH = Path("outputs/scout_memory.db")


def _ensure_tables(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS successful_channels (
            channel_id      TEXT PRIMARY KEY,
            channel_title   TEXT,
            niche           TEXT,
            success_count   INTEGER DEFAULT 1,
            last_success    TEXT,
            avg_virality    REAL DEFAULT 0.0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS successful_queries (
            query           TEXT,
            niche           TEXT,
            success_count   INTEGER DEFAULT 1,
            last_success    TEXT,
            avg_virality    REAL DEFAULT 0.0,
            PRIMARY KEY (query, niche)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS successful_videos (
            video_id        TEXT PRIMARY KEY,
            title           TEXT,
            channel_id      TEXT,
            niche           TEXT,
            virality_score  REAL,
            view_count      INTEGER,
            published_at    TEXT,
            clipped_at      TEXT
        )
    """)


def record_success(winner: dict, niche: str, query: str, virality: float) -> None:
    """Called after a successful clip. Updates all learning tables."""
    now_str = datetime.now().isoformat()
    channel_id = winner.get("channel_id", "")
    channel_title = winner.get("channel_title", "")
    video_id = winner.get("video_id") or winner.get("id", "")
    title = winner.get("title", "")
    view_count = winner.get("view_count", 0)
    published_at = winner.get("published_at", "")

    with _lock:
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            import contextlib

            with contextlib.closing(sqlite3.connect(_DB_PATH, check_same_thread=False)) as con:
                _ensure_tables(con)

                if channel_id:
                    # Update channel
                    row = con.execute(
                        "SELECT success_count, avg_virality FROM successful_channels WHERE channel_id = ?",
                        (channel_id,),
                    ).fetchone()
                    if row:
                        sc = row[0] + 1
                        av = (row[1] * row[0] + virality) / sc
                        con.execute(
                            "UPDATE successful_channels SET success_count = ?, avg_virality = ?, last_success = ? WHERE channel_id = ?",
                            (sc, av, now_str, channel_id),
                        )
                    else:
                        con.execute(
                            "INSERT INTO successful_channels (channel_id, channel_title, niche, success_count, last_success, avg_virality) VALUES (?, ?, ?, 1, ?, ?)",
                            (channel_id, channel_title, niche, now_str, virality),
                        )

                if query:
                    # Update query
                    row = con.execute(
                        "SELECT success_count, avg_virality FROM successful_queries WHERE query = ? AND niche = ?",
                        (query, niche),
                    ).fetchone()
                    if row:
                        sc = row[0] + 1
                        av = (row[1] * row[0] + virality) / sc
                        con.execute(
                            "UPDATE successful_queries SET success_count = ?, avg_virality = ?, last_success = ? WHERE query = ? AND niche = ?",
                            (sc, av, now_str, query, niche),
                        )
                    else:
                        con.execute(
                            "INSERT INTO successful_queries (query, niche, success_count, last_success, avg_virality) VALUES (?, ?, 1, ?, ?)",
                            (query, niche, now_str, virality),
                        )

                if video_id:
                    # Record video
                    con.execute(
                        "INSERT OR REPLACE INTO successful_videos (video_id, title, channel_id, niche, virality_score, view_count, published_at, clipped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            video_id,
                            title,
                            channel_id,
                            niche,
                            virality,
                            view_count,
                            published_at,
                            now_str,
                        ),
                    )

                con.commit()
        except Exception:
            pass


def get_successful_channels(niche: str, limit: int = 10) -> list[str]:
    """
    Returns channel IDs that have produced good clips for this niche.
    Returns empty list on first run or unknown niche — caller must handle gracefully.
    """
    with _lock:
        try:
            if not _DB_PATH.exists():
                return []
            import contextlib

            with contextlib.closing(sqlite3.connect(_DB_PATH, check_same_thread=False)) as con:
                _ensure_tables(con)
                rows = con.execute(
                    "SELECT channel_id FROM successful_channels WHERE niche = ? ORDER BY avg_virality DESC, success_count DESC LIMIT ?",
                    (niche, limit),
                ).fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []


def get_successful_queries(niche: str, limit: int = 5) -> list[str]:
    """
    Returns queries that found good candidates for this niche.
    Returns empty list on first run — caller must handle gracefully.
    """
    with _lock:
        try:
            if not _DB_PATH.exists():
                return []
            import contextlib

            with contextlib.closing(sqlite3.connect(_DB_PATH, check_same_thread=False)) as con:
                _ensure_tables(con)
                rows = con.execute(
                    "SELECT query FROM successful_queries WHERE niche = ? ORDER BY avg_virality DESC, success_count DESC LIMIT ?",
                    (niche, limit),
                ).fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []
