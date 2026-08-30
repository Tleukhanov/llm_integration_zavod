import logging

from .base import Publisher

log = logging.getLogger(__name__)


class PublisherRegistry:
    """Registry to hold all available publisher implementations."""

    _publishers: dict[str, type[Publisher]] = {}

    @classmethod
    def register(cls, publisher_class: type[Publisher]) -> type[Publisher]:
        """Decorator to register a Publisher class."""
        instance = publisher_class()
        cls._publishers[instance.platform_name] = publisher_class
        log.debug(f"Registered publisher: {instance.platform_name}")
        return publisher_class

    @classmethod
    def get_publisher(cls, platform_name: str) -> Publisher:
        """Get a new instance of a publisher by platform name."""
        if platform_name not in cls._publishers:
            raise ValueError(f"Publisher for platform '{platform_name}' not found.")
        return cls._publishers[platform_name]()

    @classmethod
    def get_all_publishers(cls) -> dict[str, type[Publisher]]:
        """Get a dictionary of all registered publishers."""
        return cls._publishers.copy()
