"""FastAPI production web server for Vanguard Clipper Console.

Integrates the SQLite job queue for persistent job tracking,
performance feedback, and SSE live log streaming.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import tempfile
import threading
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shorts_clipper.analyze.feedback import ClipFeedback, FeedbackStore
from shorts_clipper.core.queue import JobQueue, JobStatus
from shorts_clipper.core.settings import Settings
from shorts_clipper.downloader.yt_dlp import download_audio, fetch_subtitles
from shorts_clipper.providers.gemini import GeminiProvider
from shorts_clipper.transcription.whisper import transcribe_clip

# ---------------------------------------------------------------------------
# App & Logging Setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Vanguard Clipper Server",
    description="SaaS-grade automation backend for shorts-clipper.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

log_queue: queue.Queue[str] = queue.Queue(maxsize=2000)


class SSELogHandler(logging.Handler):
    """Custom logging handler to stream logging entries via SSE."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            log_queue.put_nowait(msg)
        except Exception:
            pass


# Attach SSE logging handler to shorts_clipper logger
logger = logging.getLogger("shorts_clipper")
sse_handler = SSELogHandler()
sse_formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
sse_handler.setFormatter(sse_formatter)
logger.addHandler(sse_handler)

# ContextVar to track the job ID for the current background task thread
active_job_id: ContextVar[str | None] = ContextVar("active_job_id", default=None)

# Registry mapping job_id -> list of active subprocess Popen objects
_proc_lock = threading.Lock()
_active_processes: dict[str, list[subprocess.Popen]] = {}

# Intercept subprocess.Popen creation to automatically register processes for active jobs
_original_popen_init = subprocess.Popen.__init__


def _tracked_popen_init(self, *args, **kwargs):
    _original_popen_init(self, *args, **kwargs)
    job_id = active_job_id.get()
    if job_id:
        with _proc_lock:
            if job_id not in _active_processes:
                _active_processes[job_id] = []
            _active_processes[job_id].append(self)
            logger.info("Registered subprocess PID %s for active job %s", self.pid, job_id)


subprocess.Popen.__init__ = _tracked_popen_init


def ensure_worker_running() -> None:
    import sys
    import time

    heartbeat_path = Path("outputs/worker_heartbeat.txt")
    worker_running = False
    if heartbeat_path.exists():
        try:
            last_heartbeat = float(heartbeat_path.read_text(encoding="utf-8").strip())
            if time.time() - last_heartbeat < 30.0:
                worker_running = True
        except Exception:
            pass

    if not worker_running:
        logger.info("⚠️  Background worker not running. Spawning new worker process...")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "shorts_clipper.core.worker"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            logger.info("✅ Worker process spawned successfully.")
        except Exception as e:
            logger.error("Failed to spawn worker process: %s", e)


@app.on_event("startup")
def startup_event():
    import json
    from pathlib import Path

    from shorts_clipper.core.logging import configure_logging

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    ensure_worker_running()

    # Clear old logs on startup so the web terminal is clean
    log_file = Path("outputs/app.log")
    if log_file.exists():
        try:
            log_file.write_text("")
        except Exception:
            pass

    # Reset any stuck "uploading" statuses from previous runs
    out_dir = Path(settings.output_dir)
    if out_dir.exists():
        for json_path in out_dir.glob("*.json"):
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(meta, dict) and meta.get("publish_status") == "uploading":
                    meta["publish_status"] = "failed"
                    meta["publish_error"] = "Upload interrupted by server restart."
                    json_path.write_text(
                        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
            except Exception as e:
                logger.error("Failed to reset stuck uploading status for %s: %s", json_path, e)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": str(exc)},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": str(exc.detail)},
    )


def cancel_job(job_id: str) -> None:
    """Cancel a job by updating its status in the DB. The worker polls this status and cancels its subprocesses."""
    _job_queue.update_status(job_id, JobStatus.CANCELLED, error="Job cancelled by user.")
    logger.info("Job %s cancelled in SQLite DB; worker will handle termination.", job_id)


# Singletons — initialized once at import
_job_queue = JobQueue()
_feedback_store = FeedbackStore()

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class JobResponse(BaseModel):
    id: str
    kind: str
    status: str
    progress: int
    error: str
    created_at: float
    updated_at: float
    output_paths: list[str]


class StatusResponse(BaseModel):
    status: str
    message: str | None = None
    job_id: str | None = None


class GeminiChatRequest(BaseModel):
    prompt: str
    context: str
    history: list[dict[str, str]] = []


class WatchdogConfigRequest(BaseModel):
    enabled: bool
    channels: list[dict[str, Any]]


class AutopilotRequest(BaseModel):
    niche: str | None = None
    keyword: str | None = None
    channel: str | None = None
    count: int = Field(default=3, ge=1, le=10)
    upload: bool = False
    privacy: str = "private"  # "private" | "public" | "unlisted"
    scout_duration: str | None = "all"  # "all" | "today" | "week" | "month"


class CustomClipRequest(BaseModel):
    url: str
    count: int = Field(default=1, ge=1, le=10)
    upload: bool = False
    privacy: str = "private"  # "private" | "public" | "unlisted"


