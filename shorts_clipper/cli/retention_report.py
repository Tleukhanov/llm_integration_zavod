"""Decision-science retention report ("retention-report" subcommand).

Deterministic, regex-free aggregation over the metrics store
(``settings.metrics_path``) grouped per (platform, niche). For every group
with at least one clip the report computes production/publishing counters,
view/like/comment totals and medians, plus an ``A/B/C/D`` retention grade
derived from publish rate x engagement, so the operator can see whether the
algorithm's hook/attention/energy decisions actually produce watchable clips
and feed that back into the attention/editorial weights.

Grade formula (mirrored in the JSON ``schema`` block and the Markdown footer):

    engagement = median(likes + comments) / median(views)   # rows with views > 0
    retention_score = publish_rate * engagement
    fallback (no row with views > 0): retention_score = publish_rate
    A >= 0.75, B >= 0.50, C >= 0.25, otherwise D

Missing columns (older schemas) are reported as ``"not_available"`` instead of
crashing the run. Rows are sorted by (platform, niche), then retention grade
best-first.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shorts_clipper.core.settings import Settings

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

_DEFAULT_OUT_JSON = Path("outputs") / "retention_report.json"
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


def _fmt(value: Any, places: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Retention Report", ""]
    lines.append(f"Generated: {payload['generated_at']}")
    lines.append(f"Metrics DB: `{payload['metrics_path']}`")
    lines.append(
        f"Lookback: last {payload['window_days']} day(s) by `publish_ts` "
        "(rows without / unparseable `publish_ts` always count)"
    )
    filters = payload["filters"]
    lines.append(f"Filters: platform={filters['platform'] or 'all'}, "
                 f"niche={filters['niche'] or 'all'}")
    lines.append("")
    header = [
        "Platform",
        "Niche",
        "Grade",
        "Clips",
        "Published",
        "Publish rate",
        "Views (med)",
        "Likes (med)",
        "Comments (med)",
        "Engagement",
        "Avg hook",
        "Avg energy",
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for r in payload["rows"]:
        values = [
            _fmt(r["platform"], 0) or "-",
            _fmt(r["niche"], 0) or "-",
f"**{r['retention_grade']}**",
                _fmt(r["clips"], 0),
                _fmt(r["published"], 0),
            _fmt(r["publish_rate"]),
            _fmt(r["median_views"]),
            _fmt(r["median_likes"]),
            _fmt(r["median_comments"]),
            _fmt(r["engagement"]),
            _fmt(r["avg_hook_score"]),
            _fmt(r["avg_energy"]),
        ]
        lines.append("| " + " | ".join(values) + " |")
    lines.extend(["", "## Methodology", "", "```"])
    schema = payload["schema"]
    lines.extend(
        [
            f"retention_score = {schema['retention_score']}",
            f"engagement = {schema['engagement']}",
            "",
            "retention_grade thresholds:",
        ]
    )
    for grade, threshold in schema["grade_thresholds"].items():
        lines.append(f"  {grade}: {threshold}")
    lines.extend(
        [
            "",
            f"missing_data = {schema['missing_data']}",
            f"lookback = {schema['lookback']}",
            "",
            "Rows are sorted by (platform, niche), then retention grade best-first.",
        ]
    )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def run_retention_report(settings: Settings, args: argparse.Namespace) -> int:
    """Read the metrics DB and write the JSON + Markdown retention report."""
    db_path = Path(settings.metrics_path)
    platform_filter = getattr(args, "platform", None)
    days = getattr(args, "days", 30)
    niche_filter = getattr(args, "niche", None)
    out_path = Path(getattr(args, "out", None)) if getattr(args, "out", None) else _DEFAULT_OUT_JSON

    if days < 1:
        print(f"[ERR] --days must be >= 1 (got {days}).", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"[ERR] Metrics database not found: {db_path}", file=sys.stderr)
        print(
            "[INFO] Run the factory first (e.g. "
            "`shorts-clipper autopilot --batch-file sources.txt`).",
            file=sys.stderr,
        )
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "clips" not in tables:
            print(f"[ERR] Metrics database has no `clips` table: {db_path}", file=sys.stderr)
            return 1
        cols = _table_columns(conn, "clips")
        select_columns = [c for c in _BASE_COLUMNS + _OPTIONAL_COLUMNS if c in cols]
        if not select_columns:
            print(
                f"[ERR] `clips` table in {db_path} has none of the expected columns.",
                file=sys.stderr,
            )
            return 1
        rows = [
            dict(r)
            for r in conn.execute(f"SELECT {', '.join(select_columns)} FROM clips").fetchall()
        ]
    finally:
        conn.close()

    if not rows:
        print(f"[ERR] No clip records found in {db_path}.", file=sys.stderr)
        print(
            "[INFO] Run the factory first (e.g. "
            "`shorts-clipper autopilot --batch-file sources.txt`).",
            file=sys.stderr,
        )
        return 1

    has_niche = "niche" in cols
    if niche_filter and not has_niche:
        print(
            f"[ERR] --niche '{niche_filter}' requested but the metrics DB has no "
            "`niche` column (older schema).",
            file=sys.stderr,
        )
        return 1

    rows = [
        r
        for r in rows
        if (platform_filter is None or (r.get("platform") or "") == platform_filter)
        and (niche_filter is None or has_niche and (r.get("niche") or "") == niche_filter)
        and _in_window(r.get("publish_ts"), days)
    ]
    if not rows:
        print("[ERR] No clip records match the given filters.", file=sys.stderr)
        return 1

    groups: dict[tuple[str | None, str | None], list[dict]] = {}
    for row in rows:
        key = (row.get("platform"), row.get("niche") if has_niche else None)
        groups.setdefault(key, []).append(row)

    report_rows = [_aggregate_group(group, cols) for group in groups.values()]
    report_rows.sort(
        key=lambda r: (
            str(r["platform"] or ""),
            str(r["niche"] or ""),
            -_GRADE_ORDER[r["retention_grade"]],
        )
    )

    payload: dict[str, Any] = {
        "command": "retention-report",
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics_path": str(db_path),
        "window_days": days,
        "filters": {"platform": platform_filter, "niche": niche_filter},
        "schema": {
            "retention_score": "publish_rate * engagement",
            "engagement": (
                "median(likes + comments) / median(views) over rows with views > 0; "
                "falls back to publish_rate only when no row has views > 0"
            ),
            "grade_thresholds": {
                "A": "retention_score >= 0.75",
                "B": "0.50 <= retention_score < 0.75",
                "C": "0.25 <= retention_score < 0.50",
                "D": "retention_score < 0.25",
            },
            "missing_data": (
                "columns absent from the metrics schema are reported as "
                "'not_available' instead of failing the run"
            ),
            "lookback": (
                "rows are included when publish_ts is null/unparseable or falls "
                "within the last N days"
            ),
        },
        "rows": report_rows,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")

    best = report_rows[0]["retention_grade"]
    best_context = report_rows[0]["platform"] or "unknown-platform"
    print(f"[OK] retention report written for {len(report_rows)} (platform, niche) row(s).")
    print(f"  JSON: {out_path}")
    print(f"  MD:   {md_path}")
    print(f"  Best observed grade: {best} ({best_context})")
    return 0