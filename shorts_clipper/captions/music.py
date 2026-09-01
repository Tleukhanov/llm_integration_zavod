"""Background-music helpers for the shorts render pipeline."""

from __future__ import annotations

import logging
import random
import wave
from pathlib import Path

log = logging.getLogger(__name__)

_MUSIC_EXTS = {".mp3", ".m4a", ".ogg", ".wav"}

# Minimum duration (seconds) for a track to be considered "long enough" to cover a
# clip in a single pass without looping.
LONG_TRACK_SECONDS = 60.0

# Best-effort, per-process cache of {path: duration_seconds|None}.  Reading
# duration (especially via ffmpeg for non-WAV formats) is only cheap when cached,
# so we never re-probe the same file more than once per process.
_duration_cache: dict[str, float | None] = {}


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


def track_duration(path: Path | str) -> float | None:
    """Best-effort duration (seconds) of an audio track, or ``None`` if unknown.

    WAV files are read natively via the stdlib ``wave`` module (no subprocess).
    Other formats fall back to an ffmpeg probe.  Results are cached per process
    so repeated calls (and repeated ``pick_track`` runs within one process) are
    cheap.  A probe failure returns ``None`` and is cached, so callers can rely
    on this never raising.
    """
    path = Path(path)
    key = str(path)
    if key in _duration_cache:
        return _duration_cache[key]

    try:
        if path.suffix.lower() == ".wav":
            duration = _wav_duration(path)
        else:
            duration = _ffmpeg_duration(path)
    except Exception:
        log.debug("Failed to probe duration for %s", path, exc_info=True)
        duration = None

    _duration_cache[key] = duration
    return duration


def _wav_duration(path: Path) -> float | None:
    with wave.open(str(path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
    if rate <= 0:
        return None
    return frames / rate


def _ffmpeg_duration(path: Path) -> float | None:
    from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

    import re
    import subprocess

    cmd = [ffmpeg_path(), "-i", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    text = (proc.stderr or "") + (proc.stdout or "")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def pick_track(
    music_dir: Path,
    rng: random.Random,
    last_track: Path | None = None,
) -> Path | None:
    """Pick a random music track, avoiding immediate repeat when possible.

    Prefers tracks that are long enough (``>= LONG_TRACK_SECONDS``) to cover a
    clip in a single pass, so the BGM doesn't run out part-way.  If no track has
    a known duration >= threshold (e.g. only short or unprobeable files), falls
    back to a fully random pick.  Returns ``None`` when the directory has no
    playable tracks.
    """
    tracks = list_tracks(music_dir)
    if not tracks:
        return None
    if len(tracks) == 1:
        return tracks[0]
    candidates = [t for t in tracks if t != last_track] if last_track else tracks
    if not candidates:
        candidates = tracks

    long = [t for t in candidates if (track_duration(t) or 0.0) >= LONG_TRACK_SECONDS]
    # Prefer long tracks, but only when there are at least two so a single long
    # track isn't picked every time (avoiding immediate repeats); otherwise
    # fall back to the full candidate pool.
    pool = long if len(long) >= 2 else candidates
    return rng.choice(pool)
