"""Auto-discover fresh, unprocessed VODs for batch clipping."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from shorts_clipper.core.processed_store import ProcessedStore
from shorts_clipper.scout.source_scout import SourceVideo, scout
from shorts_clipper.scout.trends import rank_vods, score_vod

log = logging.getLogger(__name__)

_CHANNEL_PROVIDER = "youtube_channels"


def _load_performance_map(metrics_db: str | Path | None, settings: Any) -> dict | None:
    """Load ``{channel: avg_views}`` from the metrics store, best-effort.

    Uses *metrics_db* when given, otherwise falls back to ``settings.metrics_path``.
    Returns ``None`` (the pre-feedback behaviour) when no path is configured,
    the file is missing, or reading it fails, so the pipeline degrades
    gracefully instead of crashing discovery.
    """
    db_path: str | Path | None = metrics_db
    if db_path is None:
        db_path = getattr(settings, "metrics_path", None)
    if not db_path:
        return None
    path = Path(db_path)
    if not path.exists():
        return None
    try:
        from shorts_clipper.core.metrics import MetricsStore

        store = MetricsStore(path)
        try:
            rows = store.channel_performance()
        finally:
            store.close()
    except Exception:
        log.warning("Could not load channel performance map from %s", path, exc_info=True)
        return None
    return {row["channel"]: row["avg_views"] for row in rows if row["avg_views"]}


def auto_discover(
    settings: Any,
    query: str = "cs2 gameplay",
    providers: tuple[str, ...] = ("youtube",),
    max_results: int = 10,
    channels: tuple[str, ...] | None = None,
    min_recent_days: int | None = None,
    ranking: bool = True,
    metrics_db: str | Path | None = None,
) -> list[dict]:
    """Find VODs not yet in the processed store and return them as dicts.

    Args:
        settings: Application settings object exposing ``processed_videos_path``
            (and optionally ``metrics_path``).
        query: Free-text search term forwarded to each provider.
        providers: Tuple of registered provider names to invoke.
        max_results: Maximum number of fresh results to return.
        channels: Channel videos-page URIs; overrides ``config/vod_sources.json``
            for the ``"youtube_channels"`` provider.
        min_recent_days: When *ranking* and set, drop VODs older than this many
            days (and any VOD without a parseable upload date).
        ranking: Sort results with ``rank_vods`` (trend score desc) instead of
            raw discovery order; adds ``score`` and ``source`` keys to each dict.
        metrics_db: Optional path to the clip metrics sqlite. When provided
            (or when ``settings.metrics_path`` points at an existing file) the
            channel performance feedback map is loaded once and threaded into
            ranking, so proven source channels get a score bonus. A missing or
            unreadable DB is ignored gracefully.

    Returns:
        List of dicts with keys ``url``, ``video_id``, ``title``, ``platform``,
        plus ``score`` (rounded 1 dp) and ``source`` (``"channel"`` / ``"search"``)
        when *ranking* is on. Returns ``[]`` on any error so the pipeline is
        never interrupted.
    """
    try:
        store = ProcessedStore.from_path(settings.processed_videos_path)
        processed_ids = store.all_ids()

        candidates: list[tuple[str, SourceVideo]] = []
        seen: set[str] = set(processed_ids)
        for name in providers:
            remaining = max_results - len(candidates)
            if remaining <= 0:
                break
            if name == _CHANNEL_PROVIDER:
                hits = scout(
                    query,
                    providers=(name,),
                    limit=remaining,
                    exclude_ids=seen,
                    channel_urls=list(channels) if channels else None,
                )
                source_kind = "channel"
            else:
                hits = scout(query, providers=(name,), limit=remaining, exclude_ids=seen)
                source_kind = "search"
            for video in hits:
                if video.video_id in seen:
                    continue
                seen.add(video.video_id)
                candidates.append((source_kind, video))

        if ranking:
            performance = _load_performance_map(metrics_db, settings)
            videos = [v for _, v in candidates]
            scores_map: dict[str, float] = {
                v.video_id: round(score_vod(v, performance=performance), 1)
                for _, v in candidates
            }
            ordered = rank_vods(
                videos,
                min_recent_days=min_recent_days,
                max_results=max_results,
                performance=performance,
            )
        else:
            scores_map = {}
            ordered = [v for _, v in candidates[:max_results]]

        kind_map: dict[str, str] = {v.video_id: kind for kind, v in candidates}
        out: list[dict] = []
        for video in ordered:
            item: dict[str, Any] = {
                "url": video.url,
                "video_id": video.video_id,
                "title": video.title,
                "platform": video.platform,
            }
            if ranking and video.video_id in scores_map:
                item["source"] = kind_map.get(video.video_id, "search")
                item["score"] = scores_map[video.video_id]
            out.append(item)
        return out
    except Exception:
        log.warning("auto_discover failed", exc_info=True)
        return []
