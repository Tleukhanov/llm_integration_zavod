import json
import unittest
from unittest import mock

from shorts_clipper.downloader.yt_dlp import search_cc_videos


class SearchCCVideosTests(unittest.TestCase):
    def _run_result(self, stdout="", stderr="", returncode=0):
        proc = mock.Mock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_parses_json_lines(self, fake_run):
        item = {
            "id": "abc123",
            "title": "CS2 Clone Gameplay",
            "upload_date": "20260101",
            "channel": "SomeChannel",
        }
        proc = self._run_result(stdout=json.dumps(item) + "\n" + json.dumps(item))
        fake_run.return_value = proc
        results = search_cc_videos("CS2 gameplay", max_results=5)
        self.assertEqual(len(results), 2)  # one result per JSON line
        self.assertEqual(results[0]["id"], "abc123")
        self.assertEqual(results[0]["url"], "https://www.youtube.com/watch?v=abc123")
        # verify match-filters used for CC
        cmd = fake_run.call_args.args[0]
        self.assertIn("--match-filters", cmd)
        fi = cmd.index("--match-filters")
        self.assertIn("Creative Commons", cmd[fi + 1])

    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_empty_on_timeout(self, fake_run):
        fake_run.side_effect = TimeoutExpired("cmd", 5)
        self.assertEqual(search_cc_videos("x", max_results=3), [])

    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_empty_on_nonzero_returncode(self, fake_run):
        fake_run.return_value = self._run_result(stderr="403", returncode=1)
        self.assertEqual(search_cc_videos("x", max_results=3), [])

    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_ignores_non_json_lines(self, fake_run):
        proc = self._run_result(stdout="some yt-dlp progress\n{\"id\":\"v2\",\"title\":\"T\"}\n")
        fake_run.return_value = proc
        results = search_cc_videos("x", max_results=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "v2")

    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_channel_url_uses_it(self, fake_run):
        proc = self._run_result(stdout="")
        fake_run.return_value = proc
        search_cc_videos("x", channel_url="https://www.youtube.com/@C/videos", max_results=2)
        cmd = fake_run.call_args.args[0]
        self.assertIn("https://www.youtube.com/@C/videos", cmd)

    @mock.patch("shorts_clipper.downloader.yt_dlp.subprocess.run")
    def test_dateafter_passed(self, fake_run):
        proc = self._run_result(stdout="")
        fake_run.return_value = proc
        search_cc_videos("x", dateafter="20250101", max_results=2)
        cmd = fake_run.call_args.args[0]
        self.assertIn("--dateafter", cmd)
        self.assertIn("20250101", cmd)


from subprocess import TimeoutExpired

if __name__ == "__main__":
    unittest.main()
