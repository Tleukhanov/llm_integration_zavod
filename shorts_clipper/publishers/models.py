from dataclasses import dataclass, field


class PublishError(Exception):
    """Base class for publisher-side failures that must NOT be retried."""


class PublishValidationError(PublishError):
    """Raised when a platform accepted an upload but returned no usable identifier.

    The publish is a hard (permanent) failure: without a platform-side id the
    pipeline cannot verify the clip or track metrics, and retrying may create
    a duplicate upload.
    """


@dataclass
class ClipMetadata:
    """Universal metadata object for a clip."""

    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    privacy_status: str = "private"
    language: str = "en"


@dataclass
class PublishResult:
    """Result of a publishing attempt."""

    platform: str
    success: bool
    url: str | None = None
    platform_id: str | None = None
    published_at: str | None = None
    retry_count: int = 0
    error_message: str | None = None
