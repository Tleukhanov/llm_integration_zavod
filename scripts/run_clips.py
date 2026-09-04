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
    src.add_argument("--batch", metavar="FILE",
                     help="Text file with one URL or source-query per line.")
    p.add_argument("--list", action="store_true", dest="list_items",
                   help="Dry-run with --batch: print items without running the pipeline.")
    p.add_argument("--count", "--clips", type=int, default=1, dest="count",
                   help="Number of clips/moments to extract (default 1).")
    p.add_argument("--channel", default=None,
                   help="Restrict --source search to this channel uploads URL.")
    p.add_argument("--dateafter", default=None,
                   help="Only search results newer than YYYYMMDD (with --source).")
    p.add_argument("--upload", action="store_true",
                   help="Publish after rendering (default: private).")
    p.add_argument("--gameplay", action="store_true",
                   help="Enable hype/gameplay mode (peak-energy clip selection + music-forward BGM). Matches SHORTS_GAMEPLAY_MODE.")
    p.add_argument("--clutch", choices=["energy", "emotion"], default=None,
                   help="Gameplay clutch window mode (SHORTS_GAMEPLAY_CLUTCH).")
    p.add_argument("--clips-seconds", type=float, dest="clip_seconds",
                   default=None,
                   help="Target clip length in seconds (SHORTS_GAMEPLAY_CLIP_SECONDS, default 30).")
    p.add_argument("--aspect", choices=["vertical", "wide", "both"],
                   default=None, help="Output aspect (default from .env).")
    p.add_argument("--bgm", choices=["off", "always", "hybrid"],
                   default=None,
                   help="Background music mode (SHORTS_BGM_MODE). Default off; "
                        "'always' adds phonk BGM to every clip.")
    return p


def _resolve_url(line: str, channel: str | None, dateafter: str | None) -> str | None:
    """Turn a batch line into a concrete URL (or None on failure)."""
    if line.startswith("http"):
        return line
    from shorts_clipper.downloader.yt_dlp import search_cc_videos

    results = search_cc_videos(
        line,
        max_results=1,
        channel_url=channel,
        dateafter=dateafter,
    )
    if not results:
        print(f"  No Creative-Commons video found for source: {line}")
        return None
    url = results[0]["url"]
    print(f"  Picking CC video: {url}")
    print(f"    -> {results[0].get('title')}")
    return url


def _parse_batch_file(path: Path) -> list[str]:
    """Return non-blank, non-comment lines from *path*."""
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


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
    if args.clip_seconds:
        overrides["gameplay_clip_seconds"] = float(args.clip_seconds)
    if args.aspect:
        overrides["output_aspect"] = args.aspect
    if args.bgm:
        overrides["bgm_mode"] = args.bgm
    if overrides:
        settings = replace(settings, **overrides)

    if args.list_items and args.batch is None:
        print("--list requires --batch.")
        return 2

    # ── batch mode ──────────────────────────────────────────────────────
    if args.batch is not None:
        batch_path = Path(args.batch)
        if not batch_path.is_file():
            print(f"Batch file not found: {batch_path}")
            return 2

        items = _parse_batch_file(batch_path)
        if not items:
            print("Batch file is empty (no items to process).")
            return 2

        if args.list_items:
            for i, item in enumerate(items, 1):
                kind = "url" if item.startswith("http") else "source"
                print(f"  {i:>3}. [{kind}] {item}")
            print(f"\n{len(items)} item(s) would be processed.")
            return 0

        from shorts_clipper.pipeline.runner import run

        total = len(items)
        ok = 0
        fail = 0
        for idx, item in enumerate(items, 1):
            print(f"\n[{idx}/{total}] {item}")
            url = _resolve_url(item, args.channel, args.dateafter)
            if url is None:
                fail += 1
                continue
            try:
                outputs = run(
                    url,
                    settings=settings,
                    count=args.count,
                    upload=args.upload,
                )
                out_list = outputs if isinstance(outputs, list) else [outputs]
                print(f"  SUCCESS: {len(out_list)} clip(s) ready:")
                for o in out_list:
                    print(f"    - {o}")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED: {exc}")
                fail += 1

        print(f"\n{'=' * 50}")
        print(f"Batch complete: {ok} succeeded, {fail} failed out of {total}.")
        return 0 if ok else 1

    # ── single-video mode (original) ───────────────────────────────────
    from shorts_clipper.pipeline.runner import run

    url = args.url
    if url is None and args.source:
        url = _resolve_url(args.source, args.channel, args.dateafter)
        if url is None:
            return 2

    if url is None:
        print("Provide --url, --source, or --batch.")
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
