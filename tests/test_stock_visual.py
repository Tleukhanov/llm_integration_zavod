import unittest
from pathlib import Path

from shorts_clipper.visual import stock as stock_visual


class StockVisualTests(unittest.TestCase):
    def test_load_stock_script_uses_file_lines(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# comment\nПервая строка\nВторая строка\n")
            path = f.name
        try:
            text = stock_visual.load_stock_script(Path(path), seed=0)
            self.assertIn(text, ["Первая строка", "Вторая строка"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_stock_script_builtin_fallback(self):
        text = stock_visual.load_stock_script(None, seed=42)
        self.assertIsNotNone(text)
        self.assertTrue(text.strip())

    def test_find_stock_background_none_when_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            result = stock_visual.find_stock_background(Path(d), "self-growth", seed=1)
            self.assertIsNone(result)

    def test_find_stock_background_prefers_niche_subfolder(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            stock_dir = Path(d)
            niche_dir = stock_dir / "philosophy"
            niche_dir.mkdir(parents=True)
            (stock_dir / "gen.mp4").write_bytes(b"x")
            (niche_dir / "niche.mp4").write_bytes(b"x")
            result = stock_visual.find_stock_background(stock_dir, "philosophy", seed=0)
            self.assertEqual(result, niche_dir / "niche.mp4")

    def test_build_word_segments_covers_duration(self):
        segments = stock_visual.build_word_segments(
            "Стоицизм учит принимать то, что не зависит от нас", duration=9.0, max_words=3
        )
        self.assertTrue(segments)
        self.assertAlmostEqual(segments[-1].end, 9.0, delta=0.2)
        words = [w for s in segments for w in s.words]
        texts = " ".join(w.word for w in words)
        self.assertIn("Стоицизм", texts)
        self.assertIn("нас", texts)

    def test_build_word_segments_empty(self):
        self.assertEqual(stock_visual.build_word_segments("", duration=5.0), [])
        self.assertEqual(stock_visual.build_word_segments("x", duration=0.0), [])

    def test_procedural_render_uses_ffmpeg(self):
        # Only verify command plumbing does not raise at import time.
        from shorts_clipper.utils.ffmpeg_path import ffmpeg_path

        self.assertTrue(ffmpeg_path(), "ffmpeg should be resolvable")

    def test_render_functions_exist(self):
        self.assertTrue(callable(stock_visual.render_stock_background))
        self.assertTrue(callable(stock_visual.render_procedural_background))

    def test_default_scripts_are_single_language(self):
        import unicodedata

        for script in stock_visual._DEFAULT_SCRIPTS:
            cyr = sum(1 for ch in script if "CYRILLIC" in unicodedata.name(ch, ""))
            lat = sum(1 for ch in script if "LATIN" in unicodedata.name(ch, ""))
            self.assertFalse(
                cyr and lat,
                f"script mixes languages: {script!r}",
            )

    def test_fetch_pexels_returns_cached_without_api_key(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            stock_dir = Path(d)
            cache = stock_dir / "_pexels_cache" / "morning-sunrise-forest-calm"
            cache.mkdir(parents=True)
            (cache / "00.mp4").write_bytes(b"x" * 200_000)
            result = stock_visual.fetch_pexels_background(stock_dir, "self-growth", seed=5)
            self.assertEqual(result, cache / "00.mp4")

    def test_fetch_pexels_custom_query_slug(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            stock_dir = Path(d)
            cache = stock_dir / "_pexels_cache" / "minimal-ocean-horizon-dawn"
            cache.mkdir(parents=True)
            (cache / "00.mp4").write_bytes(b"x" * 200_000)
            result = stock_visual.fetch_pexels_background(stock_dir, "philosophy", seed=5)
            self.assertEqual(result, cache / "00.mp4")

    def test_find_stock_background_prefers_local_over_pexels(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            stock_dir = Path(d)
            niche_dir = stock_dir / "self-growth"
            niche_dir.mkdir(parents=True)
            (niche_dir / "local.mp4").write_bytes(b"x")
            result = stock_visual.find_stock_background(
                stock_dir, "self-growth", seed=0, pexels_api_key="fake-key"
            )
            self.assertEqual(result, niche_dir / "local.mp4")

    def test_list_stock_backgrounds_local_pool(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            stock_dir = Path(d)
            niche_dir = stock_dir / "self-growth"
            niche_dir.mkdir(parents=True)
            for name in ("a.mp4", "b.mp4", "c.mp4"):
                (niche_dir / name).write_bytes(b"x")
            pool = stock_visual.list_stock_backgrounds(stock_dir, "self-growth", seed=0, limit=4)
            self.assertEqual(len(pool), 3)
            self.assertTrue(all(p.parent == niche_dir for p in pool))

    def test_list_stock_backgrounds_deterministic(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            stock_dir = Path(d)
            for name in ("a.mp4", "b.mp4", "c.mp4", "d.mp4"):
                (stock_dir / name).write_bytes(b"x")
            pool1 = stock_visual.list_stock_backgrounds(stock_dir, None, seed=7, limit=4)
            pool2 = stock_visual.list_stock_backgrounds(stock_dir, None, seed=7, limit=4)
            self.assertEqual(pool1, pool2)

    def test_montage_plan_total_matches_duration(self):
        plan = stock_visual._montage_plan(4, 9.0, seed=11)
        self.assertEqual(len(plan["transitions"]), 3)
        self.assertAlmostEqual(plan["total"], 9.0, delta=0.1)
        offsets = [o for o, _ in plan["transitions"]]
        self.assertEqual(offsets, sorted(offsets))
        self.assertTrue(all(o > 0 for o in offsets))

    def test_montage_plan_only_soft_transitions(self):
        for seed in range(20):
            plan = stock_visual._montage_plan(4, 9.0, seed=seed)
            for _, kind in plan["transitions"]:
                self.assertIn(kind, {"fade", "dissolve", "smoothleft", "smoothup"})

    def test_word_segment_shift_applies_to_all_words(self):
        from dataclasses import replace

        segments = stock_visual.build_word_segments("Ты не то что случилось", duration=3.0)
        self.assertGreater(len(segments), 0)
        shift = 0.5
        shifted = []
        for seg in segments:
            new_seg = replace(seg, start=seg.start + shift, end=seg.end + shift)
            new_seg = replace(
                new_seg,
                words=[
                    replace(w, start=w.start + shift, end=w.end + shift)
                    for w in (seg.words or [])
                ],
            )
            shifted.append(new_seg)
        self.assertGreaterEqual(shifted[0].start, shift - 1e-6)
        for seg in shifted:
            for w in seg.words or []:
                self.assertGreaterEqual(w.start, shift - 1e-6)
                self.assertGreaterEqual(w.end, w.start)


if __name__ == "__main__":
    unittest.main()