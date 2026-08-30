# Multi-Platform Publishing Engine

Shorts Clipper now includes a generic Multi-Platform Publishing Engine that makes it trivial to distribute clips across multiple platforms autonomously.

## Architecture

The engine uses a Registry-based design:
- `PublishingEngine`: Core orchestrator. Iterates through platforms, attempts authentication, executes publishing, handles retries, and generates a manifest.
- `Publisher`: An abstract base class defining the contract for all platform integrations (`authenticate`, `publish`, `verify`).
- `PublisherRegistry`: A centralized factory where publishers register themselves.

When a clip finishes rendering, the pipeline simply asks the `PublishingEngine` to publish the file to a list of target platforms. The engine operates entirely platform-agnostic.

## Supported Platforms

1. **YouTube Shorts** (`youtube`)
2. **Instagram Reels** (`instagram`)

## Configuration

Publishing is controlled via the `Settings` class and your `.env` file. By default, both platforms are enabled if credentials exist.

```env
# Platforms to publish to (comma-separated)
SHORTS_PUBLISH_PLATFORMS=youtube,instagram

# Instagram Credentials (Graph API)
IG_ACCESS_TOKEN=your_token
IG_ACCOUNT_ID=your_id
PUBLIC_URL=https://your-domain.com
```

## Authentication Flow

### YouTube
YouTube uses OAuth2. The first time you use it, you must run the Web UI and link your account via the sidebar. This generates a `.cache/shorts-clipper/token.pickle` file containing access and refresh tokens. The Publishing Engine will automatically use and refresh these tokens.

### Instagram
Instagram uses the official Meta Graph API. It authenticates directly using your `IG_ACCESS_TOKEN` and `IG_ACCOUNT_ID` from `.env`. Because the Graph API requires your video to be publicly accessible during upload, you must also specify `PUBLIC_URL` pointing to a reverse proxy (like ngrok or localtunnel) if running locally, or the direct domain of your server.

## How to Add a Future Publisher

Adding a new platform like TikTok or Facebook is simple and requires **zero** changes to the main pipeline.

1. Create a new directory in `shorts_clipper/publishers/` (e.g., `tiktok/`).
2. Create `publisher.py` and implement the `Publisher` interface.
3. Define the `platform_name`, `authenticate()`, `publish()`, and `verify()` methods.
4. Open `shorts_clipper/publishers/__init__.py` and register your new class:
   ```python
   from .tiktok.publisher import TikTokPublisher
   PublisherRegistry.register(TikTokPublisher)
   ```
5. Add `tiktok` to `SHORTS_PUBLISH_PLATFORMS` in your `.env`.

The Publishing Engine will automatically detect and execute your new publisher in the next run.
