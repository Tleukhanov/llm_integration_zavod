# Shorts Clipper

Finds trending YouTube videos, selects an engaging segment, crops to vertical 9:16, burns word-level subtitles, generates metadata, and publishes to YouTube Shorts and Instagram Reels.

## What is this and what problem does it solve?
Creating short-form content from long-form video traditionally requires hours of manual scouting, clipping, and editing. Shorts Clipper automates the entire pipeline. It discovers trending videos, finds the most engaging segment, edits it for vertical viewing, and publishes it autonomously.

## What is different from other projects?
Most AI video clippers rely heavily on Large Language Models (LLMs) to choose where to clip. This is slow, expensive, and non-deterministic. Shorts Clipper’s segment selection is **deterministic** — no LLM is involved in choosing *what* to clip. An ensemble of 8 scoring judges evaluates transcript features (hooks, silence gaps, narrative arc, information density, etc.) to pick the best segment locally. Gemini is used only for metadata generation (titles/tags) and upstream semantic filtering during discovery.

## Architecture

```mermaid
graph LR
    A["Scout V2<br/>Trending discovery"] --> B["Editorial Engine<br/>Segment selection"]
    B --> C["Download<br/>yt-dlp segment"]
    C --> D["Transcription<br/>faster-whisper"]
    D --> E["Rendering<br/>FFmpeg 2-pass"]
    E --> F["Metadata<br/>Gemini"]
    F --> G["Publishing<br/>YouTube / Instagram"]

    style A fill:#2d333b,stroke:#444,color:#e6edf3
    style B fill:#2d333b,stroke:#444,color:#e6edf3
    style C fill:#2d333b,stroke:#444,color:#e6edf3
    style D fill:#2d333b,stroke:#444,color:#e6edf3
    style E fill:#2d333b,stroke:#444,color:#e6edf3
    style F fill:#2d333b,stroke:#444,color:#e6edf3
    style G fill:#2d333b,stroke:#444,color:#e6edf3
```

| Stage | Directory | What it does |
|---|---|---|
| **Scout V2** | `shorts_clipper/scout/` | Searches YouTube via yt-dlp (optionally YouTube Data API). Stage A ranks candidates by views, recency, engagement. Top 3 go to Stage B where Gemini evaluates clip generation potential. Counterfactual variant simulation optimizes the final pick. |
| **Editorial Engine** | `shorts_clipper/editorial/` | Deterministic segment selection. 8 plugin judges (hook, silence, length, context, emotion, narrative arc, information density, question-answer) score candidate segments. Feature store pre-computes transcript features. No LLM. |
| **Download** | `shorts_clipper/downloader/` | Downloads only the selected segment via yt-dlp, not the full video. |
| **Transcription** | `shorts_clipper/transcription/` | Runs faster-whisper locally (CPU or GPU). Produces word-level timestamps for subtitle generation. |
| **Rendering** | `shorts_clipper/rendering/` | Two-pass FFmpeg. Pass 1: vertical crop (16:9 → 9:16, center crop). Pass 2: subtitle burn with ASS styling and configurable pacing. |
| **Metadata** | `shorts_clipper/providers/gemini.py` | Gemini generates title, description, and tags. This is the only LLM call in the main pipeline. |
| **Publishing** | `shorts_clipper/publishers/` | Registry-based. Ships with YouTube (OAuth2, resumable upload) and Instagram (Graph API). |
| **Observability** | `shorts_clipper/core/observability.py` | RunContext singleton tracks decision traces, attention reports, variant reports, score breakdowns. Exports JSON artifacts per run. |

## Quick Start

**Prerequisites:** Python ≥ 3.11, FFmpeg with libx264 in PATH.

