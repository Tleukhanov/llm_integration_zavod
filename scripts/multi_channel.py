"""Multi-channel sequential runner.

Runs one factory round per channel, iterating over a comma-separated list.

Usage::

    python scripts/multi_channel.py --channels alpha,bravo --count 3 --gameplay --bgm always
    python scripts/multi_channel.py --channels alpha --max-videos 0   # dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run one factory round per channel (sequential).",
    )
    p.add_argument("--channels", required=True,
                   help="Comma-separated channel profile names (e.g. 'alpha,bravo').")
    p.add_argument("--query", default="cs2 gameplay",
                   help="Search query for VOD discovery (default: 'cs2 gameplay').")
    p.add_argument("--providers", default="youtube",
                   help="Comma-separated provider names (default: 'youtube').")
    p.add_argument("--count", type=int, default=3, dest="count",
                   help="Number of clips per video (default: 3).")
    p.add_argument("--max-videos", type=int, default=3, dest="max_videos",
                   help="Max VODs to discover per channel (default: 3). 0 = dry-run.")
    p.add_argument("--publish", action="store_true",
                   help="Publish each clip with public visibility after rendering.")
    p.add_argument("--gameplay", action="store_true",
                   help="Enable hype/gameplay mode.")
    p.add_argument("--bgm", choices=["off", "always", "hybrid"], default=None,
                   help="Background music mode.")
    p.add_argument("--clutch", choices=["energy", "emotion"], default=None,
                   help="Gameplay clutch window mode.")
    p.add_argument("--clips-seconds", type=float, dest="clip_seconds", default=None,
                   help="Target clip length in seconds.")
    p.add_argument("--aspect", choices=["vertical", "wide", "both"], default=None,
                   help="Output aspect ratio.")
    p.add_argument("--title-variants", choices=["auto", "fixed"], default="auto",
                   dest="title_variants",
                   help="Title A/B strategy across channels (default: 'auto'). "
                        "'auto' sets SHORTS_TITLE_VARIANT to the channel index "
                        "(channel 0 -> variant 0, channel 1 -> variant 1, ...). "
                        "'fixed' pins every channel to variant 1 unless "
                        "--title-variant N is given.")
    p.add_argument("--title-variant", type=int, default=-1, dest="title_variant",
                   help="0-based title candidate index used with "
                        "--title-variants fixed (default: -1 -> variant 1).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    from dataclasses import replace

    from shorts_clipper.core.settings import Settings
    from shorts_clipper.publishers.youtube.auth import get_youtube_service

    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    if not channels:
        print("No channels provided.")
        return 2

    providers = tuple(p.strip() for p in args.providers.split(",") if p.strip())

    summary: dict[str, dict[str, int]] = {}
    overall_ok = 0
    overall_err = 0

    for ch_idx, ch in enumerate(channels):
        print(f"\n{'=' * 60}")
        print(f"  CHANNEL: {ch}")
        print(f"{'=' * 60}")

        os.environ["SHORTS_CHANNEL"] = ch

        if args.title_variants == "auto":
            os.environ["SHORTS_TITLE_VARIANT"] = str(ch_idx)
        elif args.title_variant >= 0:
            os.environ["SHORTS_TITLE_VARIANT"] = str(args.title_variant)
        else:
            os.environ["SHORTS_TITLE_VARIANT"] = "1"

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

        try:
            from shorts_clipper.scout.auto_batch import auto_discover
            from shorts_clipper.pipeline.runner import run

            discovered = auto_discover(
                settings, query=args.query, providers=providers, max_results=args.max_videos,
            )
            n_discovered = len(discovered)
            n_clipped = 0
            n_errors = 0

            if discovered:
                privacy = "public" if args.publish else "private"
                for idx, video in enumerate(discovered, 1):
                    url = video.get("url", "")
                    title = video.get("title") or video.get("video_id") or url
                    print(f"  [{idx}/{n_discovered}] {title}")
                    print(f"        {url}")
                    try:
                        outputs = run(url, settings=settings, count=args.count, upload=args.publish, privacy=privacy)
                        out_list = outputs if isinstance(outputs, list) else [outputs]
                        print(f"        -> {len(out_list)} clip(s) generated")
                        n_clipped += 1
                    except Exception as exc:
                        print(f"        FAILED: {exc}")
                        n_errors += 1

            summary[ch] = {"discovered": n_discovered, "clipped": n_clipped, "errors": n_errors}
            overall_ok += n_clipped
            overall_err += n_errors

            print(
                f"Channel {ch}: discovered {n_discovered} videos, "
                f"clipped {n_clipped}, errors {n_errors}"
            )
        except Exception as exc:
            print(f"Channel {ch} FAILED: {exc}")
            summary[ch] = {"discovered": 0, "clipped": 0, "errors": 1}
            overall_err += 1

    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    for ch, s in summary.items():
        print(f"  {ch}: discovered={s['discovered']} clipped={s['clipped']} errors={s['errors']}")
    print(f"  TOTAL: clipped={overall_ok} errors={overall_err}")

    return 0 if overall_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
