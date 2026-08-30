import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import requests

from shorts_clipper.core.exceptions import ConfigurationError
from shorts_clipper.core.settings import Settings

from ..base import Publisher
from ..models import ClipMetadata, PublishResult

log = logging.getLogger(__name__)


class InstagramGraphPublisher(Publisher):
    """Handles publishing vertical clips to Instagram Reels using the official Meta Graph API."""

    def __init__(self):
        self.settings = Settings.from_env()

    @property
    def platform_name(self) -> str:
        return "instagram"

    def authenticate(self) -> None:
        """Verify Graph API credentials exist."""
        if not self.settings.ig_access_token or not self.settings.ig_account_id:
            raise ConfigurationError("IG_ACCESS_TOKEN or IG_ACCOUNT_ID not found in settings.")
        log.info("Instagram Graph API credentials verified.")

    def publish(
        self,
        video_path: Path,
        metadata: ClipMetadata,
        signed_url: str | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> PublishResult:
        token = self.settings.ig_access_token
        ig_id = self.settings.ig_account_id

        if not token or not ig_id:
            return PublishResult(
                platform=self.platform_name,
                success=False,
                error_message="Graph API credentials missing.",
            )

        tags_str = " ".join([f"#{t}" for t in metadata.tags])
        caption = f"{metadata.title}\n\n{metadata.description}\n\n{tags_str}"

        try:
            if not signed_url:
                raise ValueError("Instagram publishing requires a signed URL.")

            # Step 1: Create Media Container
            log.info("Initializing Reel upload via Graph API...")
            url = f"https://graph.instagram.com/v19.0/{ig_id}/media"
            payload = {
                "media_type": "REELS",
                "video_url": signed_url,
                "caption": caption,
                "access_token": token,
            }
            res = requests.post(url, data=payload, timeout=60)
            res_data = res.json()

            if "error" in res_data:
                raise RuntimeError(f"Graph API Error: {res_data['error']['message']}")

            creation_id = res_data.get("id")
            if not creation_id:
                raise RuntimeError(f"Failed to get creation_id: {res_data}")

            # Step 3: Check Status until FINISHED
            log.info(
                f"Container created ({creation_id}). Waiting for Meta to download and process the video..."
            )
            status_url = f"https://graph.instagram.com/v19.0/{creation_id}?fields=status_code,status&access_token={token}"

            max_attempts = 60
            for attempt in range(max_attempts):
                status_res = requests.get(status_url, timeout=30).json()
                status = status_res.get("status_code")
                log.info(f"Processing status: {status} | Full response: {status_res}")
                if status == "FINISHED":
                    break
                elif status == "ERROR":
                    raise RuntimeError(
                        f"Meta failed to process the video. Full response: {status_res}"
                    )

                if progress_callback:
                    progress_callback(min(90, int(90 * (attempt / max_attempts))))
                time.sleep(5)
            else:
                raise RuntimeError("Timeout waiting for Meta to process the video.")

            # Step 4: Publish
            log.info("Processing complete. Publishing Reel to Instagram...")
            publish_url = f"https://graph.instagram.com/v19.0/{ig_id}/media_publish"
            pub_res = requests.post(
                publish_url, data={"creation_id": creation_id, "access_token": token}, timeout=30
            )
            pub_data = pub_res.json()

            if "error" in pub_data:
                raise RuntimeError(f"Publish Error: {pub_data['error']['message']}")

            published_id = pub_data.get("id")
            if progress_callback:
                progress_callback(100)

            return PublishResult(
                platform=self.platform_name,
                success=True,
                url=f"https://www.instagram.com/reel/{published_id}/" if published_id else None,
                platform_id=published_id,
                published_at=datetime.utcnow().isoformat() + "Z",
            )

        except ConfigurationError:
            raise
        except Exception as e:
            log.error(f"Graph API Publishing failed: {e}")
            return PublishResult(platform=self.platform_name, success=False, error_message=str(e))

    def verify(self, platform_id: str) -> bool:
        """Verify that the Instagram Reel exists and is accessible."""
        token = self.settings.ig_access_token
        if not token:
            return False

        try:
            # Check the published media ID
            url = f"https://graph.instagram.com/v19.0/{platform_id}?fields=id,media_type&access_token={token}"
            res = requests.get(url, timeout=30)
            data = res.json()
            if res.status_code == 200 and data.get("id") == platform_id:
                log.info("Successfully verified Instagram Reel exists.")
                return True
            log.warning("Verification failed for Instagram Reel %s: %s", platform_id, data)
            return False
        except Exception as e:
            log.error("Failed to verify Instagram Reel %s: %s", platform_id, e)
            return False
