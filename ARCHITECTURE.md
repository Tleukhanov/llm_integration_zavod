# System Architecture

## High-level pipeline

```
Scout V2 → Editorial Engine → Download → Transcription → Rendering → Metadata → Publishing
```

## Core modules

### Scout (`shorts_clipper/scout/`)
Discovers trending YouTube videos by keyword or niche. Uses yt-dlp flat search (and optionally the YouTube Data API). Evaluates candidates across two stages:
- **Stage A:** Metadata ranking by views, recency, engagement ratio, and channel diversity.
- **Stage B:** Subtitle fetch and Gemini-based clip generation scoring for the top candidates.
- **Semantic gating:** Gemini filters candidates for niche relevance before ranking.
- **Counterfactual simulation:** Generates variant clips (e.g., trimmed pauses, alternate start points) and picks the best.

### Editorial Engine (`shorts_clipper/editorial/`)
Deterministic segment selection using 8 plugin judges:
1. Hook quality
2. Silence/dead-air detection
3. Length fitness
4. Topical context coherence
5. Emotional intensity
6. Narrative arc
7. Information density
8. Question-answer structure

A feature store pre-computes transcript metrics (speech rate, pause distribution, sentence boundaries). Each judge scores independently. Confidence aggregation combines scores with weighted profiles that vary by niche.

If a single plugin crashes, it is disabled for that run and confidence math redistributes the weights.

### Rendering (`shorts_clipper/rendering/`)
Two-pass FFmpeg pipeline:
- Pass 1: Vertical crop (16:9 → 9:16, center crop).
- Pass 2: Subtitle burn with ASS styling and configurable pacing multiplier.

Transcription uses `faster-whisper` running locally (CPU or GPU) for word-level timestamps.

### Publishing (`shorts_clipper/publishers/`)
Registry-based architecture. Ships with:
- **YouTube:** OAuth2 with resumable chunked upload.
- **Instagram:** Graph API using `IG_ACCESS_TOKEN` and `IG_ACCOUNT_ID`. Requires `PUBLIC_URL` or temp file hosting for media staging.

Adding a new platform requires implementing the `Publisher` interface and registering it. No pipeline changes needed.

### Job queue and API (`shorts_clipper/core/`, `shorts_clipper/api/`)
- SQLite-backed job queue (`core/queue.py`) with a decoupled worker (`core/worker.py`).
- FastAPI web dashboard (Vanguard Console) with SSE live logs.

## Data persistence
- **Queue and jobs:** SQLite
- **Cache:** SQLite for metadata, AI selections, and transcription artifacts
- **Tokens:** Pickle file for YouTube OAuth2 credentials
