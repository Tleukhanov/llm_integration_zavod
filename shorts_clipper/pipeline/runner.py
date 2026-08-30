"""Main pipeline runner — the orchestration brain.

Wires together every module into a single clean flow:
  Scout → Subtitles → Gemini → Download → Crop → Burn Subs → Output

Pipeline encode passes:
  Pass 1 — crop + scale to vertical (lossless-ish CRF 18)
  Pass 2 — burn ASS subtitles + 1.15× pacing in ONE combined ffmpeg call

No intermediate re-encode for pacing; it is baked into the subtitle step
so we never triple-encode the video.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from shorts_clipper.captions.generator import burn_subtitles
from shorts_clipper.core.exceptions import MediaProcessingError
from shorts_clipper.core.logging import configure_logging
from shorts_clipper.core.settings import Settings
from shorts_clipper.downloader.yt_dlp import (
    download_audio,
    download_clip,
    fetch_subtitles,
)
from shorts_clipper.pipeline.finisher import EditorialFinisher
from shorts_clipper.providers.gemini import GeminiProvider
from shorts_clipper.publishers import ClipMetadata, PublishingEngine
from shorts_clipper.rendering.crop import process_to_vertical
from shorts_clipper.scout.trending import get_trending_link
from shorts_clipper.transcription.whisper import transcribe_clip

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(
    url: str,
    *,
    settings: Settings | None = None,
    output_path: Path | None = None,
    count: int = 1,
    upload: bool = False,
    platforms: list[str] | None = None,
    privacy: str = "private",
    niche: str | None = None,
    progress_callback: Callable[[int], None] | None = None,
    preselected_clips: list | None = None,
    source_title: str | None = None,
    source_channel: str | None = None,
) -> Path | list[Path]:
    """
    Run the full shorts clipping pipeline for a given YouTube URL.

    Encode flow (2 passes — no triple re-encode):
      1. process_to_vertical   — scale + crop to 1080×1920
      2. burn_subtitles        — ASS subtitles + 1.15× pacing in one pass

    Args:
        url:         YouTube video URL.
        settings:    App settings (loaded from .env if not provided).
        output_path: Override output path (default: outputs/clip_TIMESTAMP.mp4).
                     If count > 1, files are saved as PATH_1.mp4, PATH_2.mp4, etc.
        count:       Number of clips to extract.

    Returns:
        Path to the final output video if count == 1, or list of Paths if count > 1.

    Raises:
        MediaProcessingError: If any stage of the pipeline fails.
    """
    if settings is None:
        settings = Settings.from_env()

    if platforms is None:
        platforms = settings.publish_platforms

    configure_logging(settings.log_level)
    log.info("🚀 PIPELINE START: %s (extracting %d clip(s))", url, count)

    settings.output_dir.mkdir(parents=True, exist_ok=True)

    from shorts_clipper.core.observability import get_run_context

    run_ctx = get_run_context()
    # Ensure run context has the directory set.
    run_dir = run_ctx.set_run_dir(settings.output_dir)

    with tempfile.TemporaryDirectory(prefix="shorts_clipper_") as work_dir:
        work_path = Path(work_dir)

        try:
            clips = []
            if preselected_clips:
                clips = preselected_clips[:count]
                log.info("🔥 Using %d pre-selected clips provided by coordinator!", len(clips))

            if len(clips) < count:
                # ── PASS 1: ROUGH TRANSCRIPT FOR AI SELECTION ────────────────
                log.info("\n--- PASS 1: ROUGH TRANSCRIPT FOR AI SELECTION ---")
                if progress_callback:
                    progress_callback(10)
                rough_segments = fetch_subtitles(url, work_path)

                if not rough_segments:
                    log.warning(
                        "⚠️  No native subtitles. Downloading 5-min audio sample for rough transcript..."
                    )
                    audio_path = work_path / "rough_audio.m4a"
                    download_audio(url, audio_path, start_time=0.0, end_time=300.0)
                    rough_segments = transcribe_clip(audio_path)

                from shorts_clipper.attention.engine import SimulationEngine
                from shorts_clipper.core.models import ClipWindow
                from shorts_clipper.highlight_detection.scoring import SemanticCandidateGenerator

                try:
                    log.info("Generating semantic candidates")
                    generator = SemanticCandidateGenerator()
                    candidate_generation_score, best_local_window, reasoning = (
                        generator.generate_candidate(rough_segments)
                    )

                    if not best_local_window:
                        raise MediaProcessingError(
                            "Semantic Candidate Generator failed to find a valid window."
                        )

                    log.info("Generating counterfactual variants")
                    sim_engine = SimulationEngine()
                    log.info("Running attention simulation")
                    sim_result = sim_engine.optimize_clip(best_local_window)

                    log.info("Selecting optimal narrative")
                    winner_variant = next(
                        (v for v in sim_result.variants if v.variant_id == sim_result.winner_id),
                        sim_result.base_variant,
                    )

                    # Log artifacts
                    from dataclasses import asdict

                    def safe_asdict(obj):
                        try:
                            # Convert enums to string
                            data = asdict(obj)

                            def recursive_enum_to_str(d):
                                if isinstance(d, dict):
                                    for k, v in d.items():
                                        if hasattr(v, "value"):
                                            d[k] = v.value
                                        else:
                                            recursive_enum_to_str(v)
                                elif isinstance(d, list):
                                    for i in range(len(d)):
                                        if hasattr(d[i], "value"):
                                            d[i] = d[i].value
                                        else:
                                            recursive_enum_to_str(d[i])

                            recursive_enum_to_str(data)
                            return data
                        except Exception:
                            return str(obj)

                    run_ctx.add_decision_trace(
                        {
                            "video_url": url,
                            "candidate_windows": [
                                {"start": s.start, "end": s.end} for s in best_local_window
                            ],
                            "semantic_score": candidate_generation_score,
                            "winner_variant_id": sim_result.winner_id,
                            "winner_reason": sim_result.reason,
                            "runner_up": None,  # Could extract if needed
                            "confidence": sim_result.reports[
                                sim_result.winner_id
                            ].overall_confidence,
                        }
                    )

                    from shorts_clipper.core.stats import get_optimizer_stats

                    optimizer_stats = get_optimizer_stats()
                    optimizer_stats.record_run(
                        sim_result.winner_id,
                        sim_result.reports[sim_result.winner_id].overall_confidence,
                        len(sim_result.variants),
                        sim_result.runner_up_id,
                        sim_result.improvement_percentage,
                    )

                    run_ctx.add_attention_report(
                        "clip_1", safe_asdict(sim_result.reports[sim_result.winner_id])
                    )
                    run_ctx.add_variant(safe_asdict(sim_result))

                    # Score Breakdown
                    run_ctx.add_score_breakdown(
                        "clip_1",
                        {
                            "Narrative Score": candidate_generation_score,
                            "Final Editorial Score": sim_result.reports[
                                sim_result.winner_id
                            ].completion_prob
                            * 100.0,
                            "judge_results": {
                                k: v.score
                                for k, v in sim_result.reports[
                                    sim_result.winner_id
                                ].judge_results.items()
                            },
                        },
                    )

                    # Editorial Summary Markdown
                    editorial_md = f"""# Editorial Decision for Clip 1
