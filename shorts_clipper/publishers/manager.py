import concurrent.futures
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from shorts_clipper.core.exceptions import ConfigurationError
from shorts_clipper.core.settings import Settings

from .cloudflare_r2 import R2Storage
from .models import ClipMetadata, PublishResult
from .registry import PublisherRegistry

log = logging.getLogger(__name__)

RETRIABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}

_RETRIABLE_ERROR_FRAGMENTS = (
    "quotaexceeded",
    "dailylimitexceeded",
    "userratelimitexceeded",
    "ratelimitexceeded",
    "rateexceeded",
    "resource_exhausted",
    "resumableuploaderror",
    "too many requests",
    "temporarily_unavailable",
    "internal error",
    "backend",
    "connection",
    "timeout",
    "timed out",
    "refused",
    "reset",
    "429",
    "500",
)

_ID_KEYS = ("video_id", "publish_id", "media_id", "creation_id", "id")


def _error_text(exc: Exception) -> str:
    """Best-effort flattened error description (message + response body + reasons)."""
    parts = [str(exc)]
    for attr in ("resp", "response"):
        resp = getattr(exc, attr, None)
        if resp is None:
            continue
        data = getattr(resp, "data", None)
        if data is None:
            data = getattr(resp, "text", None)
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8", "replace")
            except Exception:
                data = None
        if isinstance(data, str):
            parts.append(data)
            try:
                data = json.loads(data)
            except Exception:
                data = None
        if isinstance(data, dict):
            err = data.get("error", data)
            if isinstance(err, dict):
                if err.get("message"):
                    parts.append(str(err["message"]))
                for item in err.get("errors", []) or []:
                    if isinstance(item, dict):
                        if item.get("reason"):
                            parts.append(str(item["reason"]))
                        if item.get("message"):
                            parts.append(str(item["message"]))
    return " ".join(p for p in parts if p).lower()


def _status_code(exc: Exception) -> int | None:
    """Extract an HTTP status code from a wide range of exception shapes."""
    resp = getattr(exc, "resp", None)
    if resp is not None:
        status = getattr(resp, "status", None)
        if hasattr(resp, "get") and status is None:
            try:
                status = resp.get("status") or resp.get("code")
            except Exception:
                status = None
        if status is not None:
            try:
                return int(str(status).lstrip("#"))
            except (TypeError, ValueError):
                return None
    response = getattr(exc, "response", None)
    if response is not None:
        status = (
            getattr(response, "status_code", None)
            or getattr(response, "status", None)
        )
        if status is not None:
            try:
                return int(str(status).lstrip("#"))
            except (TypeError, ValueError):
                return None
    status = getattr(exc, "status_code", None)
    if status is not None:
        try:
            return int(str(status).lstrip("#"))
        except (TypeError, ValueError):
            return None
    return None


def _retry_after_seconds(exc: Exception) -> int | None:
    """Read a Retry-After hint (seconds) from the wrapped response, if any."""
    resp = getattr(exc, "resp", None)
    if resp is not None and hasattr(resp, "get"):
        try:
            value = resp.get("retry-after") or resp.get("Retry-After")
        except Exception:
            value = None
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None) or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _is_retriable_error(exc: Exception) -> bool:
    """Classify an exception as transient (retryable) or permanent.

    Retryable covers HTTP 408/429/500/502/503/504 as well as Google quota /
    resumable-upload errors and network-style failures. Everything else
    (4xx, protocol mistakes, validation) is permanent.
    """
    status = _status_code(exc)
    if status is not None and status in RETRIABLE_HTTP_STATUSES:
        return True
    if exc.__class__.__name__.lower() == "resumableuploaderror":
        return True
    text = _error_text(exc)
    return any(frag in text for frag in _RETRIABLE_ERROR_FRAGMENTS)


def _find_id(data) -> str | None:
    """Recursively pluck the first believable platform id from a JSON-ish dict."""
    if isinstance(data, dict):
        for key in _ID_KEYS:
            val = data.get(key)
            if val and isinstance(val, str):
                return val
        for sub in ("data", "status", "result", "media", "video", "post"):
            nested = data.get(sub)
            if nested and isinstance(nested, dict):
                found = _find_id(nested)
                if found:
                    return found
    return None


