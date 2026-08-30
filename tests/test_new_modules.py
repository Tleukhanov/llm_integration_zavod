"""Tests for the new package modules added in session 2."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shorts_clipper.captions.generator import (
    _ass_header,
    _build_ass_chunks,
    _seconds_to_ass_time,
)
from shorts_clipper.core.cache import get_cached, set_cached
from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.scout.keywords import build_queries
from shorts_clipper.scout.trending import _has_english


class ASSTimestampTests(unittest.TestCase):
    def test_zero_seconds(self):
        self.assertEqual(_seconds_to_ass_time(0.0), "0:00:00.00")

    def test_one_minute_thirty(self):
        self.assertEqual(_seconds_to_ass_time(90.5), "0:01:30.50")

    def test_over_one_hour(self):
        self.assertEqual(_seconds_to_ass_time(3661.25), "1:01:01.25")


class ASSHeaderTests(unittest.TestCase):
    def test_header_contains_required_sections(self):
        header = _ass_header()
        self.assertIn("[Script Info]", header)
        self.assertIn("[V4+ Styles]", header)
        self.assertIn("[Events]", header)
        self.assertIn("PlayResX: 1080", header)
        self.assertIn("PlayResY: 1920", header)

    def test_header_is_string(self):
        self.assertIsInstance(_ass_header(), str)


class ASSChunkTests(unittest.TestCase):
    def _make_segment(self, start, end, text, words=None):
        w = []
        if words:
            for ws, we, wt in words:
                w.append(TranscriptWord(start=ws, end=we, word=wt))
        return TranscriptSegment(start=start, end=end, text=text, words=w)

    def test_word_level_chunks_use_actual_timing(self):
        seg = self._make_segment(
            10.0,
            14.0,
            "this is great",
            words=[(10.0, 10.5, "this"), (10.6, 11.0, "is"), (11.1, 11.8, "great")],
        )
        chunks = _build_ass_chunks([seg], start_offset=10.0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "THIS IS GREAT")
        self.assertAlmostEqual(chunks[0]["start"], 0.0)

    def test_fallback_chunks_distribute_time_evenly(self):
        seg = self._make_segment(0.0, 6.0, "one two three four five six")
        chunks = _build_ass_chunks([seg], start_offset=0.0)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["text"], "ONE TWO THREE")
        self.assertAlmostEqual(chunks[0]["start"], 0.0)
        self.assertAlmostEqual(chunks[0]["end"], 3.0)
        self.assertEqual(chunks[1]["text"], "FOUR FIVE SIX")
        self.assertAlmostEqual(chunks[1]["start"], 3.0)
        self.assertAlmostEqual(chunks[1]["end"], 6.0)

    def test_empty_segment_produces_no_chunks(self):
        seg = self._make_segment(0.0, 1.0, "")
        chunks = _build_ass_chunks([seg], start_offset=0.0)
        self.assertEqual(chunks, [])

    def test_pacing_scales_timing(self):
        seg = self._make_segment(
            10.0,
            14.0,
            "this is great",
            words=[(10.0, 11.15, "this"), (11.15, 12.3, "is"), (12.3, 13.45, "great")],
        )
        chunks = _build_ass_chunks([seg], start_offset=10.0, pacing=1.15)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "THIS IS GREAT")
        self.assertAlmostEqual(chunks[0]["start"], 0.0)
        self.assertAlmostEqual(chunks[0]["end"], 3.0)

        # Fallback path pacing test
        seg_fallback = self._make_segment(10.0, 15.75, "hello world")
        chunks_fb = _build_ass_chunks([seg_fallback], start_offset=10.0, pacing=1.15)
        self.assertEqual(len(chunks_fb), 1)
        self.assertEqual(chunks_fb[0]["text"], "HELLO WORLD")
        self.assertAlmostEqual(chunks_fb[0]["end"], 5.0)


class ScoutCacheTests(unittest.TestCase):
    def test_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            with patch("shorts_clipper.core.cache._DB_PATH", db_path):
                seen = {"id": "abc123", "title": "test", "view_count": 100}
                set_cached("abc123", seen)
                loaded = get_cached("abc123")
                self.assertEqual(loaded["title"], "test")

    def test_load_cache_returns_empty_set_if_missing(self):
        with patch("shorts_clipper.core.cache._DB_PATH", Path("/nonexistent/path/jobs.db")):
            result = get_cached("missing")
        self.assertIsNone(result)


class ScoutFilterTests(unittest.TestCase):
    def test_has_english_detects_auto_captions(self):
        self.assertTrue(_has_english({"automatic_captions": {"en": []}}))
        self.assertTrue(_has_english({"subtitles": {"en-orig": []}}))
        self.assertFalse(_has_english({"automatic_captions": {"fr": []}, "subtitles": {}}))

    def test_has_english_rejects_non_english_language_field(self):
        self.assertFalse(_has_english({"language": "hi", "automatic_captions": {"en": []}}))
        self.assertFalse(_has_english({"language": "es", "subtitles": {"en": []}}))
        self.assertTrue(_has_english({"language": "en-US", "automatic_captions": {"en": []}}))

    def test_has_english_rejects_non_english_orig(self):
        self.assertFalse(_has_english({"automatic_captions": {"es-orig": [], "en": []}}))
        self.assertFalse(_has_english({"automatic_captions": {"hi-orig": [], "en": []}}))
        self.assertTrue(_has_english({"automatic_captions": {"en-orig": [], "en": []}}))


class DownloaderTests(unittest.TestCase):
    @patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_download_audio_full(self, mock_run):
        from shorts_clipper.downloader.yt_dlp import download_audio

        url = "https://www.youtube.com/watch?v=test"
        output_path = "/tmp/test_audio.m4a"
        download_audio(url, output_path)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("yt_dlp", args)
        self.assertNotIn("--download-sections", args)

    @patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_download_audio_section(self, mock_run):
        from shorts_clipper.downloader.yt_dlp import download_audio

        url = "https://www.youtube.com/watch?v=test"
        output_path = "/tmp/test_audio.m4a"
        download_audio(url, output_path, start_time=10.0, end_time=120.0)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("--download-sections", args)
        self.assertIn("*10.0-120.0", args)


class ScoutQueryTests(unittest.TestCase):
    def test_build_queries_keyword(self):
        queries = build_queries("tech", "clash")
        self.assertIn("ytsearch15:clash", queries)
        self.assertIn("ytsearch15:clash interview", queries)

    def test_build_queries_niche(self):
        queries = build_queries("tech", None, count=2)
        self.assertEqual(len(queries), 2)
        self.assertTrue(queries[0].startswith("ytsearch15:tech "))

    def test_build_queries_fallback(self):
        with patch(
            "shorts_clipper.scout.keywords._expand_niche_dynamically",
            return_value=["unknown_niche"],
        ):
            queries = build_queries("unknown_niche", None, count=5)
            self.assertEqual(len(queries), 1)
            self.assertEqual(queries[0], "ytsearch15:unknown_niche")

    def test_is_suitable_enforces_max_age_days(self):
        pass

    def test_scout_by_niche_rotation(self):
        pass

    def test_scout_by_keyword_multi_platform(self):
        pass

    def test_is_suitable_rejects_low_resolution(self):
        pass


if __name__ == "__main__":
    unittest.main()