class TranscriptRequest(BaseModel):
    url: str


class SegmentModel(BaseModel):
    start: float
    end: float
    text: str


class HighlightsRequest(BaseModel):
    segments: list[SegmentModel]
    count: int = Field(default=3, ge=1, le=10)


class CustomClipRenderRequest(BaseModel):
    url: str
    start: float
    end: float
    layout: str = "crop_center"
    upload: bool = False
    privacy: str = "private"  # "private" | "public" | "unlisted"


class ClipMetadataUpdate(BaseModel):
    title: str
    description: str | None = None
    tags: list[str] | None = None


class SettingsModel(BaseModel):
    gemini_api_key: str | None = None
    whisper_model: str = "tiny.en"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    video_codec: str = "libx264"
    video_preset: str = "ultrafast"
    scout_max_age_days: int = 90
    enable_gpu: bool = False
    subtitle_style: str = "default"


class FeedbackModel(BaseModel):
    clip_name: str
    views: int = 0
    likes: int = 0
    shares: int = 0
    comments: int = 0
    watch_time_avg: float = 0.0
    retention_pct: float = 0.0
    notes: str = ""


# ---------------------------------------------------------------------------
# API Routes — Jobs
# ---------------------------------------------------------------------------


@app.get("/api/scout/metrics")
async def api_scout_metrics() -> list[dict]:
    import json
    from pathlib import Path

    metrics_dir = Path("outputs")
    if not metrics_dir.exists():
        return []

    results = []
    for mf in metrics_dir.glob("scout_metrics_*.json"):
        try:
            data = json.loads(mf.read_text())
            results.append(data)
        except Exception:
            continue

    # Sort by started_at timestamp, descending (newest first)
    results.sort(key=lambda x: x.get("started_at", 0), reverse=True)
    return results


