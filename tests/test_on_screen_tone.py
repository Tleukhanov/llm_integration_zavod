"""Guards for the on-screen tone audit: hook banner, caption motion, accents.

Each test pins one decision from the audit so the defaults cannot silently
drift back to the engagement-bait treatment.
"""

import os
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shorts_clipper.captions.generator import (
    EMOTIONAL_TRIGGERS,
    _ass_header,
    _escape_drawtext_literal,
    generate_ass_file,
    style_primary_color,
)
from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.core.settings import Settings
from shorts_clipper.visual import stock as stock_visual


def _segments(texts):
    segments = []
    for idx, text in enumerate(texts):
        words = [
            TranscriptWord(start=idx + i * 0.4, end=idx + i * 0.4 + 0.35, word=w)
            for i, w in enumerate(text.split())
        ]
        segments.append(
            TranscriptSegment(
                start=float(idx),
                end=float(idx) + len(words) * 0.4,
                text=text,
                words=words,
            )
        )
    return segments


def _render(**kwargs):
    with TemporaryDirectory() as tmp:
        out = generate_ass_file(
            _segments(["страх денег", "ошибка в цене"]),
            0.0,
            Path(tmp) / "subs.ass",
            **kwargs,
        )
        return out.read_text(encoding="utf-8")


class HookBannerDefaultTests(unittest.TestCase):
    """The English stock banner must not reach a Russian render by default."""

    def test_no_banner_text_without_explicit_env(self):
        saved = os.environ.pop("SHORTS_HOOK_BANNER_TEXT", None)
        try:
            s = Settings.from_env("_nonexistent.env")
        finally:
            if saved is not None:
                os.environ["SHORTS_HOOK_BANNER_TEXT"] = saved
        self.assertEqual(s.hook_banner_text, "")

    def test_explicit_env_restores_the_banner(self):
        saved = os.environ.get("SHORTS_HOOK_BANNER_TEXT")
        os.environ["SHORTS_HOOK_BANNER_TEXT"] = "ДО КОНЦА"
        try:
            s = Settings.from_env("_nonexistent.env")
        finally:
            if saved is None:
                os.environ.pop("SHORTS_HOOK_BANNER_TEXT", None)
            else:
                os.environ["SHORTS_HOOK_BANNER_TEXT"] = saved
        self.assertEqual(s.hook_banner_text, "ДО КОНЦА")

    def test_dataclass_default_is_unchanged(self):
        self.assertEqual(Settings().hook_banner_text, "WAIT FOR IT\u2026")

    def test_env_file_counts_as_explicit(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("SHORTS_HOOK_BANNER_TEXT=ДО КОНЦА\n", encoding="utf-8")
            saved = os.environ.pop("SHORTS_HOOK_BANNER_TEXT", None)
            try:
                s = Settings.from_env(env_file)
            finally:
                if saved is not None:
                    os.environ["SHORTS_HOOK_BANNER_TEXT"] = saved
        self.assertEqual(s.hook_banner_text, "ДО КОНЦА")


class DrawtextEscapingTests(unittest.TestCase):
    """Copy with commas or percents must not break the filter graph."""

    def test_graph_separators_are_escaped(self):
        for char in (":", ",", ";", "[", "]", "%"):
            with self.subTest(char=char):
                self.assertEqual(
                    _escape_drawtext_literal(f"a{char}b"), f"a\\{char}b"
                )

    def test_backslash_and_quote_are_escaped(self):
        self.assertEqual(_escape_drawtext_literal("a\\b"), "a\\\\b")
        self.assertEqual(_escape_drawtext_literal("it's"), "it'\\''s")

    def test_plain_text_is_untouched(self):
        self.assertEqual(_escape_drawtext_literal("WAIT FOR IT..."), "WAIT FOR IT...")


class CaptionMotionTests(unittest.TestCase):
    """Captions hold still unless the punch-in is asked for."""

    def test_pop_is_absent_by_default(self):
        text = _render()
        self.assertNotIn(r"\fscx110", text)
        self.assertIn(r"{\blur0.5\fad(50,50)}", text)

    def test_pop_is_restored_on_opt_in(self):
        text = _render(caption_pop=True)
        self.assertIn(r"\fscx110\fscy110", text)
        self.assertIn(r"\t(0,50,\fscx100\fscy100)", text)

    def test_opt_in_never_pops_a_dense_caption(self):
        with TemporaryDirectory() as tmp:
            out = generate_ass_file(
                _segments(["четыре месяца в почте лежит строка давай обсудим"]),
                0.0,
                Path(tmp) / "subs.ass",
                caption_pop=True,
            )
            text = out.read_text(encoding="utf-8")
        self.assertIn(r"{\blur0.5\fad(50,50)}", text)
        self.assertNotIn(r"\fscx110", text)


class AccentColourTests(unittest.TestCase):
    """Highlights must not repaint the rest of the caption."""

    def test_reset_matches_default_style_fill(self):
        self.assertIn(
            "{\\c&H00F2F2F2&}",
            _render(style_name="default"),
        )

    def test_reset_matches_preset_fill(self):
        text = _render(style_name="mrbeast")
        self.assertIn("{\\c&H0000FFFF&}", text)
        self.assertNotIn("{\\c&H00F2F2F2&}", text)

    def test_render_is_deterministic_without_rng(self):
        random.seed(1)
        first = _render(style_name="gold")
        random.seed(999)
        second = _render(style_name="gold")
        self.assertEqual(first, second)

    def test_style_primary_colour_per_preset(self):
        self.assertEqual(style_primary_color("mrbeast"), "&H0000FFFF&")
        self.assertEqual(style_primary_color("gold"), "&H0000D7FF&")
        self.assertEqual(style_primary_color("minimal"), "&H00FFFFFF&")
        self.assertEqual(style_primary_color("nonsense"), "&H00F2F2F2&")

    def test_custom_preset_uses_its_own_fill(self):
        style_name = "custom_Inter_58_FF5500_000000_2_1"
        self.assertEqual(style_primary_color(style_name), "&H000055FF&")
        header = _ass_header(style_name=style_name)
        self.assertIn("&H000055FF&", header)

    def test_preset_fill_matches_emitted_style_line(self):
        for preset in ("mrbeast", "hormozi", "clean", "gold", "minimal"):
            with self.subTest(preset=preset):
                line = [
                    ln
                    for ln in _ass_header(style_name=preset).splitlines()
                    if ln.startswith("Style:")
                ][0]
                self.assertEqual(line.split(",")[3], style_primary_color(preset))


class AccentTriggerTests(unittest.TestCase):
    """Trigger words carry meaning; ordinary words never get an accent."""

    def test_plain_english_function_words_are_not_triggers(self):
        for word in ("NO", "WAY"):
            self.assertNotIn(word, EMOTIONAL_TRIGGERS)

    def test_russian_bank_intensity_words_are_triggers(self):
        for word in ("СТРАХ", "ТРЕВОГА", "УСТАЛОСТЬ", "ОШИБКА"):
            self.assertIn(word, EMOTIONAL_TRIGGERS)

    def test_trigger_hits_are_rare_on_a_normal_script(self):
        lines = _render().splitlines()
        accented = [ln for ln in lines if ln.startswith("Dialogue") and "{\\c&H00E6" in ln]
        self.assertLessEqual(len(accented), 2)

    def test_russian_narrative_word_is_accented(self):
        text = _render(style_name="default")
        self.assertIn("{\\c&H00E6E64D&}СТРАХ", text)


class FlashCadenceTests(unittest.TestCase):
    """Text flashes are an accent, not a permanent overlay."""

    def test_default_stride_keeps_prior_art_cadence(self):
        flashes = stock_visual.edit_flash_schedule(9.0)
        self.assertEqual(len(flashes), 4)  # 3 bar slots + cold open

    def test_stride_two_leaves_bars_clear(self):
        flashes = stock_visual.edit_flash_schedule(30.0, bars=2)
        # 8 bar-aligned slots + the cold open.
        self.assertEqual(len(flashes), 9)
        bar = 4 * stock_visual.beat_seconds(132)
        opener, *grid = flashes
        self.assertAlmostEqual(opener["start"], stock_visual._EDIT_FLASH_OPEN_OFFSET, places=3)
        for flash in grid:
            self.assertAlmostEqual(
                flash["start"], round(flash["start"] / bar) * bar, places=3
            )
            self.assertLess(flash["start"], flash["end"])

    def test_stride_two_cuts_on_screen_coverage(self):
        def coverage(bars):
            flashes = stock_visual.edit_flash_schedule(30.0, bars=bars)
            return sum(f["end"] - f["start"] for f in flashes)

        self.assertLess(coverage(2), coverage(1) * 0.6)

    def test_stride_is_clamped_to_at_least_one_bar(self):
        self.assertEqual(
            stock_visual.edit_flash_schedule(9.0, bars=0),
            stock_visual.edit_flash_schedule(9.0, bars=1),
        )

    def test_stride_two_never_repeats_a_phrase(self):
        flashes = stock_visual.edit_flash_schedule(30.0, bars=2)
        texts = [f["text"] for f in flashes]
        for prev, cur in zip(texts, texts[1:], strict=False):
            self.assertNotEqual(prev, cur)

    def test_stride_two_is_deterministic(self):
        self.assertEqual(
            stock_visual.edit_flash_schedule(20.0, bars=2, seed=3),
            stock_visual.edit_flash_schedule(20.0, bars=2, seed=3),
        )

    def test_short_timeline_still_yields_a_flash(self):
        self.assertTrue(stock_visual.edit_flash_schedule(4.0, bars=2))


class CaptionMotionSettingsTests(unittest.TestCase):
    def test_defaults_are_calm(self):
        s = Settings.from_env("_nonexistent.env")
        self.assertFalse(s.caption_scale_pop)
        self.assertEqual(s.stock_edit_flash_bars, 2)

    def test_env_overrides(self):
        saved = {
            k: os.environ.get(k)
            for k in ("SHORTS_CAPTION_SCALE_POP", "SHORTS_STOCK_EDIT_FLASH_BARS")
        }
        os.environ["SHORTS_CAPTION_SCALE_POP"] = "true"
        os.environ["SHORTS_STOCK_EDIT_FLASH_BARS"] = "1"
        try:
            s = Settings.from_env("_nonexistent.env")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertTrue(s.caption_scale_pop)
        self.assertEqual(s.stock_edit_flash_bars, 1)

    def test_flash_bars_value_is_clamped(self):
        saved = os.environ.get("SHORTS_STOCK_EDIT_FLASH_BARS")
        os.environ["SHORTS_STOCK_EDIT_FLASH_BARS"] = "99"
        try:
            s = Settings.from_env("_nonexistent.env")
        finally:
            if saved is None:
                os.environ.pop("SHORTS_STOCK_EDIT_FLASH_BARS", None)
            else:
                os.environ["SHORTS_STOCK_EDIT_FLASH_BARS"] = saved
        self.assertLessEqual(s.stock_edit_flash_bars, 8)

    def test_invalid_flash_bars_falls_back(self):
        saved = os.environ.get("SHORTS_STOCK_EDIT_FLASH_BARS")
        os.environ["SHORTS_STOCK_EDIT_FLASH_BARS"] = "abc"
        try:
            s = Settings.from_env("_nonexistent.env")
        finally:
            if saved is None:
                os.environ.pop("SHORTS_STOCK_EDIT_FLASH_BARS", None)
            else:
                os.environ["SHORTS_STOCK_EDIT_FLASH_BARS"] = saved
        self.assertEqual(s.stock_edit_flash_bars, 2)


if __name__ == "__main__":
    unittest.main()