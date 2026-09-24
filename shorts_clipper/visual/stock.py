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
import subprocess
from pathlib import Path

from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)

_TARGET_W = 1080
_TARGET_H = 1920
_FPS = 30

# Built-in quotes when no script file is configured. Sorted by rough
# language so the TTS voice picker can align with the text.
_DEFAULT_SCRIPTS: list[str] = [
    "Ты не то, что с тобой случилось. Ты то, что ты решил стать.",
    "Дисциплина — это выбор между тем, чего ты хочешь сейчас, и тем, чего ты хочешь больше всего.",
    "Стоицизм не про подавление эмоций. Он про то, что действительно имеет значение.",
    "Хочешь изменить жизнь — начни с утра, которое ты контролируешь.",
    "Одиночество — это не пустота. Это пространство, где рождается ты.",
    "The obstacle is the way. Препятствие — это и есть путь.",
    "Сила — это спокойствие, которое остаётся, когда всё остальное кричит.",
    "Маленькие шаги каждый день. Огромная разница за год.",
    "Ты не можешь управлять ветром. Но можешь настроить паруса.",
    "Чем больше ты практикуешь тишину, тем яснее слышишь себя.",
]


def find_stock_background(stock_dir: str | Path, niche: str | None = None, seed: int = 0) -> Path | None:
    """Pick a background MP4 from the stock folder, favouring the niche subfolder.

    The niche subfolder (``<stock_dir>/<niche>/``) shadows the top-level folder
    when it contains at least one MP4, so per-niche asset libraries stay clean.
    """
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


def load_stock_script(script_path: str | Path | None, seed: int = 0) -> str:
    """Return one spoken line for the next short.

    When *script_path* points to an existing file, every non-empty,
    non-comment line is a candidate.  Otherwise a built-in bank is used.
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