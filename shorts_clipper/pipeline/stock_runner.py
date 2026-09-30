"""Stock-visual short runner (``SHORTS_VISUAL_MODE=stock``).

Assembles an original quote/philosophy short without touching anyone's VOD:
    background frame -> Ken-Burns zoompan -> word subtitles -> AI voice

The pipeline reuses the standard editorial machinery — ASS subtitle burn,
affiliate offers, BGM, metadata and the publishing engine — so a stock short
goes through the exact same quality/compliance gate as every other clip.

Configuration:
    SHORTS_VISUAL_MODE=stock
    SHORTS_STOCK_DIR=data/stock            # folder to scan for *.mp4
    SHORTS_STOCK_SCRIPT_PATH=scripts.txt   # optional, one quote per line
    SHORTS_VO_ENABLED=true                 # AI voiceover (required here)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
import tempfile
import time
from pathlib import Path

from shorts_clipper.captions.generator import burn_subtitles, generate_ass_file
from shorts_clipper.captions.music import pick_track, should_use_bgm
from shorts_clipper.core.settings import STOCK_MOTIVATION_NICHES, Settings
from shorts_clipper.pipeline.runner import _refresh_retention_grades
from shorts_clipper.visual import stock as stock_visual

log = logging.getLogger(__name__)


def stock_affiliate_allowed(settings: Settings, niche: str | None = None) -> bool:
    """Return True when affiliate overlays may render for a stock short."""
    niche_name = (niche or getattr(settings, "niche", "") or "").strip().lower()
    if niche_name not in STOCK_MOTIVATION_NICHES:
        return True
    return bool(getattr(settings, "stock_affiliate_cards_enabled", False))


def _next_free_stable_path(base: Path) -> Path:
    """Return base path when free else first free suffixed sibling."""
    if not base.exists() and not base.with_suffix(".json").exists():
        return base
    stem = base.stem
    suffix = base.suffix
    parent = base.parent
    n = 2
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists() and not candidate.with_suffix(".json").exists():
            return candidate
        n += 1


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write json payload atomically via temp file and os.replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _persist_file_atomic(src: Path, dst: Path) -> None:
    """Copy src to dst atomically via temp file and os.replace."""
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _bgm_seed(seed: int, bg_path: Path) -> random.Random:
    """Build deterministic RNG for BGM choice from stable digest."""
    digest = hashlib.sha256(f"{seed}:{bg_path}".encode()).hexdigest()
    return random.Random(digest)


def _ensure_outputs_or_raise(output_paths: list[Path]) -> None:
    """Raise RuntimeError when zero shorts were produced."""
    if not output_paths:
        log.error("Stock run produced zero shorts.")
        raise RuntimeError("Stock run produced zero shorts: no voiceover or background available.")


def run_stock_short(
    *,
    settings: Settings,
    niche: str | None = None,
    count: int = 1,
    upload: bool = False,
    privacy: str = "private",
    progress_callback=None,
) -> Path | list[Path] | None:
    """Render *count* stock-visual shorts and optionally publish them."""
    if settings is None:
        settings = Settings.from_env()

    actual_niche = (niche or settings.niche or "self-growth").strip().lower() or "self-growth"
    retention_grades = _refresh_retention_grades(settings)
    video_id = f"stock-{int(time.time())}"

    output_paths: list[Path] = []
    last_track: Path | None = None

    with tempfile.TemporaryDirectory(prefix="shorts_stock_") as work_dir:
        work_path = Path(work_dir)

        for idx in range(1, count + 1):
            log.info(
                "\n--- STOCK SHORT %d/%d (niche=%s) ---",
                idx,
                count,
                actual_niche,
            )
            if progress_callback:
                progress_callback(10 + 60 * (idx - 1) // count)

            clip_work_dir = work_path / f"clip_{idx}"
            clip_work_dir.mkdir(parents=True, exist_ok=True)
            seed = random.Random(f"{video_id}|{idx}").randrange(0, 0xFFFFFFFF)

            # 1. Script line for this short.
            script = stock_visual.load_stock_script(
                settings.stock_script_path,
                seed,
                niche=actual_niche,
                niche_dir=settings.niche_dir,
            )

            # 2. AI voiceover determines the clip duration.  Word timing is
            #    captured from edge-tts word boundaries so subtitles follow the
            #    actual speech instead of a uniform grid.
            from shorts_clipper.audio.tts import synthesize_voiceover_boundaries
            from shorts_clipper.captions.music import track_duration

            vo_path = clip_work_dir / "vo.wav"
            # For motivational stock shorts a natural pacing reads far better
            # than the global fast default; explicit SHORTS_VO_RATE still wins.
            stock_rate = "+0%"
            if settings.vo_rate not in ("", "+8%"):
                stock_rate = settings.vo_rate

            vo_path, word_bounds = synthesize_voiceover_boundaries(
                script,
                vo_path,
                voice=None,
                rate=stock_rate,
                pitch=settings.vo_pitch or "+3Hz",
            )
            if vo_path is None:
                log.error("Stock short needs an AI voiceover (SHORTS_VO_ENABLED). Skipping.")
                continue
            speak_duration = track_duration(vo_path) or 8.0
            render_duration = speak_duration + 1.2  # tail room

            # 3. Build subtitle segments from real spoken word boundaries when
            #    available; otherwise fall back to the uniform grid.
            seg_shift = 0.0
            from shorts_clipper.audio.tts import speech_window as tts_speech_window

            speech = tts_speech_window(vo_path)
            if speech is None:
                speech = stock_visual.speech_window(vo_path)
            if speech is not None:
                seg_shift, speech_end = speech
                effective_dur = max(0.5, speech_end - seg_shift)
            else:
                effective_dur = speak_duration

            if word_bounds and len(word_bounds) >= 2:
                segments = _segments_from_word_bounds(
                    word_bounds,
                    seg_shift=0.0,
                )
            else:
                segments = stock_visual.build_word_segments(script, effective_dur)
                if seg_shift > 0.0:
                    from dataclasses import replace

                    shifted: list = []
                    for seg in segments:
                        new_words = [
                            replace(w, start=w.start + seg_shift, end=w.end + seg_shift)
                            for w in (seg.words or [])
                        ]
                        shifted.append(
                            replace(
                                seg,
                                start=seg.start + seg_shift,
                                end=seg.end + seg_shift,
                                words=new_words,
                            )
                        )
                    segments = shifted

            # 4. Background: montage of several stock clips, a single clip,
            #    or a procedural gradient when nothing is available.
            clips = stock_visual.list_stock_backgrounds(
                settings.stock_dir,
                actual_niche,
                seed,
                pexels_api_key=settings.pexels_api_key,
                limit=4,
                niche_dir=settings.niche_dir,
            )
            bg_path = clip_work_dir / "background.mp4"
            if len(clips) >= 2 and getattr(settings, "stock_edit", False):
                stock_visual.render_stock_background_edit(
                    clips,
                    clip_work_dir,
                    bg_path,
                    render_duration,
                    bpm=getattr(settings, "stock_edit_bpm", 132.0) or 132.0,
                    seed=seed,
                    video_codec=settings.video_codec,
                    preset=settings.video_preset,
                )
            elif len(clips) >= 2:
                stock_visual.render_stock_background_montage(
                    clips,
                    clip_work_dir,
                    bg_path,
                    render_duration,
                    seed=seed,
                    video_codec=settings.video_codec,
                    preset=settings.video_preset,
                )
            elif clips:
                stock_visual.render_stock_background(
                    clips[0],
                    clip_work_dir,
                    bg_path,
                    render_duration,
                    seed=seed,
                    video_codec=settings.video_codec,
                    preset=settings.video_preset,
                )
            else:
                stock_visual.render_procedural_background(
                    clip_work_dir,
                    bg_path,
                    render_duration,
                    seed=seed,
                    video_codec=settings.video_codec,
                    preset=settings.video_preset,
                )
            if not bg_path.is_file():
                log.error("Background render produced no file. Skipping.")
                continue

            # 5. Word timing drives both subtitles and the final duration.
            ass_path = clip_work_dir / "subs.ass"
            edit_flash = None
            if getattr(settings, "stock_edit", False):
                edit_flash = stock_visual.edit_flash_schedule(
                    render_duration,
                    bpm=getattr(settings, "stock_edit_bpm", 132.0) or 132.0,
                    seed=seed,
                )
            generate_ass_file(
                segments,
                start_offset=0.0,
                output_path=ass_path,
                pacing=1.0,
                style_name=settings.subtitle_style,
                flash_events=edit_flash,
            )

            # 6. Affiliate + BGM enrichments (mirror standard pipeline).
            affiliate_partner = None
            if settings.affiliate_enabled and stock_affiliate_allowed(settings, actual_niche):
                try:
                    from shorts_clipper.affiliate import (
                        load_affiliate_partners,
                        select_affiliate_partner,
                        select_affiliate_transcript_text,
                    )

                    affiliate_partner = select_affiliate_partner(
                        load_affiliate_partners(settings),
                        transcript_text=select_affiliate_transcript_text(segments),
                        round_robin_index=idx - 1,
                    )
                except Exception as aff_err:
                    log.warning("Affiliate selection failed for stock short %d: %s", idx, aff_err)

            banner_kwargs = {}
            banner_path = getattr(affiliate_partner, "banner_path", None) if affiliate_partner else None
            if (
                banner_path
                and Path(banner_path).exists()
                and stock_affiliate_allowed(settings, actual_niche)
            ):
                banner_kwargs = {
                    "banner_image": banner_path,
                    "banner_position": settings.affiliate_banner_position,
                }

            bgm_kwargs = {}
            track: Path | None = None
            if settings.bgm_mode != "off":
                run_seed = _bgm_seed(seed, bg_path)
                if should_use_bgm(settings.bgm_mode, run_seed):
                    track = pick_track(settings.music_dir, run_seed, last_track)
                    if track is not None:
                        last_track = track
                        bgm_kwargs = {
                            "bgm_audio": track,
                            "bgm_volume": settings.bgm_volume,
                            "bgm_music_forward": False,
                        }

            # 7. Burn subtitles + voice + BGM in one pass.
            current_output_path = clip_work_dir / f"stock_short_{idx}.mp4"
            burn_subtitles(
                bg_path,
                segments,
                start_offset=0.0,
                output_path=current_output_path,
                pacing=1.0,
                video_codec=settings.video_codec,
                preset=settings.video_preset,
                style_name=settings.subtitle_style,
                vo_output_path=vo_path,
                flash_events=edit_flash,
                **banner_kwargs,
                **bgm_kwargs,
            )

            # 8. Metadata sidecar (LLM when available, local fallback otherwise).
            meta = _build_stock_meta(settings, current_output_path, script, idx, actual_niche)
            json_path = current_output_path.with_suffix(".json")
            _write_json_atomic(json_path, meta)

            output_paths.append(current_output_path)
            log.info("✅ Stock short %d ready at: %s", idx, current_output_path)

            # Persist a copy into the factory outputs folder so a temp-dir
            # cleanup can never eat a successful render (stock shorts, like
            # every other clip, land where the dashboard expects them).
            try:
                settings.output_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            try:
                base = settings.output_dir / current_output_path.name
                stable = _next_free_stable_path(base)
                _persist_file_atomic(current_output_path, stable)
                stable_meta = stable.with_suffix(".json")
                _write_json_atomic(stable_meta, meta)
                log.info("✅ Stock short persisted to outputs: %s", stable)
            except Exception as persist_err:
                log.warning("Could not persist stock short to outputs: %s", persist_err)

            if upload and settings.publish_platforms:
                _publish_stock(
                    settings,
                    current_output_path,
                    meta,
                    json_path,
                    segments,
                    actual_niche,
                    video_id,
                    privacy,
                    retention_grades,
                )

    _ensure_outputs_or_raise(output_paths)
    if count == 1:
        return output_paths[0]
    return output_paths


def _segments_from_word_bounds(
    word_bounds: list,
    seg_shift: float = 0.0,
    words_per_seg: int = 3,
):
    """Build ``TranscriptSegment``s from real spoken word boundaries.

    Groups consecutive words into short subtitle chunks (default 3 words)
    using the exact edge-tts start/end times so on-screen text follows the
    voice.  Applying *seg_shift* aligns the chunk window with the audible
    speech envelope (the TTS file may carry silent intro/outro).
    """
    from shorts_clipper.core.models import TranscriptSegment, TranscriptWord

    if not word_bounds:
        return []
    segments: list[TranscriptSegment] = []
    for i in range(0, len(word_bounds), words_per_seg):
        group = word_bounds[i : i + words_per_seg]
        if not group:
            break
        words: list[TranscriptWord] = []
        for w, start, end in group:
            words.append(
                TranscriptWord(
                    start=max(0.0, start + seg_shift),
                    end=max(0.0, end + seg_shift),
                    word=w,
                )
            )
        seg_start = min(w.start for w in words)
        seg_end = max(w.end for w in words)
        segments.append(
            TranscriptSegment(
                start=seg_start,
                end=seg_end,
                text=" ".join(w.word for w in words),
                words=words,
            )
        )
    if not segments:
        return []
    return segments


def _build_stock_meta(settings, video_path, script, idx, niche) -> dict:
    """Build title/description/tags for a stock short."""
    try:
        from shorts_clipper.core.models import TranscriptSegment
        from shorts_clipper.metadata.fallback import generate_fallback_metadata

        seg = TranscriptSegment(start=0.0, end=1.0, text=script)
        fallback = generate_fallback_metadata([seg], source_title=script, niche=niche)
        title = fallback.get("title") or script[:60]
        description = fallback.get("description") or script
        tags = list(fallback.get("tags") or []) + ["shorts", niche]
    except Exception as exc:
        log.warning("Stock metadata generation failed: %s", exc)
        title, description, tags = script[:60], script, ["shorts", niche]

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "publish_status": "idle",
        "video_id": f"stock-{idx}",
        "source_url": "",
        "niche": niche,
        "script": script,
    }


def _publish_stock(settings, video_path, meta, json_path, segments, niche, video_id, privacy, ret_grades) -> None:
    """Publish a stock short via the standard publishing engine."""
    from shorts_clipper.publishers import ClipMetadata, PublishingEngine

    meta["publish_status"] = "uploading"
    _write_json_atomic(json_path, meta)

    clip_metadata = ClipMetadata(
        title=meta["title"],
        description=meta["description"],
        tags=meta.get("tags", ["shorts"]),
        privacy_status=privacy,
    )
    try:
        engine = PublishingEngine()
        results = engine.publish(
            video_path=video_path,
            metadata=clip_metadata,
            platforms=settings.publish_platforms,
            video_id=video_id,
            transcript_text=" ".join(s.text for s in segments),
            niche=niche,
            retention_grades=ret_grades,
        )
        meta["publish_results"] = {
            p: {
                "success": r.success,
                "url": r.url,
                "platform_id": r.platform_id,
                "error_message": r.error_message,
            }
            for p, r in results.items()
        }
        successes = [r for r in results.values() if r.success]
        meta["publish_status"] = (
            "success" if len(successes) == len(results) and results else "failed"
        )
        log.info("Stock short publish status: %s", meta["publish_status"])
    except Exception as upload_err:
        meta["publish_status"] = "failed"
        meta["publish_error"] = str(upload_err)
        log.error("Stock short publish failed: %s", upload_err)
    finally:
        _write_json_atomic(json_path, meta)
