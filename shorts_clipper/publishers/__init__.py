from .instagram.publisher import InstagramGraphPublisher
from .manager import PublishingEngine
from .models import ClipMetadata, PublishResult
from .registry import PublisherRegistry
from .tiktok.publisher import TikTokPublisher
from .youtube.publisher import YouTubePublisher

PublisherRegistry.register(YouTubePublisher)
PublisherRegistry.register(InstagramGraphPublisher)
PublisherRegistry.register(TikTokPublisher)

__all__ = [
    "PublishingEngine",
    "ClipMetadata",
    "PublishResult",
    "PublisherRegistry",
]