@app.get("/api/jobs")
def list_jobs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List all jobs, optionally filtered by status."""
    if status:
        try:
            st = JobStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}") from exc
        jobs = _job_queue.list_by_status(st, limit=limit)
    else:
        jobs = _job_queue.list_all(limit=limit)
    return [j.to_dict() for j in jobs]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    """Get a specific job by ID."""
    job = _job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, str]:
    """Delete a job from the queue."""
    if _job_queue.delete(job_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Job not found")


@app.post("/api/jobs/{job_id}/cancel")
def cancel_active_job(job_id: str) -> dict[str, str]:
    """Cancel an active job and terminate its subprocesses."""
    job = _job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        return {
            "status": "ignored",
            "message": f"Job is in '{job.status}' state, cannot cancel.",
        }

    cancel_job(job_id)
    return {"status": "cancelled", "message": f"Job {job_id} cancellation triggered."}


# ---------------------------------------------------------------------------
# API Routes — Clips
# ---------------------------------------------------------------------------


@app.get("/api/clips")
def list_clips() -> list[dict[str, Any]]:
    """Scan outputs directory and return generated vertical video clips with sidecar metadata."""
    settings = Settings.from_env()
    out_dir = Path(settings.output_dir)
    if not out_dir.exists():
        out_dir.mkdir(parents=True, exist_ok=True)

    clips = []
    import re

    for path in sorted(
        out_dir.rglob("rendered_clip*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if "ig_hosted" in path.parts:
            continue

        if path.name == "rendered_clip.mp4" and path.with_name("rendered_clip_1.mp4").exists():
            continue

        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()

        # Check for thumbnail
        thumb = path.with_suffix(".jpg")
        match = re.match(r"rendered_clip_(\d+)\.mp4", path.name)
        if match:
            alt_thumb = path.with_name(f"thumbnail_{match.group(1)}.jpg")
            legacy_thumb = path.with_name("rendered_clip.jpg")
            if alt_thumb.exists():
                thumb = alt_thumb
            elif legacy_thumb.exists():
                thumb = legacy_thumb

        # Load or generate sidecar metadata
        json_path = path.with_suffix(".json")
        if match:
            alt_json = path.with_name(f"final_metadata_{match.group(1)}.json")
            if alt_json.exists():
                json_path = alt_json

        meta = {}
        if json_path.exists():
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if not meta:
            # Fallback initialization
            meta = {
                "title": path.name.replace(".mp4", "").replace("_", " ").title(),
                "description": "",
                "tags": [],
                "publish_status": "idle",
                "publish_error": "Metadata sidecar missing — title auto-generated from filename. Use AI Title Generator to create proper metadata.",
            }
            try:
                json_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass

        rel_path = path.relative_to(out_dir).as_posix()
        rel_thumb = thumb.relative_to(out_dir).as_posix()
        clips.append(
            {
                "name": rel_path,
                "path": f"/clips/{rel_path}",
                "thumbnail": f"/clips/{rel_thumb}" if thumb.exists() else None,
                "size": stat.st_size,
                "created_at": mtime,
                "metadata": meta,
            }
        )
    return clips


@app.delete("/api/clips/{clip_name:path}")
def delete_clip(clip_name: str) -> dict[str, str]:
    settings = Settings.from_env()
    path = Path(settings.output_dir) / clip_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")
    path.unlink()

    thumb = path.with_suffix(".jpg")
    if thumb.exists():
        thumb.unlink()

    json_path = path.with_suffix(".json")
    if json_path.exists():
        try:
            json_path.unlink()
        except Exception:
            pass

    return {"status": "deleted"}


@app.post("/api/clips/{clip_name:path}/metadata")
def update_clip_metadata(clip_name: str, payload: ClipMetadataUpdate) -> dict[str, str]:
    """Update sidecar metadata for a clip."""
    settings = Settings.from_env()
    path = Path(settings.output_dir) / clip_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")

    json_path = path.with_suffix(".json")

    meta = {}
    if json_path.exists():
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    meta["title"] = payload.title
    if payload.description is not None:
        meta["description"] = payload.description
    if payload.tags is not None:
        meta["tags"] = payload.tags

    try:
        json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"status": "success", "message": "Clip metadata updated successfully."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/clips/{clip_name:path}/publish")
def publish_clip(
    clip_name: str,
    background_tasks: BackgroundTasks,
    privacy: str = "private",
) -> dict[str, str]:
    settings = Settings.from_env()
    path = Path(settings.output_dir) / clip_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")

    json_path = path.with_suffix(".json")
    import re

    match = re.match(r"rendered_clip_(\d+)\.mp4", path.name)
    if match:
        alt_json = path.with_name(f"final_metadata_{match.group(1)}.json")
        if alt_json.exists():
            json_path = alt_json

    meta = {}
    if json_path.exists():
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    title = (meta.get("title") or "").strip()
    desc = (meta.get("description") or "").strip()
    tags = meta.get("tags", ["shorts"])

    if meta.get("publish_status") == "uploading":
        raise HTTPException(
            status_code=400, detail="Publishing is already in progress for this clip."
        )

    if not title:
        title = path.stem.replace("_", " ").title()

    if not desc:
        desc = "Auto-generated clip from Shorts Clipper. #shorts #trending"

    def _upload() -> None:
        try:
            from shorts_clipper.publishers.manager import PublishingEngine
            from shorts_clipper.publishers.models import ClipMetadata

            meta["publish_status"] = "uploading"
            meta["publish_progress"] = 0
            meta["publish_error"] = None
            try:
                json_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass

            clip_meta = ClipMetadata(
                title=title,
                description=desc,
                tags=tags,
                privacy_status=privacy,
                language="en",
            )
            engine = PublishingEngine()

            # Only target platforms that haven't succeeded yet
            target_platforms = []
            for p in settings.publish_platforms:
                if p == "youtube" and meta.get("youtube_video_id"):
                    continue
                if p == "instagram" and meta.get("instagram_video_id"):
                    continue
                target_platforms.append(p)

            if not target_platforms:
                meta["publish_status"] = "success"
                meta["publish_progress"] = 100
                meta["publish_error"] = None
                json_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                return

            results = engine.publish(path, clip_meta, target_platforms)

            meta["publish_progress"] = 100

            # Always save IDs for successful platforms
            if "youtube" in results and results["youtube"].success:
                meta["youtube_video_id"] = results["youtube"].platform_id
            if "instagram" in results and results["instagram"].success:
                meta["instagram_video_id"] = results["instagram"].platform_id

            # Check if all originally requested platforms are now successful
            all_successful = True
            for p in settings.publish_platforms:
                if p == "youtube" and not meta.get("youtube_video_id"):
                    all_successful = False
                if p == "instagram" and not meta.get("instagram_video_id"):
                    all_successful = False

            any_successful = False
            if meta.get("youtube_video_id") or meta.get("instagram_video_id"):
                any_successful = True

            if all_successful:
                meta["publish_status"] = "success"
                meta["publish_error"] = None
                logger.info("✅ Clip %s uploaded successfully to requested platforms!", clip_name)
            elif any_successful:
                meta["publish_status"] = "partial_success"
                errors = "; ".join(
                    [f"{p}: {r.error_message}" for p, r in results.items() if not r.success]
                )
                meta["publish_error"] = errors
                logger.warning("⚠️ Clip %s uploaded partially. Errors: %s", clip_name, errors)
            else:
                meta["publish_status"] = "failed"
                errors = "; ".join(
                    [f"{p}: {r.error_message}" for p, r in results.items() if not r.success]
                )
                meta["publish_error"] = errors
                logger.error("❌ Failed to upload clip %s completely: %s", clip_name, errors)

            try:
                json_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass

        except Exception as e:
            meta["publish_status"] = "failed"
            meta["publish_error"] = str(e)
            try:
                json_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass
            logger.error("❌ Failed to upload clip %s: %s", clip_name, e)

    background_tasks.add_task(_upload)
    return {
        "status": "started",
        "message": f"Publishing {clip_name} to {', '.join(settings.publish_platforms)}...",
    }


@app.post("/api/clips/{clip_name:path}/autogen-title")
def autogen_clip_title(clip_name: str) -> dict[str, Any]:
    """Transcribe clip (if missing segments) and call Gemini to generate viral titles & hashtags."""
    settings = Settings.from_env()
    path = Path(settings.output_dir) / clip_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")

    json_path = path.with_suffix(".json")

    meta = {}
    if json_path.exists():
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    segments_raw = meta.get("segments", [])
    if not segments_raw:
        try:
            from shorts_clipper.transcription.whisper import transcribe_clip

            logger.info("Transcribing clip %s for AI title generation...", clip_name)
            precision_segments = transcribe_clip(path)
            segments_raw = [
                {"start": s.start, "end": s.end, "text": s.text} for s in precision_segments
            ]
        except Exception as trans_err:
            logger.warning("Fast transcription fallback failed: %s", trans_err)

    if not segments_raw:
        raise HTTPException(
            status_code=400,
            detail="No transcript segments found and transcription failed.",
        )

    from shorts_clipper.core.models import TranscriptSegment

    segments = [
        TranscriptSegment(start=s["start"], end=s["end"], text=s["text"]) for s in segments_raw
    ]

    try:
        provider = GeminiProvider(api_key=settings.gemini_api_key)
        ai_meta = provider.generate_clip_metadata(segments)

        meta["title"] = ai_meta["title"]
        meta["description"] = ai_meta["description"]
        meta["tags"] = ai_meta["tags"]
        meta["segments"] = segments_raw

        json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"status": "success", "title": ai_meta["title"], "metadata": meta}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


_status_cache = {}


@app.get("/api/youtube/status")
def get_youtube_status() -> dict[str, Any]:
    """Check connection status for YouTube."""
    now = time.time()
    if "youtube" in _status_cache and now - _status_cache["youtube"]["time"] < 60:
        return _status_cache["youtube"]["data"]

    try:
        from google.auth.exceptions import DefaultCredentialsError, RefreshError

        from shorts_clipper.publishers.youtube.auth import get_youtube_service

        youtube = get_youtube_service()
        res = youtube.channels().list(part="snippet,statistics", mine=True).execute()
        if res.get("items"):
            item = res["items"][0]
            snippet = item["snippet"]
            stats = item["statistics"]
            data = {
                "connected": True,
                "channel_name": snippet.get("title", "YouTube Channel"),
                "channel_id": item.get("id"),
                "avatar_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "subscriber_count": stats.get("subscriberCount", "0"),
                "message": "YouTube connected successfully.",
            }
            _status_cache["youtube"] = {"time": time.time(), "data": data}
            return data

    except (DefaultCredentialsError, RefreshError, ValueError, RuntimeError) as e:
        logger.info("YouTube not connected: %s", e)
        # Clear cache if disconnected
        if "youtube" in _status_cache:
            del _status_cache["youtube"]
        return {
            "connected": False,
            "channel_name": None,
            "message": "No YouTube account linked or token expired.",
        }
    except Exception as e:
        logger.warning("YouTube status error: %s", e)

        # Fallback to last known good data if available
        if "youtube" in _status_cache:
            return _status_cache["youtube"]["data"]

        res_data = {
            "connected": True,
            "channel_name": "YouTube Channel",
            "channel_id": None,
            "avatar_url": None,
            "subscriber_count": "0",
            "message": "Connected but couldn't fetch live data.",
        }
        return res_data


@app.get("/api/instagram/status")
def get_instagram_status() -> dict[str, Any]:
    """Check connection status for Instagram Graph API based on .env credentials."""
    now = time.time()
    if "instagram_error" in _status_cache and now - _status_cache["instagram_error"] < 30:
        return _status_cache.get("instagram", {}).get(
            "data",
            {
                "connected": True,
                "channel_name": "Instagram Pro",
                "message": "Connected but API is degraded.",
            },
        )
    if "instagram" in _status_cache and now - _status_cache["instagram"]["time"] < 60:
        return _status_cache["instagram"]["data"]

    import requests

    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()
    token = settings.ig_access_token
    acc_id = settings.ig_account_id

    if token and acc_id:
        try:
            url = f"https://graph.instagram.com/v19.0/{acc_id}?fields=username,profile_picture_url,followers_count"
            headers = {"Authorization": f"Bearer {token}"}
            res = requests.get(url, headers=headers, timeout=5)
            data = res.json()

            if "error" not in data:
                res_data = {
                    "connected": True,
                    "channel_name": data.get("username", "Instagram Pro"),
                    "channel_id": acc_id,
                    "avatar_url": data.get("profile_picture_url"),
                    "subscriber_count": str(data.get("followers_count", 0)),
                    "message": "Instagram connected successfully.",
                }
                _status_cache["instagram"] = {"time": time.time(), "data": res_data}
                return res_data
            else:
                logger.warning("Instagram Graph API error: %s", data["error"])
                _status_cache["instagram_error"] = time.time()
        except Exception as e:
            logger.warning("Instagram fetch error: %s", type(e).__name__)
            _status_cache["instagram_error"] = time.time()

        # Fallback to last known good data if available
        if "instagram" in _status_cache:
            return _status_cache["instagram"]["data"]

        res_data = {
            "connected": True,
            "channel_name": "Instagram Pro",
            "channel_id": acc_id,
            "avatar_url": None,
            "subscriber_count": "0",
            "message": "Connected but couldn't fetch live data.",
        }
        # Cache the fallback data for 30 seconds (60 - 30) to prevent thundering herds on timeout
        _status_cache["instagram"] = {"time": time.time() - 30, "data": res_data}
        return res_data

    return {
        "connected": False,
        "channel_name": None,
        "message": "No Instagram account linked in .env",
    }


def _get_oauth_redirect_uri(request: Request) -> str:
    """Build the OAuth redirect URI reliably across local dev and cloud proxies."""
    import os

    # 1. Explicit BASE_URL env var — most reliable for cloud deployments
    base_url = os.environ.get("BASE_URL", "").rstrip("/")
    if base_url:
        return f"{base_url}/api/youtube/callback"

    # 2. SPACE_HOST env var set automatically by Hugging Face Spaces
    space_host = os.environ.get("SPACE_HOST")
    if space_host:
        return f"https://{space_host}/api/youtube/callback"

    # 3. Proxy headers
    x_forwarded_host = request.headers.get("x-forwarded-host")
    if x_forwarded_host:
        proto = request.headers.get("x-forwarded-proto", "https")
        return f"{proto}://{x_forwarded_host}/api/youtube/callback"

    # 4. Browser origin header or request URL
    origin = request.headers.get("origin") or f"{request.url.scheme}://{request.url.netloc}"
    if "localhost" not in origin and "127.0.0.1" not in origin:
        origin = origin.replace("http://", "https://")
    return f"{origin}/api/youtube/callback"


@app.get("/api/youtube/connect")
def connect_youtube(request: Request) -> dict[str, str]:
    """Generate dynamic Google OAuth URL for the user's browser."""
    import os

    env_secret = os.environ.get("YOUTUBE_CLIENT_SECRET_JSON")
    file_exists = Path("client_secret.json").exists()

    if not env_secret and not file_exists:
        raise HTTPException(
            status_code=400,
            detail="Missing client_secret.json file in project root or YOUTUBE_CLIENT_SECRET_JSON env secret. Please follow the YouTube API Setup guide in the README.",
        )

    from google_auth_oauthlib.flow import Flow

    redirect_uri = _get_oauth_redirect_uri(request)
    logger.info("OAuth redirect_uri resolved to: %s", redirect_uri)

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    try:
        if env_secret:
            try:
                client_config = json.loads(env_secret)
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to parse YOUTUBE_CLIENT_SECRET_JSON env: {e}",
                ) from e
            flow = Flow.from_client_config(
                client_config,
                scopes=SCOPES,
                redirect_uri=redirect_uri,
                autogenerate_code_verifier=False,
            )
        else:
            flow = Flow.from_client_secrets_file(
                "client_secret.json",
                scopes=SCOPES,
                redirect_uri=redirect_uri,
                autogenerate_code_verifier=False,
            )
        auth_url, _ = flow.authorization_url(
            prompt="consent", access_type="offline", include_granted_scopes="true"
        )
        return {"auth_url": auth_url}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate OAuth URL: {exc}") from exc


