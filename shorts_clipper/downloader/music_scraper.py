"""Download royalty-free phonk / phonk-style BGM tracks for the render pipeline.

Tried sources, in order:

1. **Pixabay Music** – large phonk catalog under the Pixabay Content License
   (free commercial use, no attribution). No official music API, but CDN MP3
   URLs are embedded in the search HTML. `urlopen` may be blocked (HTTP 403,
   Cloudflare) on some hosts/IPs.
2. **free-stock-music.com** – royalty-free library; most tracks are CC BY
   (attribution required). Exposes direct MP3 paths in the search HTML and is
   not Cloudflare-blocked on hosts where Pixabay returns 403, so it acts as a
   robust fallback. We record the artist/title so the caller can add a credit
   line to the video description.

Both feed the existing ``pick_track`` machinery
(``shorts_clipper/captions/music.py``) with real, license-safe music instead of
relying only on the procedural generator.

This module keeps its dependency footprint minimal (stdlib ``urllib`` only).
"""

from __future__ import annotations

import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_PIXABAY_SEARCH = "https://pixabay.com/music/search/phonk/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_AUDIO_RE = re.compile(r"https://cdn\.pixabay\.com/(?:audio|video)/[^\"'\\ ]+\.mp3")
_MP3_RE = re.compile(
    r"""(?:href|src)=["']([^"']+?\.mp3)["']"""
)
_AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".wav"}

# Audible file magic signatures used to reject HTML/error pages that some
# sources return instead of real audio (e.g. cloudflare/403 bodies saved with
# an .mp3 name). Keyed by the extension we expect.
_AUDIO_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".wav": (b"RIFF",),
    ".ogg": (b"OggS",),
    ".m4a": (b"\x00\x00\x00", b"ftyp"),
}


def _is_audible_bytes(data: bytes, suffix: str) -> bool:
    """Return True if *data* looks like real audio for *suffix* (or is empty).

    Sources occasionally answer HTTP 200 with an HTML/error page body instead
    of the requested audio (Cloudflare challenges, reference-blocked CDNs). We
    refuse to keep those under an audio name so we never feed garbage to the
    FFmpeg mix.
    """
    if not data:
        return False
    magic = _AUDIO_MAGIC.get(suffix.lower())
    if magic is None:
        return len(data) > 0
    return any(data.startswith(sig) for sig in magic)

# free-stock-music.com base. Tracks carry CC BY licences; we wrap each MP3 with
# its human-readable artist/title so callers can emit an attribution line.
_FREESTOK_BASE = "https://www.free-stock-music.com"
_FREESTOK_SEARCH = _FREESTOK_BASE + "/?s=phonk"


@dataclass
class Track:
    """A downloadable music track with optional attribution metadata."""

    url: str
    name: str
    artist: str | None = None
    license: str | None = None


def _fetch_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def scrape_pixabay_urls(max_pages: int = 1) -> list[str]:
    """Return de-duplicated Pixabay CDN MP3 URLs for phonk search.

    May raise/return-empty when Pixabay blocks the request (403) or parses to
    nothing. Callers should fall back to another source on failure.
    """
    urls: list[str] = []
    for page in range(1, max_pages + 1):
        url = f"{_PIXABAY_SEARCH}?pagi={page}"
        try:
            html = _fetch_html(url)
        except Exception as exc:
            log.warning("Pixabay scrape page %d failed: %s", page, exc)
            continue
        page_urls = _AUDIO_RE.findall(html)
        log.info("Pixabay phonk page %d: found %d audio URLs", page, len(page_urls))
        urls.extend(page_urls)
    return list(dict.fromkeys(urls))


