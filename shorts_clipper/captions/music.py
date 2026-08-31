"""Background-music helpers for the shorts render pipeline."""

from __future__ import annotations

import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)

_MUSIC_EXTS = {".mp3", ".m4a", ".ogg", ".wav"}


def list_tracks(music_dir: Path) -> list[Path]:
    """Return sorted music files inside *music_dir*.

    Returns an empty list when the directory does not exist or contains
    no supported audio files.  The result is deterministic (alphabetical
    sort) so that callers can rely on stable ordering.
    """
    try:
        if not music_dir.is_dir():
            return []
        return sorted(
            p for p in music_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _MUSIC_EXTS
        )
    except OSError:
        return []


def should_use_bgm(mode: str, rng: random.Random) -> bool:
    """Decide whether the current clip should receive background music.

    Parameters
    ----------
    mode:
        ``"off"``   – never use bgm
        ``"music"`` – always use bgm
        ``"mix50"`` – 50 / 50 coin-flip (deterministic via *rng*)
        ``"auto"``  – always True (future: tie to energetic windows)
    rng:
        Seeded random instance for reproducibility.
    """
    if mode == "off":
        return False
    if mode in ("music", "auto"):
        return True
    if mode == "mix50":
        return rng.random() < 0.5
    return False


def pick_track(
    music_dir: Path,
    rng: random.Random,
    last_track: Path | None = None,
) -> Path | None:
    """Pick a random music track, avoiding immediate repeat when possible.

    Returns ``None`` when the directory has no playable tracks.
    """
    tracks = list_tracks(music_dir)
    if not tracks:
        return None
    if len(tracks) == 1:
        return tracks[0]
    candidates = [t for t in tracks if t != last_track] if last_track else tracks
    if not candidates:
        candidates = tracks
    return rng.choice(candidates)
