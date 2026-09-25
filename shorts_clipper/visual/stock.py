"""Stock-visual short assembly for quote/philosophy niches.

When ``SHORTS_VISUAL_MODE=stock`` the factory does *not* cut someone's
YouTube VOD.  Instead it assembles an original "InnerVoicez-style" short:

    background frame → slow Ken-Burns zoompan → word-subtitles → AI voice

The background is any local MP4 you drop into ``data/stock/<niche>/``
(bin folders are fine).  The spoken script comes from
``SHORTS_STOCK_SCRIPT_PATH`` (one quote per line) or, if unset, from a small
built-in bank of self-growth wisdom phrases.  Everything is pure FFmpeg,
no external licenses, no API keys required.
"""

from __future__ import annotations

import logging
import random
import re
import subprocess
from pathlib import Path

from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)

_TARGET_W = 1080
_TARGET_H = 1920
_FPS = 30

# Built-in quotes when no script file is configured. Pure Russian so the
# auto-picked Edge TTS voice (ru-RU) matches the spoken text every time.
# Written like hooks, not affirmations: direct address, emotional contrast,
# a sting in the first few words — the viewer must feel seen in 2 seconds.
_DEFAULT_SCRIPTS: list[str] = [
    "Ты не сдался. Ты просто перестал нажимать — продолжить.",
    "Ты врешь, что тебе всё равно. Если бы было всё равно, ты бы не дочитал.",
    "Ты не ленивый. Ты тратишь все силы на чужие требования и ничего не оставляешь себе.",
    "Дело не в мотивации. Дело в том, что тебе надоело быть версией себя, которую ты не выбирал.",
    "Каждое утро ты выбираешь: проснуться своей жизнью или чужой.",
    "Они назовут это удачей. Удача не встаёт в пять утра, пока все спят — это ты.",
    "Ты не боишься провала. Ты боишься, что даже стараться бессмысленно. Это и есть ложь.",
    "Никто не придёт. Это не грустно. Это твой шанс.",
    "Твои 23:00 — это твой завтрашний 06:00. Тело уже считает.",
    "Ты не устал. Ты перестал драться за то, во что веришь в два часа ночи.",
    "Самая дорогая валюта — не деньги. Это уверенность, которую ты отдал чужому мнению.",
    "Ты сам себе либо самый жёсткий критик, либо самый надёжный союзник. Выбери, с кем дружить.",
    "Когда ты перестанешь себя жалеть, начнётся то, что ты заслужил.",
    "Тебе не нужен новый год. Тебе нужен душ и один несделанный звонок.",
    "Злость — это твоя сила, которую ты раздал бесплатно. Забери её.",
    "Потом уже живёт тот, кто решил сегодня.",
]

# Background search terms per niche, so Pexels photos complement the topic
# (Portrait videos only — 1080×1920 is requested downstream).
PEXELS_QUERY_BY_NICHE: dict[str, str] = {
    "self-growth": "morning sunrise forest calm",
    "philosophy": "minimal ocean horizon dawn",
    "motivation": "mountain peak sky sunrise",
    "lifestyle": "city night walking lights",
    "nature": "nature slow motion green",
    "money": "city skyscraper business night",
    "relationships": "couple silhouette sunset warm",
}

_PEXELS_CACHE_SUBDIR = "_pexels_cache"
_PEXELS_LIMIT = 5  # max cached videos per query slug


def niche_query(niche: str | None, niche_dir: str | Path = "data/niches") -> str:
    """Return the Pexels query configured for *niche*."""
    normalized = (niche or "").strip().lower() or "self-growth"
    fallback = PEXELS_QUERY_BY_NICHE.get(normalized, "morning sunrise calm")
    try:
        profile = Path(niche_dir) / normalized / "pexels_query.txt"
        for line in profile.read_text(encoding="utf-8").splitlines():
            query = line.strip()
            if query and not query.startswith("#"):
                return query
    except Exception:
        return fallback
    return fallback


