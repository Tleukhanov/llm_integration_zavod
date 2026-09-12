"""Collect view/like/comment stats for produced clips into the metrics DB.

For every clip row still awaiting stats (or stale), queries the source
video via yt-dlp and writes the numbers back with
``MetricsStore.update_stats``.

Usage::

    python scripts/collect_metrics.py
    python scripts/collect_metrics.py --db data/metrics.sqlite --min-age-min 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _build_parser(metrics_path: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect view/like/comment stats for produced clips.",
    )
    p.add_argument("--db", default=metrics_path,
                   help="Metrics sqlite path (default from SHORTS_METRICS_PATH).")
    p.add_argument("--min-age-min", type=int, default=60, dest="min_age_min",
                   help="Minimum age in minutes before collecting stats (default 60).")
    p.add_argument("--limit", type=int, default=50,
                   help="Max records to process per run (default 50).")
    return p


def main(argv: list[str] | None = None) -> int:
    from shorts_clipper.core.metrics import MetricsStore
    from shorts_clipper.core.settings import Settings
    from shorts_clipper.downloader.yt_dlp import fetch_video_stats

    settings = Settings.from_env()
    args = _build_parser(str(settings.metrics_path)).parse_args(argv)

    store = MetricsStore(Path(args.db))
    try:
        rows = store.unpublished(min_age_seconds=args.min_age_min * 60)
        for row in rows[: args.limit]:
            url = row.get("source_url") or ""
            if not url.startswith(("http://", "https://")):
                continue
            stats = fetch_video_stats(url)
            if stats is None:
                continue
            store.update_stats(
                row["video_id"],
                views=stats.get("views"),
                likes=stats.get("likes"),
                comments=stats.get("comments"),
            )
            print(
                f"{row['video_id']} {stats.get('views')} "
                f"{stats.get('likes')} {stats.get('comments')}"
            )
        summary = store.summary()
        print(
            f"summary: produced={summary['produced']} "
            f"published={summary['published']} "
            f"with_stats={summary['with_stats']} "
            f"avg_views={summary['avg_views']} "
            f"best={summary['best_video_id']} ({summary['best_views']} views)"
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())