def scrape_freestock_tracks(max_results: int = 12) -> list[Track]:
    """Scrape phonk search on free-stock-music.com for downloadable MP3 tracks.

    Exposes tracks as ``Track`` objects so callers can also show an
    attribution (CC BY requires it). Never raises on network errors.
    """
    try:
        html = _fetch_html(_FREESTOK_SEARCH)
    except Exception as exc:
        log.warning("free-stock-music.com scrape failed: %s", exc)
        return []

    tracks: list[Track] = []
    seen: set[str] = set()
    # Find each MP3 reference plus a recognizable title slug derived from path.
    for match in _MP3_RE.finditer(html):
        raw = match.group(1)
        if not raw.startswith(("http://", "https://")):
            raw = _FREESTOK_BASE + raw
        if raw in seen:
            continue
        seen.add(raw)
        if not raw.startswith(_FREESTOK_BASE):
            continue
        slug = raw.rstrip("/").split("/")[-1].removesuffix(".mp3")
        tracks.append(
            Track(
                url=raw,
                name=slug,
                artist=None,
                license="CC BY (attribution required)",
            )
        )
        if len(tracks) >= max_results:
            break
    log.info("free-stock-music.com phonk: found %d tracks", len(tracks))
    return tracks


def _pixabay_track_name(audio_url: str) -> str:
    stem = audio_url.rstrip("/").split("/")[-1]
    stem = re.sub(r"\.[^.\/]+$", "", stem)
    return f"pixabay_phonk_{stem}.mp3"


def fetch_phonk_tracks(music_dir: Path, max_tracks: int = 10, max_pages: int = 2) -> list[Path]:
    """Scrape any reachable source and download up to *max_tracks* MP3s.

    Tries Pixabay first; if every page fails (403/cloudflare), falls back to
    free-stock-music.com so the music pool is still topped up. Skips files
    already present. Never raises.
    """
    music_dir = Path(music_dir)
    music_dir.mkdir(parents=True, exist_ok=True)

    pixabay_urls = scrape_pixabay_urls(max_pages=max_pages)
    downloaded: list[Path] = []

    if pixabay_urls:
        for audio_url in pixabay_urls:
            if len(downloaded) >= max_tracks:
                break
            name = _pixabay_track_name(audio_url)
            dest = music_dir / name
            if dest.exists():
                downloaded.append(dest)
                continue
            if _download(audio_url, dest):
                downloaded.append(dest)
        if downloaded:
            return downloaded
        log.info("Pixabay produced no tracks; falling back to free-stock-music.com")

    for track in scrape_freestock_tracks(max_results=max_tracks):
        if len(downloaded) >= max_tracks:
            break
        dest = music_dir / f"freestock_{track.name}.mp3"
        if dest.exists():
            downloaded.append(dest)
            continue
        if _download(track.url, dest):
            # Record attribution alongside for later credit line assembly.
            _write_attribution(music_dir, dest, track)
            downloaded.append(dest)
    return downloaded


def _download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
            fh.write(resp.read())
        if dest.stat().st_size == 0 or not _is_audible_bytes(
            dest.read_bytes(), dest.suffix
        ):
            dest.unlink(missing_ok=True)
            log.warning("Rejected non-audio payload from %s", url)
            return False
        log.info("Downloaded track -> %s", dest)
        return True
    except Exception as exc:
        log.warning("Failed to download %s: %s", url, exc)
        if dest.exists() and dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
        return False


def _write_attribution(music_dir: Path, dest: Path, track: Track) -> None:
    """Persist a credit line for CC-BY tracks so publishers can attribute."""
    credit_file = music_dir / f"{dest.stem}.attribution.txt"
    try:
        credit_file.write_text(
            f"Track: {track.name}\n"
            f"Source: {track.url}\n"
            f"License: {track.license or 'unknown'}\n"
            f"Credit: Free Stock Music (free-stock-music.com)\n",
            encoding="utf-8",
        )
    except OSError:
        log.debug("Could not write attribution file %s", credit_file, exc_info=True)


def ensure_phonk_tracks(
    music_dir: Path,
    min_tracks: int = 2,
    fetch_count: int = 6,
    max_pages: int = 2,
) -> None:
    """Top up *music_dir* with license-safe tracks when it is too sparse.

    Call this before ``pick_track`` to guarantee a usable music pool. Limited
    to a handful of downloads per run so repeated invocations stay cheap and
    don't hammer the sources.
    """
    music_dir = Path(music_dir)
    existing = [
        p for p in music_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
    ] if music_dir.is_dir() else []
    if len(existing) >= min_tracks:
        log.info("music_dir already has %d tracks; skipping fetch", len(existing))
        return
    log.info("music_dir sparse (%d tracks); fetching royalty-free tracks", len(existing))
    fetch_phonk_tracks(music_dir, max_tracks=fetch_count, max_pages=max_pages)

