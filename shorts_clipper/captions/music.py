"""Background-music helpers for the shorts render pipeline."""

from __future__ import annotations

import contextlib
import logging
import os
import random
import wave
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # numpy stays off the import path of non-scanning renders
    import numpy as np

log = logging.getLogger(__name__)

_MUSIC_EXTS = {".mp3", ".m4a", ".ogg", ".wav"}

# Procedurally synthesized loops (``scripts/make_phonk.py`` and friends) are a
# last-resort fallback, not real music: they are never auto-selected unless
# SHORTS_ALLOW_PROCEDURAL_MUSIC says otherwise.  Matching is by convention so
# any generator may adopt it — filename starts with ``generated_`` AND contains
# ``loop`` (e.g. ``generated_phonk_loop.wav``, ``generated_dark_techno_loop``).
_GENERATED_PREFIX = "generated_"
_GENERATED_LOOP_MARKER = "loop"

# Scratch/debug artifacts that must never reach a rendered clip. Unlike
# ``_GENERATED_PREFIX`` -- which is a legitimate procedural loop and is opt-in --
# these are byproducts of testing: a real run burned ``test_phonk_132.wav`` over
# a published short because a leftover sat in the pool beside the intended track
# and ``pick_track`` chose between them at random. Junk is filtered
# unconditionally, with no env override, because there is no operator scenario
# where a debug render is the BGM they want.
_JUNK_STEMS = ("test_", "test-", "tmp_", "tmp-", "debug_", "debug-", "scratch_")

# Operator switch for the last-resort synthesized loop
# (``ensure_synthesized_track``).  Default ON: the alternative is shipping a
# silent clip when the music scraper cannot reach the network.  Surfaced as
# ``Settings.synthesize_music`` (SHORTS_SYNTHESIZE_MUSIC) for the pipeline.
#
# The synthesized file therefore keeps a plain name
# (``dark_industrial.DEFAULT_FILENAME`` = ``dark_industrial_loop.wav``) instead
# of the ``generated_`` prefix: it is the *only* track in the pool by
# construction, so filtering it out would defeat the fallback.
_SYNTHESIZE_MUSIC_ENV = "SHORTS_SYNTHESIZE_MUSIC"

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


def _is_junk_music(path: Path) -> bool:
    """Return ``True`` for scratch/debug audio that must not be played.

    Matched on the filename stem prefix, case-insensitively, so ``Test_Render``
    and ``test_phonk_132.wav`` are both caught.
    """
    stem = path.stem.lower()
    return stem.startswith(_JUNK_STEMS)


def should_use_bgm(mode: str, rng: random.Random) -> bool:
    """Decide whether the current clip should receive background music.

    Parameters
    ----------
    mode:
        ``"off"``   – never use bgm
        ``"music"`` – always use bgm
        ``"always"` – always use bgm (CLI vocabulary)
        ``"hybrid"` – caller decides (runner ties it to gameplay clips)
        ``"mix50"`` – 50 / 50 coin-flip (deterministic via *rng*)
        ``"auto"``  – always True (future: tie to energetic windows)
    rng:
        Seeded random instance for reproducibility.
    """
    if mode == "off":
        return False
    if mode in ("music", "auto", "always"):
        return True
    if mode == "hybrid":
        return False  # contextual: runner resolves it against gameplay_mode
    if mode == "mix50":
        return rng.random() < 0.5
    return False


