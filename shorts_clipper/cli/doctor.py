"""Local environment diagnostics ("doctor" subcommand).

Prints a plain-ASCII setup checklist and returns a process exit code.
Only Python / ffmpeg / PyAV failures are critical (exit 1). Missing
API tokens, low disk space and a missing .env are printed as [WARN]
lines and never fail the run; the Whisper model dir check is reported
[MISS] when absent but that also does not affect the exit code.
"""

from __future__ import annotations

import importlib
import logging
import platform
import shutil
import sys
from pathlib import Path

from shorts_clipper.core.settings import Settings
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)

_MIN_PYTHON = (3, 11)
_MIN_FREE_GB = 8

_PLATFORM_VARS: dict[str, tuple[str, ...]] = {
    "youtube": ("YOUTUBE_API_KEY",),
    "instagram": ("IG_ACCESS_TOKEN", "IG_ACCOUNT_ID"),
    "tiktok": ("TT_CLIENT_KEY", "TT_CLIENT_SECRET", "TT_ACCESS_TOKEN"),
}


def _check(ok: bool, label: str, detail: str = "") -> None:
    status = "[OK]" if ok else "[MISS]"
    suffix = f" - {detail}" if detail else ""
    print(f"{status} {label}{suffix}")


def _warn(label: str, detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"[WARN] {label}{suffix}")


def _token_value(env_name: str, settings: Settings) -> str | None:
    attr = {
        "GEMINI_API_KEY": "gemini_api_key",
        "YOUTUBE_API_KEY": "youtube_api_key",
        "IG_ACCESS_TOKEN": "ig_access_token",
        "IG_ACCOUNT_ID": "ig_account_id",
        "TT_CLIENT_KEY": "tiktok_client_key",
        "TT_CLIENT_SECRET": "tiktok_client_secret",
        "TT_ACCESS_TOKEN": "tiktok_access_token",
    }.get(env_name)
    return getattr(settings, attr, None) if attr else None


def _token_set(env_name: str, settings: Settings) -> bool:
    if _token_value(env_name, settings):
        return True
    if env_name == "YOUTUBE_API_KEY" and (settings.cache_dir / "token.pickle").exists():
        return True
    return False


def _check_youtube_auth(settings: Settings) -> None:
    if settings.youtube_api_key:
        _check(True, "YouTube publish auth", "YOUTUBE_API_KEY set")
    elif (settings.cache_dir / "token.pickle").exists():
        _check(True, "YouTube publish auth", "OAuth token.pickle found in cache dir")
    else:
        _warn(
            "YouTube publish auth",
            "set YOUTUBE_API_KEY in .env or link the channel via the Web UI "
            "(writes token.pickle into the cache dir)",
        )


def _check_platform_keys(platform_name: str, keys: tuple[str, ...], settings: Settings) -> None:
    missing = [env for env in keys if not _token_set(env, settings)]
    if missing:
        _warn(
            f"{platform_name} publish keys",
            "missing: " + ", ".join(missing) + " (add them to .env)",
        )
    else:
        _check(True, f"{platform_name} publish keys", ", ".join(keys))


def _required_env_vars(settings: Settings) -> list[str]:
    required = ["GEMINI_API_KEY"]
    for platform_name in settings.publish_platforms:
        required.extend(_PLATFORM_VARS.get(platform_name, ()))
    return required


def _disk_free_target(path: Path) -> Path:
    if path.exists():
        return path
    return next((parent for parent in path.parents if parent.exists()), Path.cwd())


def run_doctor(settings: Settings) -> int:
    critical_fail = False

    python_ok = sys.version_info[:2] >= _MIN_PYTHON
    if not python_ok:
        critical_fail = True
    _check(
        python_ok,
        f"Python {platform.python_version()}",
        "" if python_ok else f">= {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]} required",
    )

    try:
        exe = ffmpeg_path()
        _check(True, "ffmpeg", exe)
    except Exception as exc:
        critical_fail = True
        _check(False, "ffmpeg", f"pip install imageio-ffmpeg (or set FFMPEG_PATH) - {exc}")

    try:
        av_module = importlib.import_module("av")
        _check(True, "PyAV (video metadata)", getattr(av_module, "__version__", "installed"))
    except Exception:
        critical_fail = True
        _check(False, "PyAV (video metadata)", "pip install av")

    _check(True, f"Whisper model '{settings.whisper_model}' on {settings.whisper_device}")
    models_dir = Path(settings.models_dir)
    if models_dir.exists():
        _check(True, f"Whisper model dir {models_dir}")
    else:
        _check(
            False,
            f"Whisper model dir {models_dir}",
            "does not exist yet - will download on first use",
        )

    if settings.gemini_api_key:
        _check(True, "Gemini API key (GEMINI_API_KEY)")
    else:
        _warn("Gemini API key (GEMINI_API_KEY)", "set GEMINI_API_KEY in .env")
    for platform_name in settings.publish_platforms:
        keys = _PLATFORM_VARS.get(platform_name)
        if not keys:
            continue
        if platform_name == "youtube":
            _check_youtube_auth(settings)
        else:
            _check_platform_keys(platform_name, keys, settings)

    r2_fields = (
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
        settings.r2_bucket_name,
    )
    r2_set = [value for value in r2_fields if value]
    if r2_set:
        _check(True, "R2 storage", f"{len(r2_set)}/4 fields set")
    else:
        print(
            "[INFO] R2 storage not configured - Instagram/TikTok instant publishing needs "
            "R2 for signed URLs"
        )

    usage_target = Path(settings.output_dir)
    try:
        free_bytes = shutil.disk_usage(_disk_free_target(usage_target)).free
    except OSError:
        free_bytes = None
    if free_bytes is None:
        _warn("Disk free", f"could not stat output dir {usage_target}")
    else:
        free_gb = free_bytes / (1024**3)
        label = f"Disk free {free_gb:.1f} GB on {_disk_free_target(usage_target)}"
        if free_gb >= _MIN_FREE_GB:
            _check(True, label)
        else:
            _warn(
                label,
                f"less than {_MIN_FREE_GB} GB free - point output/models dirs at another drive",
            )

    env_file = Path(".env")
    if env_file.exists():
        _check(True, f".env file {env_file}")
    else:
        _warn(f".env file {env_file}", "copy .env.example to .env and fill in values")

    required_vars = _required_env_vars(settings)
    missing_vars = [env for env in required_vars if not _token_set(env, settings)]
    if missing_vars:
        _warn(
            f"Required env vars ({len(required_vars) - len(missing_vars)}/{len(required_vars)} set)",
            "missing: " + ", ".join(missing_vars),
        )
    else:
        _check(True, f"Required env vars ({len(required_vars)}/{len(required_vars)} set)")

    return 1 if critical_fail else 0