```bash
# Clone and install
git clone https://github.com/random-or/shorts-clipper.git
cd shorts-clipper
pip install -e .

# Minimum config — just metadata generation needs an API key
echo "GEMINI_API_KEY=your-key-here" > .env

# Clip a specific video (no publishing)
shorts-clipper clip https://www.youtube.com/watch?v=dQw4w9WgXcQ

# Discover trending videos for a keyword and clip the best one
shorts-clipper autopilot --keyword "python programming"

# Discover + clip + upload to YouTube
shorts-clipper autopilot --keyword "tech reviews" --upload

# Just scout — print ranked URLs and exit
shorts-clipper scout --keyword "AI news" -n 5

# Start the web dashboard
shorts-clipper web
```

You can also run via module: `python -m shorts_clipper <command>`.

## CLI Reference

```
shorts-clipper [--log-level {DEBUG,INFO,WARNING,ERROR}] [--env <path>] <command>

Commands:
  autopilot          Scout + clip + optional publish
    --keyword TEXT       Search keyword
    --niche TEXT         Content niche for editorial profile
    --channel TEXT       Specific channel to search
    --count INT          Number of clips to produce
    --upload             Publish after clipping

  clip <url>           Clip a specific YouTube video
    -o, --output PATH    Output file path
    -c, --count INT      Number of clips
    --upload             Publish after clipping

  scout                Print trending URLs and exit
    -n, --count INT      Number of results
    --keyword TEXT       Search keyword
    --niche TEXT         Content niche
    --channel TEXT       Specific channel

  web                  Start FastAPI web dashboard (Vanguard Console)
    --host TEXT          Bind address (default: 127.0.0.1)
    --port INT           Port (default: 8000)

  repair-metadata      Backfill missing metadata on existing clips
```

## Environment Variables

### Required

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key. Used for metadata generation and semantic validation in Scout. |

### Publishing (required for `--upload`)

| Variable | Description |
|---|---|
| `YOUTUBE_CLIENT_ID` | YouTube OAuth2 client ID |
| `YOUTUBE_CLIENT_SECRET` | YouTube OAuth2 client secret |
| `IG_ACCESS_TOKEN` | Instagram Graph API access token |
| `IG_ACCOUNT_ID` | Instagram account ID |
| `PUBLIC_URL` | Base URL where server is publicly reachable (required for Instagram unless using temp hosts) |
| `SHORTS_USE_TEMP_HOSTS` | Set to `true` to use temporary file hosting instead of `PUBLIC_URL` for Instagram |

### Optional

| Variable | Default | Description |
|---|---|---|
| `YOUTUBE_API_KEY` | — | YouTube Data API key. Helps Scout avoid yt-dlp rate limits. |
| `SHORTS_ENABLE_GPU` | `false` | Enable CUDA for whisper + nvenc for FFmpeg |
| `SHORTS_WHISPER_MODEL` | `tiny.en` | Whisper model size |
| `SHORTS_WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` (auto-set to `cuda` if GPU enabled) |
| `SHORTS_WHISPER_COMPUTE_TYPE` | `int8` | `int8` or `float16` (auto-set to `float16` if GPU enabled) |
| `SHORTS_VIDEO_CODEC` | `libx264` | `libx264` or `h264_nvenc` (auto-set if GPU enabled) |
| `SHORTS_VIDEO_PRESET` | `ultrafast` | `ultrafast` or `fast` (auto-set to `fast` if GPU enabled) |
| `SHORTS_SCOUT_MAX_AGE_DAYS` | `90` | Max age of videos Scout considers |
| `SHORTS_SUBTITLE_STYLE` | `default` | Subtitle styling preset |
| `SHORTS_PROXY` | — | HTTP proxy for yt-dlp and network requests |
| `SHORTS_PUBLISH_PLATFORMS` | `youtube,instagram` | Comma-separated list of platforms |
| `SHORTS_PROVIDER` | `gemini` | AI provider: `gemini`, `openai`, `anthropic`, `ollama` |
| `SHORTS_LOG_LEVEL` | `INFO` | Logging verbosity |
| `SHORTS_OUTPUT_DIR` | `outputs` | Output directory for rendered clips |
| `SHORTS_CACHE_DIR` | `.cache/shorts-clipper` | Cache directory |
| `SHORTS_MODELS_DIR` | `models` | Whisper models directory |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OPENAI_API_KEY` | — | OpenAI API key (if using `openai` provider) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (if using `anthropic` provider) |

> **Note:** The `.env.example` file in the repo is outdated. Use this table as the source of truth.

## Example Output

A successful `autopilot` run looks like this:

```
========== SCOUT STAGE A: METADATA RANKING (DIVERSIFIED) ==========
Rank  | Video ID    | Views      | Score    | Title
1     | 8reaJG7z-is | 4594029    | 32.87    | I Visited Apple's Secret iPhone Testing Labs!
2     | 55OfSZ1EU2A | 2420142    | 20.01    | I Surprised Tim Cook With The First iPhone
3     | cwmqgI8MYY4 | 367329     | 16.29    | We Tried to Break the iPhone Air