def _is_generated_music(path: Path) -> bool:
    """Return True for a procedurally generated loop (see ``_GENERATED_PREFIX``)."""
    name = path.name.lower()
    return name.startswith(_GENERATED_PREFIX) and _GENERATED_LOOP_MARKER in name


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
    import re
    import subprocess

    from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

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
    back to a fully random pick.  Procedurally generated loops (see
    ``_GENERATED_PREFIX``) are skipped unless ``SHORTS_ALLOW_PROCEDURAL_MUSIC``
    is set.  Returns ``None`` when the directory has no playable tracks.
    """
    tracks = list_tracks(music_dir)
    if not tracks:
        return None
    tracks = [t for t in tracks if not _is_junk_music(t)]
    if not tracks:
        return None
    if os.getenv("SHORTS_ALLOW_PROCEDURAL_MUSIC", "0").lower() not in ("1", "true", "on"):
        tracks = [t for t in tracks if not _is_generated_music(t)]
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


def usable_tracks(music_dir: Path) -> list[Path]:
    """Return tracks that could actually be mixed: non-empty and not junk.

    ``list_tracks`` is name/extension based, so a truncated download (0 bytes)
    counts as a track there but is useless as BGM.  Scratch files are excluded
    too, so a directory holding nothing but ``test_*.wav`` is correctly treated
    as empty and the synthesis fallback fires.  Used by
    ``ensure_synthesized_track`` to decide whether a fallback is still needed.
    """
    usable: list[Path] = []
    for path in list_tracks(music_dir):
        if _is_junk_music(path):
            continue
        try:
            if path.stat().st_size > 0:
                usable.append(path)
        except OSError:
            continue
    return usable


def ensure_synthesized_track(
    music_dir: Path,
    *,
    enabled: bool | None = None,
    duration: float | None = None,
) -> Path | None:
    """Synthesize an original loop into *music_dir* if no usable track exists.

    The last-resort BGM guard.  ``ensure_phonk_tracks`` needs the network (or
    Jamendo/Pixabay API keys) and ``SHORTS_MUSIC_DIR`` is empty on a fresh
    clone, which used to mean stock runs shipped silent.  This renders the
    licence-free ``shorts_clipper.audio.dark_industrial`` loop into *music_dir*
    so the normal ``pick_track`` has something to play.

    Guarantees:

    * Real tracks always win -- synthesis only happens when ``usable_tracks`` is
      empty, so nothing is ever overwritten or displaced.
    * Idempotent -- the pool is checked *before* rendering, so repeat runs cost
      nothing and the bytes stay identical (fixed seed).
    * Non-raising -- any failure (numpy missing, disk full, ...) is logged as a
      warning and ``None`` is returned, so the render continues without BGM.

    *enabled* is passed explicitly by the pipeline (``Settings.synthesize_music``,
    from ``SHORTS_SYNTHESIZE_MUSIC``); when omitted it is read straight from the
    environment so every call site honours the gate.  Returns the written path,
    or ``None`` if synthesis was skipped or failed.
    """
    if enabled is None:
        enabled = os.getenv(_SYNTHESIZE_MUSIC_ENV, "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
    if not enabled:
        log.info("Music synthesis disabled (SHORTS_SYNTHESIZE_MUSIC); skipping fallback")
        return None

    music_dir = Path(music_dir)
    try:
        existing = usable_tracks(music_dir)
        if existing:
            log.debug("Music pool already has %d usable track(s); no synthesis", len(existing))
            return None

        # Imported lazily: numpy + the synth DSP stay off the import path of every
        # render that already has music.
        from shorts_clipper.audio import dark_industrial

        if duration is None:
            duration = dark_industrial.DEFAULT_DURATION
        dest = music_dir / dark_industrial.DEFAULT_FILENAME
        # Render to a sidecar first: a crash mid-write must never leave a
        # half-written .wav that ``list_tracks`` would happily hand to ffmpeg.
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            dark_industrial.write_wav(
                tmp, dark_industrial.make_dark_industrial(duration=duration)
            )
            tmp.replace(dest)
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        log.info("Synthesized %ss fallback BGM track -> %s", duration, dest)
        return dest
    except Exception as exc:
        log.warning("Could not synthesize fallback music in %s: %s", music_dir, exc)
        return None


def track_attribution(track: Path) -> str | None:
    """Return a short human-readable credit line for a BGM track, if any.

    The scraper (``shorts_clipper.downloader.music_scraper``) writes a
    ``<track>.attribution.txt`` sidecar next to CC-BY tracks so publishers can
    credit the source.  Returns e.g. ``"Track by Author — source"`` or ``None``
    when the track has no recorded attribution (e.g. procedurally generated or
    no-attribute-required music).
    """
    track = Path(track)
    credit_file = track.with_suffix(track.suffix + ".attribution.txt")
    if not credit_file.is_file():
        return None
    try:
        text = credit_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    credit = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Credit:"):
            credit = line[len("Credit:"):].strip()
            break
    if not credit:
        return None
    source = None
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("source:"):
            source = line[len("Source:"):].strip().split("?")[0]
            break
    return credit if not source else f"{credit} ({source})"


# A short is 13-18s, but a produced track opens with an atmospheric intro far
# longer than that. Measured on data/music/dark_industrial_loop.wav: the first
# 5s sit at -41 dBFS, 5-10s at -35, 10-15s at -25, and only reach -9 dBFS after
# 15s. Since every clip mixed the bed from offset 0, the energetic part of the
# track we generate was inaudible in the whole duration of the output.
ENERGY_WINDOW_SECONDS = 1.0
ENERGY_THRESHOLD_DB = -20.0
ENERGY_SEARCH_FRACTION = 0.75


def _decode_mono_16k(path: Path) -> np.ndarray | None:
    """Decode *path* to mono 16 kHz float samples, or ``None`` on any failure."""
    try:
        import numpy as np

        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as w:
                raw = w.readframes(w.getnframes())
                ch = w.getnchannels()
                data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                if ch > 1:
                    data = data.reshape(-1, ch).mean(axis=1)
                return data
        import subprocess

        from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

        proc = subprocess.run(
            [ffmpeg_path(), "-v", "quiet", "-i", str(path), "-f", "s16le",
             "-ac", "1", "-ar", "16000", "-"],
            capture_output=True,
            timeout=60,
        )
        if not proc.stdout:
            return None
        return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0
    except Exception:
        log.debug("Could not decode %s for energy scan", path, exc_info=True)
        return None


def energetic_offset(
    path: str | Path,
    rng: random.Random | None = None,
    clip_duration: float | None = None,
) -> float:
    """Return a start offset into *path* where the track is already loud.

    Picks a deterministic-by-seed 1s window whose RMS clears
    ``ENERGY_THRESHOLD_DB``, searched within ``ENERGY_SEARCH_FRACTION`` of the
    track so the choice is reproducible and biased to the front. When
    *clip_duration* is given, windows that would run past the end of the track
    are rejected. Falls back to ``0.0`` whenever the track cannot be decoded or
    nothing is loud enough -- never raises, because a quiet opening is a much
    smaller problem than a failed render.
    """
    path = Path(path)
    total = track_duration(path)
    if total is None or total <= 0:
        return 0.0
    data = _decode_mono_16k(path)
    if data is None or not len(data):
        return 0.0

    sr = 16000
    win = int(ENERGY_WINDOW_SECONDS * sr)
    usable = len(data) - win
    if usable <= 0:
        return 0.0
    limit = int(total * ENERGY_SEARCH_FRACTION)

    threshold = 10 ** (ENERGY_THRESHOLD_DB / 20.0)
    candidates: list[float] = []
    for start in range(0, usable, win):
        offset = start / sr
        if offset >= limit:
            break
        if clip_duration and offset + clip_duration > total:
            continue
        if float(np_rms(data[start : start + win])) > threshold:
            candidates.append(round(offset, 3))
    if not candidates:
        return 0.0
    if rng is None:
        return candidates[0]
    return rng.choice(candidates)


def np_rms(chunk):
    """RMS of a float array (kept separate so the scan reads cleanly)."""
    import numpy as np

    return float(np.sqrt(np.mean(np.square(chunk))))
