"""Retention-driven publish amplification gate.

Reuses the deterministic A/B/C/D retention-grade computation shared with the
``retention-report`` CLI (this module is the single source of truth; the CLI
re-imports the pure helpers from here). When the feature is enabled the gate
stops the factory from wasting publish quota on (niche, platform) pairs that
retain poorly and scales the daily-cap budget for pairs that nail an ``A``.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Columns every report needs when present in the ``clips`` table.
_BASE_COLUMNS: tuple[str, ...] = (
    "platform",
    "published",
    "publish_ts",
    "views",
    "likes",
    "comments",
)

#: Optional scoring/grouping columns probed when present (older schemas skip them).
_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "niche",
    "hook_score",
    "hook_strength",
    "energy",
    "pacing_score",
    "attention_score",
)

#: Candidate source columns for ``avg_hook_score`` / ``avg_energy`` (first present wins).
_HOOK_SCORE_SOURCES: tuple[str, ...] = ("hook_score", "hook_strength")
_ENERGY_SOURCES: tuple[str, ...] = ("energy", "pacing_score", "attention_score")

_GRADE_ORDER: dict[str, int] = {"A": 4, "B": 3, "C": 2, "D": 1}

_NOT_AVAILABLE = "not_available"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_published(row: dict) -> bool:
    value = row.get("published")
    return value not in (None, 0, "0", "")


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _in_window(value: Any, days: int) -> bool:
    """True when *value* is null, unparseable or within the last *days* days."""
    ts = _parse_ts(value)
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return ts >= cutoff


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _total(rows: list[dict], key: str, cols: set[str]) -> int | str:
    if key not in cols:
        return _NOT_AVAILABLE
    values = [v for r in rows if (v := _to_float(r.get(key))) is not None]
    return int(sum(values)) if values else 0


def _median_of(rows: list[dict], key: str, cols: set[str]) -> float | None | str:
    if key not in cols:
        return _NOT_AVAILABLE
    values = [v for r in rows if (v := _to_float(r.get(key))) is not None]
    return round(statistics.median(values), 1) if values else None


def _engagement(rows: list[dict], cols: set[str]) -> float | None:
    """Median(likes+comments) / median(views) over rows with ``views > 0``."""
    if not {"views", "likes", "comments"} <= cols:
        return None
    pairs: list[tuple[float, float]] = []
    for row in rows:
        views = _to_float(row.get("views"))
        if views is None or views <= 0:
            continue
        likes = _to_float(row.get("likes")) or 0.0
        comments = _to_float(row.get("comments")) or 0.0
        pairs.append((likes + comments, views))
    if not pairs:
        return None
    med_views = statistics.median(p[1] for p in pairs)
    if med_views <= 0:
        return None
    med_lc = statistics.median(p[0] for p in pairs)
    return med_lc / med_views


def _avg_score(
    rows: list[dict], source_columns: tuple[str, ...], cols: set[str]
) -> float | None | str:
    source = next((column for column in source_columns if column in cols), None)
    if source is None:
        return _NOT_AVAILABLE
    values = [v for r in rows if (v := _to_float(r.get(source))) is not None]
    return round(statistics.mean(values), 4) if values else None


def _grade(
    publish_rate: float | None, engagement: float | None
) -> tuple[str, float | None, str]:
    """Map (publish_rate, engagement) to a retention grade + score + source."""
    if publish_rate is not None and engagement is not None:
        score: float | None = publish_rate * engagement
        source = "median(likes+comments)/median(views)"
    elif engagement is not None:
        score = engagement
        source = "median(likes+comments)/median(views) (publish_rate not available)"
    elif publish_rate is not None:
        score = publish_rate
        source = "publish_rate_only (no row with views > 0)"
    else:
        return "D", None, "not_available (no publish_rate or engagement)"
    if score >= 0.75:
        grade = "A"
    elif score >= 0.50:
        grade = "B"
    elif score >= 0.25:
        grade = "C"
    else:
        grade = "D"
    return grade, round(score, 4), source


def _aggregate_group(rows: list[dict], cols: set[str]) -> dict[str, Any]:
    clips = len(rows)
    published: int | str = (
        sum(1 for r in rows if _is_published(r)) if "published" in cols else _NOT_AVAILABLE
    )
    publish_rate: float | str = (
        round(published / clips, 4) if isinstance(published, int) else _NOT_AVAILABLE
    )

    engagement = _engagement(rows, cols)
    if not {"views", "likes", "comments"} <= cols:
        engagement_out: float | None | str = _NOT_AVAILABLE
    else:
        engagement_out = engagement

    grade, retention_score, engagement_source = _grade(
        publish_rate if isinstance(publish_rate, float) else None, engagement
    )

    return {
        "platform": rows[0].get("platform"),
        "niche": rows[0].get("niche"),
        "clips": clips,
        "published": published,
        "publish_rate": publish_rate,
        "views_total": _total(rows, "views", cols),
        "likes_total": _total(rows, "likes", cols),
        "comments_total": _total(rows, "comments", cols),
        "median_views": _median_of(rows, "views", cols),
        "median_likes": _median_of(rows, "likes", cols),
        "median_comments": _median_of(rows, "comments", cols),
        "engagement": engagement_out,
        "engagement_source": engagement_source,
        "avg_hook_score": _avg_score(rows, _HOOK_SCORE_SOURCES, cols),
        "avg_energy": _avg_score(rows, _ENERGY_SOURCES, cols),
        "retention_score": retention_score,
        "retention_grade": grade,
    }


def current_retention_grades(
    settings,
    *,
    window_days: int = 30,
) -> dict[tuple[str | None, str | None], RetentionGrade]:
    """Current retention grade per (niche, platform) pair.

    Reaches into the same metrics DB (``settings.metrics_path``) and
    aggregation the ``retention-report`` CLI uses, so the amplifier and the
    report cannot drift apart. A pair with no data (or a metrics DB that is
    missing/unreadable) is simply absent from the result — callers treat a
    missing pair as "no evidence to restrict", i.e. feature-opt behavior.
    """
    db_path = Path(settings.metrics_path)
    if not db_path.exists():
        log.warning("[amplifier] metrics DB not found: %s", db_path)
        return {}

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "clips" not in tables:
            log.warning("[amplifier] metrics DB has no `clips` table: %s", db_path)
            return {}
        cols = _table_columns(conn, "clips")
        select_columns = [c for c in _BASE_COLUMNS + _OPTIONAL_COLUMNS if c in cols]
        if not select_columns:
            log.warning(
                "[amplifier] `clips` table in %s has none of the expected columns.",
                db_path,
            )
            return {}
        rows = [
            dict(r)
            for r in conn.execute(f"SELECT {', '.join(select_columns)} FROM clips").fetchall()
        ]
    except Exception as exc:
        log.warning("[amplifier] metrics DB unreadable (%s): %s", db_path, exc)
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not rows:
        return {}

    has_niche = "niche" in cols
    groups: dict[tuple[str | None, str | None], list[dict]] = {}
    for row in rows:
        if not _in_window(row.get("publish_ts"), window_days):
            continue
        platform = row.get("platform")
        niche = row.get("niche") if has_niche else None
        groups.setdefault((niche, platform), []).append(row)

    grades: dict[tuple[str | None, str | None], RetentionGrade] = {}
    for key, group in groups.items():
        agg = _aggregate_group(group, cols)
        grades[key] = RetentionGrade(
            letter=str(agg["retention_grade"]),
            value=agg["retention_score"],
        )
    return grades


def decide_publish(
    profile,
    niche: str | None,
    platform: str | None,
    grades: dict[tuple[str | None, str | None], RetentionGrade],
    *,
    min_grade: str = "B",
    amplify_factor: float = 1.5,
) -> PublishDecision:
    """Publish decision for one (niche, platform) pair under the amplifier.

    When the feature is on: a pair whose grade letter is below ``min_grade``
    is skipped; a profile ``retention_floor`` (when set) additionally requires
    the retention value to reach it; a pair graded ``A`` gets its daily-cap
    budget multiplied by ``amplify_factor``. Missing data always allows with a
    neutral multiplier (feature-opt fallback).
    """
    grade = grades.get((niche, platform))
    if grade is None:
        return PublishDecision(
            gate="retention",
            allowed=True,
            reason="no retention grade data",
            amplify_factor=1.0,
        )

    if _GRADE_ORDER.get(grade.letter, 0) < _GRADE_ORDER.get(min_grade.upper(), 0):
        return PublishDecision(
            gate="retention",
            allowed=False,
            reason=f"retention grade {grade.letter} below min_grade {min_grade.upper()}",
            amplify_factor=1.0,
        )

    floor = getattr(profile, "retention_floor", None)
    if floor is not None and grade.value is not None and grade.value < floor:
        return PublishDecision(
            gate="retention",
            allowed=False,
            reason=f"retention value {grade.value:.4f} below floor {floor:.4f}",
            amplify_factor=1.0,
        )

    if grade.letter == "A":
        return PublishDecision(
            gate="retention",
            allowed=True,
            reason="retention grade A",
            amplify_factor=max(1.0, amplify_factor),
        )

    return PublishDecision(
        gate="retention",
        allowed=True,
        reason=f"retention grade {grade.letter}",
        amplify_factor=1.0,
    )


@dataclass(frozen=True)
class RetentionGrade:
    """A single (niche, platform) retention grade: letter + 0..1 value."""

    letter: str
    value: float | None


@dataclass(frozen=True)
class PublishDecision:
    """Outcome of the retention amplifier gate for one (niche, platform)."""

    gate: str
    allowed: bool
    reason: str
    amplify_factor: float = 1.0