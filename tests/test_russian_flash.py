import re
import unicodedata
import unittest

from shorts_clipper.visual import stock as stock_visual


class RussianFlashTests(unittest.TestCase):
    def test_flash_phrases_are_cyrillic(self):
        flashes = stock_visual.edit_flash_schedule(9.0)
        self.assertTrue(flashes)
        for flash in flashes:
            letters = [ch for ch in flash["text"] if ch.isalpha()]
            self.assertTrue(letters)
            for ch in letters:
                name = unicodedata.name(ch, "")
                self.assertIn("CYRILLIC", name)
                self.assertNotIn("LATIN", name)
            self.assertNotRegex(flash["text"], re.compile(r"[A-Za-z]"))

    def test_flash_phrase_count_unchanged(self):
        self.assertEqual(len(stock_visual._EDIT_FLASH_PHRASES), 4)

    def test_flash_schedule_timing_unchanged(self):
        flashes = stock_visual.edit_flash_schedule(9.0)
        bar = 4 * stock_visual.beat_seconds(132)
        self.assertTrue(flashes)
        for flash in flashes:
            self.assertAlmostEqual(
                flash["start"], round(flash["start"] / bar) * bar, places=3
            )
            self.assertLess(flash["start"], flash["end"])
            self.assertLessEqual(flash["end"], 9.0)

    def test_flash_schedule_deterministic(self):
        self.assertEqual(
            stock_visual.edit_flash_schedule(9.0, seed=2),
            stock_visual.edit_flash_schedule(9.0, seed=2),
        )


if __name__ == "__main__":
    unittest.main()
