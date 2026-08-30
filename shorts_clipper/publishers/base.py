from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from .models import ClipMetadata, PublishResult


class Publisher(ABC):
    """Abstract base class for all platform publishers."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the unique name of the platform (e.g., 'youtube', 'instagram')."""
        pass

    @abstractmethod
    def authenticate(self) -> None:
        """
        Authenticate with the platform.
        Raises an exception if authentication fails permanently.
        """
        pass

    @abstractmethod
    def publish(
        self,
        video_path: Path,
        metadata: ClipMetadata,
        signed_url: str | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> PublishResult:
        """
        Publish a vertical video clip to the destination platform.

        Args:
            video_path: Path to the MP4 file to upload.
            metadata: ClipMetadata object containing title, description, etc.
            signed_url: Optional presigned URL for platforms that require pulling from a public URL.
            progress_callback: Optional callback for reporting upload progress.

        Returns:
            PublishResult object representing the outcome.
        """
        pass

    @abstractmethod
    def verify(self, platform_id: str) -> bool:
        """
        Verify that the upload with the given ID was successful and is live.

        Args:
            platform_id: The ID of the uploaded media returned by publish().

        Returns:
            True if verification succeeds, False otherwise.
        """
        pass