========== FINALIST SELECTED ==========
Video 55OfSZ1EU2A selected as finalist with Candidate Generation Score: 98.50

========== AUTOPILOT REPORT ==========
Query: ytsearch15:iPhone interview
Candidates Found: 30
Candidates Filtered Out: 21
Top Candidate: I Surprised Tim Cook With The First iPhone
Processing Time: 1269.42s
API Calls: 3
```

Output files land in `outputs/` (or `SHORTS_OUTPUT_DIR`). Each run produces the rendered MP4, a thumbnail, and a JSON observability artifact with full decision traces.

## Performance

Rough numbers from a 2-core CPU, `tiny.en` model, no GPU. Not rigorously benchmarked.

| Stage | Time |
|---|---|
| End-to-end (single clip) | ~20 min |
| Scout discovery + ranking | ~3 min |
| Whisper transcription (110s audio) | ~64s |
| FFmpeg crop | ~3.5 min |
| FFmpeg subtitle burn | ~2.5 min |
| YouTube upload (120 MB) | ~5 min |

API cost per clip is effectively zero on the Gemini free tier. GPU (`SHORTS_ENABLE_GPU=true`) significantly reduces transcription and rendering time.

## Project Structure

```
shorts_clipper/
├── api/                    # FastAPI server (Vanguard Console)
├── analyze/                # Post-hoc feedback analysis
├── attention/              # Counterfactual variant simulation
├── captions/               # ASS subtitle generation + ffmpeg burn
├── cli/                    # Auxiliary CLI commands (repair-metadata)
├── core/                   # Settings, cache, queue, worker, models, logging, observability
├── cropping/               # Crop geometry calculations
├── downloader/             # yt-dlp integration
├── editorial/              # Deterministic segment selection
│   ├── engine.py           # EditorialEngine orchestrator
│   ├── feature_store.py    # Transcript feature computation
│   ├── confidence.py       # Confidence aggregation
│   ├── profiles.py         # Weighted presets for different niches
│   ├── registry.py         # Plugin registry
│   └── plugins/            # 8 scoring judges
├── highlight_detection/    # Legacy deterministic scoring (pre-Editorial Engine)
├── metadata/               # Fallback metadata generation
├── pipeline/               # Pipeline orchestrator
├── providers/              # LLM provider abstraction
├── publishers/             # Multi-platform publishing
│   ├── youtube/            # YouTube OAuth2 + resumable upload
│   └── instagram/          # Instagram Graph API
├── rendering/              # FFmpeg crop, render, thumbnail
├── scout/                  # Scout V2 trending discovery
├── transcription/          # faster-whisper integration
├── ui/                     # Static HTML/CSS/JS for web dashboard
└── utils/                  # Video utilities
tests/                      # 75 tests + 2 benchmarks
```

## Known Limitations

1. YouTube upload retry restarts from 0% on timeout rather than resuming from the last byte. Large uploads on unstable connections may fail.
2. Instagram publishing requires either `PUBLIC_URL` pointing to a publicly reachable server, or `SHORTS_USE_TEMP_HOSTS=true` to use temporary file hosting.
3. Scout ranking weights were tuned manually. Consistent quality across all niches is not validated at scale.
4. Whisper on CPU is slow (~64s for 110s of audio with `tiny.en`). GPU is recommended for regular use.
5. No face or person detection for smart cropping. Center crop only.
6. The web dashboard (Vanguard Console) is functional but minimal.
7. Docker support exists (`Dockerfile` + `docker-compose.yml`) but is not regularly tested.
8. Only YouTube discovery is supported. No TikTok or Instagram discovery.
9. The counterfactual variant simulation engine uses heuristic models, not learned ones.

## Troubleshooting

**`ffmpeg: command not found`**
Install FFmpeg with libx264 support. On Ubuntu: `sudo apt install ffmpeg`. On macOS: `brew install ffmpeg`.

**Whisper model download hangs or fails**
Models are downloaded on first use. If the download fails, delete `models/` (or `SHORTS_MODELS_DIR`) and retry. Check your network and proxy settings (`SHORTS_PROXY`).

**YouTube upload fails with auth error**
Delete `.cache/shorts-clipper/token.pickle` and re-authorize. Run `shorts-clipper web`, open the dashboard, and use the sidebar to link your YouTube account.

**Instagram publish fails with "URL not reachable"**
Instagram needs to fetch your video from a public URL. Either set `PUBLIC_URL` to a publicly reachable address where the web server is running, or set `SHORTS_USE_TEMP_HOSTS=true` to upload to a temporary file host.

**`CUDA out of memory`**
Reduce the Whisper model size: `SHORTS_WHISPER_MODEL=tiny.en`. Or switch to CPU: `SHORTS_ENABLE_GPU=false`.

**Scout returns no candidates**
Try broader keywords. Check that yt-dlp is up to date (`pip install -U yt-dlp`). If you hit YouTube rate limits, set `YOUTUBE_API_KEY` for Data API fallback.

**Slow rendering**
Rendering is CPU-bound by default. Enable GPU acceleration with `SHORTS_ENABLE_GPU=true` if you have an NVIDIA GPU with CUDA and nvenc support.

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Lint
ruff check . && ruff format --check .
```

