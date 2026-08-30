from .instagram.publisher import InstagramGraphPublisher
from .manager import PublishingEngine
from .models import ClipMetadata, PublishResult
from .registry import PublisherRegistry
from .youtube.publisher import YouTubePublisher

PublisherRegistry.register(YouTubePublisher)
PublisherRegistry.register(InstagramGraphPublisher)

__all__ = [
    "PublishingEngine",
    "ClipMetadata",
    "PublishResult",
    "PublisherRegistry",
]
