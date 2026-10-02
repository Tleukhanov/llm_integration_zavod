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
import re
import subprocess
import tempfile
from pathlib import Path

from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

log = logging.getLogger(__name__)

# Words that get highlighted in captions (EN + RU). Compared against
# uppercased word tokens, so Russian entries are written uppercase.
#
# Only genuinely loud tokens belong here: highlighting an ordinary word reads
# as a random colour flash rather than an accent. Plain function words were
# dropped for exactly that reason ("no" and "way" are among the most frequent
# English words, so they fired on ordinary sentences, never on a beat).
EMOTIONAL_TRIGGERS = {
    "NEVER",
    "INSANE",
    "BRO",
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
    "ВАУ",
    "БЛИН",
    "ОФИГЕТЬ",
    "НЕВЕРОЯТНО",
    "СМОТРИ",
    "СТОП",
    "СУПЕР",
    "УЖАСНО",
    "КОШМАР",
    # The Russian script banks are written in a flat, concrete register, so the
    # intensity lives in these four nouns rather than in interjections.
    "СТРАХ",
    "ТРЕВОГА",
    "УСТАЛОСТЬ",
    "ОШИБКА",
}

# Primary fill of every caption preset, plus the default style's fill. A
# highlighted word has to reset back to the colour of the preset being burned,
# so these live next to the trigger words that consume them.
_STYLE_PRIMARY_COLORS = {
    "mrbeast": "&H0000FFFF&",
    "hormozi": "&H00FFFFFF&",
    "clean": "&H00FFFFFF&",
    "gold": "&H0000D7FF&",
    "minimal": "&H00FFFFFF&",
}
DEFAULT_PRIMARY_COLOR = "&H00F2F2F2&"

# Fullscreen "edit mode" text flashes (e.g. "NEVER GIVE UP") layered on top of
# the word subtitles. Kept in its own style so the caption styles stay intact;
# it is pinned to the upper third (Alignment 8) so it never lands on the words.
FLASH_STYLE_NAME = "Flash"
FLASH_MAX_LINE_CHARS = 18
FLASH_MAX_WORD_CHARS = 14
FLASH_MAX_LINES = 3
FLASH_BASE_FONT_SIZE = 96
FLASH_MIN_FONT_SIZE = 44
_FLASH_EFFECT = r"{\fad(30,150)\fscx70\fscy70\t(0,80,\fscx100\fscy100)}"
_FLASH_STYLE_DEF = (
    f"{FLASH_STYLE_NAME},Montserrat Black,{FLASH_BASE_FONT_SIZE},"
    "&H00FFFFFF&,&H00FFFF00&,&H00000000&,&H80000000&,"
    "-1,0,0,0,88,100,0,0,1,4,1.5,8,40,40,300,1"
)

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


def style_primary_color(style_name: str) -> str:
    """Return the ASS primary fill used by a caption preset.

    A highlighted word resets its colour back to this value, so it has to come
    from the preset actually being burned rather than being hard-coded to the
    default style's off-white — otherwise the tail of every caption carrying a
    trigger word changes colour mid-line under the coloured presets.
    """
    name = str(style_name)
    if name.lower().startswith("custom_"):
        parts = name.split("_")
        if len(parts) > 3:
            return hex_to_ass_color(parts[3])
    return _STYLE_PRIMARY_COLORS.get(name.lower(), DEFAULT_PRIMARY_COLOR)


