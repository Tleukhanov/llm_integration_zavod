"""FFmpeg-native subtitle burning via ASS format.

Why ASS over MoviePy TextClip:
- MoviePy renders subtitles in Python frame-by-frame via ImageMagick.
  On a 30s clip this can take 3-5 minutes.
- FFmpeg's native ``ass`` filter hands subtitle rendering to the GPU-
  accelerated libass library. The same operation takes seconds.
- ASS gives precise per-word timing, custom fonts, drop shadows, and
  karaoke-style highlights that MoviePy cannot do cleanly.
"""

from __future__ import annotations

import logging
import random
import subprocess
import tempfile
from pathlib import Path

from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ASS file generation
# ---------------------------------------------------------------------------


def hex_to_ass_color(hex_str: str) -> str:
    """Convert a standard hex color like #FF5500 to ASS &H00BBGGRR& format."""
    clean_hex = hex_str.strip().lstrip("#")
    if len(clean_hex) == 6:
        r, g, b = clean_hex[0:2], clean_hex[2:4], clean_hex[4:6]
        return f"&H00{b}{g}{r}&"
    elif len(clean_hex) == 8:
        a, r, g, b = clean_hex[0:2], clean_hex[2:4], clean_hex[4:6], clean_hex[6:8]
        return f"&H{a}{b}{g}{r}&"
    return "&H00FFFFFF&"


def _ass_header(style_name: str = "default") -> str:
    """Build the ASS subtitle file header with custom style overrides."""
    style_format = (
        "Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
        " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
        " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding"
    )

    style_name = str(style_name)
    style_name_lower = style_name.lower()

    if style_name_lower.startswith("custom_"):
        # custom_FontName_FontSize_PrimaryHex_OutlineHex_OutlineVal_ShadowVal
        try:
            parts = style_name.split("_")
            font_name = parts[1]
            font_size = parts[2]
            pri_color = hex_to_ass_color(parts[3])
            out_color = hex_to_ass_color(parts[4])
            out_val = parts[5]
            shd_val = parts[6]
            style_def = (
                f"Default,{font_name},{font_size},"
                f"{pri_color},&H0000FF00&,{out_color},&H00000000&,"
                f"-1,0,0,0,100,100,0,0,1,{out_val},{shd_val},2,40,40,180,1"
            )
        except Exception:
            style_def = (
                "Default,Inter Bold,58,"
                "&H00F2F2F2&,&H00FFFF00&,&H00000000&,&H80000000&,"
                "-1,0,0,0,100,100,0,0,1,2.5,1,2,40,40,180,1"
            )
    elif style_name_lower == "mrbeast":
        # Montserrat Black, size 68, Yellow Primary, heavy Black border (4.0), no shadow
        style_def = (
            "Default,Montserrat Black,68,"
            "&H0000FFFF&,&H0000FF00&,&H00000000&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,4.0,0,2,40,40,220,1"
        )
    elif style_name_lower == "hormozi":
        # Montserrat ExtraBold, size 65, White Primary, green border outline, large size
        style_def = (
            "Default,Montserrat ExtraBold,65,"
            "&H00FFFFFF&,&H0000FF00&,&H0000B300&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,4.0,1.5,2,40,40,200,1"
        )
    elif style_name_lower == "clean":
        # Arial Bold, size 60, clean layout, white primary, subtle gray outline, no shadow
        style_def = (
            "Default,Arial Bold,60,"
            "&H00FFFFFF&,&H0000FF00&,&H004D4D4D&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,1.8,0,2,40,40,180,1"
        )
    elif style_name_lower == "gold":
        # Outfit ExtraBold, size 64, Gold Primary, dark border
        style_def = (
            "Default,Outfit ExtraBold,64,"
            "&H0000D7FF&,&H0000FF00&,&H00111111&,&H80000000&,"
            "-1,0,0,0,100,100,0,0,1,3.0,1.0,2,40,40,180,1"
        )
    elif style_name_lower == "minimal":
        # Arial, size 50, solid white text, no outline, translucent black capsule background box (BorderStyle=3)
        style_def = (
            "Default,Arial,50,"
            "&H00FFFFFF&,&H00000000&,&H00000000&,&H80000000&,"
            "-1,0,0,0,100,100,0,0,3,0,0,2,40,40,180,1"
        )
    else:
        # Default: Inter Bold, size 58, white text, outline 2.5, drop shadow 1
        style_def = (
            "Default,Inter Bold,58,"
            "&H00F2F2F2&,&H00FFFF00&,&H00000000&,&H80000000&,"
            "-1,0,0,0,100,100,0,0,1,2.5,1,2,40,40,180,1"
        )

    event_format = "Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            f"Format: {style_format}",
            f"Style: {style_def}",
            "",
            "[Events]",
            f"Format: {event_format}",
            "",
        ]
    )


