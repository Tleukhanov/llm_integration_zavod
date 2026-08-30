from dataclasses import dataclass, field


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
