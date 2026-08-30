"""CLI entry point for shorts-clipper.

Usage:
    python -m shorts_clipper clip <url> [options]
    python -m shorts_clipper autopilot [options]
    python -m shorts_clipper scout

Examples:
    python -m shorts_clipper clip https://youtu.be/xyz --output ./clips/
    python -m shorts_clipper autopilot --log-level DEBUG
    python -m shorts_clipper scout --count 3
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from shorts_clipper.core.logging import configure_logging
from shorts_clipper.core.settings import Settings


def _cmd_clip(args: argparse.Namespace, settings: Settings) -> int:
    from shorts_clipper.pipeline.runner import run

    out = Path(args.output) if args.output else None
    count = getattr(args, "count", 1)
    upload = getattr(args, "upload", False)

    source_title = None
    source_channel = None
    try:
        import subprocess

        from shorts_clipper.downloader.yt_dlp import get_base_yt_dlp_cmd

        cmd = get_base_yt_dlp_cmd()
        cmd.extend(["--skip-download", "--print", "%(title)s\n%(uploader)s", "--", args.url])
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        lines = res.stdout.strip().split("\n")
        source_title = lines[0] if len(lines) > 0 else "YouTube Video"
        source_channel = lines[1] if len(lines) > 1 else ""
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to fetch video details via yt-dlp: %s. Using placeholders.", e
        )
        source_title = "Unknown Video"
        source_channel = "Unknown Channel"

    try:
        path_or_paths = run(
            args.url,
            settings=settings,
            output_path=out,
            count=count,
            upload=upload,
            source_title=source_title,
            source_channel=source_channel,
        )
        if isinstance(path_or_paths, list):
            print("\n🔥 Clips ready:")
            for p in path_or_paths:
                print(f"  - {p}")
        else:
            print(f"\n🔥 Clip ready: {path_or_paths}")
        return 0
    except Exception as exc:
        logging.getLogger(__name__).error("Pipeline failed: %s", exc)
        return 1


def _cmd_autopilot(args: argparse.Namespace, settings: Settings) -> int:
    from shorts_clipper.pipeline.runner import run_autopilot

    count = getattr(args, "count", 1)
    upload = getattr(args, "upload", False)
    path_or_paths = run_autopilot(
        settings=settings,
        channel=getattr(args, "channel", None),
        niche=getattr(args, "niche", None),
        keyword=getattr(args, "keyword", None),
        count=count,
        upload=upload,
    )
    if path_or_paths:
        if isinstance(path_or_paths, list):
            print("\n🔥 Clips ready:")
            for p in path_or_paths:
                print(f"  - {p}")
        else:
            print(f"\n🔥 Clip ready: {path_or_paths}")
        return 0
    print("❌ Autopilot could not find a suitable video.")
    return 1


def _cmd_scout(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    from shorts_clipper.scout.trending import get_trending_link

    count = getattr(args, "count", 1)
    found = 0
    while found < count:
        url = get_trending_link(
            channel=getattr(args, "channel", None),
            niche=getattr(args, "niche", None),
            keyword=getattr(args, "keyword", None),
            max_age_days=settings.scout_max_age_days,
        )
        if url:
            print(url)
            found += 1
        else:
            print("❌ No suitable video found.", file=sys.stderr)
            break
    return 0 if found == count else 1


def _cmd_web(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    from shorts_clipper.api.server import app

    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)

    print(f"\n🚀 Launching Vanguard Clipper Web Console on http://{host}:{port}...")
    try:
        uvicorn.run(app, host=host, port=port)
        return 0
    except Exception as exc:
        print(f"❌ Failed to run Vanguard server: {exc}")
        return 1


def _cmd_repair_metadata(args: argparse.Namespace, settings: Settings) -> int:
    from shorts_clipper.cli.repair_metadata import run_repair

    return run_repair()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m shorts_clipper",
        description="AI-powered viral shorts clipping pipeline.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--env",
        default=".env",
        metavar="FILE",
        help="Path to .env file (default: .env)",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ── clip ──────────────────────────────────────────────────────────────
    clip_p = sub.add_parser("clip", help="Clip a specific YouTube video.")
    clip_p.add_argument("url", help="YouTube video URL.")
    clip_p.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="Output file path (default: outputs/clip_TIMESTAMP.mp4)",
    )
    clip_p.add_argument(
        "-c",
        "--count",
        type=int,
        default=1,
        help="Number of viral clips to extract (default: 1)",
    )
    clip_p.add_argument(
        "--upload",
        action="store_true",
        help="Upload the resulting clips to YouTube Shorts",
    )

    # ── autopilot ─────────────────────────────────────────────────────────
    autopilot_p = sub.add_parser(
        "autopilot",
        help="Scout a trending video and clip it automatically.",
    )
    autopilot_p.add_argument(
        "--channel",
        help="Search only this channel's recent videos.",
    )
    autopilot_p.add_argument(
        "--niche",
        help="Build 5 targeted search queries around this niche and rotate between them.",
    )
    autopilot_p.add_argument(
        "--keyword",
        help="Search specifically for this term across multiple platforms.",
    )
    autopilot_p.add_argument(
        "-c",
        "--count",
        type=int,
        default=1,
        help="Number of viral clips to extract (default: 1)",
    )
    autopilot_p.add_argument(
        "--upload",
        action="store_true",
        help="Upload the resulting clips to YouTube Shorts",
    )

    # ── scout ─────────────────────────────────────────────────────────────
    scout_p = sub.add_parser("scout", help="Print trending video URLs and exit.")
    scout_p.add_argument(
        "-n",
        "--count",
        type=int,
        default=1,
        help="Number of URLs to find (default: 1)",
    )
    scout_p.add_argument(
        "--channel",
        help="Search only this channel's recent videos.",
    )
    scout_p.add_argument(
        "--niche",
        help="Build 5 targeted search queries around this niche and rotate between them.",
    )
    scout_p.add_argument(
        "--keyword",
        help="Search specifically for this term across multiple platforms.",
    )

    # ── web ───────────────────────────────────────────────────────────────
    web_p = sub.add_parser("web", help="Start the Vanguard Web Console Dashboard.")
    web_p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind to (default: 127.0.0.1)",
    )
    web_p.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )

    # ── repair-metadata ───────────────────────────────────────────────────────────────
    sub.add_parser("repair-metadata", help="Repair clips missing metadata.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    settings = Settings.from_env(env_path=args.env)

    dispatch = {
        "clip": _cmd_clip,
        "autopilot": _cmd_autopilot,
        "scout": _cmd_scout,
        "web": _cmd_web,
        "repair-metadata": _cmd_repair_metadata,
    }
    return dispatch[args.command](args, settings)


if __name__ == "__main__":
    sys.exit(main())