def niche_script_lines(niche: str | None, niche_dir: str | Path = "data/niches") -> list[str]:
    """Return the script lines configured for *niche*."""
    normalized = (niche or "").strip().lower() or "self-growth"
    try:
        profile = Path(niche_dir) / normalized / "scripts.txt"
        if profile.is_file():
            return [
                line.strip()
                for line in profile.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
    except Exception:
        return []
    return []


def _local_backgrounds(stock_dir: str | Path, niche: str | None) -> list[Path]:
    """All local MP4s for *niche* (niche subfolder preferred, else top-level)."""
    base = Path(stock_dir)
    if niche:
        sub = base / niche
        if sub.is_dir():
            niche_files = sorted(p for p in sub.glob("*.mp4") if p.is_file())
            if niche_files:
                return niche_files
    if base.is_dir():
        return sorted(p for p in base.glob("*.mp4") if p.is_file())
    return []


def ensure_pexels_cache(
    stock_dir: str | Path,
    niche: str | None,
    api_key: str,
    *,
    limit: int = _PEXELS_LIMIT,
    niche_dir: str | Path = "data/niches",
) -> list[Path]:
    """Return the cached Pexels pool for *niche*, downloading on first fill.

    Videos are cached under ``<stock_dir>/_pexels_cache/<slug>/`` and reused
    forever, so a full pipeline run (and CI) stays offline after one warm-up.
    """
    query = niche_query(niche, niche_dir)
    slug = re.sub(r"[^a-z0-9-]+", "-", query.lower()).strip("-")
    cache_dir = Path(stock_dir) / _PEXELS_CACHE_SUBDIR / slug
    cache_dir.mkdir(parents=True, exist_ok=True)

    cached = sorted(p for p in cache_dir.glob("*.mp4") if p.is_file())
    if not cached and api_key:
        _download_pexels_videos(query, cache_dir, api_key, limit)
        cached = sorted(p for p in cache_dir.glob("*.mp4") if p.is_file())
    return cached


def fetch_pexels_background(
    stock_dir: str | Path,
    niche: str | None,
    seed: int = 0,
    *,
    api_key: str = "",
    limit: int = _PEXELS_LIMIT,
    niche_dir: str | Path = "data/niches",
) -> Path | None:
    """Return a seed-deterministic Pexels video for *niche* (or ``None``)."""
    pool = ensure_pexels_cache(
        stock_dir, niche, api_key, limit=limit, niche_dir=niche_dir
    )
    if not pool:
        return None
    return random.Random(seed).choice(pool)


def list_stock_backgrounds(
    stock_dir: str | Path,
    niche: str | None = None,
    seed: int = 0,
    *,
    pexels_api_key: str = "",
    limit: int = _PEXELS_LIMIT,
    niche_dir: str | Path = "data/niches",
) -> list[Path]:
    """Seed-deterministic pool of distinct backdrop videos for *niche*.

    Local files win; the Pexels cache tops up the pool when the local folder
    is short.  Used by the montage renderer so each short mixes several
    different clips with cross-fades.
    """
    pool = _local_backgrounds(stock_dir, niche)
    if len(pool) < limit and pexels_api_key:
        extra = ensure_pexels_cache(
            stock_dir,
            niche,
            pexels_api_key,
            limit=limit - len(pool),
            niche_dir=niche_dir,
        )
        for p in extra:
            if p not in pool:
                pool.append(p)
    if not pool:
        return []
    return random.Random(seed).sample(pool, min(limit, len(pool)))


def find_stock_background(
    stock_dir: str | Path,
    niche: str | None = None,
    seed: int = 0,
    pexels_api_key: str = "",
    *,
    niche_dir: str | Path = "data/niches",
) -> Path | None:
    """Pick a background MP4 for a short.

    Priority:
      1. local ``<stock_dir>/<niche>/*.mp4`` (niche subfolder shadows base)
      2. local ``<stock_dir>/*.mp4``
      3. Pexels cache (auto-refilled from the API when *pexels_api_key* set)

    Returns ``None`` when nothing is available — callers fall back to a
    procedural gradient render.
    """
    local = _find_local_background(stock_dir, niche, seed)
    if local is not None:
        return local

    return fetch_pexels_background(
        stock_dir,
        niche,
        seed,
        api_key=pexels_api_key,
        niche_dir=niche_dir,
    )


def _find_local_background(stock_dir: str | Path, niche: str | None, seed: int) -> Path | None:
    base = Path(stock_dir)
    rng = random.Random(seed)

    if niche:
        sub = base / niche
        if sub.is_dir():
            niche_files = sorted(p for p in sub.glob("*.mp4") if p.is_file())
            if niche_files:
                return rng.choice(niche_files)

    if base.is_dir():
        base_files = sorted(p for p in base.glob("*.mp4") if p.is_file())
        if base_files:
            return rng.choice(base_files)

    return None


def _montage_plan(n: int, duration: float, fade: float = 0.9, seed: int = 0) -> dict:
    """Compute t/offsets/transitions for an n-clip crossfade montage.

    Each source clip lasts *t* seconds; consecutive clips overlap by *fade*
    via xfade, so the total is  n*t - (n-1)*f ≈ *duration*.  Only soft,
    non-slicing transitions are used so the cut never feels harsh.
    """
    dur = max(duration, n * fade)
    t = (dur + (n - 1) * fade) / n
    transitions: list[tuple[float, str]] = []
    rng = random.Random(seed)
    kinds = ["fade", "dissolve", "smoothleft", "smoothup"]
    for k in range(1, n):
        offset = k * (t - fade)
        transitions.append((offset, rng.choice(kinds)))
    return {
        "clip_duration": t,
        "fade": fade,
        "transitions": transitions,
        "total": n * t - (n - 1) * fade,
    }


def render_stock_background_montage(
    clips: list[Path],
    work_dir: Path,
    out_path: Path,
    duration: float,
    *,
    seed: int = 0,
    video_codec: str = "libx264",
    preset: str = "ultrafast",
) -> Path:
    """Render a multi-clip background: several videos blended with cross-fades.

    Every clip is normalized to 1080×1920@30fps, trimmed to an even segment,
    then chained with ``xfade`` transitions (fade/dissolve/slide variants).  A
    quiet stereo audio bed is included for downstream ``[0:a]`` mixing.
    """
    out_path = Path(out_path)
    plan = _montage_plan(len(clips), duration, seed=seed)
    t = plan["clip_duration"]
    fade = plan["fade"]

    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", str(clip)]

    prep: list[str] = []
    for i in range(len(clips)):
        prep.append(
            f"[{i}:v]scale={_TARGET_W}:{_TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={_TARGET_W}:{_TARGET_H},setpts=PTS-STARTPTS,trim=duration={t:.3f},"
            f"setpts=PTS-STARTPTS,fps={_FPS},format=yuv420p[v{i}]"
        )

    chain: list[str] = []
    prev = "[v0]"
    for k, (offset, kind) in enumerate(plan["transitions"], start=1):
        out_label = f"[x{k}]"
        chain.append(
            f"{prev}[v{k}]xfade=transition={kind}:duration={fade:.3f}:"
            f"offset={offset:.3f}{out_label}"
        )
        prev = out_label
    final_video = prev

    filter_complex = ";".join(prep + chain)
    audio_idx = len(clips)
    cmd = [
        ffmpeg_path(),
        "-y",
        *inputs,
        "-f",
        "lavfi",
        "-t",
        f"{duration:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex",
        filter_complex,
        "-map",
        final_video,
        "-map",
        f"{audio_idx}:a",
        "-t",
        f"{duration:.3f}",
        "-c:v",
        video_codec,
    ]
    if video_codec == "libx264":
        cmd.extend(["-crf", "24", "-preset", preset])
    else:
        cmd.extend(["-preset", preset])
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not out_path.is_file():
        log.error("Stock montage render failed:\n%s", result.stderr[-3000:])
        raise RuntimeError(f"Stock montage render failed (exit {result.returncode})")
    log.info(
        "✅ Stock montage background (%d clips, %.1fs) → %s",
        len(clips),
        duration,
        out_path,
    )
    return out_path


def _download_pexels_videos(query: str, cache_dir: Path, api_key: str, limit: int) -> list[Path]:
    """Query Pexels *videos/search* and download up to *limit* portrait MP4s."""
    import httpx

    log.info("Pexels: fetching '%s' (%d videos)...", query, limit)
    headers = {"Authorization": api_key}
    try:
        resp = httpx.get(
            "https://api.pexels.com/videos/search",
            params={
                "query": query,
                "orientation": "portrait",
                "per_page": min(limit * 2, 20),
                "size": "medium",
            },
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        videos = (resp.json() or {}).get("videos") or []
    except Exception as exc:
        log.error("Pexels search failed: %s", exc)
        return []

    saved: list[Path] = []
    for idx, video in enumerate(videos):
        if len(saved) >= limit:
            break
        files = video.get("video_files") or []
        target = None
        # Prefer an MP4 that is at least portrait-FHD, else the largest file.
        for f in files:
            if not (f.get("file_type") or "").startswith("video/mp4"):
                continue
            _w, h = f.get("width") or 0, f.get("height") or 0
            if h >= 1920:
                target = f
                break
        if target is None and files:
            target = sorted(
                files,
                key=lambda f: (f.get("height") or 0) * (f.get("width") or 0),
                reverse=True,
            )[0]
        if target is None:
            continue
        link = target.get("link")
        if not link:
            continue
        out_path = cache_dir / f"{idx:02d}.mp4"
        try:
            with httpx.stream("GET", link, follow_redirects=True, timeout=120) as dl:
                dl.raise_for_status()
                with open(out_path, "wb") as fh:
                    for chunk in dl.iter_bytes(chunk_size=1 << 16):
                        fh.write(chunk)
            if out_path.stat().st_size > 100_000:
                saved.append(out_path)
                log.info("Pexels: cached %s (%.1f MB)", out_path.name, out_path.stat().st_size / 1e6)
        except Exception as exc:
            log.error("Pexels download failed for %s: %s", link, exc)
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
    return saved


def load_stock_script(
    script_path: str | Path | None,
    seed: int = 0,
    *,
    niche: str | None = None,
    niche_dir: str | Path = "data/niches",
) -> str:
    """Return one spoken line for the next short.

    An existing *script_path* wins, followed by the niche profile and the
    built-in bank.
    """
    if script_path:
        path = Path(script_path)
        if path.is_file():
            lines = [
                ln.strip()
                for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if lines:
                return random.Random(seed).choice(lines)

    lines = niche_script_lines(niche, niche_dir)
    if lines:
        return random.Random(seed).choice(lines)

    return random.Random(seed).choice(_DEFAULT_SCRIPTS)


def build_word_segments(text: str, duration: float, max_words: int = 4) -> list[TranscriptSegment]:
    """Distribute a spoken line into evenly timed word segments.

    Used to drive both the ASS word-subtitles and the publisher transcript
    without any source transcription.  Returns segments covering ``[0, duration]``.
    """
    words = [w for w in text.replace("—", " ").split() if w]
    if not words or duration <= 0:
        return []

    count = max(1, len(words))
    # Pad a small tail gap so the last word isn't cut mid-frame.
    step = duration / count
    segments: list[TranscriptSegment] = []
    current = 0.0
    for i in range(0, count, max_words):
        group = words[i : i + max_words]
        if not group:
            break
        seg_start = current
        seg_end = min(duration, seg_start + step * len(group))
        word_objs = []
        w_start = seg_start
        w_step = max(step, (seg_end - seg_start) / len(group)) if len(group) else step
        for w in group:
            w_end = min(seg_end, w_start + w_step)
            word_objs.append(TranscriptWord(start=w_start, end=w_end, word=w))
            w_start = w_end
        segments.append(
            TranscriptSegment(
                start=seg_start,
                end=seg_end,
                text=" ".join(group),
                words=word_objs,
            )
        )
        current = seg_end
    return segments


def speech_window(
    audio_path: Path,
    threshold_db: float = -35.0,
    min_silence: float = 0.4,
) -> tuple[float, float] | None:
    """Locate the actual speech window of *audio_path* via silencedetect.

    TTS files start with a small lead-in silence and end with a longer tail.
    Captions should span exactly the audible part, so words match what the
    viewer hears instead of drifting against a silent intro/outro.  Returns
    ``(start, end)`` in seconds or ``None`` when no speech is detected.
    """
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.warning("silencedetect failed on %s", audio_path)
        return None

    silence_starts: list[float] = []
    silence_ends: list[float] = []
    for line in result.stderr.splitlines():
        line = line.strip()
        try:
            if "silence_start" in line:
                silence_starts.append(float(line.rsplit(":", 1)[1].strip()))
            elif "silence_end" in line:
                silence_ends.append(float(line.rsplit("|", 1)[0].split(":", 1)[1].strip()))
        except ValueError:
            continue

    if not silence_starts and not silence_ends:
        return None

    # silencedetect emits alternating silence_start / silence_end events, so
    # zip them into [start, end] silent intervals in order.
    intervals = list(zip(silence_starts, silence_ends, strict=False))

    # Speech window = envelope spanning all non-silent audio (mid-sentence
    # pauses are included so captions only float a little on natural breaks).
    if intervals and intervals[0][0] <= 0.05:
        speech_start = intervals[0][1]
    else:
        speech_start = 0.0
    speech_end = intervals[-1][0] if intervals else speech_start

    if speech_end <= speech_start + 0.3:
        return None
    return speech_start, speech_end


def _extract_background_frame(bg: Path, work_dir: Path) -> Path:
    """Extract a single frame from the background video for zoompan looping."""
    frame_path = work_dir / "bg_frame.jpg"
    cmd = [
        ffmpeg_path(),
        "-y",
        "-ss",
        "0",
        "-i",
        str(bg),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(frame_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not frame_path.is_file():
        log.error("Stock bg frame extract failed: %s", result.stderr[-1000:])
        raise RuntimeError("Could not extract a frame from the stock background")
    return frame_path


def render_stock_background(
    bg: Path,
    work_dir: Path,
    out_path: Path,
    duration: float,
    *,
    seed: int = 0,
    video_codec: str = "libx264",
    preset: str = "ultrafast",
) -> Path:
    """Render a vertical, slow-zoom (Ken-Burns) background of *duration* seconds.

    The result carries a quiet audio bed so downstream burns mixing
    ``[0:a]`` (voiceover / BGM) always have a stream to mix against.
    """
    out_path = Path(out_path)
    frame = _extract_background_frame(bg, work_dir)

    # Deterministic drift: zoom direction and focal point vary per seed so
    # consecutive shorts of the same background never look identical.
    rng = random.Random(seed)
    z_expr = rng.choice(
        [
            "min(zoom+0.0012,1.18)",
            "max(1.0,zoom-0.0009)",
        ]
    )
    ox = rng.choice(["iw/2", "iw*0.35", "iw*0.65"])
    oy = rng.choice(["ih/2", "ih*0.4", "ih*0.6"])

    dur_float = max(duration, 1.0)
    vf = (
        f"scale={_TARGET_W}:{_TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={_TARGET_W}:{_TARGET_H},"
        + "zoompan=z='{z}':d={frames}:x='{ox}-iw/zoom/{nz}':y='{oy}-ih/zoom/{nz}':"
        "s={tw}x{th}:fps={fps},setsar=1".format(
            z=z_expr,
            frames=int(dur_float * _FPS),
            ox=ox,
            oy=oy,
            nz=2 if "iw/" in ox else 1,
            tw=_TARGET_W,
            th=_TARGET_H,
            fps=_FPS,
        )
    )

    cmd = [
        ffmpeg_path(),
        "-y",
        "-loop",
        "1",
        "-i",
        str(frame),
        "-f",
        "lavfi",
        "-t",
        f"{dur_float:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf",
        vf,
        "-t",
        f"{dur_float:.3f}",
        "-c:v",
        video_codec,
    ]
    if video_codec == "libx264":
        cmd.extend(["-crf", "26", "-preset", preset])
    else:
        cmd.extend(["-preset", preset])
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        log.error("Stock bg render failed:\n%s", result.stderr[-3000:])
        raise RuntimeError(f"Stock background render failed (exit {result.returncode})")
    log.info("✅ Stock background rendered → %s (%.1fs)", out_path, dur_float)
    return out_path


def render_procedural_background(
    work_dir: Path,
    out_path: Path,
    duration: float,
    *,
    seed: int = 0,
    video_codec: str = "libx264",
    preset: str = "ultrafast",
) -> Path:
    """Render an animated gradient background when no stock clip is configured.

    Pure ffmpeg ``lavfi`` (no assets, no licenses), seeded color pairs so
    consecutive shorts differ.  A quiet stereo audio bed is included so
    downstream burns mixing ``[0:a]`` always have a stream to mix against.
    """
    out_path = Path(out_path)
    rng = random.Random(seed)
    colors = [
        f"0x{rng.randint(0x202030, 0x6A2E8C):06x}",
        f"0x{rng.randint(0x101020, 0x2E4A6A):06x}",
        f"0x{rng.randint(0x222233, 0x704214):06x}",
    ]
    c0, c1, c2 = (c.lstrip("0x") for c in colors[:3])
    dur_float = max(duration, 1.0)

    vf = (
        f"gradients=size={_TARGET_W}x{_TARGET_H}:speed=0.02:c0={c0}:c1={c1}:c2={c2}:x0=0:y0=0:"
        f"x1={_TARGET_W}:y1={_TARGET_H}"
        + ",format=yuv420p"
    )
    cmd = [
        ffmpeg_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        vf,
        "-f",
        "lavfi",
        "-t",
        f"{dur_float:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t",
        f"{dur_float:.3f}",
        "-c:v",
        video_codec,
    ]
    if video_codec == "libx264":
        cmd.extend(["-crf", "26", "-preset", preset])
    else:
        cmd.extend(["-preset", preset])
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        log.error("Procedural bg render failed:\n%s", result.stderr[-3000:])
        raise RuntimeError(f"Procedural background render failed (exit {result.returncode})")
    log.info("✅ Procedural gradient background → %s (%.1fs)", out_path, dur_float)
    return out_path