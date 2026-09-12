"""SQLite-backed metrics store for produced clip records.

Each row tracks one source video we clipped: how it was published,
whether stats have been collected, and the latest view/like/comment counts.
The store is intentionally small — ``sqlite3`` + ``pathlib`` only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string (the store's canonical format)."""
    return datetime.now(UTC).isoformat()


@dataclass
class ClipRecord:
    """One row in the ``clips`` table — a single produced or scheduled clip."""

    video_id: str
    source_url: str
    title: str = ""
    hook: str | None = None
    affiliate_id: str | None = None
    channel: str = ""
    published: bool = False
    publish_ts: str = field(default_factory=_now_iso)
    rendered_path: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    source_url TEXT,
    title TEXT,
    hook TEXT,
    affiliate_id TEXT,
    channel TEXT,
    published INTEGER DEFAULT 0,
    publish_ts TEXT,
    rendered_path TEXT,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    collected_at TEXT,
    UNIQUE(video_id)
)
"""


class MetricsStore:
    """SQLite store for clip records and their collected performance stats."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create the ``clips`` table if it does not exist yet."""
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_clip(self, rec: ClipRecord) -> bool:
        """Insert *rec*, returning ``False`` when the video_id already exists."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO clips "
            "(video_id, source_url, title, hook, affiliate_id, channel, "
            "published, publish_ts, rendered_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.video_id,
                rec.source_url,
                rec.title,
                rec.hook,
                rec.affiliate_id,
                rec.channel,
                1 if rec.published else 0,
                rec.publish_ts,
                rec.rendered_path,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def mark_published(self, video_id: str, publish_ts: str | None = None) -> None:
        """Mark *video_id* as published, optionally backdating its publish_ts."""
        self._conn.execute(
            "UPDATE clips SET published=1, publish_ts=COALESCE(?, publish_ts) "
            "WHERE video_id=?",
            (publish_ts, video_id),
        )
        self._conn.commit()

    def update_stats(
        self,
        video_id: str,
        views: int | None = None,
        likes: int | None = None,
        comments: int | None = None,
    ) -> None:
        """Coalesce new stats into the row and stamp it as collected now.

        Any ``None`` argument keeps the existing value (via ``COALESCE``),
        and ``collected_at`` is always refreshed so the row is not
        re-fetched by subsequent collection passes.
        """
        self._conn.execute(
            "UPDATE clips "
            "SET views=COALESCE(?, views), "
            "    likes=COALESCE(?, likes), "
            "    comments=COALESCE(?, comments), "
            "    collected_at=? "
            "WHERE video_id=?",
            (views, likes, comments, _now_iso(), video_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def unpublished(self, min_age_seconds: int = 0) -> list[dict]:
        """Rows still needing stats collection, oldest publish_ts first.

        Returns rows where ``published=0`` OR ``published=1`` and
        ``collected_at IS NULL``, filtered to have a ``publish_ts``
        older than ``min_age_seconds`` from now (NULL publish_ts passes).
        """
        cutoff = (datetime.now(UTC) - timedelta(seconds=min_age_seconds)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM clips "
            "WHERE (published=0 OR collected_at IS NULL) "
            "AND (publish_ts IS NULL OR publish_ts <= ?) "
            "ORDER BY publish_ts ASC, id ASC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def stats_channel(self, channel: str) -> dict:
        """Aggregate production/publishing/stats totals for a single channel."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS produced, "
            "SUM(CASE WHEN published=1 THEN 1 ELSE 0 END) AS published, "
            "SUM(CASE WHEN collected_at IS NOT NULL THEN 1 ELSE 0 END) AS with_stats, "
            "SUM(views) AS views, SUM(likes) AS likes, SUM(comments) AS comments, "
            "AVG(views) AS avg_views "
            "FROM clips WHERE channel=?",
            (channel,),
        ).fetchone()
        return {
            "channel": channel,
            "produced": row["produced"] or 0,
            "published": row["published"] or 0,
            "with_stats": row["with_stats"] or 0,
            "views": row["views"] or 0,
            "likes": row["likes"] or 0,
            "comments": row["comments"] or 0,
            "avg_views": round(row["avg_views"] or 0.0, 1),
        }

    def channels(self) -> list[str]:
        """Distinct non-empty channel names, sorted."""
        rows = self._conn.execute(
            "SELECT DISTINCT channel FROM clips WHERE channel <> '' ORDER BY channel"
        ).fetchall()
        return [r["channel"] for r in rows]

    def _channel_avg_median_views(self, channel: str) -> tuple[float | None, float | None]:
        """(avg, median) of ``views`` over published clips for *channel*."""
        values = [
            r["views"]
            for r in self._conn.execute(
                "SELECT views FROM clips "
                "WHERE channel=? AND published=1 AND views IS NOT NULL "
                "ORDER BY views",
                (channel,),
            ).fetchall()
        ]
        n = len(values)
        if n == 0:
            return None, None
        avg = sum(values) / n
        mid = n // 2
        if n % 2 == 1:
            median = float(values[mid])
        else:
            median = (values[mid - 1] + values[mid]) / 2.0
        return avg, median

    def channel_performance(self, limit: int = 10) -> list[dict]:
        """Aggregate real-world performance per source channel.

        ``produced``/``published``/``with_stats``/``views``/``likes``/
        ``comments`` count all rows for the channel (matching
        :meth:`stats_channel`), while ``avg_views`` and ``median_views`` are
        computed strictly over **published** clips with a non-NULL ``views``,
        so never-collected rows cannot dilute the figures the scout feedback
        loop is based on. Rows are sorted by ``avg_views`` descending and
        capped at *limit*.
        """
        rows = self._conn.execute(
            "SELECT channel, "
            "COUNT(*) AS produced, "
            "SUM(CASE WHEN published=1 THEN 1 ELSE 0 END) AS published, "
            "SUM(CASE WHEN collected_at IS NOT NULL THEN 1 ELSE 0 END) AS with_stats, "
            "SUM(views) AS views, SUM(likes) AS likes, SUM(comments) AS comments "
            "FROM clips WHERE channel <> '' GROUP BY channel"
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            avg, median = self._channel_avg_median_views(row["channel"])
            out.append(
                {
                    "channel": row["channel"],
                    "produced": row["produced"] or 0,
                    "published": row["published"] or 0,
                    "with_stats": row["with_stats"] or 0,
                    "views": row["views"] or 0,
                    "likes": row["likes"] or 0,
                    "comments": row["comments"] or 0,
                    "avg_views": round(avg, 1) if avg is not None else 0.0,
                    "median_views": round(median, 1) if median is not None else None,
                }
            )
        out.sort(key=lambda r: r["avg_views"], reverse=True)
        return out[:limit]

    def top_hooks(self, limit: int = 10) -> list[dict]:
        """Best-performing hook texts by average views (non-empty only)."""
        rows = self._conn.execute(
            "SELECT hook, COUNT(*) AS count, AVG(views) AS avg_views "
            "FROM clips "
            "WHERE hook IS NOT NULL AND hook <> '' "
            "GROUP BY hook "
            "ORDER BY avg_views DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "hook": r["hook"],
                "count": r["count"],
                "avg_views": round(r["avg_views"], 1) if r["avg_views"] is not None else None,
            }
            for r in rows
        ]

    def top_partners(self, limit: int = 10) -> list[dict]:
        """Best-performing affiliate partners by average views (non-empty only)."""
        rows = self._conn.execute(
            "SELECT affiliate_id, COUNT(*) AS count, AVG(views) AS avg_views "
            "FROM clips "
            "WHERE affiliate_id IS NOT NULL AND affiliate_id <> '' "
            "GROUP BY affiliate_id "
            "ORDER BY avg_views DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "affiliate_id": r["affiliate_id"],
                "count": r["count"],
                "avg_views": round(r["avg_views"], 1) if r["avg_views"] is not None else None,
            }
            for r in rows
        ]

    def recent(self, limit: int = 8) -> list[dict]:
        """Newest clips first (last N clips by insertion order)."""
        rows = self._conn.execute(
            "SELECT * FROM clips ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict:
        """Store-wide totals plus the single best-performing video."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS produced, "
            "SUM(CASE WHEN published=1 THEN 1 ELSE 0 END) AS published, "
            "SUM(CASE WHEN collected_at IS NOT NULL THEN 1 ELSE 0 END) AS with_stats, "
            "AVG(views) AS avg_views "
            "FROM clips"
        ).fetchone()
        best = self._conn.execute(
            "SELECT video_id, title, views FROM clips "
            "WHERE views IS NOT NULL "
            "ORDER BY views DESC LIMIT 1"
        ).fetchone()
        return {
            "produced": row["produced"],
            "published": row["published"] or 0,
            "with_stats": row["with_stats"] or 0,
            "avg_views": round(row["avg_views"], 1) if row["avg_views"] is not None else None,
            "best_video_id": best["video_id"] if best else None,
            "best_title": best["title"] if best else None,
            "best_views": best["views"] if best else None,
        }


def should_publish_today(store: MetricsStore, channel: str, daily_cap: int) -> bool:
    """True when *channel* can still publish today under *daily_cap*.

    Counts rows with ``published=1`` whose ``publish_ts`` starts with
    today's UTC date, then returns whether that count is below the cap.
    """
    today = datetime.now(UTC).date().isoformat()
    row = store._conn.execute(
        "SELECT COUNT(*) AS n FROM clips "
        "WHERE published=1 AND channel=? AND publish_ts LIKE ?",
        (channel, f"{today}%"),
    ).fetchone()
    return row["n"] < daily_cap
