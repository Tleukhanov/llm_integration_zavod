"""Viral/trend scoring for scouted VODs.

Pure stdlib. ``score_vod`` grades a single ``SourceVideo`` on a 0-100 scale
(recent uploads, high view counts, hype keywords and mid-length VODs win);
``rank_vods`` orders a list by that score with deterministic ties.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from shorts_clipper.scout.source_scout import SourceVideo

# --- recency -----------------------------------------------------------------
_RECENCY_MAX = 25.0
_RECENCY_FULL_DAYS = 3.0
_RECENCY_WINDOW_DAYS = 14.0
_RECENCY_DEFAULT = 10.0

# --- view velocity ------------------------------------------------------------
_VIEWS_MAX = 30.0
_VIEWS_COEF = 20.0
_VIEWS_BASE = 1000.0
_VIEWS_DEFAULT = 15.0

# --- title keywords -----------------------------------------------------------
_KEYWORD_TOKENS: tuple[str, ...] = (
    "major",
    "qualifier",
    "clutch",
    "ace",
    "1v",
    "2016",
    "final",
    "last round",
    "overtime",
    "record",
    "powerful",
    "best",
    "insane",
)
_KEYWORD_VALUE = 5.0
_KEYWORD_CAP = 25.0
_EMPHASIS_CAP = 5.0

# --- duration band ------------------------------------------------------------
_DURATION_DEFAULT = 10.0


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extra_value(video: SourceVideo, key: str) -> object:
    extra = video.extra or {}
    return extra.get(key)


def _parse_upload_date(video: SourceVideo) -> datetime | None:
    """Parse an ``YYYYMMDD`` upload date from ``extra`` into a UTC datetime."""
    raw = _extra_value(video, "upload_date")
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _recency_score(uploaded: datetime | None, now: datetime) -> float:
    if uploaded is None:
        return _RECENCY_DEFAULT
    age_days = max((now - uploaded).total_seconds() / 86400.0, 0.0)
    if age_days <= _RECENCY_FULL_DAYS:
        return _RECENCY_MAX
    if age_days >= _RECENCY_WINDOW_DAYS:
        return 0.0
    span = _RECENCY_WINDOW_DAYS - _RECENCY_FULL_DAYS
    return _RECENCY_MAX * (1 - (age_days - _RECENCY_FULL_DAYS) / span)


def _velocity_score(video: SourceVideo) -> float:
    views = _as_int(_extra_value(video, "view_count"), 0)
    if views <= 0:
        return _VIEWS_DEFAULT
    return min(_VIEWS_MAX, _VIEWS_COEF * math.log10(1 + views / _VIEWS_BASE))


def _keyword_score(title: str) -> float:
    lowered = title.lower()
    bonus = sum(_KEYWORD_VALUE for token in _KEYWORD_TOKENS if token in lowered)
    bonus += min(lowered.count("!"), _EMPHASIS_CAP)
    return min(bonus, _KEYWORD_CAP)


def _duration_score(video: SourceVideo) -> float:
    seconds = video.duration_seconds
    if seconds is None:
        seconds = _as_float(_extra_value(video, "duration"))
    if seconds is None:
        return _DURATION_DEFAULT
    minutes = seconds / 60.0
    if 20 <= minutes < 60:
        return 20.0
    if 60 <= minutes <= 90:
        return 15.0
    if (10 <= minutes < 20) or (90 < minutes <= 180):
        return 5.0
    return 0.0


def score_vod(video: SourceVideo, now: datetime | None = None) -> float:
    """Return a viral-potential heuristic in ``[0, 100]`` for *video*.

    Weights: recency up to 25 (full marks inside 3 days, linear decay to 0 by
    14 days, 10 when unknown), view velocity up to 30 (``20*log10(1+views/1k)``,
    15 when unknown), title keywords up to 25 (+5 per hype token, +1 per ``!``
    capped at 5, 0 otherwise) and duration band up to 20 (20 for 20-60 min,
    15 for 60-90, 5 for 10-20 or 90-180, 10 when unknown, else 0).
    """
    now_utc = now
    if now_utc is None:
        now_utc = datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    total = (
        _recency_score(_parse_upload_date(video), now_utc)
        + _velocity_score(video)
        + _keyword_score(video.title or "")
        + _duration_score(video)
    )
    return max(0.0, min(100.0, total))


def rank_vods(
    videos: list[SourceVideo],
    *,
    min_recent_days: int | None = None,
    max_results: int = 10,
) -> list[SourceVideo]:
    """Sort *videos* by ``score_vod`` descending, optionally filtered by recency.

    When ``min_recent_days`` is set, VODs without a parseable ``upload_date``
    are dropped along with anything older than the cutoff. Ties break by
    ``video_id`` lexicographically, so ordering is fully deterministic.
    """
    cutoff: datetime | None = None
    if min_recent_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=min_recent_days)

    scored: list[tuple[float, SourceVideo]] = []
    for video in videos:
        if cutoff is not None:
            uploaded = _parse_upload_date(video)
            if uploaded is None or uploaded < cutoff:
                continue
        scored.append((score_vod(video), video))

    scored.sort(key=lambda pair: (-pair[0], pair[1].video_id))
    return [video for _, video in scored[:max_results]]
