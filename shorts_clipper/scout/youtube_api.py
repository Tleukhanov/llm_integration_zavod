"""
YouTube Data API v3 client.
Uses publishedAfter to enforce age windows at the API level.
Falls back gracefully when key absent or quota exhausted.
Uses stdlib urllib only — no new dependencies.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

QUOTA_FILE = Path("outputs/yt_api_quota.json")
QUOTA_SEARCH_LIMIT = 9_000  # pause searches, still allow video.list
QUOTA_HARD_LIMIT = 9_500  # disable API entirely for the day
QUOTA_MAX = 10_000


class YouTubeAPIClient:
    BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._quota = self._load_quota()

    def _load_quota(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        if QUOTA_FILE.exists():
            try:
                data = json.loads(QUOTA_FILE.read_text())
                if data.get("date") == today:
                    return data
            except Exception:
                pass
        quota = {
            "date": today,
            "units_used": 0,
            "searches": 0,
            "video_lookups": 0,
            "caption_lookups": 0,
        }
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUOTA_FILE.write_text(json.dumps(quota, indent=2))
        return quota

    def _charge(self, units: int) -> bool:
        if self._quota["units_used"] + units > QUOTA_HARD_LIMIT:
            log.warning(
                "YouTube API quota at hard limit (%d/%d). Using yt-dlp fallback.",
                self._quota["units_used"],
                QUOTA_MAX,
            )
            return False
        self._quota["units_used"] += units
        QUOTA_FILE.write_text(json.dumps(self._quota, indent=2))
        return True

    @property
    def searches_available(self) -> bool:
        return self._quota["units_used"] < QUOTA_SEARCH_LIMIT

    def search(
        self,
        query: str,
        published_after: datetime,
        max_results: int = 25,
    ) -> list[str]:
        """
        Search YouTube for video IDs published after the given datetime.
        publishedAfter enforces the age window at the YouTube API level.
        Costs 100 quota units.
        Returns list of video IDs only.
        """
        if not self.searches_available:
            log.warning("API search quota limit reached. Falling back to yt-dlp.")
            return []
        if not self._charge(100):
            return []

        # YouTube API requires RFC 3339 UTC
        after_str = published_after.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "part": "id",
            "type": "video",
            "publishedAfter": after_str,
            "maxResults": 50,
            "videoDuration": "medium",  # 4-20 minutes
            "videoEmbeddable": "true",
            "relevanceLanguage": "en",
            "order": "date",
            "key": self.api_key,
        }

        if query.startswith("from:"):
            channel_id = query[5:]
            params["channelId"] = channel_id
        else:
            params["q"] = query
            params["order"] = "viewCount"

        log.debug("YouTube API search params: %s", {k: v for k, v in params.items() if k != "key"})
        url = f"{self.BASE}/search?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
            self._quota["searches"] += 1
            QUOTA_FILE.write_text(json.dumps(self._quota, indent=2))
            ids = [
                item["id"]["videoId"]
                for item in data.get("items", [])
                if item.get("id", {}).get("kind") == "youtube#video"
            ]
            log.info("YouTube API search: '%s' → %d results", query, len(ids))
            return ids
        except Exception as exc:
            import urllib.error as uerror

            if isinstance(exc, uerror.HTTPError):
                log.warning("YouTube API search failed: %s. Response: %s", exc, exc.read().decode())
            else:
                log.warning("YouTube API search failed: %s", exc)
            return []

    def get_video_details(self, video_ids: list[str]) -> list[dict]:
        """
        Batch fetch full metadata for up to 50 video IDs per call.
        Returns list of YouTube video resource dicts with:
            snippet (title, publishedAt, channelId, defaultAudioLanguage)
            statistics (viewCount, likeCount, commentCount)
            contentDetails (duration in ISO 8601)
        Costs 1 unit per video.
        """
        if not video_ids:
            return []
        results = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            if not self._charge(len(batch)):
                break
            params = {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(batch),
                "key": self.api_key,
            }
            url = f"{self.BASE}/videos?" + urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    data = json.loads(resp.read())
                self._quota["video_lookups"] += len(batch)
                QUOTA_FILE.write_text(json.dumps(self._quota, indent=2))
                results.extend(data.get("items", []))
            except Exception as exc:
                log.warning("YouTube API video details failed: %s", exc)
        return results

    def parse_duration_seconds(self, iso_duration: str) -> int:
        """Parse ISO 8601 duration (PT4M13S) to seconds."""
        import re

        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
        if not m:
            return 0
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        s = int(m.group(3) or 0)
        return h * 3600 + mins * 60 + s

    def has_english_captions(self, video_id: str) -> bool | None:
        """
        Check if video has English captions using the captions API.
        Costs 1 unit per video.
        Returns True/False if successful, None if API fails.
        """
        if not self._charge(1):
            return None

        params = {
            "part": "snippet",
            "videoId": video_id,
            "key": self.api_key,
        }
        url = f"{self.BASE}/captions?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
            self._quota.setdefault("caption_lookups", 0)
            self._quota["caption_lookups"] += 1
            QUOTA_FILE.write_text(json.dumps(self._quota, indent=2))

            for item in data.get("items", []):
                lang = item.get("snippet", {}).get("language", "").lower()
                if lang.startswith("en"):
                    return True
            return False
        except Exception as exc:
            log.warning("YouTube API captions details failed for %s: %s", video_id, exc)
            return None
