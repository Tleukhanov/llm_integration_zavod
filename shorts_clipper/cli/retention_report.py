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
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shorts_clipper.core.settings import Settings

# The deterministic grade aggregation lives in
# ``shorts_clipper.editorial.retention_amplify`` so the retention-report CLI and
# the retention-driven publish amplification gate share the exact same
# computation; this module re-exports the helpers to keep its public surface
# (and the ``retention-report`` CLI behavior/args) identical.
from shorts_clipper.editorial.retention_amplify import (
    _BASE_COLUMNS,
    _GRADE_ORDER,
    _OPTIONAL_COLUMNS,
    _aggregate_group,
    _in_window,
    _table_columns,
)

_DEFAULT_OUT_JSON = Path("outputs") / "retention_report.json"


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