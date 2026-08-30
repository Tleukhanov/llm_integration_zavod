import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..base import Publisher
from ..models import ClipMetadata, PublishResult
from .auth import get_youtube_service
from .uploader import upload_short

log = logging.getLogger(__name__)


class YouTubePublisher(Publisher):
    """Handles publishing vertical clips to YouTube Shorts."""

    @property
    def platform_name(self) -> str:
        return "youtube"

    def authenticate(self) -> None:
        """Authenticate with YouTube to ensure credentials are valid."""
        get_youtube_service()
        log.info("YouTube authentication verified.")

    def publish(
        self,
        video_path: Path,
        metadata: ClipMetadata,
        signed_url: str | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> PublishResult:
        try:
            video_id = upload_short(
                video_path=video_path,
                title=metadata.title,
                description=metadata.description,
                tags=metadata.tags,
                privacy_status=metadata.privacy_status,
                progress_callback=progress_callback,
            )
            return PublishResult(
                platform=self.platform_name,
                success=True,
                url=f"https://youtube.com/shorts/{video_id}" if video_id else None,
                platform_id=video_id,
                published_at=datetime.utcnow().isoformat() + "Z",
            )
        except Exception as e:
            log.error(f"YouTube publishing failed: {e}")
            return PublishResult(
                platform=self.platform_name,
                success=False,
                error_message=str(e),
            )

    def verify(self, platform_id: str) -> bool:
        """Verify that the YouTube video is accessible and processed."""
        try:
            youtube = get_youtube_service()
            request = youtube.videos().list(part="status", id=platform_id)
            response = request.execute()
            items = response.get("items", [])
            if items:
                status = items[0].get("status", {})
                upload_status = status.get("uploadStatus")
                if upload_status in ("uploaded", "processed"):
                    log.info("Successfully verified YouTube video status: %s", upload_status)
                    return True
                else:
                    log.warning("YouTube video exists but status is: %s", upload_status)
            return False
        except Exception as e:
            log.error("Failed to verify YouTube upload %s: %s", platform_id, e)
            return False