@app.get("/api/youtube/callback", response_class=HTMLResponse)
def youtube_callback(request: Request, code: str, state: str | None = None) -> HTMLResponse:
    """Exchange OAuth code for credentials token and save it to pickle."""
    import os
    import pickle

    from google_auth_oauthlib.flow import Flow

    env_secret = os.environ.get("YOUTUBE_CLIENT_SECRET_JSON")
    file_exists = Path("client_secret.json").exists()

    if not env_secret and not file_exists:
        return HTMLResponse(
            content="<h3>Error: Missing client_secret.json or YOUTUBE_CLIENT_SECRET_JSON env secret.</h3>",
            status_code=400,
        )

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    redirect_uri = _get_oauth_redirect_uri(request)
    logger.info("OAuth callback redirect_uri resolved to: %s", redirect_uri)

    try:
        if env_secret:
            try:
                client_config = json.loads(env_secret)
            except Exception as e:
                return HTMLResponse(
                    content=f"<h3>Error: Failed to parse YOUTUBE_CLIENT_SECRET_JSON env: {e}</h3>",
                    status_code=400,
                )
            flow = Flow.from_client_config(
                client_config,
                scopes=SCOPES,
                redirect_uri=redirect_uri,
                autogenerate_code_verifier=False,
            )
        else:
            flow = Flow.from_client_secrets_file(
                "client_secret.json",
                scopes=SCOPES,
                redirect_uri=redirect_uri,
                autogenerate_code_verifier=False,
            )
        flow.fetch_token(code=code)
        creds = flow.credentials

        token_path = Path(".cache/shorts-clipper/token.pickle")
        token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "wb") as token:
            pickle.dump(creds, token)

        if "youtube" in _status_cache:
            del _status_cache["youtube"]

        return HTMLResponse(
            content="""
            <html>
                <head>
                    <title>YouTube Linked Successfully</title>
                    <style>
                        body {
                            background: #0b0d19;
                            color: #f3f4f6;
                            font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                            display: flex;
                            flex-direction: column;
                            align-items: center;
                            justify-content: center;
                            height: 100vh;
                            margin: 0;
                            text-align: center;
                        }
                        .card {
                            background: rgba(19, 23, 42, 0.6);
                            border: 1px solid #c9a84c;
                            box-shadow: 0 0 25px rgba(201, 168, 76, 0.2);
                            border-radius: 12px;
                            padding: 3rem;
                            max-width: 450px;
                        }
                        h1 {
                            color: #c9a84c;
                            margin-top: 0;
                        }
                        p {
                            color: #9ca3af;
                            font-size: 1.05rem;
                        }
                        .btn-close {
                            background: linear-gradient(135deg, #c9a84c, #f472b6);
                            border: none;
                            color: #0b0d19;
                            padding: 10px 20px;
                            border-radius: 6px;
                            font-weight: bold;
                            cursor: pointer;
                            margin-top: 1.5rem;
                            text-transform: uppercase;
                            letter-spacing: 0.05em;
                        }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <div style="font-size: 3rem; margin-bottom: 1rem;">✅</div>
                        <h1>YouTube Linked!</h1>
                        <p>Your YouTube Shorts channel has been authenticated successfully.</p>
                        <p style="font-size: 0.9rem; color: #6b7280;">You can now close this browser tab and return to the Shorts Clipper Console.</p>
                        <button class="btn-close" onclick="window.close()">Close Window</button>
                    </div>
                </body>
            </html>
            """
        )
    except Exception as e:
        logger.error("OAuth token exchange failed: %s", e)
        return HTMLResponse(
            content=f"""
            <html>
                <body style="background: #0b0d19; color: #ef4444; font-family: sans-serif; text-align:center; padding-top: 100px;">
                    <h2>❌ Authentication Failed</h2>
                    <p>{e}</p>
                    <p style="color: #6b7280;">Make sure your Client Secret matches the redirect callback URI exactly.</p>
                    <p><a href="/" style="color: #c9a84c;">Back to Dashboard</a></p>
                </body>
            </html>
            """,
            status_code=500,
        )