def _ass_header(style_name: str = "default", include_flash: bool = False) -> str:
    """Build the ASS subtitle file header with custom style overrides.

    ``include_flash`` appends the extra fullscreen ``Flash`` style used for
    text flashes; the caption style itself is untouched either way.
    """
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
                f"{DEFAULT_PRIMARY_COLOR},&H00FFFF00&,&H00000000&,&H80000000&,"
                "-1,0,0,0,100,100,0,0,1,2.5,1,2,40,40,180,1"
            )
    elif style_name_lower == "mrbeast":
        # Montserrat Black, size 68, Yellow Primary, heavy Black border (4.0), no shadow
        style_def = (
            "Default,Montserrat Black,68,"
            f"{_STYLE_PRIMARY_COLORS['mrbeast']},&H0000FF00&,&H00000000&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,4.0,0,2,40,40,220,1"
        )
    elif style_name_lower == "hormozi":
        # Montserrat ExtraBold, size 65, White Primary, green border outline, large size
        style_def = (
            "Default,Montserrat ExtraBold,65,"
            f"{_STYLE_PRIMARY_COLORS['hormozi']},&H0000FF00&,&H0000B300&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,4.0,1.5,2,40,40,200,1"
        )
    elif style_name_lower == "clean":
        # Arial Bold, size 60, clean layout, white primary, subtle gray outline, no shadow
        style_def = (
            "Default,Arial Bold,60,"
            f"{_STYLE_PRIMARY_COLORS['clean']},&H0000FF00&,&H004D4D4D&,&H00000000&,"
            "-1,0,0,0,100,100,0,0,1,1.8,0,2,40,40,180,1"
        )
    elif style_name_lower == "gold":
        # Outfit ExtraBold, size 64, Gold Primary, dark border
        style_def = (
            "Default,Outfit ExtraBold,64,"
            f"{_STYLE_PRIMARY_COLORS['gold']},&H0000FF00&,&H00111111&,&H80000000&,"
            "-1,0,0,0,100,100,0,0,1,3.0,1.0,2,40,40,180,1"
        )
    elif style_name_lower == "minimal":
        # Arial, size 50, solid white text, no outline, translucent black capsule background box (BorderStyle=3)
        style_def = (
            "Default,Arial,50,"
            f"{_STYLE_PRIMARY_COLORS['minimal']},&H00000000&,&H00000000&,&H80000000&,"
            "-1,0,0,0,100,100,0,0,3,0,0,2,40,40,180,1"
        )
    else:
        # Default: Inter Bold, size 58, white text, outline 2.5, drop shadow 1
        style_def = (
            "Default,Inter Bold,58,"
            f"{DEFAULT_PRIMARY_COLOR},&H00FFFF00&,&H00000000&,&H80000000&,"
            "-1,0,0,0,100,100,0,0,1,2.5,1,2,40,40,180,1"
        )

    event_format = "Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    styles_block = [f"Style: {style_def}"]
    if include_flash:
        styles_block.append(f"Style: {_FLASH_STYLE_DEF}")

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
            *styles_block,
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

    clamped = 0
    merged = 0
    kept: list[dict] = []
    pending_texts: list[str] = []
    pending_start: float | None = None
    for chunk in chunks:
        if kept and chunk["start"] < kept[-1]["end"]:
            chunk["start"] = kept[-1]["end"]
            clamped += 1
        if chunk["start"] < chunk["end"]:
            if pending_texts:
                chunk["text"] = " ".join([*pending_texts, chunk["text"]])
                if pending_start is not None:
                    chunk["start"] = min(pending_start, chunk["start"])
                if chunk["start"] >= chunk["end"]:
                    chunk["end"] = chunk["start"] + 0.02
                pending_texts = []
                pending_start = None
            kept.append(chunk)
        else:
            merged += 1
            if kept:
                prev_text = kept[-1]["text"]
                cur_text = chunk["text"]
                kept[-1]["text"] = f"{prev_text} {cur_text}".strip()
                if chunk["end"] > kept[-1]["end"]:
                    kept[-1]["end"] = chunk["end"]
            else:
                pending_texts.append(chunk["text"])
                if pending_start is None:
                    pending_start = chunk["start"]
                else:
                    pending_start = min(pending_start, chunk["start"])
    if pending_texts and not kept:
        start = pending_start if pending_start is not None else 0.0
        kept.append({"text": " ".join(pending_texts), "start": start, "end": start + 0.02})
    if clamped or merged:
        log.warning("Clamped %d chunk starts and merged %d degenerate chunks", clamped, merged)
    return kept


# ---------------------------------------------------------------------------
# Fullscreen text flashes
# ---------------------------------------------------------------------------


