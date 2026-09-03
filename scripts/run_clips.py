#!/usr/bin/env python3
"""CLI entry point for the shorts-clipper pipeline.

Usage::

    python scripts/run_clips.py --url <youtube-url> --count 4
    python scripts/run_clips.py --source "cs2 gameplay" --count 4 \
        --picker cc  --channel "@SomeCS2Channel"

Config is read from the repo ``.env`` (Settings.from_env), so flags here are
thin conveniences on top of the normal environment variables.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cut CS2 gameplay Shorts from a video / CC-licensed VOD."
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--url", help="Explicit YouTube video URL to clip.")
    src.add_argument(
        "--source",
        help=(
            "Search text for CC-licensed gameplay (e.g. 'cs2 gameplay'). "
            "Overrides --url with the top Creative-Commons result."
        ),
    )
    p.add_argument("--count", "--clips", type=int, default=1, dest="count",
                   help="Number of clips/moments to extract (default 1).")
    p.add_argument("--channel", default=None,
                   help="Restrict --source search to this channel uploads URL.")
    p.add_argument("--dateafter", default=None,
                   help="Only search results newer than YYYYMMDD (with --source).")
    p.add_argument("--upload", action="store_true",
                   help="Publish after rendering (default: private).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    from shorts_clipper.core.settings import Settings
    from shorts_clipper.pipeline.runner import run

    settings = Settings.from_env()

    url = args.url
    if url is None and args.source:
        from shorts_clipper.downloader.yt_dlp import search_cc_videos

        results = search_cc_videos(
            args.source,
            max_results=1,
            channel_url=args.channel,
            dateafter=args.dateafter,
        )
        if not results:
            print("No Creative-Commons gameplay found for source:", args.source)
            return 2
        url = results[0]["url"]
        print(f"Picking CC video: {url}")
        print(f"  -> {results[0].get('title')}")

    if url is None:
        print("Provide --url or --source.")
        return 2

    outputs = run(
        url,
        settings=settings,
        count=args.count,
        upload=args.upload,
    )

    out_list = outputs if isinstance(outputs, list) else [outputs]
    print(f"\nSUCCESS: {len(out_list)} clip(s) ready:")
    for o in out_list:
        print(f"  - {o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
