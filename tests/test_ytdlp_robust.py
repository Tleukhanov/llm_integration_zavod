"""Robustness tests for the yt-dlp layer: video-id parsing, disk cache,
player-client fallback on HTTP 403/429, subtitle + stats caching."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, r"D:\Projects\shorts-clipper")

from shorts_clipper.downloader import yt_dlp

VID = "dQw4w9WgXcQ"
VID_URL = f"https://www.youtube.com/watch?v={VID}"


def _patch_env(cache_dir: Path, *extra_vars):
    vars_to_set = {"SHORTS_CACHE_DIR": str(cache_dir)}
    for k, v in extra_vars:
        vars_to_set[k] = v
    return mock.patch.dict("os.environ", vars_to_set, clear=False)


class TmpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class VideoIdTests(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(yt_dlp.video_id_from_url(VID_URL), VID)

    def test_youtu_be(self):
        self.assertEqual(yt_dlp.video_id_from_url(f"https://youtu.be/{VID}"), VID)

    def test_shorts(self):
        self.assertEqual(yt_dlp.video_id_from_url(f"https://www.youtube.com/shorts/{VID}"), VID)

    def test_embed(self):
        self.assertEqual(
            yt_dlp.video_id_from_url(f"https://www.youtube.com/embed/{VID}"), VID
        )

    def test_no_id(self):
        self.assertIsNone(yt_dlp.video_id_from_url("https://www.youtube.com/"))
        self.assertIsNone(yt_dlp.video_id_from_url(None))
        self.assertIsNone(yt_dlp.video_id_from_url(""))
        self.assertIsNone(yt_dlp.video_id_from_url(f"https://youtu.be/{VID[:8]}"))


class DownloadAudioCacheTests(TmpTestCase):
    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    @mock.patch.object(yt_dlp, "_impersonate_flag", return_value=["--impersonate", "Chrome"])
    def test_cache_hit_skips_network(self, mock_imp, mock_run):
        cache_dir = self.tmp_path / "cache"
        out = self.tmp_path / "test_audio.m4a"
        with _patch_env(cache_dir):
            # Pre-seed a complete cached artifact
            cached = yt_dlp.dl_cache.media_cache_path(VID, "audio", None, None, ".m4a")
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(b"CACHED_AUDIO")
            (yt_dlp.dl_cache._sidecar_path(cached)).write_text(
                json.dumps({"complete": True}), encoding="utf-8"
            )

            result = yt_dlp.download_audio(VID_URL, out)

            mock_run.assert_not_called()
            self.assertEqual(result, out)
            self.assertEqual(out.read_bytes(), b"CACHED_AUDIO")

    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_cache_miss_stores_artifact(self, mock_run):
        cache_dir = self.tmp_path / "cache"
        out = self.tmp_path / "test_audio.m4a"

        def fake_run(cmd, **kwargs):
            out.write_bytes(b"FRESH_AUDIO")
            comp = subprocess.CompletedProcess(cmd, 0)
            comp.stderr = b""
            return comp

        mock_run.side_effect = fake_run
        with _patch_env(cache_dir):
            yt_dlp.download_audio(VID_URL, out, start_time=0.0, end_time=300.0)

            self.assertEqual(mock_run.call_count, 1)
            cached = yt_dlp.dl_cache.media_cache_path(VID, "audio", 0.0, 300.0, ".m4a")
            self.assertTrue(cached.exists())
            self.assertEqual(cached.read_bytes(), b"FRESH_AUDIO")
            sidecar = yt_dlp.dl_cache._sidecar_path(cached)
            self.assertTrue(json.loads(sidecar.read_text(encoding="utf-8"))["complete"])

    @mock.patch("shorts_clipper.downloader.yt_dlp.time.sleep")
    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_fallback_switches_client_on_403(self, mock_run, mock_sleep):
        cache_dir = self.tmp_path / "cache"
        out = self.tmp_path / "test_audio.m4a"
        err_403 = subprocess.CalledProcessError(
            1, ["cmd"], stderr=b"ERROR: Unable to download webpage: HTTP Error 403"
        )

        def fake_run(cmd, **kwargs):
            calls = mock_run.call_count
            if calls == 1:
                # First variant must be the default client
                self.assertIn("player_client=default", " ".join(cmd))
                raise err_403
            argstr = " ".join(cmd)
            self.assertIn("player_client=tv", argstr)
            self.assertIn("--download-sections", argstr)
            self.assertIn("*10.0-120.0", argstr)
            out.write_bytes(b"RETRIED_AUDIO")
            comp = subprocess.CompletedProcess(cmd, 0, b"", b"")
            comp.stderr = b""
            return comp

        mock_run.side_effect = fake_run

        with _patch_env(cache_dir):
            yt_dlp.download_audio(VID_URL, out, start_time=10.0, end_time=120.0)
            self.assertEqual(mock_run.call_count, 2)
            self.assertEqual(out.read_bytes(), b"RETRIED_AUDIO")


class DownloadClipTests(TmpTestCase):
    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_cache_hit_used(self, mock_run):
        cache_dir = self.tmp_path / "cache"
        out = self.tmp_path / "clip.mp4"
        with _patch_env(cache_dir):
            cached = yt_dlp.dl_cache.media_cache_path(VID, "video", 10.0, 60.0, ".mp4")
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(b"CACHED_CLIP")
            (yt_dlp.dl_cache._sidecar_path(cached)).write_text(
                json.dumps({"complete": True}), encoding="utf-8"
            )
            yt_dlp.download_clip(VID_URL, out, start_time=10.0, end_time=60.0)
            mock_run.assert_not_called()
            self.assertEqual(out.read_bytes(), b"CACHED_CLIP")


class SubtitleCacheTests(TmpTestCase):
    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    @mock.patch.object(yt_dlp, "_subtitle_langs", return_value=["ru", "en"])
    def test_cache_hit_skips_network(self, mock_langs, mock_run):
        from shorts_clipper.core.models import TranscriptSegment

        cache_dir = self.tmp_path / "cache"
        work = self.tmp_path / "work"
        work.mkdir()
        with _patch_env(cache_dir):
            yt_dlp.dl_cache.store_subtitles(
                VID,
                ["ru", "en"],
                [TranscriptSegment(start=1.0, end=2.0, text="hello")],
            )
            segments = yt_dlp.fetch_subtitles(VID_URL, work)
            mock_run.assert_not_called()
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0].text, "hello")
            self.assertEqual(segments[0].start, 1.0)


class StatsCacheTests(TmpTestCase):
    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_stats_cache_hit(self, mock_run):
        cache_dir = self.tmp_path / "cache"
        with _patch_env(cache_dir):
            from shorts_clipper.core.cache import set_cached

            set_cached(VID, {"views": 5, "likes": 2, "comments": 1})
            stats = yt_dlp.fetch_video_stats(VID_URL)
            mock_run.assert_not_called()
            self.assertEqual(stats, {"views": 5, "likes": 2, "comments": 1})


if __name__ == "__main__":
    unittest.main()