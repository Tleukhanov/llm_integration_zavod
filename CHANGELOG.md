# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.2.0] - 2026-07-03

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

## [3.1.0] - Previous releases...
- Initial implementation of Scout V2 and multi-publisher pipelines.
