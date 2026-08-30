"""Typed exceptions used across the clipping pipeline."""


class ShortsClipperError(Exception):
    """Base class for all project-specific exceptions."""


class ConfigurationError(ShortsClipperError):
    """Raised when settings or credentials are invalid."""


class ProviderError(ShortsClipperError):
    """Raised when an AI provider fails or returns invalid output."""


class MediaProcessingError(ShortsClipperError):
    """Raised when ffmpeg/movie processing fails."""


class SUBTITLE_NOT_AVAILABLE(ShortsClipperError):
    """video has no English subtitles"""


class YOUTUBE_RATE_LIMIT_429(ShortsClipperError):
    """yt-dlp blocked, infrastructure issue"""


class GEMINI_UNAVAILABLE_503(ShortsClipperError):
    """Gemini overloaded, infrastructure issue"""


class WHISPER_TIMEOUT(ShortsClipperError):
    """transcription took too long"""


class TRANSCRIPT_EMPTY(ShortsClipperError):
    """transcription returned nothing"""
