"""Human-readable report over the clip metrics store.

Prints store totals, per-channel stats, top hooks/partners, recent clips,
and the next unpublished clips awaiting stats.

Usage::

    python scripts/report.py
    python scripts/report.py --db data/metrics.sqlite
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _fmt(value, default: str = "") -> str:
    """Stringify a possibly-None value, falling back to *default*."""
    return default if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    from shorts_clipper.core.metrics import MetricsStore
    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()
    p = argparse.ArgumentParser(description="Report clip metrics.")
    p.add_argument("--db", default=str(settings.metrics_path),
                   help="Metrics sqlite path (default from SHORTS_METRICS_PATH).")
    args = p.parse_args(argv)

    store = MetricsStore(Path(args.db))
    try:
        summary = store.summary()
        print("=== Store summary ===")
        print(f"  produced:   {summary['produced']}")
        print(f"  published:  {summary['published']}")
        print(f"  with_stats: {summary['with_stats']}")
        print(f"  avg_views:  {summary['avg_views']}")
        print(f"  best clip:  {summary['best_video_id']} "
              f"({_fmt(summary['best_title'])} / {summary['best_views']} views)")

        print("\n=== Per-channel ===")
        print(f"  {'channel':<24} {'produced':>8} {'published':>9} {'avg_views':>10}")
        for channel in store.channels():
            row = store.stats_channel(channel)
            print(f"  {channel[:24]:<24} {row['produced']:>8} "
                  f"{row['published']:>9} {row['avg_views']:>10}")

        print("\n=== Top hooks (by avg views) ===")
        print(f"  {'hook':<70} {'clips':>5} {'avg_views':>10}")
        for row in store.top_hooks():
            print(f"  {(_fmt(row['hook']))[:70]:<70} {row['count']:>5} "
                  f"{_fmt(row['avg_views']):>10}")

        print("\n=== Top partners (by avg views) ===")
        print(f"  {'partner':<30} {'clips':>5} {'avg_views':>10}")
        for row in store.top_partners():
            print(f"  {(_fmt(row['affiliate_id']))[:30]:<30} {row['count']:>5} "
                  f"{_fmt(row['avg_views']):>10}")

        print("\n=== Last 8 clips ===")
        print(f"  {'video_id':<14} {'channel':<16} {'title':<40} "
              f"{'published':>5} {'views':>8}")
        for row in store.recent(8):
            print(f"  {(_fmt(row['video_id']))[:14]:<14} "
                  f"{(_fmt(row.get('channel')))[:16]:<16} "
                  f"{(_fmt(row.get('title')))[:40]:<40} "
                  f"{(row.get('published') or 0):>5} "
                  f"{_fmt(row.get('views')):>8}")

        print("\n=== Next unpublished suggestions (top 5, newest first) ===")
        candidates = store.unpublished(min_age_seconds=3600)
        newest_first = sorted(
            candidates, key=lambda r: (r.get("publish_ts") or ""), reverse=True
        )[:5]
        for row in newest_first:
            print(f"  {row['video_id']}  {_fmt(row.get('publish_ts'))}  "
                  f"{_fmt(row.get('title'))}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())