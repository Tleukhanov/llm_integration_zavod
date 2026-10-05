"""Stock-visual short assembly for quote/philosophy niches.

When ``SHORTS_VISUAL_MODE=stock`` the factory does *not* cut someone's
YouTube VOD.  Instead it assembles an original "InnerVoicez-style" short:

    background frame → slow Ken-Burns zoompan → word-subtitles → AI voice

The background is any local MP4 you drop into ``data/stock/<niche>/``
(bin folders are fine).  The spoken script comes from
``SHORTS_STOCK_SCRIPT_PATH`` (one quote per line) or, if unset, from a small
built-in bank of self-growth wisdom phrases.  Everything is pure FFmpeg,
no external licenses, no API keys required.

The quote is not drawn blindly: the background's semantic tags (from its
filename or an optional tag sidecar, see :mod:`shorts_clipper.visual.stock_tags`)
narrow the script pool to lines that match the clip, so a line about 23:00 no
longer lands on a sunrise in the forest.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path
from shorts_clipper.utils.video import get_video_metadata
from shorts_clipper.visual import stock_tags

log = logging.getLogger(__name__)

_TARGET_W = 1080
_TARGET_H = 1920
_FPS = 30

# Built-in quotes when no script file is configured. Pure Russian so the
# auto-picked Edge TTS voice (ru-RU) matches the spoken text every time.
# Each line is a mini narrative arc — concrete setup, the reframe, one short
# landing line — and the openings deliberately alternate (scene, question,
# impersonal, first person) so a batch of shorts never sounds like one voice.
_DEFAULT_SCRIPTS: list[str] = [
    "Пять утра. Двенадцать подтягиваний, пот на ладонях, отказ на третьем подходе. Завтра будет тринадцать. Через год — сорок. Сейчас двенадцать. Не пропусти этот раз.",
    "04:11. Глаза открыты, в голове один вопрос: сколько осталось. В 23:00 было выбрано «ещё чуть-чуть». Вот это «чуть-чуть» стоит до утра.",
    "Телефон убран — и через пятнадцать минут выясняется, что занимал их сам. Не ты без него. Он без тебя.",
    "Кто-то лежит и считает шаги. Зачем — не понимаю. Понимаю другое: он не лежит.",
    "Будильник на 7:12. Тело встало, рука берёт телефон, и день начинается не с тебя, а с ленты. Это не подъём. Это смена позы.",
    "Откладываешь — и точно знаешь, что именно. Знаешь дату, знаешь, сколько это займёт минут. Зная, всё равно откладываешь. Значит, дело не в нехватке времени: оно было в тот момент, когда ты согласился.",
    "Прогресс — не прыжок. Это скука по линейке. Вчера 60, сегодня 80. Разница ничтожна, поэтому линейку и выбрасывают.",
    "Человек ушёл из дома в двенадцать. Соседи сказали: зачем учиться, если есть интернет. Он не ответил. Через десять лет он был единственным в комнате, кто умел думать сам.",
    "В 8:40 открыт ролик про «дисциплину в пять утра». В 8:42 лежит тот, кто его смотрит. Между роликом и режимом — восемь минут.",
    "Я умел начинать. Проверка простая: сколько раз начинал и бросал? Оказалось — двенадцать. Ни одно из этих «начал» ничего не начинало. Они были разминкой.",
    "Пока выбираешь, чем заняться, проходит вечер. Пока выбираешь, кем быть, — жизнь. Выбор — это не мысль. Это сорок минут без переключений.",
    "Восемь часов в состоянии «скоро начну». Шесть из них можно было отдать делу, которое ты всё равно сделал вечером. Шесть часов — не ошибка. Шесть часов — решение.",
    "Ты боишься провала — это хороший знак, значит, вопрос стоит денег. Но вопрос в другом: сколько уже уплачено за ожидание? Посчитай, и сравнится.",
    "Три года боялся. Потом разобрался: боялся не провала, а того, что окажется — сразу. Сразу бывает у единиц, остальные платят за вход.",
    "Понедельник был 48 раз. Сделано 6. Остальные 42 отработаны. Считать надо не понедельники, а вторники — их тоже сорок восемь, но сделано больше.",
    "«А вдруг не получится» — самая дорогая фраза в русском языке. Ею оплачивается вся жизнь, которая могла случиться.",
    "Три раза в неделю по тридцать минут. Год — двести шестьдесят часов. Он не стал быстрым. Он стал тем, кто не бросил.",
    "Лень лежит и не мешает. Неизвестность мешает: с ней надо идти. Значит, это не лень. Страх лечится поступком, а не мыслью.",
    "Между «не могу» и «не могу ещё раз» — весь разрыв. Там ничего нет, кроме одного повтора.",
    "Обещания любят воскресенье: в воскресенье вечером их легче дать, чем в понедельник утром. Обещай в понедельник — отменять будет некому.",
    "В 7:00 решаешь, что день будет хороший. В 21:00 не решаешь ничего. День прошёл, решение не принято. Утренние решения не считаются.",
    "Сорок дней «примерно» — это сорок дней наугад. Один день с записанным результатом весит больше месяца ощущений. Прогресс, который не записан, не существует.",
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

# Edit mode: cut spacing may only be a whole number of beats, and the visual
# switch stays on the 1-2 beat ads-style grid instead of drifting to 4-8 beats.
_EDIT_SPACING_BEATS: tuple[int, ...] = (1, 2)
# Ken-Burns envelope per shot: zoom depth grows with the shot length and is
# capped, so a one-beat cut still drifts visibly without smearing.
_EDIT_ZOOM_PER_BEAT = 0.07
_EDIT_ZOOM_RANGE = 0.14
# Horizontal pan sweeps the whole slack window, flipping direction every shot.
_EDIT_PAN_FROM = 0.06
_EDIT_PAN_TO = 0.94
# In-point inside a source clip is drawn from [_EDIT_MIN_OFFSET, _EDIT_MAX_OFFSET].
_EDIT_MIN_OFFSET = 0.05
_EDIT_MAX_OFFSET = 4.0
# Two extra frames of source are read per shot so the frame-exact trims below
# always have enough material to cut from.
_EDIT_SOURCE_PAD = 2.0 / _FPS
# Down-beat flashes wait a beat-and-a-half before the first one lands.
_EDIT_FLASH_LEAD = 0.3
# Cold-open hit. Measured across rendered shorts: speech starts at 0.10s, but
# the first beat-aligned flash used to land at 1.82s (lead_bar=1 at 132BPM), so
# the first ~1.8s of every clip was a caption over a static frame -- exactly the
# window in which a viewer decides to leave. The opener fires just after the
# first word and the beat grid resumes from bar `stride` behind it, so the
# cadence stays even instead of two flashes 1.4s apart. The flash sits in the
# upper third and captions at the bottom, so they do not collide.
_EDIT_FLASH_OPEN_OFFSET = 0.45
# Last-resort phrases only. The flash is supposed to be said BY the short, so a
# script's own punchy line wins (see :func:`flash_phrases_from_script`); this
# bank exists for scripts that yield nothing readable and must never read as
# generic motivation pasted onto unrelated narration.
_EDIT_FLASH_PHRASES: list[str] = [
    "НЕ СДАВАЙСЯ",
    "ПРОБИВАЙСЯ",
    "НЕ ОСТАНОВЛЯЙСЯ",
    "БЕЗ ОТГОВОРОК",
]
# Readable window for a fullscreen flash. The style is Montserrat Black and wraps
# at 18 chars (captions.generator.FLASH_MAX_LINE_CHARS) and is built to lay out
# up to 3 lines (FLASH_MAX_LINES), so the char budget is 18 x 3 = 54; 44 keeps
# the typical flash at 2-3 lines with margin for a long word.
#
# This was 34, which admitted only 2 lines and silently cost 20% of the shipped
# scripts their on-screen punch: "Хорошего специалиста замечают поздно" is 37
# chars and was rejected by 3 characters, as were 53 other scripts. Measured over
# all 271 lines in data/niches: 34 chars -> 80.1% coverage, 44 -> 87.5%.
# Loosening the admission window does not inflate what is burned, because ranking
# prefers late and brief: median chosen length stays 21 chars, p90 31.
_EDIT_FLASH_MIN_WORDS = 2
_EDIT_FLASH_MAX_WORDS = 6
_EDIT_FLASH_MAX_CHARS = 44
# 2-3 words is the punchiest flash; longer lines lose a step each word.
_EDIT_FLASH_PUNCH_WORDS = 3
_EDIT_FLASH_BREVITY_STEP = 0.2
# Lateness dominates: the narrative rewrite puts the payoff last, and the payoff
# is what deserves the screen. Brevity only breaks near-ties.
_EDIT_FLASH_LATE_WEIGHT = 0.6
_EDIT_FLASH_SHORT_WEIGHT = 0.4
# Distinct script lines the flash rotates through.
_EDIT_FLASH_POOL_MAX = 4
# How many already-burned slots stay blocked from re-opening the next cycle, so
# the tightest possible repeat is 3 slots apart (~11s at the bars=2 stride).
_EDIT_FLASH_REPEAT_GUARD = 2
# Punctuation that ends a spoken sentence; a trailing ellipsis counts too.
_EDIT_FLASH_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|[\r\n]+")
# Characters trimmed off both ends of a candidate line.
_EDIT_FLASH_EDGE_CHARS = " \t«»\"'“”„‘’—–-,;:."


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
    if not slug:
        slug = "q-" + hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
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


def beat_seconds(bpm: float) -> float:
    """Return the beat length in seconds for *bpm*."""
    return 60.0 / bpm


def _edit_cut_grid(
    duration: float,
    bpm: float = 132,
    min_cut: float = 0.9,
    seed: int = 0,
) -> list[float]:
    """Beat-quantized hard-cut grid: strictly increasing cut times in seconds.

    Shot length is a whole number of beats (never a fractional drift), picked
    as the candidate landing closest to *min_cut*.  The walk stops one full
    beat before the end so no sliver segment is ever emitted.
    """
    if bpm <= 0 or duration <= 0:
        return []
    beat = beat_seconds(bpm)
    best = min(_EDIT_SPACING_BEATS, key=lambda s: abs(s * beat - min_cut))
    closest = abs(best * beat - min_cut)
    tied = [s for s in _EDIT_SPACING_BEATS if abs(s * beat - min_cut) - closest < 1e-9]
    spacing = random.Random(seed).choice(tied) if len(tied) > 1 else best

    step = spacing * beat
    cuts: list[float] = []
    t = step
    while t + beat <= duration - 0.01:
        cuts.append(round(t, 3))
        t += step
    return cuts


def _fill_cut_tail(
    cuts: list[float],
    needed: int,
    duration: float,
    bpm: float,
    seed: int = 0,
) -> list[float]:
    """Extend *cuts* to *needed* entries with beat-quantized times."""
    filled = list(cuts)
    if len(filled) >= needed or bpm <= 0 or duration <= 0:
        return filled
    beat = beat_seconds(bpm)
    if beat <= 0:
        return filled
    rng = random.Random(seed)
    base = filled[-1] if filled else 0.0
    k = int(base / beat) + 1
    if k < 1:
        k = 1
    while len(filled) < needed:
        candidate = round(k * beat, 3)
        if candidate <= base + 0.01:
            k += 1
            continue
        if candidate >= duration - 0.01:
            break
        filled.append(candidate)
        base = candidate
        k += rng.choice(_EDIT_SPACING_BEATS)
    return filled


def _clip_durations(clips: list[Path]) -> list[float]:
    """Source durations in seconds, ``0.0`` for clips that cannot be probed."""
    durations: list[float] = []
    for clip in clips:
        try:
            durations.append(max(0.0, get_video_metadata(str(clip)).duration))
        except Exception as exc:
            log.debug("Could not probe %s: %s", clip, exc)
            durations.append(0.0)
    return durations


def _source_offset(source_duration: float, seed: int, shot: int, seg: float) -> float:
    """Seeded non-zero in-point for *shot* inside its source clip.

    Repeated clips therefore show a different part of the take every time, and
    the offset is clamped so ``*seg*`` still fits inside the source.
    """
    span = source_duration - seg - 0.1
    if span <= _EDIT_MIN_OFFSET:
        return 0.0
    rng = random.Random(f"{seed}:{shot}")
    return round(rng.uniform(_EDIT_MIN_OFFSET, min(span, _EDIT_MAX_OFFSET)), 3)


def _ken_burns(shot: int, seg: float, beat: float) -> str:
    """Return the ``zoompan`` filter drifting shot *shot* over *seg* seconds.

    Mirrors the Ken-Burns envelope of :func:`render_stock_background`, but the
    zoom is normalized to the shot length and the direction alternates with
    *shot* (even = zoom in and pan right, odd = zoom out and pan left) so
    back-to-back cuts never drift the same way.  The zoom and the pan ramp
    linearly from the first to the last frame, so no step is ever visible.
    """
    last = max(1, int(round(seg * _FPS)) - 1)
    zoom = min(_EDIT_ZOOM_RANGE, _EDIT_ZOOM_PER_BEAT * max(1.0, seg / beat))
    even = shot % 2 == 0
    z_expr = f"1+{zoom:.4f}*in/{last}" if even else f"{1.0 + zoom:.4f}-{zoom:.4f}*in/{last}"
    pan_from, pan_to = (_EDIT_PAN_FROM, _EDIT_PAN_TO) if even else (_EDIT_PAN_TO, _EDIT_PAN_FROM)
    x_expr = f"(iw-iw/zoom)*({pan_from:.3f}{pan_to - pan_from:+.3f}*in/{last})"
    return (
        f"zoompan=z='{z_expr}':d=1:x='{x_expr}':y='(ih-ih/zoom)/2':"
        f"s={_TARGET_W}x{_TARGET_H}:fps={_FPS},setsar=1"
    )


def render_stock_background_edit(
    clips: list[Path],
    work_dir: Path,
    out_path: Path,
    duration: float,
    *,
    bpm: float = 132,
    seed: int = 0,
    video_codec: str = "libx264",
    preset: str = "ultrafast",
) -> Path:
    """Render a beat-synced background where clips snap-cut on the music.

    Every boundary sits on a beat from :func:`_edit_cut_grid`; spare timeline is
    spread over even extra cuts, and clips repeat cyclically until the whole
    *duration* is covered.  Each segment gets its own seeded in-point inside
    the source plus an alternating Ken-Burns drift, so reused clips and back to
    back cuts never look like the same frozen frame.  Segments are chained with
    the ``concat`` filter (hard cut, no transition) and a quiet stereo bed is
    muxed for downstream ``[0:a]`` mixing.
    """
    out_path = Path(out_path)
    if not clips or duration <= 0:
        raise ValueError("render_stock_background_edit needs clips and a positive duration")

    cuts = _edit_cut_grid(duration, bpm, min_cut=duration / len(clips), seed=seed)
    needed = max(1, len(clips) - 1)
    cuts = _fill_cut_tail(cuts, needed, duration, bpm, seed)

    bounds = [0.0, *cuts, duration]
    segments = [
        (k % len(clips), bounds[k], bounds[k + 1])
        for k in range(len(bounds) - 1)
        if bounds[k + 1] - bounds[k] > 0.01
    ]
    if not segments:
        raise ValueError("render_stock_background_edit produced no usable segments")

    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", str(clip)]

    durations = _clip_durations(clips)
    beat = beat_seconds(bpm)
    prep: list[str] = []
    for k, (src, _start, end) in enumerate(segments):
        seg = end - _start
        frames = max(2, int(round(seg * _FPS)))
        offset = _source_offset(durations[src], seed, k, seg)
        prep.append(
            f"[{src}:v]trim=start={offset:.3f}:duration={seg + _EDIT_SOURCE_PAD:.3f},"
            f"setpts=PTS-STARTPTS,scale={_TARGET_W}:{_TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={_TARGET_W}:{_TARGET_H},fps={_FPS},trim=end_frame={frames + 1},"
            f"{_ken_burns(k, seg, beat)},trim=end_frame={frames},"
            f"setpts=PTS-STARTPTS,format=yuv420p[va{k}]"
        )
    chain = "".join(f"[va{k}]" for k in range(len(segments)))
    filter_complex = ";".join(
        prep + [f"{chain}concat=n={len(segments)}:v=1:a=0[m]", "[m]format=yuv420p[vout]"]
    )
    final_video = "[vout]"
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
        log.error("Stock edit render failed:\n%s", result.stderr[-3000:])
        raise RuntimeError(f"Stock edit background render failed (exit {result.returncode})")
    log.info(
        "✅ Stock edit background (%d segments, %d clips, %.1fs) → %s",
        len(segments),
        len(clips),
        duration,
        out_path,
    )
    return out_path


def _flash_candidate_text(line: str) -> str:
    """Return *line* trimmed to bare flash text (no quotes, dashes, padding)."""
    return re.sub(r"\s+", " ", line).strip(_EDIT_FLASH_EDGE_CHARS)


def _flash_sentences(script: str) -> list[str]:
    """Split *script* into spoken sentences, order preserved."""
    return [s for s in _EDIT_FLASH_SENTENCE_SPLIT.split(script or "") if s and s.strip()]


def flash_phrases_from_script(
    script: str | None,
    limit: int = _EDIT_FLASH_POOL_MAX,
) -> list[str]:
    """Rank the flash-sized lines of *script*, strongest first (deterministic).

    A flash is one big line of caps, so a candidate has to read in a glance:
    2-6 words and at most ~2 wrapped lines. Within that window candidates are
    scored on two axes — how late the sentence lands and how terse it is — and
    ties break on position, then on the text itself, so the ranking is a pure
    function of the script.

    Lateness wins because the script arc (setup → reframe → payoff) is written
    so the payoff comes last; terseness breaks near-ties so the punchiest of two
    equally late lines burns. Returns ``[]`` when the script has nothing
    readable, leaving the caller to fall back.
    """
    sentences = _flash_sentences(script)
    total = len(sentences)
    if not total:
        return []
    scored: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for index, sentence in enumerate(sentences):
        text = _flash_candidate_text(sentence)
        words = text.split()
        if not _EDIT_FLASH_MIN_WORDS <= len(words) <= _EDIT_FLASH_MAX_WORDS:
            continue
        if len(text) > _EDIT_FLASH_MAX_CHARS:
            continue
        if not any(ch.isalpha() for ch in text):
            continue
        phrase = text.upper()
        if phrase in seen:
            continue
        seen.add(phrase)
        lateness = index / (total - 1) if total > 1 else 1.0
        brevity = 1.0 - _EDIT_FLASH_BREVITY_STEP * max(0, len(words) - _EDIT_FLASH_PUNCH_WORDS)
        score = _EDIT_FLASH_LATE_WEIGHT * lateness + _EDIT_FLASH_SHORT_WEIGHT * brevity
        scored.append((score, index, phrase))
    # Highest score first; earlier sentence and smaller text break ties so the
    # order never depends on dict/set iteration.
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [phrase for _, _, phrase in scored[: max(1, int(limit))]]


def edit_flash_schedule(
    duration: float,
    bpm: float = 132,
    phrases: list[str] | None = None,
    seed: int = 0,
    bars: int = 1,
    script: str | None = None,
) -> list[dict]:
    """Fullscreen text flashes pinned to down-beats, one per flash slot.

    Each flash spans exactly one bar (4 beats) and is clipped to *duration*.
    ``bars`` sets the slot stride: ``1`` flashes on every bar, ``2`` leaves every
    other bar clear so the text reads as an accent instead of a permanent
    overlay.

    Phrase order of precedence: an explicit ``phrases`` pool, else the script's
    own punchy lines (:func:`flash_phrases_from_script`) so the screen says what
    the voice says.  The static bank applies only when no ``script`` was passed
    at all; a script that yields nothing readable gets no flash rather than a
    Russian battle-cry pasted over (say) an English voiceover.  The last
    ``_EDIT_FLASH_REPEAT_GUARD`` slots are held back from every draw, so the same
    text never lands inside a 3-slot window (no "НЕ ОСТАНОВЛЯЙСЯ" at 7s and
    again at 14s) and the tightest possible repeat is 2 slots for a 2-line pool.
    Returns ``[]`` when the timeline is shorter than a single bar.
    """
    if bpm <= 0 or duration <= 0:
        return []
    if phrases is not None:
        pool = [p for p in phrases if p]
    elif script is None:
        pool = list(_EDIT_FLASH_PHRASES)
    else:
        pool = flash_phrases_from_script(script)
        if not pool:
            # A script was supplied but yielded nothing flash-sized. Do NOT fall
            # back to the static bank here: it is Russian, so an English (or any
            # non-Cyrillic) short would get "НЕ ОСТАНОВЛЯЙСЯ" burned over
            # foreign narration -- the exact defect this replaced. No flash is
            # the honest answer.
            return []
    if not pool:
        return []

    bar = 4 * beat_seconds(bpm)
    stride = max(1, int(bars))
    lead_bar = 1
    while lead_bar * bar < _EDIT_FLASH_LEAD:
        lead_bar += 1
    lead_bar += ((lead_bar - 1) // stride) * stride

    rng = random.Random(seed)
    flashes: list[dict] = []
    recent: list[str] = []

    def _draw(start: float) -> None:
        end = min(duration, start + bar)
        if end <= start:
            return
        blocked = set(recent[-_EDIT_FLASH_REPEAT_GUARD:])
        open_pool = [p for p in pool if p not in blocked] or list(pool)
        text = rng.choice(open_pool)
        recent.append(text)
        flashes.append({"start": round(start, 3), "end": round(end, 3), "text": text})

    # Cold open first, then hand back to the beat grid. The opener still needs a
    # full bar of room, so a sub-bar timeline yields nothing as before.
    opener = min(_EDIT_FLASH_OPEN_OFFSET, duration)
    if duration > bar:
        _draw(opener)

    bar_index = stride
    while bar_index * bar < duration:
        _draw(bar_index * bar)
        bar_index += stride
    return flashes


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
    background: str | Path | None = None,
    background_tags: Iterable[str] | None = None,
) -> str:
    """Return one spoken line for the next short.

    An existing *script_path* wins, followed by the niche profile and the
    built-in bank — that precedence is unchanged.  What the pool *is* depends
    on the visual: pass the chosen *background* clip (or its already-computed
    *background_tags*) and the line is drawn only from the scripts whose
    semantic tags overlap the clip's, so a quote about 23:00 stops landing on
    a sunrise in the forest.  With no visual information the draw is exactly
    the previous unfiltered one.
    """
    tags = frozenset(background_tags) if background_tags is not None else (
        stock_tags.background_tags(background) if background is not None else frozenset()
    )

    if script_path:
        path = Path(script_path)
        if path.is_file():
            lines = [
                ln.strip()
                for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if lines:
                return random.Random(seed).choice(_tagged(lines, tags, "script file"))

    lines = niche_script_lines(niche, niche_dir)
    if lines:
        return random.Random(seed).choice(_tagged(lines, tags, f"niche profile {niche or 'default'}"))

    return random.Random(seed).choice(_tagged(list(_DEFAULT_SCRIPTS), tags, "built-in bank"))


def _tagged(
    lines: list[str],
    tags: frozenset[str],
    source: str,
) -> list[str]:
    """Narrow *lines* to those matching *tags*, logging a full-pool miss.

    The miss is logged rather than raised: a niche whose quotes are all
    off-vocabulary, or a clip nobody tagged, must still render.
    """
    matched, fell_back = stock_tags.filter_scripts_by_tags(lines, tags)
    if fell_back:
        log.info(
            "Stock script: no script matches visual tags %s — using the full %s pool (%d).",
            sorted(tags),
            source,
            len(matched),
        )
    elif tags:
        log.debug(
            "Stock script: %d/%d %s scripts match visual tags %s.",
            len(matched),
            len(lines),
            source,
            sorted(tags),
        )
    return matched


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