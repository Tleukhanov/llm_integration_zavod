"""Application settings with lightweight `.env` support."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _env(name: str, file_values: dict[str, str], default: str | None = None) -> str | None:
    return os.environ.get(name) or file_values.get(name) or default


@dataclass(frozen=True, slots=True)
class Settings:
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    youtube_api_key: str | None = None
    instagram_username: str | None = None
    instagram_password: str | None = None
    ig_access_token: str | None = None
    ig_account_id: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    default_provider: str = "gemini"
    whisper_model: str = "tiny.en"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    models_dir: Path = Path("models")
    output_dir: Path = Path("outputs")
    cache_dir: Path = Path(".cache/shorts-clipper")
    log_level: str = "INFO"
    enable_gpu: bool = False
    video_codec: str = "libx264"
    video_preset: str = "ultrafast"
    scout_max_age_days: int = 90
    subtitle_style: str = "default"
    proxy: str | None = None
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str | None = None
    publish_platforms: list[str] = field(default_factory=lambda: ["youtube", "instagram"])

    @classmethod
    def from_env(cls, env_path: str | Path = ".env") -> Settings:
        path = Path(env_path)
        file_values = _parse_env_file(path)

        enable_gpu = (_env("SHORTS_ENABLE_GPU", file_values, "false") or "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        whisper_device = _env(
            "SHORTS_WHISPER_DEVICE", file_values, "cuda" if enable_gpu else "cpu"
        ) or ("cuda" if enable_gpu else "cpu")

        whisper_compute_type = _env(
            "SHORTS_WHISPER_COMPUTE_TYPE",
            file_values,
            "float16" if enable_gpu else "int8",
        ) or ("float16" if enable_gpu else "int8")

        default_video_codec = "h264_nvenc" if enable_gpu else "libx264"
        default_video_preset = "fast" if enable_gpu else "ultrafast"

        video_codec = (
            _env("SHORTS_VIDEO_CODEC", file_values, default_video_codec) or default_video_codec
        )
        video_preset = (
            _env("SHORTS_VIDEO_PRESET", file_values, default_video_preset) or default_video_preset
        )

        try:
            scout_max_age_days = int(_env("SHORTS_SCOUT_MAX_AGE_DAYS", file_values, "90") or "90")
        except ValueError:
            scout_max_age_days = 90
        proxy = _env("SHORTS_PROXY", file_values)

        platforms_raw = (
            _env("SHORTS_PUBLISH_PLATFORMS", file_values, "youtube,instagram")
            or "youtube,instagram"
        )
        publish_platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]

        if proxy:
            os.environ["SHORTS_PROXY"] = proxy

        youtube_api_key = _env("YOUTUBE_API_KEY", file_values)
        if youtube_api_key:
            os.environ["YOUTUBE_API_KEY"] = youtube_api_key

        return cls(
            gemini_api_key=_env("GEMINI_API_KEY", file_values),
            openai_api_key=_env("OPENAI_API_KEY", file_values),
            anthropic_api_key=_env("ANTHROPIC_API_KEY", file_values),
            youtube_api_key=_env("YOUTUBE_API_KEY", file_values),
            instagram_username=_env("INSTAGRAM_USERNAME", file_values),
            instagram_password=_env("INSTAGRAM_PASSWORD", file_values),
            ig_access_token=_env("IG_ACCESS_TOKEN", file_values),
            ig_account_id=_env("IG_ACCOUNT_ID", file_values),
            ollama_base_url=_env("OLLAMA_BASE_URL", file_values, "http://localhost:11434")
            or "http://localhost:11434",
            default_provider=_env("SHORTS_PROVIDER", file_values, "gemini") or "gemini",
            whisper_model=_env("SHORTS_WHISPER_MODEL", file_values, "tiny.en") or "tiny.en",
            whisper_device=whisper_device,
            whisper_compute_type=whisper_compute_type,
            models_dir=Path(_env("SHORTS_MODELS_DIR", file_values, "models") or "models"),
            output_dir=Path(_env("SHORTS_OUTPUT_DIR", file_values, "outputs") or "outputs"),
            cache_dir=Path(
                _env("SHORTS_CACHE_DIR", file_values, ".cache/shorts-clipper")
                or ".cache/shorts-clipper"
            ),
            log_level=(_env("SHORTS_LOG_LEVEL", file_values, "INFO") or "INFO").upper(),
            enable_gpu=enable_gpu,
            video_codec=video_codec,
            video_preset=video_preset,
            scout_max_age_days=scout_max_age_days,
            proxy=proxy,
            r2_account_id=_env("R2_ACCOUNT_ID", file_values),
            r2_access_key_id=_env("R2_ACCESS_KEY_ID", file_values),
            r2_secret_access_key=_env("R2_SECRET_ACCESS_KEY", file_values),
            r2_bucket_name=_env("R2_BUCKET_NAME", file_values),
            publish_platforms=publish_platforms,
        )