75 tests and 2 benchmarks, all passing.

## Contributing

1. Fork the repo and create a branch.
2. Make your changes.
3. Run `python -m pytest tests/ -v` — all tests must pass.
4. Run `ruff check . && ruff format --check .` — no lint errors.
5. Open a pull request.

## Explainability Reports
Because segment selection relies on deterministic heuristics rather than LLMs, every autopilot run generates a JSON observability artifact in `outputs/`. This report contains the complete decision trace, attention matrices, and score breakdowns for every stage of the pipeline so you know exactly *why* a specific timestamp was selected.

## Roadmap
- **V4.0 (SaaS Transition):** Replace SQLite with PostgreSQL, migrate local worker queue to Celery/Redis, and secure the FastAPI interface with JWT authentication for multi-tenant deployment.
- **Smart Cropping:** Integrate OpenCV/MediaPipe for face and active-speaker tracking during cropping.
- **Multi-Platform Discovery:** Add Scout support for TikTok and Instagram.
- **Learned Variant Models:** Train heuristics for counterfactual simulation instead of using static rules.

## FAQ
**Q: Can I run this without a GPU?**
A: Yes. `faster-whisper` falls back to CPU by default. Expect transcription to be slower (e.g., ~1 minute for 2 minutes of audio).

**Q: Do I need to pay for Gemini?**
A: The pipeline uses the free tier of Gemini 1.5 Flash (via Google AI Studio) for metadata generation, so the API cost per clip is effectively zero.

**Q: Why doesn't the YouTube uploader resume uploads?**
A: The current `google-api-python-client` integration resets the upload cursor on timeout. A true chunked resume implementation is planned for a future release.

## License

MIT