def _extract_platform_id(exc: Exception, current: str | None = None) -> str | None:
    """Recover a platform id from an exception that may hide a lost response.

    Some failures happen *after* the platform accepted the upload (e.g. a
    timeout while reading the response). If the exception carries any
    platform-side id we keep it so the retry can verify instead of re-uploading.
    """
    if current:
        return current
    raw = getattr(exc, "platform_id", None)
    if raw:
        return str(raw)

    payloads = []
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payloads.append(response.json() if hasattr(response, "json") else {})
        except Exception:
            try:
                if isinstance(getattr(response, "text", None), str):
                    payloads.append(json.loads(response.text))
            except Exception:
                pass
    resp = getattr(exc, "resp", None)
    if resp is not None:
        data = getattr(resp, "data", None)
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8", "replace")
            except Exception:
                data = None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = None
        if isinstance(data, dict):
            payloads.append(data)
    for payload in payloads:
        found = _find_id(payload)
        if found:
            return found
    return None


class PublishingEngine:
    """Core engine responsible for multi-platform publishing."""

    def __init__(
        self,
        max_retries: int = 4,
        base_backoff: int = 2,
        max_backoff: int = 30,
        total_retry_time: float = 90.0,
    ):
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.total_retry_time = total_retry_time
        self._video_id: str | None = None

    def publish(
        self,
        video_path: Path,
        metadata: ClipMetadata,
        platforms: list[str],
        video_id: str | None = None,
    ) -> dict[str, PublishResult]:
        """
        Publish a video to multiple platforms independently and concurrently.

        Args:
            video_path: Path to the video file.
            metadata: Universal metadata for the clip.
            platforms: List of platform names to publish to.

        Returns:
            A dictionary mapping platform names to their PublishResult.
        """
        results: dict[str, PublishResult] = {}
        self._video_id = video_id
        log.info(f"🚀 PublishingEngine started for {len(platforms)} platforms: {platforms}")

        # ── Compliance gate (runs before any platform API call) ───────
        settings = Settings.from_env()
        if getattr(settings, "compliance_enabled", True):
            try:
                from shorts_clipper.compliance.gate import ComplianceBlocked, ComplianceGate

                gate = ComplianceGate(settings)
                cta_text = getattr(settings, "affiliate_cta_text", "")
                verdict = gate.check(metadata.title, metadata.description, cta_text)

                if getattr(settings, "compliance_auto_disclaimers", True):
                    safe_desc, note = gate.suggest_description(
                        metadata.description,
                        is_finance=gate._rules.check_finance(
                            f"{metadata.title}\n{metadata.description}\n{cta_text}"
                        ),
                        affiliate_enabled=getattr(settings, "affiliate_enabled", False),
                    )
                    if note:
                        metadata = ClipMetadata(
                            title=metadata.title,
                            description=safe_desc,
                            tags=metadata.tags,
                            privacy_status=metadata.privacy_status,
                            language=metadata.language,
                        )

                if verdict.level == "block":
                    gate.write_block_report(video_path, metadata.title, metadata.description, verdict)
                    log.error(
                        "⛔ Compliance BLOCK: %s | reasons: %s",
                        metadata.title,
                        verdict.reasons,
                    )
                    raise ComplianceBlocked(
                        f"Clip blocked by compliance gate: {'; '.join(verdict.reasons)}"
                    )
                elif verdict.level == "review":
                    log.warning(
                        "⚠️ Compliance REVIEW: %s | reasons: %s",
                        metadata.title,
                        verdict.reasons,
                    )
            except ComplianceBlocked:
                raise
            except Exception as exc:
                log.warning("Compliance gate error (continuing): %s", exc)

        # Authenticate all publishers first (fail early)
        publishers = {}
        for platform_name in platforms:
            try:
                publisher = PublisherRegistry.get_publisher(platform_name)
                log.info(f"🔑 Authenticating for {platform_name}...")
                publisher.authenticate()
                publishers[platform_name] = publisher
            except ValueError as e:
                log.error(f"❌ Could not initialize publisher for {platform_name}: {e}")
                results[platform_name] = PublishResult(
                    platform=platform_name,
                    success=False,
                    error_message=str(e),
                )
            except Exception as e:
                log.error(f"❌ Authentication failed for {platform_name}: {e}")
                results[platform_name] = PublishResult(
                    platform=platform_name,
                    success=False,
                    error_message=f"Auth failed: {e}",
                )

        if not publishers:
            self._generate_manifest(video_path, metadata, results)
            return results

        r2_key = None
        signed_url = None
        r2_storage = None

        for attempt in range(1, self.max_retries + 1):
            try:
                r2_storage = R2Storage(settings)
                if attempt > 1:
                    log.info(f"☁️ Uploading to R2 (Attempt {attempt}/{self.max_retries})...")
                r2_key = r2_storage.upload(video_path)
                signed_url = r2_storage.generate_signed_url(r2_key, expires_in=3600)
                break
            except Exception as e:
                log.warning(f"⚠️ R2 Upload attempt {attempt} failed: {e}")
                if attempt < self.max_retries:
                    wait_time = self.base_backoff**attempt
                    log.info(f"⏳ Waiting {wait_time}s before retrying R2 upload...")
                    time.sleep(wait_time)
                else:
                    log.error(f"❌ R2 Upload failed after {self.max_retries} attempts: {e}")
                    for p in publishers:
                        results[p] = PublishResult(
                            platform=p,
                            success=False,
                            error_message=f"R2 Upload failed: {e}",
                        )
                    self._generate_manifest(video_path, metadata, results)
                    return results

        def publish_to_platform(platform_name: str, publisher) -> PublishResult:
            result = None
            wait_override = None
            known_platform_id = None
            max_attempts = max(1, self.max_retries)
            started = time.monotonic()
            deadline = started + max(0.0, self.total_retry_time)

            for attempt in range(1, max_attempts + 1):
                # ── Idempotency guard (PUB-1) ────────────────────────────────
                # A previous attempt may have uploaded the clip server-side
                # before the response was lost. If we already know the platform
                # id, VERIFY it up-front and re-use the result instead of
                # re-uploading (preventing duplicate videos).
                if known_platform_id:
                    try:
                        log.info(
                            f"🔎 Checking if {platform_name} upload already succeeded "
                            f"(platform_id={known_platform_id})..."
                        )
                        if publisher.verify(known_platform_id):
                            log.info(
                                f"✅ Already published to {platform_name} "
                                f"({known_platform_id}); no re-upload needed."
                            )
                            result = PublishResult(
                                platform=platform_name,
                                success=True,
                                url=self._short_url_for(publisher, known_platform_id),
                                platform_id=known_platform_id,
                                published_at=datetime.now(UTC).isoformat() + "Z",
                                retry_count=attempt - 1,
                            )
                            self._record_publish(platform_name, result)
                            break
                        else:
                            log.error(
                                f"❌ Idempotency check: id {known_platform_id} for "
                                f"{platform_name} returned verify=False; possible "
                                f"duplicate — skipping re-upload."
                            )
                            result = PublishResult(
                                platform=platform_name,
                                success=False,
                                retry_count=attempt - 1,
                                platform_id=known_platform_id,
                                error_message=(
                                    "Possible duplicate upload: id "
                                    f"{known_platform_id} was not verified "
                                    "(verify returned False); re-upload skipped"
                                ),
                            )
                            break
                    except Exception as ve:
                        log.warning(
                            f"⚠️ Idempotency verify failed for {platform_name} "
                            f"(id={known_platform_id}): {ve}"
                        )
                        if known_platform_id:
                            log.error(
                                f"❌ Upload may exist for {platform_name} but id "
                                f"{known_platform_id} could not be verified; skipping "
                                f"re-upload to avoid a duplicate."
                            )
                            result = PublishResult(
                                platform=platform_name,
                                success=False,
                                retry_count=attempt - 1,
                                error_message=(
                                    "Possible duplicate upload: id "
                                    f"{known_platform_id} could not be verified "
                                    f"(verify raised: {ve}); re-upload skipped"
                                ),
                            )
                            break

                try:
                    log.info(
                        f"📤 Publishing to {platform_name} "
                        f"(Attempt {attempt}/{max_attempts})..."
                    )

                    result = publisher.publish(video_path, metadata, signed_url)
                    result.retry_count = attempt - 1

                    if result.platform_id:
                        known_platform_id = known_platform_id or result.platform_id

                    if result.success:
                        # Optional Verification
                        try:
                            if result.platform_id and publisher.verify(result.platform_id):
                                log.info(f"✅ Verified upload for {platform_name}!")
                            else:
                                log.warning(
                                    f"⚠️ Verification inconclusive for {platform_name}"
                                )
                                result.success = False
                                result.error_message = "Verification failed."
                        except Exception as ve:
                            log.warning(
                                f"⚠️ Verification check failed for {platform_name}: {ve}"
                            )
                            result.success = False
                            result.error_message = f"Verification exception: {ve}"

                        if result.success:
                            log.info(f"✅ Successfully published to {platform_name}!")
                            self._record_publish(platform_name, result)
                            break
                    else:
                        # Explicit permanent failure (e.g. PUB-2: no platform id
                        # returned) — never retry, never re-upload.
                        log.warning(
                            f"⚠️ Publishing to {platform_name} returned failure: "
                            f"{result.error_message}"
                        )
                        break

                except ConfigurationError as e:
                    log.warning(f"⚠️ Configuration Error for {platform_name}: {e}")
                    result = PublishResult(
                        platform=platform_name,
                        success=False,
                        retry_count=attempt - 1,
                        error_message=str(e),
                    )
                    break
                except requests.exceptions.RequestException as e:
                    # The upload may have been committed server-side even though
                    # the response was lost — preserve any id that arrived with
                    # the failed response so the next attempt verifies it first.
                    known_platform_id = _extract_platform_id(e, known_platform_id)
                    log.warning(
                        f"⚠️ Transient network error on attempt {attempt} for "
                        f"{platform_name}: {e}"
                    )
                    result = PublishResult(
                        platform=platform_name,
                        success=False,
                        retry_count=attempt - 1,
                        error_message=str(e),
                    )
                except Exception as e:
                    if _is_retriable_error(e):
                        known_platform_id = _extract_platform_id(e, known_platform_id)
                        retry_after = _retry_after_seconds(e)
                        if retry_after is not None:
                            wait_override = retry_after
                            log.warning(
                                f"⚠️ Rate limited for {platform_name}. "
                                f"Received Retry-After: {retry_after}s"
                            )
                        log.warning(
                            f"⚠️ Transient platform error on attempt {attempt} for "
                            f"{platform_name}: {e}"
                        )
                        result = PublishResult(
                            platform=platform_name,
                            success=False,
                            retry_count=attempt - 1,
                            error_message=str(e),
                        )
                    else:
                        log.warning(
                            f"⚠️ Permanent error for {platform_name}: {e}"
                        )
                        result = PublishResult(
                            platform=platform_name,
                            success=False,
                            retry_count=attempt - 1,
                            error_message=str(e),
                        )
                        break

                # ── Decide whether another attempt fits the retry budget ─────
                if attempt < max_attempts:
                    wait_time = wait_override if wait_override is not None else min(
                        self.base_backoff**attempt, self.max_backoff
                    )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        log.error(
                            f"❌ Retry budget ({self.total_retry_time}s) exhausted "
                            f"for {platform_name} after {attempt} attempt(s)."
                        )
                        result = PublishResult(
                            platform=platform_name,
                            success=False,
                            retry_count=attempt - 1,
                            error_message=(
                                f"Retry budget exhausted after {attempt} attempt(s): "
                                f"{result.error_message}"
                                if result and result.error_message
                                else f"Retry budget exhausted after {attempt} attempt(s)."
                            ),
                        )
                        break
                    if wait_time > remaining:
                        log.info(
                            f"⏳ Backoff/Retry-After {wait_time:.0f}s exceeds remaining "
                            f"budget {remaining:.0f}s for {platform_name}; stopping."
                        )
                        result = PublishResult(
                            platform=platform_name,
                            success=False,
                            retry_count=attempt - 1,
                            error_message=(
                                f"Retry budget exhausted after {attempt} attempt(s): "
                                f"{result.error_message}"
                                if result and result.error_message
                                else f"Retry budget exhausted after {attempt} attempt(s)."
                            ),
                        )
                        break
                    wait_time = max(0, wait_time)
                    log.info(
                        f"⏳ Waiting {wait_time:.0f}s before retrying {platform_name}..."
                    )
                    time.sleep(wait_time)
                    wait_override = None

            if result is None:
                result = PublishResult(
                    platform=platform_name,
                    success=False,
                    retry_count=max_attempts - 1,
                    error_message=f"Publishing to {platform_name} failed unexpectedly.",
                )
            return result

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(publishers)) as executor:
                future_to_platform = {
                    executor.submit(publish_to_platform, platform_name, publisher): platform_name
                    for platform_name, publisher in publishers.items()
                }
                for future in concurrent.futures.as_completed(future_to_platform):
                    platform_name = future_to_platform[future]
                    try:
                        results[platform_name] = future.result()
                    except Exception as e:
                        log.error(f"❌ Unhandled executor exception for {platform_name}: {e}")
                        results[platform_name] = PublishResult(
                            platform=platform_name,
                            success=False,
                            error_message=str(e),
                        )
        finally:
            if r2_storage and r2_key:
                try:
                    r2_storage.delete(r2_key)
                except Exception as e:
                    log.error(f"❌ Failed to delete R2 object {r2_key}: {e}")

        # Generate manifest
        self._generate_manifest(video_path, metadata, results)

        return results

    def _short_url_for(self, publisher, platform_id: str) -> str | None:
        """Best-effort public URL for an id recovered by an idempotency check."""
        if not platform_id:
            return None
        platform = getattr(publisher, "platform_name", None)
        if platform == "youtube":
            return f"https://youtube.com/shorts/{platform_id}"
        if platform == "instagram":
            return f"https://www.instagram.com/reel/{platform_id}/"
        if platform == "tiktok":
            open_id = getattr(getattr(publisher, "settings", None), "tiktok_open_id", None)
            if open_id:
                return f"https://www.tiktok.com/@{open_id}/video/{platform_id}"
        return None

    def _record_publish(self, platform: str, result: PublishResult) -> None:
        """Persist publishing metadata (defensively; never disrupt the publish path).

        Coordinates with the metrics store added in parallel: this logs and moves
        on if the store or the ``record_publish`` method is unavailable yet.
        """
        try:
            from shorts_clipper.core.metrics import MetricsStore
            from shorts_clipper.core.settings import Settings

            store = MetricsStore(Settings.from_env().metrics_path)
            try:
                record = getattr(store, "record_publish", None)
                if callable(record):
                    record(self._video_id, platform, result.platform_id, result.url)
                else:
                    log.debug("record_publish unavailable on MetricsStore; skipped")
            finally:
                store.close()
        except Exception as exc:
            log.debug("record_publish skipped: %s", exc)

    def _generate_manifest(
        self,
        video_path: Path,
        metadata: ClipMetadata,
        results: dict[str, PublishResult],
    ) -> None:
        """Generates a publish_manifest.json file in the output directory."""
        manifest_path = video_path.with_name(f"{video_path.stem}_publish_manifest.json")

        platforms_requested = list(results.keys())
        successful = [p for p, r in results.items() if r.success]

        if len(successful) == len(platforms_requested) and len(platforms_requested) > 0:
            overall_status = "SUCCESS"
        elif len(successful) > 0:
            overall_status = "PARTIAL_SUCCESS"
        else:
            overall_status = "FAILED"

        manifest_data = {
            "clip_id": video_path.stem,
            "render_timestamp": datetime.now(UTC).isoformat() + "Z",
            "video_path": str(video_path.resolve()),
            "metadata": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": metadata.tags,
                "language": metadata.language,
            },
            "platforms_requested": platforms_requested,
            "overall_status": overall_status,
            "results": {
                platform: {
                    "success": result.success,
                    "url": result.url,
                    "platform_id": result.platform_id,
                    "published_at": result.published_at,
                    "retry_attempts": result.retry_count,
                    "error_message": result.error_message,
                }
                for platform, result in results.items()
            },
        }

        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2, ensure_ascii=False)
            log.info(f"📄 Generated publish manifest: {manifest_path}")
        except Exception as e:
            log.error(f"❌ Failed to generate publish manifest: {e}")
