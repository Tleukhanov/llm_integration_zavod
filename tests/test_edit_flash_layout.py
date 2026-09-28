"""Tests for the edit-mode flash layout: upper-third placement, wrapping, guards."""

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shorts_clipper.captions.generator import (
    FLASH_MAX_LINE_CHARS,
    FLASH_STYLE_NAME,
    _ass_header,
    flash_events_to_ass,
    generate_ass_file,
)
from shorts_clipper.core.models import TranscriptSegment, TranscriptWord

SEGMENTS = [
    TranscriptSegment(
        start=0.0,
        end=2.0,
        text="never give up bro",
        words=[
            TranscriptWord(start=0.0, end=0.5, word="never"),
            TranscriptWord(start=0.5, end=1.0, word="give"),
            TranscriptWord(start=1.0, end=1.5, word="up"),
            TranscriptWord(start=1.5, end=2.0, word="bro"),
        ],
    ),
    TranscriptSegment(start=2.0, end=4.0, text="this is crazy work"),
]

FLASH_EVENTS = [
    {"start": 1.81, "end": 3.63, "text": "NEVER GIVE UP"},
]


def _flash_style_line() -> str:
    for line in _ass_header(style_name="default", include_flash=True).splitlines():
        if line.startswith(f"Style: {FLASH_STYLE_NAME},"):
            return line
    raise AssertionError("Flash style line missing from the ASS header")


class FlashDialogueTests(unittest.TestCase):
    def test_flash_events_emit_dialogue_lines(self):
        block = flash_events_to_ass(FLASH_EVENTS)
        lines = [line for line in block.splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Dialogue: "))
        self.assertIn(f",{FLASH_STYLE_NAME},,0,0,0,,", lines[0])
        self.assertTrue(lines[0].endswith("NEVER GIVE UP"))

    def test_flash_pops_with_short_fade_and_scale_transform(self):
        line = flash_events_to_ass(FLASH_EVENTS).splitlines()[0]
        self.assertIn(r"\fad(", line)
        self.assertIn(r"\fscx70\fscy70", line)
        self.assertIn(r"\t(0,80,\fscx100\fscy100)", line)

    def test_generated_file_uses_flash_style_for_events(self):
        with TemporaryDirectory() as tmp:
            out = generate_ass_file(
                SEGMENTS,
                0.0,
                Path(tmp) / "subs.ass",
                flash_events=FLASH_EVENTS,
            )
            text = out.read_text(encoding="utf-8")
        self.assertIn(f"Style: {FLASH_STYLE_NAME},", text)
        flash_lines = [ln for ln in text.splitlines() if f",{FLASH_STYLE_NAME},," in ln]
        self.assertEqual(len(flash_lines), 1)


class FlashStylePlacementTests(unittest.TestCase):
    def test_flash_style_is_upper_third(self):
        fields = _flash_style_line().split(",")
        self.assertEqual(fields[18], "8", "flash text must use top-centre alignment")
        self.assertNotEqual(fields[18], "5")
        self.assertGreater(int(fields[21]), 0)
        self.assertLessEqual(int(fields[21]), 450)

    def test_flash_style_keeps_white_fill_and_heavy_outline(self):
        fields = _flash_style_line().split(",")
        self.assertEqual(fields[1], "Montserrat Black")
        self.assertEqual(fields[3], "&H00FFFFFF&")
        self.assertEqual(fields[5], "&H00000000&")
        self.assertEqual(fields[16], "4")
        self.assertEqual(fields[11], "88")

    def test_no_flash_style_without_events(self):
        header = _ass_header(style_name="default", include_flash=False)
        self.assertNotIn("Flash", header)


class FlashWrappingTests(unittest.TestCase):
    def test_long_phrase_is_wrapped(self):
        line = flash_events_to_ass(
            [{"start": 0.0, "end": 1.5, "text": "NEVER GIVE UP TONIGHT BRO"}]
        ).splitlines()[0]
        self.assertIn(r"\N", line)
        for part in line.split("}")[-1].split(r"\N"):
            self.assertLessEqual(len(part), FLASH_MAX_LINE_CHARS + 1)

    def test_overlong_single_word_is_scaled_down_inline(self):
        line = flash_events_to_ass(
            [{"start": 0.0, "end": 1.5, "text": "SUPERCALIFRAGILISTICEXPIALIDOCIOUS"}]
        ).splitlines()[0]
        self.assertRegex(line, r"\{\\fs\d+\}")

    def test_many_word_phrase_is_wrapped_to_several_lines(self):
        line = flash_events_to_ass(
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE",
                }
            ]
        ).splitlines()[0]
        self.assertGreaterEqual(line.count(r"\N"), 2)


