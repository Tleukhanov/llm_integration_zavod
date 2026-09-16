# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-16

### Fixed
- **Worker:** Corrected the thumbnailer import path that silently disabled thumbnails during the pipeline (`short_clipper.render.thumbnailer` → `rendering.thumbnailer`).
- **API:** `save_settings` no longer wipes unrelated `.env` keys; merged writes are now atomic (temp file + `os.replace`).
- **API:** Clip endpoints are protected against path traversal; missing clips now return a proper `404`.
- **Scout:** Fixed job-cancellation detection (`job.status`), age-relaxation returning the relaxed value instead of a hardcoded multiple, Stage B ranking over `finalists`, and numeric score coercion when the model emits junk counts. `subtitle_cache` is thread-safe (singleton init under a lock); the video keyword expander logs instead of `print` and no longer sleeps 35s after failures.
- **Rendering:** yt-dlp errors no longer deadlock the downloader (stderr drained on a background thread); transcodes force `-pix_fmt yuv420p`; ffmpeg calls have timeouts.
- **Editorial:** Replaced placeholder judges with real ones (`Length`, `NarrativeArc`, `InformationDensity`, `QuestionAnswer`); hook energy uses the opening 5-second window instead of the whole clip; partially-overlapping segments are no longer dropped; feature extraction falls back to word counting when word timestamps are missing.
- **Compliance:** Publish gate is transcript-aware and fails closed on reviewer errors (CMP-1/2/3).
- **Attention:** Simulated clip windows are clamped to the source duration (RND-2).
- **Publishers:** R2 upload is now gated to Instagram/TikTok only; YouTube publishes normally when R2 is unavailable; TikTok error handling chains exceptions properly; all timestamp code uses `datetime.UTC`.
- **Core/Cache:** SQLite queue uses WAL + `busy_timeout`; cache timestamps are UTC-consistent; stats cache has its own namespace; empty cache writes are skipped; audio cache resolves any file extension.
- **Whisper/HuggingFace:** Model files are copied instead of symlinked on Windows (fixes WinError 1314).
- **Lint:** 520 tests pass; ruff clean for all touched modules.

### Changed
- `.env.example` synced with the current `Settings` schema (R2, game-mode, BGM/phonk, hook banner, VO, metrics, daily cap, providers).

## [0.1.0] - 2026-07-03

### Added
- **Editorial Engine:** Introduced a new, local-first, deterministic editorial core (`shorts_clipper/editorial/`) to replace LLM-based timestamp selection.
- **Feature Store:** Added `FeatureStore` to parse transcript segments and compute speech rates, pause durations, and syntax densities.
- **Editorial Plugins:** Added a suite of independent judges (`hook.py`, `silence.py`, `length.py`, `context.py`, `emotion.py`) to score video segments based on human editing principles.
- **Editorial Profiles:** Added `EditorialProfile` structures to provide weighted presets for different niches.

### Changed
- **Gemini Role Reduction:** Demoted Gemini from the primary video editor to a secondary validator and metadata/SEO generator.
- **Scout V2 Logic:** Updated `scout/trending.py` to leverage the new deterministic `EditorialEngine` for selecting finalized timestamps rather than making direct LLM requests.
- **Instagram Publishing:** Migrated temporary file hosting from the deprecated Catbox API to `tmpfiles.org` for stable Graph API uploads.

### Fixed
- **Architectural Bypass Patch:** Fixed a critical bug where cached Gemini selections in the Scout module were overriding the newly integrated local Editorial Engine during end-to-end autopilot runs.

## [0.0.1] - Initial development
- Initial implementation of Scout V2 and multi-publisher pipelines.
