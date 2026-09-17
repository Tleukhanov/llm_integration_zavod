import logging
import re
from collections.abc import Callable
from pathlib import Path

from ..models import PublishValidationError
from .auth import get_youtube_service

log = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _extract_video_id(response: dict) -> str:
    """Extract and validate the YouTube video id from an insert response.

    The response must carry a syntactically valid 11-char video id. Without it
    we cannot verify the upload nor attach platform metrics, so the publish is
    raised as a permanent :class:`PublishValidationError` instead of being
    reported as a success.
    """
    video_id = response.get("id") if isinstance(response, dict) else None
    if video_id and _VIDEO_ID_RE.match(str(video_id)):
        return video_id

    upload_status = None
    if isinstance(response, dict):
        status = response.get("status") or {}
        if isinstance(status, dict):
            upload_status = status.get("uploadStatus")

    raise PublishValidationError(
        "YouTube insert returned no valid video id "
        f"(id={video_id!r}, uploadStatus={upload_status!r}); "
        "publish NOT treated as success to avoid duplicate/untracked uploads"
    )


def upload_short(
    video_path: Path | str,
    title: str,
    description: str = "#Shorts",
    tags: list[str] | None = None,
    privacy_status: str = "private",
    progress_callback: Callable[[int], None] | None = None,
) -> str:
    """Upload a video to YouTube as a Short.

    Returns:
        The YouTube video ID of the uploaded video.
    """
    from googleapiclient.http import MediaFileUpload

    log.info("Uploading %s to YouTube...", video_path)

    youtube = get_youtube_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or ["Shorts", "Trending", "Viral"],
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=2 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk(num_retries=5)
        if status:
            progress_pct = int(status.progress() * 100)
            log.info("Uploaded %d%%...", progress_pct)
            if progress_callback:
                try:
                    progress_callback(progress_pct)
                except Exception:
                    pass

    video_id = _extract_video_id(response)
    log.info("✅ Upload Complete! Video ID: %s", video_id)
    return video_id
