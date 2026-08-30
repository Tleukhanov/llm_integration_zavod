"""TikTok publisher using the official Content Posting API (Direct Post flow)."""

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from shorts_clipper.core.exceptions import ConfigurationError
from shorts_clipper.core.settings import Settings

from ..base import Publisher
from ..models import ClipMetadata, PublishResult

log = logging.getLogger(__name__)

_CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/creator_info/query/"
_DIRECT_PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/direct/"
_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/direct/status/"

# Interim statuses which mean the video is still being processed.
_INTERIM_STATUSES = {
    "PROCESSING_DOWNLOAD",
    "UPLOADING_VIDEO",
    "PUBLISHING",
    "SEND_TO_USER_INBOX",
}

try:
    from curl_cffi import requests as _curl_requests

    _USE_CURL = True
except ImportError:
    _USE_CURL = False


def _request_json(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict | None = None,
    timeout: int = 60,
) -> dict:
    """Make a JSON request and return the parsed body, raising a clear error on non-2xx."""
    if _USE_CURL:
        res = _curl_requests.request(method, url, headers=headers, json=payload, timeout=timeout)
        if res.status_code in (401, 403):
            raise ConfigurationError(
                f"TikTok API rejected the access token (status {res.status_code}): {res.text}"
            )
        if not 200 <= res.status_code < 300:
            raise RuntimeError(
                f"TikTok API {method} {url} failed with status {res.status_code}: {res.text}"
            )
        if not res.text:
            return {}
        return res.json()
    else:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code in (401, 403):
                raise ConfigurationError(
                    f"TikTok API rejected the access token (status {e.code}): {detail}"
                )
            raise RuntimeError(
                f"TikTok API {method} {url} failed with status {e.code}: {detail}"
            )
        if not body:
            return {}
        return json.loads(body)


class TikTokPublisher(Publisher):
    """Publishes vertical clips to TikTok via the official Content Posting API (Direct Post)."""

    def __init__(self):
        self.settings = Settings.from_env()

    @property
    def platform_name(self) -> str:
        return "tiktok"

    def authenticate(self) -> None:
        """Verify TikTok credentials exist and are valid."""
        client_key = self.settings.tiktok_client_key
        client_secret = self.settings.tiktok_client_secret
        access_token = self.settings.tiktok_access_token
        if not client_key or not client_secret or not access_token:
            raise ConfigurationError(
                "TT_CLIENT_KEY, TT_CLIENT_SECRET or TT_ACCESS_TOKEN not found in settings."
            )
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            data = _request_json("GET", _CREATOR_INFO_URL, headers, timeout=30)
            log.info("TikTok credentials verified via creator info endpoint.")
        except ConfigurationError:
            raise
        except Exception as e:
            log.warning("TikTok creator info check failed (continuing): %s", e)

    def publish(
        self,
        video_path: Path,
        metadata: ClipMetadata,
        signed_url: str | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> PublishResult:
        if not signed_url:
            return PublishResult(
                platform=self.platform_name,
                success=False,
                error_message="TikTok requires a public or signed video URL",
            )

        access_token = self.settings.tiktok_access_token
        if not access_token:
            return PublishResult(
                platform=self.platform_name,
                success=False,
                error_message="TT_ACCESS_TOKEN not found in settings.",
            )

        privacy_level = "SELF_ONLY" if metadata.privacy_status != "public" else "PUBLIC_TO_EVERYONE"
        body = {
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": signed_url,
                "video_size": {"width": 1080, "height": 1920},
            },
            "post_info": {
                "title": metadata.title[:150],
                "description": metadata.description,
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "brand_content_toggle": False,
                "brand_organic_toggle": False,
                "ai_label": "self_declared",
            },
        }

        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            log.info("Initiating TikTok Direct Post publish (PULL_FROM_URL)...")
            res = _request_json("POST", _DIRECT_PUBLISH_URL, headers, payload=body)

            publish_id = (res.get("data") or {}).get("publish_id")
            if not publish_id:
                raise RuntimeError(f"Failed to obtain publish_id from TikTok: {res}")

            log.info(
                f"Direct Post initiated (publish_id={publish_id}). Polling for publish status..."
            )
            status_url = f"{_STATUS_URL}?publish_id={publish_id}"

            max_attempts = 60
            for attempt in range(max_attempts):
                status_res = _request_json("GET", status_url, headers)
                status = (status_res.get("data") or {}).get("status") or "UNKNOWN"
                log.info(f"TikTok publish status: {status} | Response: {status_res}")

                if status == "PUBLISH_COMPLETE":
                    break
                if status == "FAILED":
                    fail_reason = (status_res.get("data") or {}).get("fail_reason")
                    raise RuntimeError(
                        f"TikTok failed to publish the video: {fail_reason or status_res}"
                    )
                if status not in _INTERIM_STATUSES:
                    log.warning(f"Unexpected TikTok publish status: {status}")

                if progress_callback:
                    progress_callback(min(90, int(90 * (attempt / max_attempts))))
                time.sleep(5)
            else:
                raise RuntimeError("Timeout waiting for TikTok to publish the video.")

            if progress_callback:
                progress_callback(100)

            video_id = (status_res.get("data") or {}).get("video_id") or publish_id
            platform_id = video_id
            open_id = self.settings.tiktok_open_id
            url = (
                f"https://www.tiktok.com/@{open_id}/video/{video_id}"
                if open_id
                else None
            )

            return PublishResult(
                platform=self.platform_name,
                success=True,
                url=url,
                platform_id=platform_id,
                published_at=datetime.now(UTC).isoformat() + "Z",
            )

        except ConfigurationError:
            log.error("TikTok configuration/authentication error during publish.")
            raise
        except Exception as e:
            log.error(f"TikTok publishing failed: {e}")
            return PublishResult(
                platform=self.platform_name,
                success=False,
                error_message=str(e),
            )

    def verify(self, platform_id: str) -> bool:
        """Verify that the TikTok post reached a completed state."""
        access_token = self.settings.tiktok_access_token
        if not access_token:
            return False
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            status_url = f"{_STATUS_URL}?publish_id={platform_id}"
            res = _request_json("GET", status_url, headers)
            status = (res.get("data") or {}).get("status")
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                log.info("Successfully verified TikTok publish status: %s", status)
                return True
            log.warning("TikTok publish not complete yet, status: %s", status)
            return False
        except Exception as e:
            log.error("Failed to verify TikTok publish %s: %s", platform_id, e)
            return False
