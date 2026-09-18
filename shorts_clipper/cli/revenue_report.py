"""Affiliate income report ("revenue-report" subcommand).

Aggregates ``affiliate_events`` from the metrics store
(``settings.metrics_path``) into real income numbers: total revenue,
revenue grouped per (platform, niche) and per-partner click/conversion/
revenue counts. Writes a Markdown report to ``outputs/revenue_report.md``
(default) and prints a short table to stdout.

An empty or missing ``affiliate_events`` table is not an error — the command
prints a friendly "no affiliate events yet" notice and exits 0.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shorts_clipper.core.settings import Settings

_DEFAULT_OUT_MD = Path("outputs") / "revenue_report.md"
_REVENUE_TYPES = {"click", "conversion", "revenue"}


def _round_money(value: float | None) -> float:
    return round(value or 0.0, 2)


def _fmt_money(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "0.00"


def _fmt_count(value: Any) -> str:
    return str(value or 0)


def run_revenue_report(settings: Settings, args: argparse.Namespace) -> int:
    """Read the metrics DB ``affiliate_events`` and write the Markdown report."""
    db_path = Path(settings.metrics_path)
    niche_filter = getattr(args, "niche", None)
    partner_filter = getattr(args, "partner", None)
    out_path = Path(getattr(args, "out", None)) if getattr(args, "out", None) else _DEFAULT_OUT_MD

    def _no_events(reason: str) -> int:
        print(f"[OK] No affiliate events yet — {reason}. Nothing to report.")
        print(f"  Metrics DB: {db_path}")
        return 0

    if not db_path.exists():
        return _no_events("metrics database does not exist yet")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "affiliate_events" not in tables:
            return _no_events("`affiliate_events` table is missing")

        where: list[str] = []
        params: list[Any] = []
        if niche_filter:
            where.append("niche=?")
            params.append(niche_filter)
        if partner_filter:
            where.append("partner_id=?")
            params.append(partner_filter)
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""

        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM affiliate_events{where_sql} ORDER BY id ASC", params
            ).fetchall()
        ]
    finally:
        conn.close()

    if not rows:
        return _no_events("no affiliate events recorded yet")

    # ── Aggregates ─────────────────────────────────────────────────────
    total_revenue = sum(r["amount"] for r in rows if r.get("event_type") == "revenue" and r.get("amount"))
    total_clicks = sum(1 for r in rows if r.get("event_type") == "click")
    total_conversions = sum(1 for r in rows if r.get("event_type") == "conversion")
    total_revenue_events = sum(1 for r in rows if r.get("event_type") == "revenue")

    by_platform_niche: dict[tuple[str | None, str | None], list[dict]] = {}
    for row in rows:
        key = (row.get("platform"), row.get("niche"))
        by_platform_niche.setdefault(key, []).append(row)

    platform_niche_rows: list[dict] = []
    for (platform, niche), group in by_platform_niche.items():
        group_revenue = sum(
            r["amount"] for r in group if r.get("event_type") == "revenue" and r.get("amount")
        )
        platform_niche_rows.append(
            {
                "platform": platform,
                "niche": niche,
                "events": len(group),
                "revenue_events": sum(1 for r in group if r.get("event_type") == "revenue"),
                "clicks": sum(1 for r in group if r.get("event_type") == "click"),
                "conversions": sum(1 for r in group if r.get("event_type") == "conversion"),
                "revenue": _round_money(group_revenue),
            }
        )

    by_partner: dict[str, list[dict]] = {}
    for row in rows:
        by_partner.setdefault(str(row.get("partner_id") or "unknown"), []).append(row)

    partner_rows: list[dict] = []
    for partner_id, group in by_partner.items():
        partner_revenue = sum(
            r["amount"] for r in group if r.get("event_type") == "revenue" and r.get("amount")
        )
        partner_rows.append(
            {
                "partner_id": partner_id,
                "clicks": sum(1 for r in group if r.get("event_type") == "click"),
                "conversions": sum(1 for r in group if r.get("event_type") == "conversion"),
                "revenue_events": sum(1 for r in group if r.get("event_type") == "revenue"),
                "revenue": _round_money(partner_revenue),
            }
        )

    platform_niche_rows.sort(key=lambda r: (str(r["platform"] or ""), str(r["niche"] or "")))
    partner_rows.sort(key=lambda r: (r["revenue"], r["partner_id"]), reverse=True)

    payload: dict[str, Any] = {
        "command": "revenue-report",
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics_path": str(db_path),
        "filters": {"niche": niche_filter, "partner": partner_filter},
        "total_revenue": _round_money(total_revenue),
        "total_revenue_events": total_revenue_events,
        "total_clicks": total_clicks,
        "total_conversions": total_conversions,
        "revenue_by_platform_niche": platform_niche_rows,
        "partners": partner_rows,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_markdown(payload), encoding="utf-8")

    print(
        f"[OK] revenue report written (total: ${_fmt_money(total_revenue)}) "
        f"{len(platform_niche_rows)} (platform, niche) group(s), "
        f"{len(partner_rows)} partner(s)."
    )
    print("  MD:   " + str(out_path))
    print()
    print("Revenue by platform/niche:")
    print("| Platform | Niche | Revenue | Clicks | Conversions |")
    print("|---|---|---:|---:|---:|")
    for r in platform_niche_rows:
        print(
            f"| {r['platform'] or '-'} | {r['niche'] or '-'} | "
            f"${_fmt_money(r['revenue'])} | {_fmt_count(r['clicks'])} | "
            f"{_fmt_count(r['conversions'])} |"
        )
    print()
    print("Per partner:")
    print("| Partner | Clicks | Conversions | Revenue events | Revenue |")
    print("|---|---:|---:|---:|---:|")
    for r in partner_rows:
        print(
            f"| {r['partner_id']} | {_fmt_count(r['clicks'])} | "
            f"{_fmt_count(r['conversions'])} | {_fmt_count(r['revenue_events'])} | "
            f"${_fmt_money(r['revenue'])} |"
        )
    return 0


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Revenue Report", ""]
    lines.append(f"Generated: {payload['generated_at']}")
    lines.append(f"Metrics DB: `{payload['metrics_path']}`")
    filters = payload["filters"]
    lines.append(
        f"Filters: niche={filters['niche'] or 'all'}, "
        f"partner={filters['partner'] or 'all'}"
    )
    lines.append(
        f"Total revenue: **${_fmt_money(payload['total_revenue'])}** "
        f"({payload['total_revenue_events']} revenue event(s), "
        f"{payload['total_clicks']} click(s), {payload['total_conversions']} conversion(s))"
    )
    lines.append("")
    lines.append("## Revenue by platform / niche")
    lines.append("")
    lines.append("| Platform | Niche | Events | Revenue events | Clicks | Conversions | Revenue |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for r in payload["revenue_by_platform_niche"]:
        lines.append(
            f"| {r['platform'] or '-'} | {r['niche'] or '-'} | {_fmt_count(r['events'])} | "
            f"{_fmt_count(r['revenue_events'])} | {_fmt_count(r['clicks'])} | "
            f"{_fmt_count(r['conversions'])} | ${_fmt_money(r['revenue'])} |"
        )
    lines.extend(["", "## Per-partner breakdown", ""])
    lines.append("| Partner | Clicks | Conversions | Revenue events | Revenue |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in payload["partners"]:
        lines.append(
            f"| {r['partner_id']} | {_fmt_count(r['clicks'])} | "
            f"{_fmt_count(r['conversions'])} | {_fmt_count(r['revenue_events'])} | "
            f"${_fmt_money(r['revenue'])} |"
        )
    lines.extend(["", "## Attribution", ""])
    lines.append(
        "Events are recorded via `record_affiliate_event(platform, niche, partner_id, "
        "event_type, amount)` per (platform, niche, partner); `amount` is only "
        "meaningful for `revenue` events."
    )
    lines.append("")
    return "\n".join(lines)