def _clean_flash_text(text: str) -> str:
    """Strip ASS control characters from a flash phrase."""
    cleaned = re.sub(r"[{}\r\n\t]+", " ", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _wrap_flash_text(text: str) -> str:
    """Break a long flash phrase onto balanced lines joined by ``\\N``.

    A phrase longer than ``FLASH_MAX_LINE_CHARS`` is split at the space nearest
    its middle; when a half is still too wide it is packed greedily onto extra
    lines so a long phrase reads as a block instead of one over-wide line.
    """
    if len(text) <= FLASH_MAX_LINE_CHARS or " " not in text:
        return text
    mid = len(text) // 2
    left = text.rfind(" ", 0, mid + 1)
    right = text.find(" ", mid)
    if left == -1:
        split_at = right
    elif right == -1:
        split_at = left
    else:
        split_at = left if abs(left - mid) <= abs(right - mid) else right
    if split_at <= 0:
        return text
    return "\\N".join(_pack_flash_line(text[:split_at]) + _pack_flash_line(text[split_at + 1 :]))


def _pack_flash_line(fragment: str) -> list[str]:
    """Pack a flash fragment into lines no wider than the line budget."""
    packed: list[str] = []
    current = ""
    for word in fragment.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > FLASH_MAX_LINE_CHARS:
            packed.append(current)
            current = word
        else:
            current = candidate
    if current:
        packed.append(current)
    return packed


def _flash_font_size(wrapped: str) -> int:
    """Return the render size for a wrapped flash phrase.

    Stays at ``FLASH_BASE_FONT_SIZE`` unless the phrase still overflows after
    wrapping — more than ``FLASH_MAX_LINES`` lines, a line wider than the line
    budget, or a single word over ``FLASH_MAX_WORD_CHARS`` — in which case the
    size shrinks with the widest line so the text stays inside the frame.
    """
    lines = [line for line in wrapped.split("\\N") if line.strip()]
    if not lines:
        return FLASH_BASE_FONT_SIZE
    widest = max(len(line) for line in lines)
    longest_word = max(len(word) for line in lines for word in line.split())
    overflow = max(
        widest - FLASH_MAX_LINE_CHARS,
        longest_word - FLASH_MAX_WORD_CHARS,
        len(lines) - FLASH_MAX_LINES,
    )
    if overflow <= 0:
        return FLASH_BASE_FONT_SIZE
    scale = FLASH_MAX_LINE_CHARS / widest
    return max(FLASH_MIN_FONT_SIZE, int(FLASH_BASE_FONT_SIZE * scale))


def _flash_effect(font_size: int) -> str:
    """Build the override block for one flash event, scaled down when needed."""
    if font_size == FLASH_BASE_FONT_SIZE:
        return _FLASH_EFFECT
    return f"{{\\fs{font_size}}}{_FLASH_EFFECT}"


def flash_events_to_ass(flash_events: list[dict], start_offset: float = 0.0) -> str:
    """Render flash events as ``Dialogue:`` lines ready to append to an ASS file.

    Each event is ``{"start": float, "end": float, "text": str}`` where both
    timestamps are already relative to the clip start — ``start_offset`` is
    only carried for call-site symmetry and is never added to them. Invalid or
    out-of-clip events (negative times, non-positive duration) are dropped.
    Long phrases are wrapped and shrunk inline so the upper-third flash never
    clips.
    """
    lines: list[str] = []
    for event in flash_events or []:
        try:
            start = float(event["start"])
            end = float(event["end"])
            text = _clean_flash_text(str(event["text"]))
        except (AttributeError, KeyError, TypeError, ValueError):
            log.debug("Skipping malformed flash event: %r", event)
            continue
        if not text or start < 0.0 or end <= start:
            log.debug("Skipping invalid flash event (%.3f -> %.3f): %s", start, end, text)
            continue
        wrapped = _wrap_flash_text(text)
        effect = _flash_effect(_flash_font_size(wrapped))
        lines.append(
            f"Dialogue: 0,{_seconds_to_ass_time(start)},{_seconds_to_ass_time(end)},"
            f"{FLASH_STYLE_NAME},,0,0,0,,{effect}{wrapped}"
        )
    log.debug("Built %d flash Dialogue lines (start_offset=%.3f)", len(lines), start_offset)
    return "\n".join(lines)


def generate_ass_file(
    segments: list[TranscriptSegment],
    start_offset: float,
    output_path: str | Path,
    pacing: float = 1.0,
    style_name: str = "default",
    flash_events: list[dict] | None = None,
    caption_pop: bool = False,
) -> Path:
    """Generate an ASS subtitle file from transcript segments.

    ``flash_events`` is an optional list of ``{"start", "end", "text"}`` dicts
    (seconds, already relative to the clip) rendered as fullscreen ``Flash``
    text on top of the word subtitles. ``None`` or empty keeps the previous
    word-only output byte for byte.

    ``caption_pop`` restores the per-caption 110% scale punch-in on short
    captions. It is off by default: with the four-word chunking the pop lands
    on most captions of a normal script, so the frame pulses instead of
    holding still and the text gets harder, not easier, to read.
    """
    out = Path(output_path)
    chunks = _build_ass_chunks(segments, start_offset, pacing=pacing)

    flash_block = flash_events_to_ass(flash_events) if flash_events else ""
    lines = [_ass_header(style_name=style_name, include_flash=bool(flash_block)), ""]

    highlight_colors = [
        "&H00E6E64D&",  # Soft Cyan (BGR)
        "&H0000FF00&",  # Lime
        "&H0000FFFF&",  # Warm Yellow
        "&H00FF00BF&",  # Electric Purple
    ]
    # Highlighted words reset back to the preset's own fill, and the accent
    # colour advances per accent so a render is reproducible byte for byte
    # instead of depending on the global RNG.
    base_color = style_primary_color(style_name)
    highlighted = 0

    for chunk in chunks:
        start = _seconds_to_ass_time(chunk["start"])
        end = _seconds_to_ass_time(chunk["end"])

        words = chunk["text"].split()
        if not words:
            continue

        colored_words = []
        for w in words:
            clean_word = "".join(c for c in w if c.isalpha())
            if clean_word in EMOTIONAL_TRIGGERS:
                color = highlight_colors[highlighted % len(highlight_colors)]
                highlighted += 1
                colored_words.append(f"{{\\c{color}}}{w}{{\\c{base_color}}}")
            else:
                colored_words.append(w)
        text = " ".join(colored_words)

        # Fade + a hair of blur on every caption; the scale pop is opt-in.
        effect = "{\\blur0.5\\fad(50,50)"
        if caption_pop and sum(len(w) for w in words) <= 15:
            effect += "\\fscx110\\fscy110\\t(0,50,\\fscx100\\fscy100)"
        effect += "}"
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{effect}{text}")

    # Flashes go last so they render above the word subtitles.
    if flash_block:
        lines.append(flash_block)

    out.write_text("\n".join(lines), encoding="utf-8")
    log.debug("ASS file written: %s (%d chunks)", out, len(chunks))
    return out


def _escape_drawtext_literal(text: str) -> str:
    """Escape a string for inlining into a ``drawtext`` filter option.

    The text lands inside ``text='...'`` in a filtergraph, so backslash, the
    quote and the graph separators have to be escaped, and ``%`` has to be
    escaped as well because drawtext expands it as a printf directive. Without
    this a comma or a percent sign in operator-supplied copy (Russian banners
    are full of commas) silently breaks the filter chain.
    """
    escaped = str(text).replace("\\", "\\\\").replace("'", "'\\''")
    for char in (":", ",", ";", "[", "]", "%"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped


# ---------------------------------------------------------------------------
# Ad-card text helpers
# ---------------------------------------------------------------------------

# A promo code bound to a single dash-free/URL-free word: 4-12 alphanumerics.
_OFFER_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z0-9]{4,12}(?![A-Za-z0-9])")


def _split_ad_card_text(text: str) -> tuple[str, str | None]:
    """Split ad-card CTA text into (body, offer_code).

    A code is recognized either by an explicit ``code=``/``code:`` marker or
    as a standalone all-caps alphanumeric word that is not embedded in a URL.
    Returns the body without the code and the matched code (or ``None``).
    """
    explicit = re.search(r"(?i)\bcode\s*[=:]\s*([A-Z0-9]{4,12})\b", text)
    if explicit:
        code = explicit.group(1)
        body = text.replace(explicit.group(0), "").strip()
        return (re.sub(r"\s{2,}", " ", body).strip(" ,:;-"), code)

    for candidate in _OFFER_CODE_RE.findall(text):
        # Skip codes that are part of a URL/path token (e.g. "example.com/ABC123").
        start = text.find(candidate)
        before = text[max(0, start - 1) : start]
        after = text[start + len(candidate) : start + len(candidate) + 1]
        if before in "/.:@=" or after in "/.:@=":
            continue
        body = text[:start].rstrip(" ,:;-")
        body = body + " " + text[start + len(candidate) :].lstrip(" ,:;-")
        body = re.sub(r"\s{2,}", " ", body).strip()
        return (body, candidate)

    return (text, None)


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
    banner_image: str | Path | None = None,
    banner_position: str = "bottom_left",
    bgm_audio: str | Path | None = None,
    bgm_volume: float = 0.30,
    bgm_music_forward: bool = False,
    ad_card_image: str | Path | None = None,
    ad_card_text: str | None = None,
    ad_card_start: float = 0.0,
    ad_card_duration: float | None = None,
    peak_second: float | None = None,
    hook_banner_text: str | None = None,
    vo_output_path: str | Path | None = None,
    flash_events: list[dict] | None = None,
    caption_pop: bool = False,
) -> Path:
    """
    Burn subtitles into a video using FFmpeg's native ASS filter.

    Optionally applies a pacing speedup (e.g. 1.15×) in the same FFmpeg
    pass — no extra re-encode step needed.

    Args:
        video_path:     Input video file.
        segments:       Transcript segments with timing information.
        start_offset:   Start time offset for computing relative timestamps.
        output_path:    Where to write the final video.
        crf:            FFmpeg CRF quality (18 = near-lossless, 23 = default).
        preset:         FFmpeg encode preset (fast, medium, slow).
        pacing:         Speed multiplier (1.0 = no change, 1.15 = 15% faster).
        video_codec:    FFmpeg video encoder codec to use.
        style_name:     Subtitles style preset to burn in (default, mrbeast,
                        hormozi, clean, gold, minimal, or custom_<font>_<size>_
                        <primary>_<outline>_<outline_w>_<shadow>).  Anything
                        unrecognized falls back to default.
        banner_image:   Optional partner banner image overlaid on the video.
        banner_position: Corner for the banner overlay
                        (bottom_left, bottom_right, top_left, top_right).
        bgm_audio:      Optional background-music file mixed under the commentary.
        bgm_volume:     Loudness of the background music (0.0-1.0).
        bgm_music_forward: Music-forward mix for hype/gameplay clips — BGM is
                        mixed on top of an attenuated bed instead of averaged
                        under it, so the track is clearly audible.
        ad_card_image:  Optional big brand image shown as a timed mid-roll card.
        ad_card_text:   Optional CTA text banner shown with the ad card.
        ad_card_start:  Timestamp (seconds) when the mid-roll card appears.
        ad_card_duration: How long the card is visible (None => until end of clip).
        peak_second:    Optional peak/sell moment (seconds, relative to the trimmed
                        clip).  When positive, overrides ``ad_card_start`` so the
                        card lands at the peak (clamped to the clip duration and
                        at least ~1.2s before the end).  When absent, the default
                        ``ad_card_start`` behavior is kept unchanged.
        hook_banner_text:  Optional hook caption burned into the first ~1 s of
                          the clip (upper-third, bold yellow, black border,
                          fades out).  ``None`` or empty disables it.
        vo_output_path:   Optional voiceover WAV file mixed on top of game
                          audio for an extra original audio layer.
        flash_events:     Optional list of {"start", "end", "text"} dicts
                          rendered as fullscreen "Flash" text over the subtitles.
        caption_pop:      Re-enable the per-caption scale punch-in. Off by
                          default so short captions hold still and stay legible.

    Returns:
        Path to the output video.
    """
    video_path = Path(video_path)
    output_path = Path(output_path)

    log.info("🎬 Burning subtitles via FFmpeg ASS filter...")

    # Mid-roll ad card presence
    ad_image = Path(ad_card_image) if ad_card_image else None
    use_ad_image = ad_image is not None and ad_image.is_file()
    if ad_image and not use_ad_image:
        log.warning("Affiliate ad card image not found, skipping image overlay: %s", ad_image)
    ad_text = ad_card_text if ad_card_text else None

    # Peak-aware ad-card timing: when a positive peak_second is supplied, anchor
    # the card on the sell moment instead of the fixed default start. Clamp it to
    # the clip duration and keep it at least ~1.2s from the end so it is visible.
    card_body, offer_code = (
        _split_ad_card_text(ad_text) if ad_text else (ad_text, None)
    )
    clip_duration = None
    if peak_second is not None and peak_second > 0:
        try:
            from shorts_clipper.utils.video import get_video_metadata

            clip_duration = get_video_metadata(video_path).duration
        except Exception:
            clip_duration = None
        if clip_duration is not None and clip_duration > 0:
            _start = min(float(peak_second), max(0.0, clip_duration - 1.2))
            if _start > 0:
                ad_card_start = _start
                if ad_card_duration is not None:
                    ad_card_duration = min(ad_card_duration, clip_duration - ad_card_start)
                    ad_card_duration = max(ad_card_duration, 0.0)

    with tempfile.TemporaryDirectory(prefix="ass_") as tmp:
        ass_path = Path(tmp) / "subs.ass"
        generate_ass_file(
            segments,
            start_offset,
            ass_path,
            pacing=pacing,
            style_name=style_name,
            flash_events=flash_events,
            caption_pop=caption_pop,
        )

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
        ]

        banner = Path(banner_image) if banner_image else None
        use_banner = banner is not None and banner.is_file()
        if banner and not use_banner:
            log.warning("Affiliate banner image not found, skipping overlay: %s", banner)

        music = Path(bgm_audio) if bgm_audio is not None else None
        use_bgm = music is not None and music.is_file()
        if music is not None and not use_bgm:
            log.warning("BGM audio file not found, rendering clean commentary: %s", music)

        # Extra inputs: 1 = corner banner, then ad card image, then bgm audio
        input_idx = 1
        if use_banner:
            cmd.extend(["-i", str(banner)])
            input_idx += 1
        if use_ad_image:
            cmd.extend(["-i", str(ad_image)])
            ad_image_input = input_idx
            input_idx += 1
        else:
            ad_image_input = None
        if use_bgm:
            cmd.extend(["-i", str(music)])
            bgm_index = input_idx
            input_idx += 1

        vo = Path(vo_output_path) if vo_output_path is not None else None
        use_vo = vo is not None and vo.is_file()
        if vo is not None and not use_vo:
            log.warning("Voiceover audio file not found, skipping voiceover: %s", vo)
        if use_vo:
            cmd.extend(["-i", str(vo)])
            vo_index = input_idx
            input_idx += 1

        # Drawtext font — use a Windows font but fallback to default if missing
        fontfile = "C:/Windows/Fonts/arialbd.ttf"
        font_arg = ""
        if Path("C:/Windows/Fonts/arialbd.ttf").exists():
            escaped_fontfile = fontfile.replace(":", "\\:")
            font_arg = f"fontfile='{escaped_fontfile}':"

        # Mid-roll ad card window; None => until end of clip (per-frame check)
        if ad_card_duration is not None:
            ad_end = ad_card_start + ad_card_duration
            if clip_duration is not None:
                ad_end = min(ad_end, clip_duration)
        else:
            ad_end = clip_duration if clip_duration is not None else 999999.0

        # Hook banner drawtext filter (first ~1 s, upper-third, bold yellow, fades out)
        hook_filter = ""
        if hook_banner_text:
            _hook_escaped = _escape_drawtext_literal(hook_banner_text)
            hook_filter = (
                f"drawtext={font_arg}text='{_hook_escaped}':"
                "fontsize=H/14:fontcolor=yellow:"
                "borderw=5:bordercolor=black:"
                f"x=(w-text_w)/2:y=h/6:"
                "enable='between(t,0,1.0)':"
                "alpha='if(between(t,0.85,1.0),(1.0-t)/0.15,1)'"
            )

        # Any video overlay (banner, ad card image, or drawtext CTA)?
        video_overlay = use_banner or use_ad_image or bool(ad_text)

        def _append_ad_drawtext(parts: list[str], current: str, out: str) -> str:
            """Append the ad-card CTA drawtext chain, returning the out label.

            Without an offer code it's a single card line; with an offer code
            the code is drawn as a distinct highlighted line (larger, amber)
            just above the card box for a prominent call-to-action.
            """
            if not ad_text:
                parts.append(f"[{current}]null[{out}]")
                return out

            enable = f"enable='between(t,{ad_card_start},{ad_end})'"
            # Main card box (body text, or the full CTA when no code).
            card_text = card_body if offer_code else ad_text
            body_path = Path(tmp) / "ad_text.txt"
            body_path.write_text(card_text, encoding="utf-8")
            body_escaped = str(body_path).replace("\\", "/").replace(":", "\\:")
            if offer_code:
                code_path = Path(tmp) / "ad_code.txt"
                code_path.write_text(offer_code, encoding="utf-8")
                code_escaped = str(code_path).replace("\\", "/").replace(":", "\\:")
                # Code line: larger, bold amber, sits above the card box.
                parts.append(
                    f"[{current}]drawtext={font_arg}textfile='{code_escaped}':"
                    "fontsize=H/10:fontcolor=0xFFBF00:text_align=center:"
                    "borderw=6:bordercolor=black:box=1:boxcolor=black@0.7:boxborderw=20:"
                    f"x=(w-text_w)/2:y=H-h-560:"
                    f"{enable}[{out}]"
                )
                return out
            parts.append(
                f"[{current}]drawtext={font_arg}textfile='{body_escaped}':"
                f"fontsize=H/14:fontcolor=white:borderw=3:bordercolor=black:"
                f"box=1:boxcolor=black@0.75:boxborderw=28:"
                f"x=(w-text_w)/2:y=H-h-400:"
                f"{enable}[{out}]"
            )
            return out

        if use_bgm:
            # A single -filter_complex must carry BOTH the video chain (with any
            # overlays) and the audio mix (you cannot combine -vf and -filter_complex).
            parts = [f"[0:v]{vf}[v0]"]
            current = "v0"

            if hook_filter:
                parts.append(f"[{current}]{hook_filter}[vh]")
                current = "vh"

            if use_banner:
                parts.append("[1:v]scale=200:-1[logo]")
                overlay_x = "W-w-40" if "right" in banner_position else "40"
                overlay_y = "40" if banner_position.startswith("top") else "H-h-300"
                parts.append(f"[{current}][logo]overlay={overlay_x}:{overlay_y}[v1]")
                current = "v1"

            if use_ad_image:
                parts.append(f"[{ad_image_input}:v]scale=-2:'min(ih,300)'[card]")
                parts.append(
                    f"[{current}][card]overlay=(W-w)/2:H-h-320:"
                    f"enable='between(t,{ad_card_start},{ad_end})'[v2]"
                )
                current = "v2"

            _append_ad_drawtext(parts, current, "vout")

            bgm_vol = float(bgm_volume)
            bgm_af = [p for p in af_parts if not p.startswith("atempo=")]
            if pacing != 1.0:
                audio_post = "atempo={},{}".format(pacing, ",".join(bgm_af))
            else:
                audio_post = ",".join(bgm_af)

            if bgm_music_forward:
                # Music-forward for hype clips: `amix:normalize=0` does NOT halve
                # the music like the default averaged mix, the bed is ducked to
                # 0.55 to make room, and a final limiter stops post-mix clipping.
                parts.append(f"[{bgm_index}:a]volume={bgm_vol:.3f}[bg]")
                parts.append("[0:a]volume=0.550[prog]")
                if use_vo:
                    parts.append(f"[{vo_index}:a]volume=0.700[vo]")
                    parts.append(
                        f"[prog][bg][vo]amix=inputs=3:duration=first:dropout_transition=0:normalize=0[mix];"
                        f"[mix]{audio_post},alimiter=limit=0.97[aout]"
                    )
                else:
                    parts.append(
                        f"[prog][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
                        f"[mix]{audio_post},alimiter=limit=0.97[aout]"
                    )
            else:
                parts.append(f"[{bgm_index}:a]volume={bgm_vol:.3f}[bg]")
                if use_vo:
                    parts.append(f"[{vo_index}:a]volume=0.700[vo]")
                    parts.append(
                        f"[0:a][bg][vo]amix=inputs=3:duration=first:dropout_transition=0[mix];[mix]"
                        f"{audio_post}[aout]"
                    )
                else:
                    parts.append(
                        f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[mix];[mix]"
                        f"{audio_post}[aout]"
                    )

            filter_complex = ";".join(parts)
            cmd.extend(["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"])
        elif video_overlay:
            parts = [f"[0:v]{vf}[v0]"]
            current = "v0"

            if hook_filter:
                parts.append(f"[{current}]{hook_filter}[vh]")
                current = "vh"

            if use_banner:
                parts.append("[1:v]scale=200:-1[logo]")
                overlay_x = "W-w-40" if "right" in banner_position else "40"
                overlay_y = "40" if banner_position.startswith("top") else "H-h-300"
                parts.append(f"[{current}][logo]overlay={overlay_x}:{overlay_y}[v1]")
                current = "v1"

            if use_ad_image:
                parts.append(f"[{ad_image_input}:v]scale=-2:'min(ih,300)'[card]")
                parts.append(
                    f"[{current}][card]overlay=(W-w)/2:H-h-320:"
                    f"enable='between(t,{ad_card_start},{ad_end})'[v2]"
                )
                current = "v2"

            _append_ad_drawtext(parts, current, "vout")

            if use_vo:
                bgm_af = [p for p in af_parts if not p.startswith("atempo=")]
                if pacing != 1.0:
                    audio_post = "atempo={},{}".format(pacing, ",".join(bgm_af))
                else:
                    audio_post = ",".join(bgm_af)
                parts.append(f"[{vo_index}:a]volume=1.400[vo]")
                parts.append(
                    f"[0:a][vo]amix=inputs=2:duration=first:dropout_transition=0[mix];[mix]"
                    f"{audio_post}[aout]"
                )
                filter_complex = ";".join(parts)
                cmd.extend(
                    ["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"]
                )
            else:
                filter_complex = ";".join(parts)
                cmd.extend(
                    ["-filter_complex", filter_complex, "-map", "[vout]", "-map", "0:a?"]
                )
        else:
            if use_vo:
                bgm_af = [p for p in af_parts if not p.startswith("atempo=")]
                if pacing != 1.0:
                    audio_post = "atempo={},{}".format(pacing, ",".join(bgm_af))
                else:
                    audio_post = ",".join(bgm_af)
                parts = [f"[0:v]{vf}[v0]"]
                current = "v0"
                if hook_filter:
                    parts.append(f"[{current}]{hook_filter}[vh]")
                    current = "vh"
                parts.append(f"[{vo_index}:a]volume=1.400[vo]")
                parts.append(
                    f"[0:a][vo]amix=inputs=2:duration=first:dropout_transition=0[mix];[mix]"
                    f"{audio_post}[aout]"
                )
                filter_complex = ";".join(parts)
                cmd.extend(
                    ["-filter_complex", filter_complex, "-map", f"[{current}]", "-map", "[aout]"]
                )
            else:
                simple_vf = vf
                if hook_filter:
                    simple_vf += f",{hook_filter}"
                cmd.extend(["-vf", simple_vf])

        cmd.extend(["-c:v", video_codec])

        if video_codec == "libx264":
            cmd.extend(["-crf", str(crf), "-preset", preset])
        elif video_codec == "h264_nvenc":
            cmd.extend(["-rc:v", "vbr", "-cq", str(crf), "-preset", preset])
        else:
            cmd.extend(["-preset", preset])

        audio_args = [] if (use_bgm or use_vo) else ["-af", ",".join(af_parts)]
        cmd.extend(
            audio_args
            + [
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
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg subtitle burn timed out after 600s") from None
        if result.returncode != 0:
            log.error("FFmpeg stderr: %s", result.stderr[-2000:])
            raise RuntimeError(f"FFmpeg subtitle burn failed (exit {result.returncode})")

    log.info("✅ Subtitles burned → %s", output_path)
    return output_path
