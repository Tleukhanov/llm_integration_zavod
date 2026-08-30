"""Performance feedback tracker.

Stores view counts, engagement metrics, and computed performance scores
for rendered clips so the system can learn which content performs best.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DB_PATH = Path("outputs/feedback.db")
_lock = threading.Lock()


@dataclass
class ClipFeedback:
    clip_name: str
    views: int = 0
    likes: int = 0
    shares: int = 0
    comments: int = 0
    watch_time_avg: float = 0.0
    retention_pct: float = 0.0
    performance_score: float = 0.0
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compute_score(self) -> float:
        """Compute a weighted performance score from engagement metrics."""
        score = (
            self.views * 0.001
            + self.likes * 0.1
            + self.shares * 0.5
            + self.comments * 0.2
            + self.retention_pct * 2.0
        )
        self.performance_score = round(score, 2)
        self.updated_at = time.time()
        return self.performance_score


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or _DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            clip_name TEXT PRIMARY KEY,
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            shares INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            watch_time_avg REAL NOT NULL DEFAULT 0.0,
            retention_pct REAL NOT NULL DEFAULT 0.0,
            performance_score REAL NOT NULL DEFAULT 0.0,
            notes TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.commit()


def _row_to_feedback(row: sqlite3.Row) -> ClipFeedback:
    return ClipFeedback(
        clip_name=row["clip_name"],
        views=row["views"],
        likes=row["likes"],
        shares=row["shares"],
        comments=row["comments"],
        watch_time_avg=row["watch_time_avg"],
        retention_pct=row["retention_pct"],
        performance_score=row["performance_score"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class FeedbackStore:
    """Thread-safe SQLite feedback store."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._conn = _connect(Path(db_path) if db_path else None)
        _ensure_table(self._conn)

    def close(self) -> None:
        self._conn.close()

    def upsert(self, fb: ClipFeedback) -> ClipFeedback:
        fb.compute_score()
        with _lock:
            self._conn.execute(
                "INSERT INTO feedback (clip_name, views, likes, shares, comments, "
                "watch_time_avg, retention_pct, performance_score, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(clip_name) DO UPDATE SET "
                "views=excluded.views, likes=excluded.likes, shares=excluded.shares, "
                "comments=excluded.comments, watch_time_avg=excluded.watch_time_avg, "
                "retention_pct=excluded.retention_pct, performance_score=excluded.performance_score, "
                "notes=excluded.notes, updated_at=excluded.updated_at",
                (
                    fb.clip_name,
                    fb.views,
                    fb.likes,
                    fb.shares,
                    fb.comments,
                    fb.watch_time_avg,
                    fb.retention_pct,
                    fb.performance_score,
                    fb.notes,
                    fb.created_at,
                    fb.updated_at,
                ),
            )
            self._conn.commit()
        return fb

    def get(self, clip_name: str) -> ClipFeedback | None:
        with _lock:
            row = self._conn.execute(
                "SELECT * FROM feedback WHERE clip_name = ?", (clip_name,)
            ).fetchone()
        return _row_to_feedback(row) if row else None

    def list_all(self, limit: int = 50) -> list[ClipFeedback]:
        with _lock:
            rows = self._conn.execute(
                "SELECT * FROM feedback ORDER BY performance_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_feedback(r) for r in rows]

    def delete(self, clip_name: str) -> bool:
        with _lock:
            cursor = self._conn.execute("DELETE FROM feedback WHERE clip_name = ?", (clip_name,))
            self._conn.commit()
        return cursor.rowcount > 0
