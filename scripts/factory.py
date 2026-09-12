"""Autopilot factory — discover fresh VODs, clip them, optionally publish.

Runs continuously (or once) in a simple loop with graceful Ctrl+C handling.
Records every successfully-produced clip in the metrics sqlite store and can
optionally schedule rounds and enforce a per-channel daily publish cap.

Usage::

    python scripts/factory.py --count 3 --max-videos 2 --publish
    python scripts/factory.py --loop-interval 60 --gameplay --bgm always
    python scripts/factory.py --schedule-interval-min 90 --daily-cap 6
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
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
                        ">0 = infinite loop. Ignored when --schedule-interval-min is set.")
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
    p.add_argument("--channel", default=None,
                   help="Channel profile name (sets SHORTS_CHANNEL for multi-channel env overlay).")
    p.add_argument("--schedule-interval-min", type=int, default=0, dest="schedule_interval_min",
                   metavar="MINUTES",
                   help="Minutes between scheduled factory rounds (default: 0 = run once "
                        "and exit). >0 = infinite loop; overrides --loop-interval.")
    p.add_argument("--daily-cap", type=int, default=None, dest="daily_cap",
                   help="Max clips to publish per channel per day "
                        "(default: SHORTS_FACTORY_DAILY_CAP env or 6).")
    return p


def _clip_video_id(url: str, video: dict) -> str:
    """Stable identifier for the metrics store: video_id, else url hash."""
    from shorts_clipper.core.processed_store import extract_video_id

    _id = video.get("video_id") or extract_video_id(url)
    if _id and _id != url:
        return str(_id)
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _sidecar_title(output_path) -> str:
    """Read the clip title from its sidecar .json, best-effort."""
    try:
        sidecar = Path(output_path).with_suffix(".json")
        if sidecar.is_file():
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            return str(meta.get("title") or "").strip()
    except Exception:
        pass
    return ""


def _record_produced_clip(
    settings: object,
    store: object,
    *,
    url: str,
    video: dict,
    output_path,
    channel: str,
    published: bool,
) -> None:
    """Persist one produced clip row in the metrics store (idempotent)."""
    from shorts_clipper.core.metrics import ClipRecord

    hook = settings.hook_banner_text[:80] if settings.hook_banner_enabled else None
    rec = ClipRecord(
        video_id=_clip_video_id(url, video),
        source_url=url,
        title=_sidecar_title(output_path) or video.get("title") or "",
        hook=hook,
        affiliate_id=os.environ.get("AFFILIATE_PARTNER_ID") or "",
        channel=channel,
        published=published,
        publish_ts=datetime.now(UTC).isoformat(),
        rendered_path=str(output_path),
    )
    store.record_clip(rec)


def _run_round(
    settings: object,
    *,
    query: str,
    providers: tuple[str, ...],
    count: int,
    max_videos: int,
    publish: bool,
    store: object,
    channel: str,
    daily_cap: int,
) -> tuple[int, int, int]:
    """Run one discovery + clip round. Returns (discovered, clipped, errors)."""
    from shorts_clipper.core.metrics import should_publish_today
    from shorts_clipper.pipeline.runner import run
    from shorts_clipper.scout.auto_batch import auto_discover

    discovered = auto_discover(settings, query=query, providers=providers, max_results=max_videos)
    n_discovered = len(discovered)

    if not discovered:
        return 0, 0, 0

    n_clipped = 0
    n_errors = 0

    for idx, video in enumerate(discovered, 1):
        url = video.get("url", "")
        title = video.get("title") or video.get("video_id") or url

        can_publish = publish
        if publish and not should_publish_today(store, channel, daily_cap):
            print("DAILY CAP REACHED — skipping publish for this video "
                  "(recording as unpublished)")
            can_publish = False

        print(f"  [{idx}/{n_discovered}] {title}")
        print(f"        {url}")
        try:
            outputs = run(
                url,
                settings=settings,
                count=count,
                upload=can_publish,
                privacy="public" if can_publish else "private",
            )
            out_list = outputs if isinstance(outputs, list) else [outputs]
            print(f"        -> {len(out_list)} clip(s) generated")
            n_clipped += 1
            for out in out_list:
                _record_produced_clip(
                    settings,
                    store,
                    url=url,
                    video=video,
                    output_path=out,
                    channel=channel,
                    published=can_publish,
                )
        except Exception as exc:
            print(f"        FAILED: {exc}")
            n_errors += 1

    return n_discovered, n_clipped, n_errors


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.channel:
        os.environ["SHORTS_CHANNEL"] = args.channel

    from dataclasses import replace

    from shorts_clipper.core.metrics import MetricsStore
    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()

    daily_cap = args.daily_cap if args.daily_cap is not None else settings.factory_daily_cap
    channel = os.environ.get("SHORTS_CHANNEL") or settings.channel_name or ""

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

    store = MetricsStore(settings.metrics_path)

    # Scheduling: an explicit --schedule-interval-min wins over the older
    # --loop-interval; when both are 0 the factory runs exactly once.
    interval_min = args.schedule_interval_min if args.schedule_interval_min > 0 else args.loop_interval

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
                store=store,
                channel=channel,
                daily_cap=daily_cap,
            )
            print(
                f"Round {round_num}: discovered {n_discovered} videos, "
                f"clipped {n_clipped}, errors {n_errors}"
            )

            if interval_min <= 0:
                break

            print(f"Sleeping {interval_min:.1f} minutes until next round...")
            time.sleep(interval_min * 60)

    except KeyboardInterrupt:
        print("\nInterrupted — shutting down gracefully.")
        return 0
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())