def _seconds_to_ass_time(seconds: float) -> str:
    """Convert float seconds to ASS timestamp H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)  # centiseconds
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_ass_chunks(
    segments: list[TranscriptSegment],
    start_offset: float,
    pacing: float = 1.0,
) -> list[dict]:
    """Break segments into timed chunks based on emotional rhythm (max 4 words)."""
    chunks: list[dict] = []
    for seg in segments:
        if seg.words:
            # Word-level timing available — exact sync
            current_group = []
            for w in seg.words:
                current_group.append(w)
                word_text = w.word.strip()
                # Split if we reach 4 words OR if there's a cadence marker (punctuation)
                if len(current_group) >= 4 or any(p in word_text for p in ".!?,-"):
                    text = " ".join(g.word for g in current_group).upper()
                    # Start 50ms early for visual punch, end exactly on the word
                    chunks.append(
                        {
                            "text": text,
                            "start": max(
                                0.0,
                                (current_group[0].start - start_offset) / pacing - 0.05,
                            ),
                            "end": max(0.01, (current_group[-1].end - start_offset) / pacing),
                        }
                    )
                    current_group = []
            if current_group:
                text = " ".join(g.word for g in current_group).upper()
                chunks.append(
                    {
                        "text": text,
                        "start": max(
                            0.0,
                            (current_group[0].start - start_offset) / pacing - 0.05,
                        ),
                        "end": max(0.01, (current_group[-1].end - start_offset) / pacing),
                    }
                )
        else:
            # Fallback if words missing — split into smaller chunks (max 3 words)
            # and distribute time proportionally
            words_list = seg.text.split()
            if not words_list:
                continue
            seg_start = max(0.0, (seg.start - start_offset) / pacing - 0.05)
            seg_end = max(0.01, (seg.end - start_offset) / pacing)
            duration = seg_end - seg_start

            # Group words into chunks of max 3 words
            chunk_size = 3
            word_groups = [
                words_list[i : i + chunk_size] for i in range(0, len(words_list), chunk_size)
            ]

            total_words = len(words_list)
            current_start = seg_start

            for group in word_groups:
                group_text = " ".join(group).upper()
                group_duration = (len(group) / total_words) * duration
                group_end = current_start + group_duration

                chunks.append({"text": group_text, "start": current_start, "end": group_end})
                current_start = group_end

    # Prevent overlapping with the previous chunk due to the 50ms early start
    for i in range(1, len(chunks)):
        if chunks[i]["start"] < chunks[i - 1]["end"]:
            chunks[i]["start"] = chunks[i - 1]["end"]

    # Drop chunks where overlap adjustment pushed start past end
    chunks = [c for c in chunks if c["start"] < c["end"]]

    return chunks


def generate_ass_file(
    segments: list[TranscriptSegment],
    start_offset: float,
    output_path: str | Path,
    pacing: float = 1.0,
    style_name: str = "default",
) -> Path:
    """Generate an ASS subtitle file from transcript segments."""
    out = Path(output_path)
    chunks = _build_ass_chunks(segments, start_offset, pacing=pacing)

    lines = [_ass_header(style_name=style_name), ""]

    highlight_colors = [
        "&H00E6E64D&",  # Soft Cyan (BGR)
        "&H0000FF00&",  # Lime
        "&H0000FFFF&",  # Warm Yellow
        "&H00FF00BF&",  # Electric Purple
    ]

    emotional_triggers = {
        "NEVER",
        "INSANE",
        "BRO",
        "NO",
        "WAY",
        "LISTEN",
        "WAIT",
        "CRAZY",
        "DESTROYED",
        "CRASHOUT",
        "WTF",
        "OMG",
        "TRUTH",
        "SECRET",
        "UNBELIEVABLE",
        "SHOCKING",
        "AURA",
    }

    for chunk in chunks:
        start = _seconds_to_ass_time(chunk["start"])
        end = _seconds_to_ass_time(chunk["end"])

        words = chunk["text"].split()
        if not words:
            continue

        colored_words = []
        for w in words:
            clean_word = "".join(c for c in w if c.isalpha())
            if clean_word in emotional_triggers:
                color = random.choice(highlight_colors)
                colored_words.append(f"{{\\c{color}}}{w}{{\\c&H00F2F2F2&}}")
            else:
                colored_words.append(w)
        text = " ".join(colored_words)

        # Calculate character density to prevent overflow on long lines
        char_count = sum(len(w) for w in words)
        if char_count > 15:
            effect = "{\\blur0.5\\fad(50,50)}"
        else:
            # Micro scale pop, fade in, and slight blur for premium feel
            effect = "{\\blur0.5\\fad(50,50)\\fscx110\\fscy110\\t(0,50,\\fscx100\\fscy100)}"
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{effect}{text}")

    out.write_text("\n".join(lines), encoding="utf-8")
    log.debug("ASS file written: %s (%d chunks)", out, len(chunks))
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def burn_subtitles(
    video_path: str | Path,
    segments: list[TranscriptSegment],
    start_offset: float,
    output_path: str | Path,
    crf: int = 18,
    preset: str = "ultrafast",
    pacing: float = 1.0,
    video_codec: str = "libx264",
    style_name: str = "default",
) -> Path:
    """
    Burn subtitles into a video using FFmpeg's native ASS filter.

    Optionally applies a pacing speedup (e.g. 1.15×) in the same FFmpeg
    pass — no extra re-encode step needed.

    Args:
        video_path:   Input video file.
        segments:     Transcript segments with timing information.
        start_offset: Start time offset for computing relative timestamps.
        output_path:  Where to write the final video.
        crf:          FFmpeg CRF quality (18 = near-lossless, 23 = default).
        preset:       FFmpeg encode preset (fast, medium, slow).
        pacing:       Speed multiplier (1.0 = no change, 1.15 = 15% faster).
        video_codec:  FFmpeg video encoder codec to use.
        style_name:   Subtitles style preset to burn in (default, mrbeast, minimal).

    Returns:
        Path to the output video.
    """
    video_path = Path(video_path)
    output_path = Path(output_path)

    log.info("🎬 Burning subtitles via FFmpeg ASS filter...")

    with tempfile.TemporaryDirectory(prefix="ass_") as tmp:
        ass_path = Path(tmp) / "subs.ass"
        generate_ass_file(segments, start_offset, ass_path, pacing=pacing, style_name=style_name)

        # FFmpeg ASS filter — libass renders directly during encode
        # On Linux the path needs colons escaped
        escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")

        # Build video + audio filters
        vf = f"ass='{escaped}',setsar=1"
        af_parts = [
            "acompressor=threshold=-20dB:ratio=4:makeup=4",
            "aformat=channel_layouts=stereo",
        ]

        if pacing != 1.0:
            # Bake pacing into this pass — setpts speeds video, atempo speeds audio
            pts_factor = round(1.0 / pacing, 6)
            vf = f"setpts={pts_factor}*PTS,{vf}"
            af_parts.insert(0, f"atempo={pacing}")

        cmd = [
            ffmpeg_path(),
            "-y",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-c:v",
            video_codec,
        ]

        if video_codec == "libx264":
            cmd.extend(["-crf", str(crf), "-preset", preset])
        elif video_codec == "h264_nvenc":
            cmd.extend(["-rc:v", "vbr", "-cq", str(crf), "-preset", preset])
        else:
            cmd.extend(["-preset", preset])

        cmd.extend(
            [
                "-af",
                ",".join(af_parts),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-use_editlist",
                "0",
                str(output_path),
            ]
        )

        log.info("Running FFmpeg: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.error("FFmpeg stderr: %s", result.stderr[-2000:])
            raise RuntimeError(f"FFmpeg subtitle burn failed (exit {result.returncode})")

    log.info("✅ Subtitles burned → %s", output_path)
    return output_path
