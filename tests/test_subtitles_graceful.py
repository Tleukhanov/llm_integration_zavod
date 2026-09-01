import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.core.exceptions import (
    SUBTITLE_NOT_AVAILABLE,
    YOUTUBE_RATE_LIMIT_429,
    MediaProcessingError,
)
from shorts_clipper.core.models import ClipWindow
from shorts_clipper.core.observability import get_run_context
from shorts_clipper.core.settings import Settings
from shorts_clipper.pipeline import runner


class SubtitleGracefulTests(unittest.TestCase):
    def setUp(self):
        get_run_context().reset()
        self._tmp = Path(tempfile.mkdtemp(prefix="subs_graceful_"))

    def _settings(self) -> Settings:
        return Settings(
            gameplay_mode=True,
            gameplay_scan_max_seconds=3600,
            gameplay_top_windows=5,
            gameplay_min_length=12.0,
            gameplay_max_length=60.0,
            stream_audio_energy_enabled=False,
            output_dir=self._tmp / "outputs",
        )

    @mock.patch("shorts_clipper.pipeline.runner.fetch_subtitles")
    @mock.patch("shorts_clipper.pipeline.runner.transcribe_clip")
    @mock.patch("shorts_clipper.pipeline.runner.download_audio")
    @mock.patch("shorts_clipper.attention.gameplay.windows_from_audio")
    @mock.patch("shorts_clipper.pipeline.runner.download_clip")
    @mock.patch("shorts_clipper.pipeline.runner.process_to_vertical")
    def test_missing_subtitles_routes_to_gameplay_not_5min_fallback(
        self,
        mock_vertical,
        mock_download_clip,
        mock_windows,
        mock_download_audio,
        mock_transcribe,
        mock_fetch,
    ):
        # Subtitles completely missing -> must NOT abort the run.
        mock_fetch.side_effect = SUBTITLE_NOT_AVAILABLE("no subs")

        def _make_audio(url, path, **kwargs):
            Path(path).write_bytes(b"fake-audio")
            return Path(path)

        mock_download_audio.side_effect = _make_audio
        mock_windows.return_value = [ClipWindow(start=10.0, end=40.0)]
        mock_transcribe.return_value = []  # must NOT be used in gameplay mode
        mock_download_clip.side_effect = lambda *a, **k: (
            Path(a[1]).write_bytes(b"fake") or Path(a[1])
        )
        # Stop after PASS 1 has routed correctly; reaching the render step means
        # the run got past subtitle handling.
        mock_vertical.side_effect = RuntimeError("stop-after-pass1")

        settings = self._settings()

        with self.assertRaises(MediaProcessingError):
            runner.run(
                "https://www.youtube.com/watch?v=abc123abc12",
                settings=settings,
                count=1,
            )

        # Missed subtitles still reached gameplay mode, not the 5-min fallback.
        mock_windows.assert_called_once()
        # Audio downloaded with the gameplay scan cap (not 5 min / 300s).
        _, kwargs = mock_download_audio.call_args
        self.assertEqual(kwargs["end_time"], 3600)
        self.assertEqual(kwargs["start_time"], 0.0)

        decision = get_run_context().decision_trace
        self.assertEqual(decision.get("mode"), "gameplay")

    @mock.patch("shorts_clipper.pipeline.runner.fetch_subtitles")
    @mock.patch("shorts_clipper.pipeline.runner.transcribe_clip")
    @mock.patch("shorts_clipper.pipeline.runner.download_audio")
    @mock.patch("shorts_clipper.attention.gameplay.windows_from_audio")
    @mock.patch("shorts_clipper.pipeline.runner.download_clip")
    @mock.patch("shorts_clipper.pipeline.runner.process_to_vertical")
    def test_gameplay_mode_bypasses_semantic_generation(
        self,
        mock_vertical,
        mock_download_clip,
        mock_windows,
        mock_download_audio,
        mock_transcribe,
        mock_fetch,
    ):
        # Even when subtitles ARE present, gameplay_mode always uses energy windows.
        mock_fetch.return_value = [mock.Mock(start=0.0, end=45.0)]

        def _make_audio(url, path, **kwargs):
            Path(path).write_bytes(b"fake-audio")
            return Path(path)

        mock_download_audio.side_effect = _make_audio
        mock_windows.return_value = [ClipWindow(start=5.0, end=35.0)]
        mock_transcribe.return_value = []
        mock_download_clip.side_effect = lambda *a, **k: (
            Path(a[1]).write_bytes(b"fake") or Path(a[1])
        )
        mock_vertical.side_effect = RuntimeError("stop-after-pass1")

        settings = self._settings()

        with self.assertRaises(MediaProcessingError):
            runner.run(
                "https://www.youtube.com/watch?v=abc123abc12",
                settings=settings,
                count=1,
            )

        # Semantic candidate generation (downstream of subs) was bypassed.
        mock_windows.assert_called_once()
        self.assertEqual(get_run_context().decision_trace.get("mode"), "gameplay")

    @mock.patch("shorts_clipper.pipeline.runner.fetch_subtitles")
    @mock.patch("shorts_clipper.pipeline.runner.transcribe_clip")
    @mock.patch("shorts_clipper.pipeline.runner.download_audio")
    @mock.patch("shorts_clipper.attention.gameplay.windows_from_audio")
    @mock.patch("shorts_clipper.pipeline.runner.download_clip")
    @mock.patch("shorts_clipper.pipeline.runner.process_to_vertical")
    def test_subtitle_429_in_gameplay_mode_degrades_gracefully(
        self,
        mock_vertical,
        mock_download_clip,
        mock_windows,
        mock_download_audio,
        mock_transcribe,
        mock_fetch,
    ):
        # A YouTube 429 is an IP-level block on the subtitle endpoint; in gameplay
        # mode subtitles aren't used for selection, so it must not abort the run.
        mock_fetch.side_effect = YOUTUBE_RATE_LIMIT_429("Rate limited by YouTube")

        def _make_audio(url, path, **kwargs):
            Path(path).write_bytes(b"fake-audio")
            return Path(path)

        mock_download_audio.side_effect = _make_audio
        mock_windows.return_value = [ClipWindow(start=10.0, end=40.0)]
        mock_transcribe.return_value = []  # must NOT be used in gameplay mode
        mock_download_clip.side_effect = lambda *a, **k: (
            Path(a[1]).write_bytes(b"fake") or Path(a[1])
        )
        mock_vertical.side_effect = RuntimeError("stop-after-pass1")

        settings = self._settings()

        with self.assertRaises(MediaProcessingError):
            runner.run(
                "https://www.youtube.com/watch?v=abc123abc12",
                settings=settings,
                count=1,
            )

        # 429 on subtitles still reached gameplay mode (audio-energy selection).
        mock_windows.assert_called_once()
        _, kwargs = mock_download_audio.call_args
        self.assertEqual(kwargs["end_time"], 3600)
        self.assertEqual(kwargs["start_time"], 0.0)
        self.assertEqual(get_run_context().decision_trace.get("mode"), "gameplay")


if __name__ == "__main__":
    unittest.main()
