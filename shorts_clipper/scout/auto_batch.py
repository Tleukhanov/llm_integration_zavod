"""Auto-discover fresh, unprocessed VODs for batch clipping."""

from __future__ import annotations

import logging
from typing import Any

from shorts_clipper.core.processed_store import ProcessedStore
from shorts_clipper.scout.source_scout import scout

log = logging.getLogger(__name__)


def auto_discover(
    settings: Any,
    query: str = "cs2 gameplay",
    providers: tuple[str, ...] = ("youtube",),
    max_results: int = 5,
) -> list[dict]:
    """Find VODs not yet in the processed store and return them as dicts.

    Args:
        settings: Application settings object exposing ``processed_videos_path``.
        query: Free-text search term forwarded to each provider.
        providers: Tuple of registered provider names to invoke.
        max_results: Maximum number of fresh results to return.

    Returns:
        List of dicts with keys ``url``, ``video_id``, ``title``, ``platform``.
        Returns ``[]`` on any error so the pipeline is never interrupted.
    """
    try:
        store = ProcessedStore.from_path(settings.processed_videos_path)
        processed_ids = store.all_ids()
        videos = scout(query, providers=providers, limit=max_results, exclude_ids=processed_ids)
        return [
            {
                "url": sv.url,
                "video_id": sv.video_id,
                "title": sv.title,
                "platform": sv.platform,
            }
            for sv in videos
        ]
    except Exception:
        log.warning("auto_discover failed", exc_info=True)
        return []
