import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.core.settings import Settings
from shorts_clipper.visual import stock as stock_visual


class StockSlugTests(unittest.TestCase):
    def _cache_slug(self, query, niche="niche-a"):
        """Run ensure_pexels_cache for *query* and return the cache slug."""
        with tempfile.TemporaryDirectory() as d:
            stock_dir = Path(d) / "stock"
            niche_dir = Path(d) / "niches"
            (niche_dir / niche).mkdir(parents=True)
            (niche_dir / niche / "pexels_query.txt").write_text(
                query + "\n", encoding="utf-8"
            )
            stock_visual.ensure_pexels_cache(
                stock_dir, niche, api_key="", niche_dir=niche_dir
            )
            subs = [p for p in (stock_dir / "_pexels_cache").iterdir() if p.is_dir()]
            self.assertEqual(len(subs), 1)
            return subs[0].name

    def test_cyrillic_slug_nonempty_deterministic_and_unique(self):
        """Cyrillic queries fall back to distinct deterministic slugs."""
        first = "утренний лес туман"
        second = "ночной город огни"
        slug_first = self._cache_slug(first, niche="ru-one")
        slug_second = self._cache_slug(second, niche="ru-two")
        self.assertTrue(slug_first)
        self.assertTrue(slug_second)
        self.assertNotEqual(slug_first, slug_second)
        self.assertEqual(self._cache_slug(first, niche="ru-one"), slug_first)
        self.assertEqual(
            slug_first, "q-" + hashlib.sha1(first.encode("utf-8")).hexdigest()[:12]
        )
        self.assertEqual(
            slug_second, "q-" + hashlib.sha1(second.encode("utf-8")).hexdigest()[:12]
        )

    def test_latin_slug_unchanged(self):
        """Latin queries keep the exact historical slug."""
        self.assertEqual(
            self._cache_slug("morning sunrise forest calm"), "morning-sunrise-forest-calm"
        )


class StockBpmTests(unittest.TestCase):
    def _settings_with(self, env_vars):
        """Build Settings with an isolated environment."""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".env", delete=False, encoding="utf-8"
        ) as f:
            f.write("")
            path = f.name
        try:
            with mock.patch.dict(os.environ, env_vars, clear=True):
                return Settings.from_env(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_bpm_valid_values_pass_through(self):
        """Valid BPM values are kept exactly."""
        for raw, expected in (("132", 132.0), ("140", 140.0), ("40", 40.0), ("300", 300.0)):
            with self.subTest(raw=raw):
                settings = self._settings_with({"SHORTS_STOCK_EDIT_BPM": raw})
                self.assertEqual(settings.stock_edit_bpm, expected)

    def test_bpm_invalid_falls_back_into_range(self):
        """Garbage and out-of-range BPM values stay inside 40..300."""
        settings = self._settings_with({"SHORTS_STOCK_EDIT_BPM": "fast"})
        self.assertEqual(settings.stock_edit_bpm, 132.0)
        for raw in ("-5", "0", "9999", ""):
            with self.subTest(raw=raw):
                value = self._settings_with(
                    {"SHORTS_STOCK_EDIT_BPM": raw}
                ).stock_edit_bpm
                self.assertGreaterEqual(value, 40.0)
                self.assertLessEqual(value, 300.0)


class StockEditFlagTests(unittest.TestCase):
    def _edit_flag(self, raw):
        """Parse SHORTS_STOCK_EDIT for a single raw value."""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".env", delete=False, encoding="utf-8"
        ) as f:
            f.write("")
            path = f.name
        try:
            with mock.patch.dict(os.environ, {"SHORTS_STOCK_EDIT": raw}, clear=True):
                return Settings.from_env(path).stock_edit
        finally:
            Path(path).unlink(missing_ok=True)

    def test_edit_flag_parsing_table(self):
        """True/false variants map to the expected boolean."""
        for raw in ("1", "true", "TRUE", "True", "yes", "YES", "on", "ON", "  on  "):
            with self.subTest(raw=raw):
                self.assertTrue(self._edit_flag(raw))
        for raw in (
            "0",
            "false",
            "FALSE",
            "False",
            "no",
            "NO",
            "off",
            "OFF",
            "",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(self._edit_flag(raw))


class StockFillCutTests(unittest.TestCase):
    def _assert_beat_exact(self, cuts, duration, bpm):
        """Assert cuts are beat multiples, increasing and inside duration."""
        beat = stock_visual.beat_seconds(bpm)
        self.assertTrue(cuts)
        self.assertTrue(all(0.0 < cut < duration for cut in cuts))
        self.assertEqual(cuts, sorted(cuts))
        self.assertEqual(len(set(cuts)), len(cuts))
        for cut in cuts:
            nearest = round(cut / beat) * beat
            self.assertAlmostEqual(cut, nearest, delta=1e-3)

    def test_fill_cuts_are_beat_quantized(self):
        """Short grids filled to needed stay beat-exact and ordered."""
        duration = 30.0
        bpm = 132
        grid = stock_visual._edit_cut_grid(duration, bpm, min_cut=duration / 6, seed=0)
        short = grid[:1]
        self.assertLess(len(short), 5)
        filled = stock_visual._fill_cut_tail(short, 5, duration, bpm, seed=0)
        self.assertEqual(len(filled), 5)
        self.assertEqual(filled[: len(short)], short)
        self._assert_beat_exact(filled, duration, bpm)
        self.assertEqual(
            filled, stock_visual._fill_cut_tail(short, 5, duration, bpm, seed=0)
        )

    def test_fill_cuts_from_empty_grid(self):
        """Filling from no cuts still lands on whole beats."""
        duration = 30.0
        bpm = 90
        filled = stock_visual._fill_cut_tail([], 3, duration, bpm, seed=1)
        self.assertEqual(len(filled), 3)
        self._assert_beat_exact(filled, duration, bpm)


if __name__ == "__main__":
    unittest.main()
