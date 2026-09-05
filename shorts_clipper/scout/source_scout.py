"""Pluggable source-scout interface for auto-sourcing content from multiple platforms.

Providers implement ``SourceProvider`` and are registered in ``_REGISTRY``.
``scout()`` runs selected providers, deduplicates by ``video_id``, and
applies the caller-requested ``limit``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SourceVideo:
    url: str
    video_id: str
    title: str = ""
    duration_seconds: float | None = None
    platform: str = "youtube"
    license: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class SourceProvider(Protocol):
    """Every content provider must satisfy this interface.

    ``search`` returns a list of ``SourceVideo`` instances.  Implementations
    must **never** raise — errors are caught internally and result in ``[]``.
    """

    def search(self, query: str, limit: int = 5, **kw) -> list[SourceVideo]: ...


class _BaseProvider(ABC):
    """Shared helper so concrete providers only implement ``_search``."""

    @abstractmethod
    def _search(self, query: str, limit: int, **kw) -> list[SourceVideo]: ...

    def search(self, query: str, limit: int = 5, **kw) -> list[SourceVideo]:
        try:
            return self._search(query, limit, **kw)
        except Exception:
            log.exception("Provider %s failed unexpectedly", type(self).__name__)
            return []


# ---------------------------------------------------------------------------
# YouTube CC provider
# ---------------------------------------------------------------------------


class YouTubeCCProvider(_BaseProvider):
    """Search YouTube for Creative-Commons-licensed videos via yt-dlp."""

    def _search(self, query: str, limit: int, **kw) -> list[SourceVideo]:
        from shorts_clipper.downloader.yt_dlp import search_cc_videos

        exclude_ids: set[str] = kw.pop("exclude_ids", set()) or set()
        raw = search_cc_videos(query, max_results=limit, **kw)

        out: list[SourceVideo] = []
        for item in raw:
            vid = item.get("id") or item.get("video_id") or ""
            if not vid or vid in exclude_ids:
                continue
            out.append(
                SourceVideo(
                    url=item.get("url") or f"https://www.youtube.com/watch?v={vid}",
                    video_id=vid,
                    title=item.get("title") or "",
                    platform="youtube",
                    license="cc",
                    extra={k: v for k, v in item.items() if k not in ("id", "url", "title")},
                )
            )
        return out[:limit]


# ---------------------------------------------------------------------------
# Twitch provider (stub)
# ---------------------------------------------------------------------------


class TwitchProvider(_BaseProvider):
    """Stub provider reserved for future Twitch VOD support.

    To implement later:
      1. Accept a Twitch client-id / OAuth token via ``__init__`` or env.
      2. Use the Twitch Helix API (``GET /videos``) or an alternative
         scraper to search for VODs matching *query*.
      3. Return ``list[SourceVideo]`` with ``platform="twitch"``.
      4. Respect *limit* and never raise.
    """

    def _search(self, query: str, limit: int, **kw) -> list[SourceVideo]:
        log.warning("Twitch provider not yet implemented (reserved)")
        return []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[SourceProvider]] = {
    "youtube": YouTubeCCProvider,
    "twitch": TwitchProvider,
}


def available_providers() -> dict[str, type[SourceProvider]]:
    """Return a mapping of registered provider names to their classes."""
    return dict(_REGISTRY)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def scout(
    query: str,
    providers: tuple[str, ...] = ("youtube",),
    limit: int = 5,
    exclude_ids: set[str] | None = None,
) -> list[SourceVideo]:
    """Run *providers* in order, deduplicate by ``video_id``, apply *limit*.

    Args:
        query: Free-text search term forwarded to each provider.
        providers: Tuple of registered provider names to invoke.
        limit: Maximum number of results to return in total.
        exclude_ids: Video IDs to skip (already processed).

    Returns:
        Deduplicated, limit-capped list of ``SourceVideo``.
    """
    seen: set[str] = set(exclude_ids or set())
    results: list[SourceVideo] = []

    for name in providers:
        cls = _REGISTRY.get(name)
        if cls is None:
            log.warning("Unknown provider %r — skipping", name)
            continue
        remaining = limit - len(results)
        if remaining <= 0:
            break
        hits = cls().search(query, limit=remaining, exclude_ids=seen)
        for v in hits:
            if v.video_id not in seen:
                seen.add(v.video_id)
                results.append(v)

    return results[:limit]
