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
    tiktok_client_key: str | None = None
    tiktok_client_secret: str | None = None
    tiktok_access_token: str | None = None
    tiktok_open_id: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    default_provider: str = "gemini"
    whisper_model: str = "tiny.en"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str | None = None
    models_dir: Path = Path("models")
    output_dir: Path = Path("outputs")
    cache_dir: Path = Path(".cache/shorts-clipper")
    archive_dir: str = "outputs/archive"
    clip_retention_days: int = 30
    max_keep_clips: int = 200
    publish_at: str | None = None
    publish_interval_seconds: int = 30
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
    affiliate_partners_path: str = "affiliate_partners.json"
    affiliate_enabled: bool = False
    affiliate_banner_position: str = "bottom_left"
    affiliate_ad_card: bool = False
    affiliate_ad_start_fraction: float = 0.45
    affiliate_ad_duration_sec: float = 4.0
    affiliate_cta_text: str = ""
    subtitle_langs: list[str] = field(default_factory=lambda: ["ru", "en"])
    stream_audio_energy_enabled: bool = True
    stream_energy_window_seconds: float = 1.0
    stream_energy_threshold: float = 0.15
    gameplay_mode: bool = False
    gameplay_scan_max_seconds: int = 3600
    gameplay_top_windows: int = 5
    gameplay_min_length: float = 12.0
    gameplay_max_length: float = 60.0
    bgm_mode: str = "off"
    music_dir: Path = Path("D:/shorts_music")
    bgm_volume: float = 0.30
    hook_judge_enabled: bool = False
    hook_min_score: float = 0.5
    compliance_enabled: bool = True
    compliance_llm: bool = True
    compliance_finance_strict: bool = False
    compliance_auto_disclaimers: bool = True
    compliance_report_dir: Path = Path("outputs/compliance")
    output_aspect: str = "vertical"

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
        if scout_max_age_days < 0:
            scout_max_age_days = 90
        try:
            clip_retention_days = int(
                _env("SHORTS_CLIP_RETENTION_DAYS", file_values, "30") or "30"
            )
        except ValueError:
            clip_retention_days = 30
        if clip_retention_days < 0:
            clip_retention_days = 30
        try:
            max_keep_clips = int(_env("SHORTS_MAX_KEEP_CLIPS", file_values, "200") or "200")
        except ValueError:
            max_keep_clips = 200
        if max_keep_clips < 0:
            max_keep_clips = 200
        try:
            publish_interval_seconds = int(
                _env("SHORTS_PUBLISH_INTERVAL", file_values, "30") or "30"
            )
        except ValueError:
            publish_interval_seconds = 30
        if publish_interval_seconds < 0:
            publish_interval_seconds = 30
        proxy = _env("SHORTS_PROXY", file_values)

        platforms_raw = (
            _env("SHORTS_PUBLISH_PLATFORMS", file_values, "youtube,instagram")
            or "youtube,instagram"
        )
        publish_platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]

        affiliate_enabled = (_env("AFFILIATE_ENABLED", file_values, "false") or "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        affiliate_banner_position = (
            _env("AFFILIATE_BANNER_POSITION", file_values, "bottom_left") or "bottom_left"
        ).strip().lower()
        if affiliate_banner_position not in {
            "bottom_left",
            "bottom_right",
            "top_left",
            "top_right",
        }:
            affiliate_banner_position = "bottom_left"

        affiliate_ad_card = (
            _env("SHORTS_AFFILIATE_AD_CARD", file_values, "false") or "false"
        ).lower() in {"1", "true", "yes", "on"}

        try:
            affiliate_ad_start_fraction = float(
                _env("SHORTS_AFFILIATE_AD_START_FRACTION", file_values, "0.45") or "0.45"
            )
        except ValueError:
            affiliate_ad_start_fraction = 0.45
        if not 0.0 <= affiliate_ad_start_fraction <= 1.0:
            affiliate_ad_start_fraction = 0.45

        try:
            affiliate_ad_duration_sec = float(
                _env("SHORTS_AFFILIATE_AD_DURATION_SEC", file_values, "4.0") or "4.0"
            )
        except ValueError:
            affiliate_ad_duration_sec = 4.0
        if affiliate_ad_duration_sec < 0:
            affiliate_ad_duration_sec = 4.0

        affiliate_cta_text = _env("SHORTS_AFFILIATE_CTA_TEXT", file_values, "") or ""

        subtitle_langs_raw = _env("SHORTS_SUBTITLE_LANGS", file_values, "ru,en") or "ru,en"
        subtitle_langs = [p.strip() for p in subtitle_langs_raw.split(",") if p.strip()]

        stream_audio_energy_enabled = (
            _env("SHORTS_STREAM_AUDIO_ENERGY", file_values, "true") or "true"
        ).lower() in {"1", "true", "yes", "on"}

        hook_judge_enabled = (
            _env("SHORTS_HOOK_JUDGE_ENABLED", file_values, "false") or "false"
        ).lower() in {"1", "true", "yes", "on"}

        try:
            hook_min_score = float(
                _env("SHORTS_HOOK_MIN_SCORE", file_values, "0.5") or "0.5"
            )
        except ValueError:
            hook_min_score = 0.5

        try:
            stream_energy_window_seconds = float(
                _env("SHORTS_STREAM_ENERGY_WINDOW", file_values, "1.0") or "1.0"
            )
        except ValueError:
            stream_energy_window_seconds = 1.0

        try:
            stream_energy_threshold = float(
                _env("SHORTS_STREAM_ENERGY_THRESHOLD", file_values, "0.15") or "0.15"
            )
        except ValueError:
            stream_energy_threshold = 0.15

        compliance_enabled = (
            _env("SHORTS_COMPLIANCE_ENABLED", file_values, "true") or "true"
        ).lower() in {"1", "true", "yes", "on"}

        compliance_llm = (
            _env("SHORTS_COMPLIANCE_LLM", file_values, "true") or "true"
        ).lower() in {"1", "true", "yes", "on"}

        compliance_finance_strict = (
            _env("SHORTS_COMPLIANCE_FINANCE_STRICT", file_values, "false") or "false"
        ).lower() in {"1", "true", "yes", "on"}

        compliance_auto_disclaimers = (
            _env("SHORTS_COMPLIANCE_AUTO_DISCLAIMERS", file_values, "true") or "true"
        ).lower() in {"1", "true", "yes", "on"}

        compliance_report_dir = Path(
            _env("SHORTS_COMPLIANCE_REPORT_DIR", file_values, "outputs/compliance")
            or "outputs/compliance"
        )

        output_aspect = (
            _env("SHORTS_OUTPUT_ASPECT", file_values, "vertical") or "vertical"
        ).lower()
        if output_aspect not in {"vertical", "wide", "both"}:
            output_aspect = "vertical"

        gameplay_mode = (
            _env("SHORTS_GAMEPLAY_MODE", file_values, "false") or "false"
        ).lower() in {"1", "true", "yes", "on"}

        try:
            gameplay_scan_max_seconds = int(
                _env("SHORTS_GAMEPLAY_SCAN_MAX_SECONDS", file_values, "3600") or "3600"
            )
        except ValueError:
            gameplay_scan_max_seconds = 3600
        if gameplay_scan_max_seconds < 0:
            gameplay_scan_max_seconds = 3600

        try:
            gameplay_top_windows = int(
                _env("SHORTS_GAMEPLAY_TOP_WINDOWS", file_values, "5") or "5"
            )
        except ValueError:
            gameplay_top_windows = 5
        if gameplay_top_windows < 0:
            gameplay_top_windows = 5

        try:
            gameplay_min_length = float(
                _env("SHORTS_GAMEPLAY_MIN_LENGTH", file_values, "12.0") or "12.0"
            )
        except ValueError:
            gameplay_min_length = 12.0

        try:
            gameplay_max_length = float(
                _env("SHORTS_GAMEPLAY_MAX_LENGTH", file_values, "60.0") or "60.0"
            )
        except ValueError:
            gameplay_max_length = 60.0

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
            tiktok_client_key=_env("TT_CLIENT_KEY", file_values),
            tiktok_client_secret=_env("TT_CLIENT_SECRET", file_values),
            tiktok_access_token=_env("TT_ACCESS_TOKEN", file_values),
            tiktok_open_id=_env("TT_OPEN_ID", file_values),
            ollama_base_url=_env("OLLAMA_BASE_URL", file_values, "http://localhost:11434")
            or "http://localhost:11434",
            default_provider=_env("SHORTS_PROVIDER", file_values, "gemini") or "gemini",
            whisper_model=_env("SHORTS_WHISPER_MODEL", file_values, "tiny.en") or "tiny.en",
            whisper_device=whisper_device,
            whisper_compute_type=whisper_compute_type,
            whisper_language=_env("SHORTS_WHISPER_LANGUAGE", file_values),
            models_dir=Path(_env("SHORTS_MODELS_DIR", file_values, "models") or "models"),
            output_dir=Path(_env("SHORTS_OUTPUT_DIR", file_values, "outputs") or "outputs"),
            cache_dir=Path(
                _env("SHORTS_CACHE_DIR", file_values, ".cache/shorts-clipper")
                or ".cache/shorts-clipper"
            ),
            archive_dir=_env("SHORTS_PUBLISHED_ARCHIVE", file_values, "outputs/archive")
            or "outputs/archive",
            clip_retention_days=clip_retention_days,
            max_keep_clips=max_keep_clips,
            publish_at=_env("SHORTS_PUBLISH_AT", file_values),
            publish_interval_seconds=publish_interval_seconds,
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
            affiliate_partners_path=_env(
                "AFFILIATE_PARTNERS_PATH", file_values, "affiliate_partners.json"
            )
            or "affiliate_partners.json",
            affiliate_enabled=affiliate_enabled,
            affiliate_banner_position=affiliate_banner_position,
            affiliate_ad_card=affiliate_ad_card,
            affiliate_ad_start_fraction=affiliate_ad_start_fraction,
            affiliate_ad_duration_sec=affiliate_ad_duration_sec,
            affiliate_cta_text=affiliate_cta_text,
            subtitle_langs=subtitle_langs,
            stream_audio_energy_enabled=stream_audio_energy_enabled,
            stream_energy_window_seconds=stream_energy_window_seconds,
            stream_energy_threshold=stream_energy_threshold,
            gameplay_mode=gameplay_mode,
            gameplay_scan_max_seconds=gameplay_scan_max_seconds,
            gameplay_top_windows=gameplay_top_windows,
            gameplay_min_length=gameplay_min_length,
            gameplay_max_length=gameplay_max_length,
            bgm_mode=(_env("SHORTS_BGM_MODE", file_values, "off") or "off").lower(),
            music_dir=Path(
                _env("SHORTS_MUSIC_DIR", file_values, "D:/shorts_music") or "D:/shorts_music"
            ),
            bgm_volume=float(
                _env("SHORTS_BGM_VOLUME", file_values, "0.30") or "0.30"
            ),
            hook_judge_enabled=hook_judge_enabled,
            hook_min_score=hook_min_score,
            compliance_enabled=compliance_enabled,
            compliance_llm=compliance_llm,
            compliance_finance_strict=compliance_finance_strict,
            compliance_auto_disclaimers=compliance_auto_disclaimers,
            compliance_report_dir=compliance_report_dir,
            output_aspect=output_aspect,
        )
