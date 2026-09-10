"""Autopilot factory — discover fresh VODs, clip them, optionally publish.

Runs continuously (or once) in a simple loop with graceful Ctrl+C handling.

Usage::

    python scripts/factory.py --count 3 --max-videos 2 --publish
    python scripts/factory.py --loop-interval 60 --gameplay --bgm always
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Autopilot factory: discover VODs, clip them, optionally publish.",
    )
    p.add_argument("--query", default="cs2 gameplay",
                   help="Search query for VOD discovery (default: 'cs2 gameplay').")
    p.add_argument("--providers", default="youtube",
                   help="Comma-separated provider names (default: 'youtube').")
    p.add_argument("--count", type=int, default=3, dest="count",
                   help="Number of clips per video (default: 3).")
    p.add_argument("--max-videos", type=int, default=3, dest="max_videos",
                   help="Max VODs to discover per round (default: 3). 0 = no-op.")
    p.add_argument("--loop-interval", type=float, default=0, dest="loop_interval",
                   metavar="MINUTES",
                   help="Minutes between rounds (default: 0 = run once and exit). "
                        ">0 = infinite loop.")
    p.add_argument("--publish", action="store_true",
                   help="Publish each clip with public visibility after rendering.")
    p.add_argument("--gameplay", action="store_true",
                   help="Enable hype/gameplay mode (peak-energy clip selection).")
    p.add_argument("--bgm", choices=["off", "always", "hybrid"], default=None,
                   help="Background music mode.")
    p.add_argument("--clutch", choices=["energy", "emotion"], default=None,
                   help="Gameplay clutch window mode.")
    p.add_argument("--clips-seconds", type=float, dest="clip_seconds", default=None,
                   help="Target clip length in seconds.")
    p.add_argument("--aspect", choices=["vertical", "wide", "both"], default=None,
                   help="Output aspect ratio.")
    return p


def _run_round(
    settings: object,
    *,
    query: str,
    providers: tuple[str, ...],
    count: int,
    max_videos: int,
    publish: bool,
) -> tuple[int, int, int]:
    """Run one discovery + clip round. Returns (discovered, clipped, errors)."""
    from shorts_clipper.scout.auto_batch import auto_discover
    from shorts_clipper.pipeline.runner import run

    discovered = auto_discover(settings, query=query, providers=providers, max_results=max_videos)
    n_discovered = len(discovered)

    if not discovered:
        return 0, 0, 0

    privacy = "public" if publish else "private"
    n_clipped = 0
    n_errors = 0

    for idx, video in enumerate(discovered, 1):
        url = video.get("url", "")
        title = video.get("title") or video.get("video_id") or url
        print(f"  [{idx}/{n_discovered}] {title}")
        print(f"        {url}")
        try:
            outputs = run(url, settings=settings, count=count, upload=publish, privacy=privacy)
            out_list = outputs if isinstance(outputs, list) else [outputs]
            print(f"        -> {len(out_list)} clip(s) generated")
            n_clipped += 1
        except Exception as exc:
            print(f"        FAILED: {exc}")
            n_errors += 1

    return n_discovered, n_clipped, n_errors


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    from dataclasses import replace

    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()

    overrides: dict = {}
    if args.gameplay:
        overrides["gameplay_mode"] = True
    if args.clutch:
        overrides["gameplay_clutch_mode"] = args.clutch
    if args.clip_seconds is not None:
        overrides["gameplay_clip_seconds"] = float(args.clip_seconds)
    if args.aspect:
        overrides["output_aspect"] = args.aspect
    if args.bgm:
        overrides["bgm_mode"] = args.bgm
    if overrides:
        settings = replace(settings, **overrides)

    providers = tuple(p.strip() for p in args.providers.split(",") if p.strip())

    if args.max_videos <= 0:
        print("No videos to process (--max-videos 0).")
        return 0

    round_num = 0
    try:
        while True:
            round_num += 1
            print(f"\n=== Round {round_num} ===")
            n_discovered, n_clipped, n_errors = _run_round(
                settings,
                query=args.query,
                providers=providers,
                count=args.count,
                max_videos=args.max_videos,
                publish=args.publish,
            )
            print(
                f"Round {round_num}: discovered {n_discovered} videos, "
                f"clipped {n_clipped}, errors {n_errors}"
            )

            if args.loop_interval <= 0:
                break

            print(f"Sleeping {args.loop_interval:.1f} minutes until next round...")
            time.sleep(args.loop_interval * 60)

    except KeyboardInterrupt:
        print("\nInterrupted — shutting down gracefully.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
