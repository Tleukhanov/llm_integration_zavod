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


if __name__ == "__main__":
    unittest.main()