@app.post("/api/youtube/disconnect")
def disconnect_youtube() -> dict[str, Any]:
    """Disconnect the YouTube account by deleting the cached token."""
    token_path = Path(".cache/shorts-clipper/token.pickle")
    if token_path.exists():
        try:
            token_path.unlink()
            if "youtube" in _status_cache:
                del _status_cache["youtube"]
            return {
                "success": True,
                "message": "YouTube account disconnected successfully.",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete token: {e}") from e
    if "youtube" in _status_cache:
        del _status_cache["youtube"]
    return {"success": True, "message": "YouTube account was already disconnected."}


@app.get("/api/settings", response_model=SettingsModel)
def get_settings() -> SettingsModel:
    """Read configuration from environment variables."""
    s = Settings.from_env()
    return SettingsModel(
        gemini_api_key=s.gemini_api_key,
        whisper_model=s.whisper_model,
        whisper_device=s.whisper_device,
        whisper_compute_type=s.whisper_compute_type,
        video_codec=s.video_codec,
        video_preset=s.video_preset,
        scout_max_age_days=s.scout_max_age_days,
        enable_gpu=s.enable_gpu,
        subtitle_style=s.subtitle_style,
    )


@app.post("/api/settings")
def save_settings(payload: SettingsModel) -> dict[str, str]:
    """Write configuration changes to the active .env file."""
    try:
        env_lines = []
        # Construct .env file lines
        env_lines.append(f"GEMINI_API_KEY={payload.gemini_api_key or ''}")
        env_lines.append(f"SHORTS_WHISPER_MODEL={payload.whisper_model}")
        env_lines.append(f"SHORTS_WHISPER_DEVICE={payload.whisper_device}")
        env_lines.append(f"SHORTS_WHISPER_COMPUTE_TYPE={payload.whisper_compute_type}")
        env_lines.append(f"SHORTS_VIDEO_CODEC={payload.video_codec}")
        env_lines.append(f"SHORTS_VIDEO_PRESET={payload.video_preset}")
        env_lines.append(f"SHORTS_SCOUT_MAX_AGE_DAYS={payload.scout_max_age_days}")
        env_lines.append(f"SHORTS_ENABLE_GPU={'true' if payload.enable_gpu else 'false'}")
        env_lines.append(f"SHORTS_SUBTITLE_STYLE={payload.subtitle_style}")

        Path(".env").write_text("\n".join(env_lines), encoding="utf-8")
        return {
            "status": "success",
            "message": "Settings saved to .env file successfully.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}") from exc


# ---------------------------------------------------------------------------
# API Routes — Pipeline Triggers (with Job Queue)
# ---------------------------------------------------------------------------


@app.post("/api/autopilot", response_model=StatusResponse)
def trigger_autopilot(payload: AutopilotRequest) -> dict[str, Any]:
    """Trigger Autopilot mode with job tracking."""
    ensure_worker_running()
    job = _job_queue.create("autopilot", payload.model_dump())
    logger.info(f"JOB CREATED:\nniche={payload.niche}\nkeyword={payload.keyword}")
    return {
        "status": "started",
        "job_id": job.id,
        "message": "Autopilot pipeline triggered.",
    }


@app.post("/api/clip", response_model=StatusResponse)
def trigger_clip(payload: CustomClipRequest) -> dict[str, Any]:
    """Run standard clipper for a specific YouTube URL with job tracking."""
    ensure_worker_running()
    job = _job_queue.create("clip", payload.model_dump())
    return {
        "status": "started",
        "job_id": job.id,
        "message": "Clipper pipeline triggered.",
    }


@app.post("/api/scout/transcript")
def trigger_scout_transcript(payload: TranscriptRequest) -> dict[str, Any]:
    """Fetch native subtitles or transcribe a 5-min rough audio sample to return rough transcript."""
    logger.info("🔍 Fetching rough transcript for: %s", payload.url)
    try:
        with tempfile.TemporaryDirectory(prefix="vanguard_scout_") as temp_dir:
            work_path = Path(temp_dir)
            rough_segments = fetch_subtitles(payload.url, work_path)

            if not rough_segments:
                logger.info("No native English subtitles. Downloading 5-min audio sample...")
                audio_path = work_path / "rough_audio.m4a"
                download_audio(payload.url, audio_path, start_time=0.0, end_time=300.0)
                logger.info("Transcribing audio sample with fast Whisper...")
                rough_segments = transcribe_clip(audio_path)

            return {
                "url": payload.url,
                "segments": [
                    {"start": s.start, "end": s.end, "text": s.text} for s in rough_segments
                ],
            }
    except Exception as exc:
        logger.error("Failed to fetch transcript: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/scout/video-details")
def get_video_details(payload: TranscriptRequest) -> dict[str, str]:
    """Get video title and thumbnail URL via yt-dlp."""
    logger.info("📺 Fetching video details for: %s", payload.url)
    try:
        from shorts_clipper.downloader.yt_dlp import get_base_yt_dlp_cmd

        cmd = get_base_yt_dlp_cmd()
        cmd.extend(["--skip-download", "--print", "%(title)s\n%(thumbnail)s", "--", payload.url])
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        lines = res.stdout.strip().split("\n")
        title = lines[0] if len(lines) > 0 else "YouTube Video"
        thumbnail = lines[1] if len(lines) > 1 else ""
        return {"title": title, "thumbnail": thumbnail}
    except Exception as e:
        logger.warning("Failed to fetch video details via yt-dlp: %s. Using placeholder.", e)
        return {
            "title": "YouTube Video",
            "thumbnail": "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=320",
        }


@app.post("/api/scout/highlights")
def trigger_scout_highlights(payload: HighlightsRequest) -> dict[str, Any]:
    """Send transcript segments to Gemini and retrieve scored candidate highlight clips."""
    logger.info("🧠 Consulting Gemini to select highlights...")
    try:
        from shorts_clipper.core.models import TranscriptSegment

        segments = [
            TranscriptSegment(start=s.start, end=s.end, text=s.text) for s in payload.segments
        ]
        settings = Settings.from_env()
        provider = GeminiProvider(api_key=settings.gemini_api_key)

        clips = provider.select_multiple_clips_detailed(segments, count=payload.count)
        return {"highlights": clips}
    except Exception as exc:
        logger.error("Failed to fetch highlights from Gemini: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/clip/render", response_model=StatusResponse)
def trigger_clip_render(payload: CustomClipRenderRequest) -> dict[str, Any]:
    """Force rendering a specific custom timestamp window from a video."""
    ensure_worker_running()
    job = _job_queue.create("render", payload.model_dump())
    return {
        "status": "started",
        "job_id": job.id,
        "message": "Precision render task triggered.",
    }


@app.post("/api/gemini/chat")
def gemini_chat(payload: GeminiChatRequest) -> dict[str, Any]:
    """Interact with Gemini as an AI metadata co-writer for titles, descriptions, hooks, and tags."""
    try:
        settings = Settings.from_env()
        provider = GeminiProvider(api_key=settings.gemini_api_key)

        system_instruction = (
            "You are an elite, highly experienced viral video editor and content strategist. "
            "Help the user craft highly engaging, click-worthy titles, hooks, tags, and description copy "
            "optimized for YouTube Shorts, TikTok, and Instagram Reels. Use the provided clip text context "
            "to make your responses highly tailored."
        )

        prompt_context = f"System Instruction: {system_instruction}\n\nClip Transcript Context:\n{payload.context}\n\nUser Request: {payload.prompt}"
        response = provider.generate_content(prompt_context)
        return {"text": response.text.strip()}
    except Exception as exc:
        logger.error("Failed to chat with Gemini: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/watchdog")
def get_watchdog_config() -> dict[str, Any]:
    """Retrieve the Autopilot Watchdog configuration."""
    watchdog_path = Path("outputs/watchdog.json")
    if not watchdog_path.exists():
        watchdog_path.parent.mkdir(parents=True, exist_ok=True)
        default_data = {"enabled": False, "channels": [], "last_poll_time": 0.0}
        watchdog_path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")
        return default_data
    try:
        return json.loads(watchdog_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to read watchdog config: %s", e)
        return {"enabled": False, "channels": []}


@app.post("/api/watchdog")
def update_watchdog_config(payload: WatchdogConfigRequest) -> dict[str, Any]:
    """Update the Autopilot Watchdog configuration."""
    watchdog_path = Path("outputs/watchdog.json")
    watchdog_path.parent.mkdir(parents=True, exist_ok=True)

    current = {"enabled": False, "channels": [], "last_poll_time": 0.0}
    if watchdog_path.exists():
        try:
            current = json.loads(watchdog_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    current["enabled"] = payload.enabled
    current["channels"] = payload.channels

    try:
        watchdog_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return {"status": "saved", "config": current}
    except Exception as e:
        logger.error("Failed to write watchdog config: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/api/watchdog/{channel_name}")
def delete_watchdog_channel(channel_name: str) -> dict[str, Any]:
    """Remove a channel from the Autopilot Watchdog list by its name."""
    watchdog_path = Path("outputs/watchdog.json")
    if not watchdog_path.exists():
        return {"status": "success", "message": "Monitored list is empty."}

    try:
        data = json.loads(watchdog_path.read_text(encoding="utf-8"))
        channels = data.get("channels", [])
        filtered = [ch for ch in channels if ch.get("name") != channel_name]
        data["channels"] = filtered
        watchdog_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {
            "status": "deleted",
            "message": f"Monitored channel '{channel_name}' removed.",
        }
    except Exception as e:
        logger.error("Failed to delete channel from watchdog: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------------------------------------------------------
# API Routes — Feedback
# ---------------------------------------------------------------------------


@app.get("/api/feedback")
def list_feedback(limit: int = 50) -> list[dict[str, Any]]:
    """List all feedback entries sorted by performance score."""
    return [fb.to_dict() for fb in _feedback_store.list_all(limit=limit)]


@app.get("/api/feedback/{clip_name:path}")
def get_feedback(clip_name: str) -> dict[str, Any]:
    """Get feedback for a specific clip."""
    fb = _feedback_store.get(clip_name)
    if fb is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return fb.to_dict()


@app.post("/api/feedback")
def submit_feedback(payload: FeedbackModel) -> dict[str, Any]:
    """Submit or update performance feedback for a clip."""
    fb = ClipFeedback(
        clip_name=payload.clip_name,
        views=payload.views,
        likes=payload.likes,
        shares=payload.shares,
        comments=payload.comments,
        watch_time_avg=payload.watch_time_avg,
        retention_pct=payload.retention_pct,
        notes=payload.notes,
    )
    result = _feedback_store.upsert(fb)
    return {
        "status": "saved",
        "performance_score": result.performance_score,
        "data": result.to_dict(),
    }


@app.delete("/api/feedback/{clip_name:path}")
def delete_feedback(clip_name: str) -> dict[str, str]:
    """Delete feedback for a clip."""
    if _feedback_store.delete(clip_name):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Feedback not found")


# ---------------------------------------------------------------------------
# SSE Log Streaming
# ---------------------------------------------------------------------------


@app.get("/api/logs/stream")
async def stream_logs() -> StreamingResponse:
    """Stream live logs generated by python clipper backend via SSE."""

    async def log_generator():
        log_file = Path("outputs/app.log")
        if not log_file.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.touch()

        with open(log_file, encoding="utf-8", errors="ignore") as f:
            # Pre-populate UI with last 50 log lines for immediate context
            lines = f.readlines()
            tail_lines = lines[-50:]
            if tail_lines:
                for tline in tail_lines:
                    yield f"data: {tline.strip()}\n\n"

            f.seek(0, 2)
            while True:
                try:
                    line = f.readline()
                    if not line:
                        await anyio.sleep(0.25)
                        continue
                    yield f"data: {line.strip()}\n\n"
                except Exception:
                    break

    return StreamingResponse(log_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Static Files & UI Serving
# ---------------------------------------------------------------------------

# Mount clips folder to serve generated mp4s
settings = Settings.from_env()
outputs_dir_path = Path(settings.output_dir).absolute()
outputs_dir_path.mkdir(parents=True, exist_ok=True)
app.mount("/clips", StaticFiles(directory=str(outputs_dir_path)), name="clips")


# Served Vanguard HTML UI
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    ui_html_path = Path(__file__).resolve().parent.parent / "ui" / "index.html"
    if ui_html_path.exists():
        return HTMLResponse(content=ui_html_path.read_text(encoding="utf-8"))

    # Fallback premium stub if ui/index.html is missing
    return HTMLResponse(
        content="""
    <html>
        <head><title>Vanguard Console</title></head>
        <body style="background:#0b0d19; color:white; font-family:sans-serif; text-align:center; padding-top:100px;">
            <h1>Vanguard Clipper Console</h1>
            <p style="color:#7e87ab;">Frontend is loading or missing in shorts_clipper/ui/index.html.</p>
        </body>
    </html>
    """
    )


# Serve the rest of the UI as static assets if needed
ui_dir_path = Path(__file__).resolve().parent.parent / "ui"
if ui_dir_path.exists():
    app.mount("/ui", StaticFiles(directory=str(ui_dir_path)), name="ui")