## Why THIS clip?
{sim_result.reason}

## Key Metrics
- **Completion Probability:** {sim_result.reports[sim_result.winner_id].completion_prob:.2f}
- **Scroll Stop Probability:** {sim_result.reports[sim_result.winner_id].scroll_stop_prob:.2f}
- **Payoff Strength:** {sim_result.reports[sim_result.winner_id].payoff_strength:.2f}
"""
                    run_ctx.set_editorial_summary("clip_1", editorial_md)

                    new_clip_window = ClipWindow(
                        start=winner_variant.start_time, end=winner_variant.end_time
                    )
                    clips.extend([(new_clip_window, "center")])

                except Exception as exc:
                    log.error("Simulation Engine selection failed: %s", exc)
                    raise MediaProcessingError("No high-quality highlights found.") from exc

            output_paths: list[Path] = []

            for idx, (window, layout) in enumerate(clips, 1):
                log.info(
                    "\n--- PROCESSING CLIP %d/%d: %.1fs → %.1fs [%s] ---",
                    idx,
                    len(clips),
                    window.start,
                    window.end,
                    layout,
                )

                # Determine output path for this specific clip
                if output_path is not None:
                    if count > 1:
                        stem = output_path.stem
                        ext = output_path.suffix
                        current_output_path = output_path.parent / f"{stem}_{idx}{ext}"
                    else:
                        current_output_path = output_path
                else:
                    log.info("Exporting optimized clip")
                    # Write directly to the run context directory
                    current_output_path = run_dir / "rendered_clip.mp4"

                clip_work_dir = work_path / f"clip_{idx}"
                clip_work_dir.mkdir(parents=True, exist_ok=True)
                micro_path = clip_work_dir / "micro_clip.mp4"

                # ── PASS 2: PRECISION TRANSCRIPTION (WITH BUFFER) ─────────────
                log.info("\n--- PASS 2: PRECISION TRANSCRIPTION ---")
                if progress_callback:
                    progress_callback(30)

                BUFFER = 45.0
                buffered_start = max(0.0, window.start - BUFFER)
                download_clip(
                    url, micro_path, start_time=buffered_start, end_time=window.end + BUFFER
                )

                log.info(
                    "Running Whisper (%s) on micro-clip for word-level timing...",
                    settings.whisper_model,
                )
                if progress_callback:
                    progress_callback(50)
                precision_segments = transcribe_clip(micro_path)

                # ── Step 2.5: Editorial Finisher ──────────────────────────────
                log.info("Applying editorial validation")
                finisher = EditorialFinisher()
                final_window = finisher.snap_boundaries(
                    window.start - buffered_start, window.end - buffered_start, precision_segments
                )
                log.info(
                    "EditorialFinisher adjusted timestamps deterministically: start %.2f -> %.2f, end %.2f -> %.2f",
                    window.start - buffered_start,
                    final_window.start,
                    window.end - buffered_start,
                    final_window.end,
                )

                # Shift timestamps in precision_segments to account for trimming
                trim_start = final_window.start
                duration = final_window.end - final_window.start
                shifted_segments = []
                for s in precision_segments:
                    shifted_words = []
                    if s.words:
                        for w in s.words:
                            new_start = w.start - trim_start
                            new_end = w.end - trim_start
                            # Strict inclusion: word must roughly fit entirely inside the new duration
                            if new_start >= -0.1 and new_end <= duration + 0.1:
                                shifted_words.append(replace(w, start=new_start, end=new_end))

                    if shifted_words:
                        seg_start = shifted_words[0].start
                        seg_end = shifted_words[-1].end
                        seg_text = " ".join(w.word for w in shifted_words)
                        shifted_segments.append(
                            replace(
                                s, start=seg_start, end=seg_end, text=seg_text, words=shifted_words
                            )
                        )
                    elif not s.words:
                        # Fallback if no word-level timestamps
                        new_start = s.start - trim_start
                        new_end = s.end - trim_start
                        if new_start >= -0.1 and new_end <= duration + 0.1:
                            shifted_segments.append(replace(s, start=new_start, end=new_end))
                precision_segments = shifted_segments

                # ── Step 3: Vertical crop + Trim ──────────────────────────────
                log.info("\n--- VERTICAL CROP & TRIM ---")
                if progress_callback:
                    progress_callback(70)
                cropped_path = clip_work_dir / "cropped.mp4"
                process_to_vertical(
                    micro_path,
                    cropped_path,
                    layout=layout,
                    video_codec=settings.video_codec,
                    preset=settings.video_preset,
                    start_time=trim_start,
                    duration=duration,
                )

                # ── Step 4: Burn subtitles + 1.15× pacing (single pass) ───────
                log.info("\n--- BURNING SUBTITLES + PACING ---")
                if progress_callback:
                    progress_callback(85)
                # Save subtitle.ass artifact
                from shorts_clipper.captions.generator import generate_ass_file

                ass_artifact_path = run_dir / f"subtitle_{idx}.ass"
                generate_ass_file(
                    precision_segments,
                    start_offset=0.0,
                    output_path=ass_artifact_path,
                    pacing=1.15,
                    style_name=settings.subtitle_style,
                )

                burn_subtitles(
                    cropped_path,
                    precision_segments,
                    start_offset=0.0,  # precision segments are relative to micro_clip
                    output_path=current_output_path,
                    pacing=1.15,
                    video_codec=settings.video_codec,
                    preset=settings.video_preset,
                    style_name=settings.subtitle_style,
                )

                try:
                    from shorts_clipper.rendering.thumbnailer import extract_thumbnail

                    extract_thumbnail(current_output_path)
                except Exception as thumb_err:
                    log.warning("Thumbnail extraction failed: %s", thumb_err)

                # Generate viral metadata using Gemini and write sidecar .json file
                meta = {
                    "title": None,
                    "description": None,
                    "tags": [],
                    "publish_status": "idle",
                    "publish_error": None,
                }

                s_title = source_title or ""
                s_channel = source_channel or ""
                actual_niche = niche or "tech"

                try:
                    provider = GeminiProvider(api_key=settings.gemini_api_key)
                    ai_meta = provider.generate_clip_metadata(
                        precision_segments, source_title=s_title, source_channel=s_channel
                    )
                    meta["title"] = ai_meta["title"]
                    meta["description"] = ai_meta["description"]
                    meta["tags"] = ai_meta["tags"]
                    log.info("🧠 Generated metadata — Title: %s", meta["title"])
                except Exception as meta_err:
                    log.warning(
                        "❌ GEMINI METADATA GENERATION FAILED for clip %d: %s. Using Local Fallback Generator.",
                        idx,
                        meta_err,
                    )
                    from shorts_clipper.metadata.fallback import generate_fallback_metadata

                    fallback_meta = generate_fallback_metadata(
                        segments=precision_segments,
                        source_title=s_title,
                        source_channel=s_channel,
                        niche=actual_niche,
                    )
                    meta["title"] = fallback_meta["title"]
                    meta["description"] = fallback_meta["description"]
                    meta["tags"] = fallback_meta["tags"]
                    meta["publish_error"] = None
                    log.info("[FALLBACK] title generated: %s", meta["title"])

                # Ensure segments are preserved in the metadata sidecar
                meta["segments"] = [
                    {"start": s.start, "end": s.end, "text": s.text} for s in precision_segments
                ]

                json_path = current_output_path.with_suffix(".json")
                try:
                    json_path.write_text(
                        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    run_ctx.add_final_metadata(f"clip_{idx}", meta)
                    log.info("💾 Generated sidecar metadata: %s", json_path)
                except Exception as write_err:
                    log.warning("Failed to write sidecar metadata: %s", write_err)

                # Copy artifacts to run directory
                import shutil

                if current_output_path.exists():
                    shutil.copy2(current_output_path, run_dir / f"rendered_clip_{idx}.mp4")
                thumb_path = current_output_path.with_suffix(".jpg")
                if thumb_path.exists():
                    shutil.copy2(thumb_path, run_dir / f"thumbnail_{idx}.jpg")
                json_artifact_path = run_dir / f"final_metadata_{idx}.json"
                if json_path.exists():
                    shutil.copy2(json_path, json_artifact_path)
                    json_path = json_artifact_path  # Update json_path so publish_status writes to the artifact

                output_paths.append(current_output_path)
                log.info("✅ Clip %d ready at: %s", idx, current_output_path)

                if upload and platforms:
                    if progress_callback:
                        progress_callback(95)
                    log.info("Publishing Clip %d to platforms: %s", idx, platforms)
                    try:
                        meta["publish_status"] = "uploading"
                        json_path.write_text(
                            json.dumps(meta, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )

                        if not meta.get("title") or not meta.get("description"):
                            log.error(
                                "❌ REFUSING TO PUBLISH clip %d: metadata is missing. "
                                "Title=%r, Description=%r",
                                idx,
                                meta.get("title"),
                                meta.get("description"),
                            )
                            meta["publish_status"] = "failed"
                            meta["publish_error"] = "Upload blocked: metadata generation failed."
                            json_path.write_text(
                                json.dumps(meta, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                            continue

                        clip_metadata = ClipMetadata(
                            title=meta["title"],
                            description=meta["description"],
                            tags=meta.get("tags", ["shorts"]),
                            privacy_status=privacy,
                        )

                        engine = PublishingEngine()
                        publish_results = engine.publish(
                            video_path=current_output_path,
                            metadata=clip_metadata,
                            platforms=platforms,
                        )

                        # Update metadata JSON with results
                        meta["publish_results"] = {
                            p: {
                                "success": r.success,
                                "url": r.url,
                                "platform_id": r.platform_id,
                                "error_message": r.error_message,
                            }
                            for p, r in publish_results.items()
                        }

                        successes = [r for r in publish_results.values() if r.success]
                        if len(successes) == len(platforms):
                            meta["publish_status"] = "success"
                        elif len(successes) > 0:
                            meta["publish_status"] = "partial_success"
                        else:
                            meta["publish_status"] = "failed"

                        json_path.write_text(
                            json.dumps(meta, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        log.info(
                            "✅ Clip %d publishing completed with status: %s",
                            idx,
                            meta["publish_status"],
                        )
                    except Exception as upload_err:
                        meta["publish_status"] = "failed"
                        meta["publish_error"] = str(upload_err)
                        json_path.write_text(
                            json.dumps(meta, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        log.error("❌ Failed to publish clip %d: %s", idx, upload_err)

        except Exception as exc:
            log.exception("❌ PIPELINE FAILED")
            raise MediaProcessingError(str(exc)) from exc

    run_ctx.add_pipeline_metrics(
        {
            "url": url,
            "count_requested": count,
            "count_generated": len(output_paths),
            "job_status": "SUCCESS" if output_paths else "FAILED",
        }
    )

    # Export observability artifacts and verify
    run_ctx.export_all()
    run_ctx.verify_run()

    if count == 1:
        log.info("\n✅ SUCCESS — Single clip ready at: %s", output_paths[0])
        return output_paths[0]

    log.info("\n✅ SUCCESS — %d clips generated successfully!", len(output_paths))
    return output_paths


def run_autopilot(
    settings: Settings | None = None,
    *,
    channel: str | None = None,
    niche: str | None = None,
    keyword: str | None = None,
    count: int = 1,
    upload: bool = False,
    privacy: str = "private",
    progress_callback: Callable[[int], None] | None = None,
    max_age_days: int | None = None,
    job_id: str | None = None,
) -> Path | list[Path] | None:
    """
    Autopilot mode: scout a trending video, then run the full pipeline.

    Returns:
        Output path (or list of paths) on success, or None if no suitable video was found.
    """
    import time
    import uuid

    start_time = time.time()

    # Ensure job_id exists for tracking
    job_id = job_id or str(uuid.uuid4())[:8]

    if settings is None:
        settings = Settings.from_env()

    log.info(f"RUNNER RECEIVED:\nniche={niche}\nkeyword={keyword}")
    log.info("🤖 AUTOPILOT MODE: Scouting trending content...")
    if progress_callback:
        progress_callback(5)

    age_days = max_age_days if max_age_days is not None else settings.scout_max_age_days
    url = get_trending_link(
        channel=channel,
        niche=niche,
        keyword=keyword,
        max_age_days=age_days,
        job_id=job_id,
    )
    log.info("RUNNER RECEIVED: %s", repr(url))
    if not url:
        log.error("Scout returned no suitable video. Aborting.")
        return None

    import re

    from shorts_clipper.core.cache import get_cached
    from shorts_clipper.core.models import ClipWindow

    vid_match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11})(?:\?|&|/|$)", url)
    vid = vid_match.group(1) if vid_match else url
    cached_data = get_cached(vid) or {}

    clips = []
    if "selected_clips" in cached_data:
        clips = [
            (ClipWindow(start=c["start"], end=c["end"]), c["layout"])
            for c in cached_data["selected_clips"]
        ]

    s_title = cached_data.get("title", "")
    s_channel = cached_data.get("uploader", "") or cached_data.get("channel_title", "")
    actual_niche = niche or cached_data.get("niche") or "tech"

    result = run(
        url,
        settings=settings,
        count=count,
        upload=upload,
        privacy=privacy,
        niche=actual_niche,
        progress_callback=progress_callback,
        preselected_clips=clips,
        source_title=s_title,
        source_channel=s_channel,
    )

    if result:
        try:
            import json
            from pathlib import Path

            from shorts_clipper.core.cache import get_cached
            from shorts_clipper.scout.memory import record_success

            mf = Path(f"outputs/scout_metrics_{job_id}.json")
            if mf.exists():
                last_m = json.loads(mf.read_text())
                vid = last_m.get("winner_id")
                niche_str = last_m.get("niche") or niche or "tech"
                query_str = last_m.get("winning_query", "")
                virality = last_m.get("winner_virality_score", 0.0)
                if vid and vid in url:
                    winner_dict = get_cached(vid) or {"id": vid}
                    record_success(winner_dict, niche_str, query_str, virality)

                duration = time.time() - start_time
                quota = last_m.get("queries_fired", 0) * 100
                discovered = last_m.get("video_ids_discovered", 0)
                rejected_low_quality = last_m.get("rejected_low_quality", 0)
                filtered_out = max(0, discovered - rejected_low_quality - 1)

                reason = "Strong structural hook and narrative velocity."
                report_file = Path(f"outputs/scout_report_{job_id}.json")
                if report_file.exists():
                    report_data = json.loads(report_file.read_text())
                    reason = report_data.get("selected_reason", reason)

                report_str = (
                    "========== AUTOPILOT REPORT ==========\n"
                    f"Query: {query_str}\n"
                    f"Window: {age_days} days\n"
                    f"Candidates Found: {discovered}\n"
                    f"Candidates Filtered Out: {filtered_out}\n"
                    f"Candidates Rejected (Low Quality): {rejected_low_quality}\n"
                    f"Top Candidate: {last_m.get('winner_title', 'N/A')}\n"
                    f"Final Winner: {url}\n"
                    f"Processing Time: {duration:.2f}s\n"
                    f"API Calls: {last_m.get('queries_fired', 0)}\n"
                    f"Quota Cost: {quota}\n"
                    f"Selected because: {reason}\n"
                    "======================================"
                )
                log.info(f"\n{report_str}")
                from shorts_clipper.core.observability import get_run_context

                get_run_context().set_editorial_summary(vid, report_str)
                get_run_context().export_all()

        except Exception as e:
            log.warning("Failed to record learning success: %s", e)

    return result
