"""SQLite-backed persistent job queue.

Stores job state so that the web dashboard can show history, progress,
and allow retries — even after a server restart.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    kind: str  # "autopilot" | "clip" | "render"
    status: JobStatus = JobStatus.PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    progress: int = 0  # 0-100
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    output_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


_DB_PATH = Path("outputs/jobs.db")
_lock = threading.RLock()


def _get_db_path() -> Path:
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    path = _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payload TEXT NOT NULL DEFAULT '{}',
            result TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            output_paths TEXT NOT NULL DEFAULT '[]'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)
    """)
    conn.commit()


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        status=JobStatus(row["status"]),
        payload=json.loads(row["payload"]),
        result=json.loads(row["result"]),
        error=row["error"],
        progress=row["progress"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        output_paths=json.loads(row["output_paths"]),
    )


_CLEANUP_DONE = False


class JobQueue:
    """Thread-safe SQLite job queue with CRUD operations."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is not None:
            global _DB_PATH
            _DB_PATH = Path(db_path)
        self._conn = _connect()
        _ensure_table(self._conn)

        global _CLEANUP_DONE
        if not _CLEANUP_DONE:
            _CLEANUP_DONE = True

    def close(self) -> None:
        self._conn.close()

    def create(self, kind: str, payload: dict[str, Any] | None = None) -> Job:
        job = Job(
            id=str(uuid.uuid4()),
            kind=kind,
            payload=payload or {},
            created_at=time.time(),
            updated_at=time.time(),
        )
        with _lock:
            self._conn.execute(
                "INSERT INTO jobs (id, kind, status, payload, result, error, progress, created_at, updated_at, output_paths) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    job.kind,
                    job.status.value,
                    json.dumps(job.payload),
                    json.dumps(job.result),
                    job.error,
                    job.progress,
                    job.created_at,
                    job.updated_at,
                    json.dumps(job.output_paths),
                ),
            )
            self._conn.commit()
        log.info("Job created: %s [%s]", job.id, job.kind)
        return job

    def get(self, job_id: str) -> Job | None:
        with _lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def list_all(self, limit: int = 50) -> list[Job]:
        with _lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def list_by_status(self, status: JobStatus, limit: int = 50) -> list[Job]:
        with _lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def claim_job(self) -> Job | None:
        """Atomically claim the oldest pending job."""
        with _lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1",
                    (JobStatus.PENDING.value,),
                ).fetchone()

                if not row:
                    self._conn.commit()
                    return None

                job_id = row["id"]
                self._conn.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                    (JobStatus.RUNNING.value, time.time(), job_id),
                )
                self._conn.commit()

                return self.get(job_id)
            except Exception:
                self._conn.rollback()
                raise

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        progress: int | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        output_paths: list[str] | None = None,
    ) -> Job | None:
        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status.value, time.time()]

        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if result is not None:
            updates.append("result = ?")
            params.append(json.dumps(result))
        if output_paths is not None:
            updates.append("output_paths = ?")
            params.append(json.dumps(output_paths))

        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"

        with _lock:
            self._conn.execute(sql, params)
            self._conn.commit()

        return self.get(job_id)

    def update_progress(self, job_id: str, progress: int) -> None:
        with _lock:
            self._conn.execute(
                "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
                (progress, time.time(), job_id),
            )
            self._conn.commit()

    def delete(self, job_id: str) -> bool:
        with _lock:
            cursor = self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()
        return cursor.rowcount > 0