class FlashEventGuardTests(unittest.TestCase):
    def test_invalid_events_are_dropped(self):
        events = [
            {"start": -1.0, "end": 1.0, "text": "NEGATIVE START"},
            {"start": 2.0, "end": 1.0, "text": "BACKWARDS"},
            {"start": 3.0, "end": 3.0, "text": "ZERO DURATION"},
            {"start": 1.0, "end": 2.0, "text": "   "},
            {"start": "bad", "end": 2.0, "text": "BAD TIMING"},
            {"start": 1.0, "end": 2.0},
            "not a dict",
        ]
        self.assertEqual(flash_events_to_ass(events), "")

    def test_negative_times_are_dropped(self):
        events = [
            {"start": -1.0, "end": 1.0, "text": "NEGATIVE START"},
            {"start": 1.0, "end": -0.5, "text": "NEGATIVE END"},
            {"start": 2.0, "end": 1.0, "text": "BACKWARDS"},
            {"start": 3.0, "end": 3.0, "text": "ZERO DURATION"},
            {"start": 1.0, "end": 2.0, "text": "   "},
            {"start": "bad", "end": 2.0, "text": "BAD TIMING"},
            {"start": 1.0, "end": 2.0},
            "not a dict",
        ]
        self.assertEqual(flash_events_to_ass(events), "")

    def test_valid_event_survives_neighbours(self):
        block = flash_events_to_ass(
            [
                {"start": -1.0, "end": 1.0, "text": "BAD"},
                {"start": 1.0, "end": 2.0, "text": "GOOD"},
            ]
        )
        self.assertEqual(len(block.splitlines()), 1)
        self.assertIn("0:00:01.00,0:00:02.00", block)
        self.assertNotIn("BAD", block)

    def test_no_events_yields_no_block(self):
        self.assertEqual(flash_events_to_ass([]), "")
        self.assertEqual(flash_events_to_ass(None), "")


class NoFlashRegressionTests(unittest.TestCase):
    def test_plain_render_has_no_flash_style_or_dialogue(self):
        with TemporaryDirectory() as tmp:
            out = generate_ass_file(
                SEGMENTS,
                0.0,
                Path(tmp) / "subs.ass",
                style_name="default",
                flash_events=None,
            )
            text = out.read_text(encoding="utf-8")
        self.assertNotIn("Flash", text)
        self.assertNotIn(f",{FLASH_STYLE_NAME},", text)
        self.assertNotIn("Style: Flash", text.splitlines())

    def test_empty_flash_events_match_plain_render(self):
        with TemporaryDirectory() as tmp:
            random.seed(7)
            plain = generate_ass_file(SEGMENTS, 0.0, Path(tmp) / "a.ass", flash_events=None)
            random.seed(7)
            empty = generate_ass_file(SEGMENTS, 0.0, Path(tmp) / "b.ass", flash_events=[])
            self.assertEqual(plain.read_bytes(), empty.read_bytes())

    def test_default_style_line_unchanged_when_flash_added(self):
        with TemporaryDirectory() as tmp:
            random.seed(7)
            plain = generate_ass_file(SEGMENTS, 0.0, Path(tmp) / "a.ass", flash_events=None)
            random.seed(7)
            flashed = generate_ass_file(
                SEGMENTS, 0.0, Path(tmp) / "b.ass", flash_events=FLASH_EVENTS
            )
            plain_styles = [
                ln for ln in plain.read_text(encoding="utf-8").splitlines() if ln.startswith("Style:")
            ]
            flash_styles = [
                ln for ln in flashed.read_text(encoding="utf-8").splitlines() if ln.startswith("Style:")
            ]
        self.assertEqual(len(flash_styles), len(plain_styles) + 1)
        self.assertIn(flash_styles[0], plain_styles)
        self.assertTrue(flash_styles[-1].startswith("Style: Flash,"))


if __name__ == "__main__":
    unittest.main()
