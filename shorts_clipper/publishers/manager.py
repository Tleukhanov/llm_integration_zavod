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


class PublishingEngine:
    """Core engine responsible for multi-platform publishing."""

    def __init__(
        self,
        max_retries: int = 3,
        base_backoff: int = 2,
    ):
        self.max_retries = max_retries
        self.base_backoff = base_backoff

    def publish(
        self,
        video_path: Path,
        metadata: ClipMetadata,
        platforms: list[str],
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
        log.info(f"🚀 PublishingEngine started for {len(platforms)} platforms: {platforms}")

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
        settings = Settings.from_env()

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
            for attempt in range(1, self.max_retries + 1):
                try:
                    log.info(
                        f"📤 Publishing to {platform_name} (Attempt {attempt}/{self.max_retries})..."
                    )

                    result = publisher.publish(video_path, metadata, signed_url)
                    result.retry_count = attempt - 1

                    if result.success:
                        # Optional Verification
                        try:
                            if result.platform_id and publisher.verify(result.platform_id):
                                log.info(f"✅ Verified upload for {platform_name}!")
                            else:
                                log.warning(f"⚠️ Verification inconclusive for {platform_name}")
                                result.success = False
                                result.error_message = "Verification failed."
                        except Exception as ve:
                            log.warning(f"⚠️ Verification check failed for {platform_name}: {ve}")
                            result.success = False
                            result.error_message = f"Verification exception: {ve}"

                        if result.success:
                            log.info(f"✅ Successfully published to {platform_name}!")
                            break
                    else:
                        log.warning(
                            f"⚠️ Publishing to {platform_name} returned failure: {result.error_message}"
                        )
                        break  # Do not retry on explicit false returns (permanent logic errors)

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
                    log.warning(
                        f"⚠️ Transient network error on attempt {attempt} for {platform_name}: {e}"
                    )
                    result = PublishResult(
                        platform=platform_name,
                        success=False,
                        retry_count=attempt - 1,
                        error_message=str(e),
                    )
                except Exception as e:
                    error_str = str(e).lower()
                    if e.__class__.__name__ == "HttpError":
                        if getattr(e, "resp", None) and e.resp.status in (429, 500, 502, 503, 504):
                            if e.resp.status == 429:
                                retry_after = e.resp.get("retry-after")
                                if retry_after and retry_after.isdigit():
                                    wait_override = int(retry_after)
                                    log.warning(
                                        f"⚠️ Rate limited. Received Retry-After: {wait_override}s"
                                    )
                            log.warning(
                                f"⚠️ Transient Google API error on attempt {attempt} for {platform_name}: {e}"
                            )
                            result = PublishResult(
                                platform=platform_name,
                                success=False,
                                retry_count=attempt - 1,
                                error_message=str(e),
                            )
                        else:
                            log.warning(f"⚠️ Permanent Google API error for {platform_name}: {e}")
                            result = PublishResult(
                                platform=platform_name,
                                success=False,
                                retry_count=attempt - 1,
                                error_message=str(e),
                            )
                            break
                    elif "timeout" in error_str or "connection" in error_str:
                        log.warning(
                            f"⚠️ Transient error on attempt {attempt} for {platform_name}: {e}"
                        )
                        result = PublishResult(
                            platform=platform_name,
                            success=False,
                            retry_count=attempt - 1,
                            error_message=str(e),
                        )
                    else:
                        log.warning(f"⚠️ Permanent error for {platform_name}: {e}")
                        result = PublishResult(
                            platform=platform_name,
                            success=False,
                            retry_count=attempt - 1,
                            error_message=str(e),
                        )
                        break

                if attempt < self.max_retries:
                    wait_time = (
                        wait_override if wait_override is not None else (self.base_backoff**attempt)
                    )
                    log.info(f"⏳ Waiting {wait_time}s before retrying {platform_name}...")
                    time.sleep(wait_time)
                    wait_override = None

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
