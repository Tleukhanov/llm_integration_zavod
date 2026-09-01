"""Tests for the caster-emotion clutch selection mode."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from shorts_clipper.attention.emotion import (
    cluster_peaks,
    compute_excitement,
    select_emotion_windows,
    windows_and_peaks_from_audio,
    windows_from_audio_emotion,
)
from shorts_clipper.core.models import ClipWindow
from shorts_clipper.core.settings import Settings


class SelectEmotionWindowsTests(unittest.TestCase):
    def test_merges_consecutive_above_threshold(self):
        emotion = [0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0]
        windows = select_emotion_windows(
            emotion, 1.0, min_length=2.0, max_length=3.0, top_n=2, threshold=0.35,
        )
        self.assertEqual(len(windows), 2)
        starts = sorted(w.start for w in windows)
        self.assertEqual(starts, [2.0, 6.0])
        for w in windows:
            self.assertEqual(w.end - w.start, 3.0)

    def test_drops_clusters_shorter_than_min_length(self):
        emotion = [0.0] * 4 + [1.0, 1.0, 0.0] + [1.0, 1.0, 1.0, 0.0]
        windows = select_emotion_windows(
            emotion, 1.0, min_length=3.0, max_length=3.0, top_n=5, threshold=0.35,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].start, 7.0)
        self.assertEqual(windows[0].end, 10.0)

    def test_expands_to_max_length_within_bounds(self):
        emotion = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        windows = select_emotion_windows(
            emotion, 1.0, min_length=1.0, max_length=4.0, top_n=1, threshold=0.35,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].start, 1.0)
        self.assertEqual(windows[0].end, 5.0)
        self.assertEqual(windows[0].duration, 4.0)

    def test_top_n_returns_highest_emotion_first(self):
        emotion = [1.0, 1.0, 0.0] + [0.0, 0.0, 0.0] + [1.0, 1.0, 1.0, 0.0]
        windows = select_emotion_windows(
            emotion, 1.0, min_length=2.0, max_length=3.0, top_n=5, threshold=0.35,
        )
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].start, 6.0)
        self.assertEqual(windows[1].start, 0.0)

    def test_windows_are_non_overlapping_after_expansion(self):
        emotion = [0.0, 1.0, 0.0, 1.0, 0.0]
        windows = select_emotion_windows(
            emotion, 1.0, min_length=1.0, max_length=3.0, top_n=5, threshold=0.35,
        )
        windows.sort(key=lambda w: w.start)
        for a, b in zip(windows, windows[1:]):
            self.assertLessEqual(a.end, b.start + 1e-6)

    def test_empty_emotion_returns_empty(self):
        self.assertEqual(
            select_emotion_windows([], 1.0, min_length=2.0, max_length=60.0, top_n=3), []
        )

    def test_returns_plain_clip_windows(self):
        emotion = [1.0, 1.0, 1.0]
        windows = select_emotion_windows(
            emotion, 1.0, min_length=2.0, max_length=60.0, top_n=1, threshold=0.35,
        )
        self.assertEqual(windows[0].start, 0.0)
        self.assertEqual(windows[0].end, 3.0)


class WindowsFromAudioEmotionTests(unittest.TestCase):
    def _settings(self):
        return Settings(
            gameplay_scan_max_seconds=3600,
            gameplay_top_windows=5,
            gameplay_min_length=12.0,
            gameplay_max_length=60.0,
        )

    @mock.patch("shorts_clipper.attention.emotion.compute_excitement")
    @mock.patch("shorts_clipper.attention.audio_energy.extract_audio_energy")
    def test_blends_emotion_and_energy(self, mock_energy, mock_emotion):
        # 60 windows each; emotion cluster at 10-25, energy at 40-55
        emo = [0.0] * 10 + [1.0] * 16 + [0.0] * 34
        ene = [0.0] * 40 + [1.0] * 16 + [0.0] * 4
        mock_emotion.return_value = emo
        mock_energy.return_value = ene

        windows = windows_from_audio_emotion(Path("fake.m4a"), 60.0, self._settings())
        self.assertTrue(mock_emotion.called)
        self.assertTrue(mock_energy.called)
        # At least one window returned
        self.assertGreater(len(windows), 0)
        for w in windows:
            self.assertGreaterEqual(w.duration, 12.0)

    @mock.patch("shorts_clipper.attention.emotion.compute_excitement")
    @mock.patch("shorts_clipper.attention.audio_energy.extract_audio_energy")
    def test_empty_when_both_empty(self, mock_energy, mock_emotion):
        mock_emotion.return_value = []
        mock_energy.return_value = []
        windows = windows_from_audio_emotion(Path("fake.m4a"), 60.0, self._settings())
        self.assertEqual(windows, [])

    @mock.patch("shorts_clipper.attention.emotion.compute_excitement")
    @mock.patch("shorts_clipper.attention.audio_energy.extract_audio_energy")
    def test_passes_scan_max_seconds(self, mock_energy, mock_emotion):
        mock_emotion.return_value = [1.0] * 30
        mock_energy.return_value = [0.5] * 30
        windows_from_audio_emotion(Path("fake.m4a"), 60.0, self._settings())
        _, kwargs = mock_emotion.call_args
        self.assertEqual(kwargs["max_seconds"], 3600)

    @mock.patch("shorts_clipper.attention.emotion.compute_excitement")
    @mock.patch("shorts_clipper.attention.audio_energy.extract_audio_energy")
    def test_and_peaks_returns_peaks_inside_windows(self, mock_energy, mock_emotion):
        emo = [0.0] * 10 + [0.8] * 15 + [0.0] * 35
        emo[15] = 1.0
        mock_emotion.return_value = emo
        mock_energy.return_value = [0.0] * 60
        s = Settings(
            gameplay_scan_max_seconds=3600,
            gameplay_top_windows=5,
            gameplay_min_length=12.0,
            gameplay_max_length=15.0,
        )
        pairs = windows_and_peaks_from_audio(Path("fake.m4a"), 60.0, s)
        self.assertEqual(len(pairs), 1)
        window, peak = pairs[0]
        self.assertEqual(window.start, 10.0)
        self.assertEqual(window.end, 25.0)
        self.assertEqual(peak, 15.5)
        # The plain wrapper returns exactly the windows of the pairs.
        self.assertEqual(
            windows_from_audio_emotion(Path("fake.m4a"), 60.0, s),
            [w for w, _ in pairs],
        )


class ClusterPeaksTests(unittest.TestCase):
    def _win(self, start, end):
        return ClipWindow(start=start, end=end)

    def test_picks_argmax_second_inside_window(self):
        signal = [0.1, 0.2, 0.4, 0.9, 0.3, 0.2, 0.1]
        peaks = cluster_peaks(signal, [self._win(0.0, 7.0)], window_seconds=1.0)
        self.assertEqual(peaks, [3.5])

    def test_clamps_out_of_range_windows_to_signal_bounds(self):
        signal = [0.1, 0.5, 0.9, 0.2]
        peaks = cluster_peaks(signal, [self._win(10.0, 12.0)], window_seconds=1.0)
        self.assertEqual(peaks, [3.5])
        peaks = cluster_peaks(signal, [self._win(2.0, 20.0)], window_seconds=1.0)
        self.assertEqual(peaks, [2.5])

    def test_empty_signal_falls_back_to_window_center(self):
        peaks = cluster_peaks([], [self._win(2.0, 6.0)], window_seconds=1.0)
        self.assertEqual(peaks, [4.0])

    def test_peak_uses_window_seconds_step(self):
        signal = [0.1, 0.1, 0.1, 0.1, 0.9, 0.1]
        peaks = cluster_peaks(signal, [self._win(1.0, 4.0)], window_seconds=0.5)
        self.assertEqual(peaks, [2.25])


class ComputeExcitementUnitTests(unittest.TestCase):
    def test_returns_list_of_floats(self):
        # ComputeExcitement needs a real audio file or ffmpeg.  We mock _decode
        # to return a synthetic sine burst signal and verify the pipeline runs.
        sr = 16000
        t = np.arange(sr * 3, dtype=np.float64) / sr  # 3 seconds
        # 1 kHz tone for 1s, then silence for 2s
        tone = np.where(t < 1.0, np.sin(2 * np.pi * 1000 * t), 0.0)
        pcm = (tone * 32767).astype(np.int16)

        with mock.patch(
            "shorts_clipper.attention.emotion._decode_mono_pcm", return_value=pcm
        ):
            result = compute_excitement(Path("dummy.m4a"), window_seconds=1.0)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)
        for v in result:
            self.assertIsInstance(v, float)
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_silent_audio_returns_zeros(self):
        pcm = np.zeros(16000, dtype=np.int16)
        with mock.patch(
            "shorts_clipper.attention.emotion._decode_mono_pcm", return_value=pcm
        ):
            result = compute_excitement(Path("silent.m4a"), window_seconds=1.0)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0], 0.0, places=5)

    def test_decode_failure_returns_empty(self):
        with mock.patch(
            "shorts_clipper.attention.emotion._decode_mono_pcm", return_value=None
        ):
            result = compute_excitement(Path("bad.m4a"))
        self.assertEqual(result, [])


class EmotionClutchSettingsTests(unittest.TestCase):
    def test_default_is_energy(self):
        s = Settings()
        self.assertEqual(s.gameplay_clutch_mode, "energy")

    def test_env_emotion(self):
        os.environ["SHORTS_GAMEPLAY_CLUTCH_MODE"] = "emotion"
        try:
            s = Settings.from_env("_nonexistent.env")
            self.assertEqual(s.gameplay_clutch_mode, "emotion")
        finally:
            os.environ.pop("SHORTS_GAMEPLAY_CLUTCH_MODE", None)

    def test_env_energy_explicit(self):
        os.environ["SHORTS_GAMEPLAY_CLUTCH_MODE"] = "energy"
        try:
            s = Settings.from_env("_nonexistent.env")
            self.assertEqual(s.gameplay_clutch_mode, "energy")
        finally:
            os.environ.pop("SHORTS_GAMEPLAY_CLUTCH_MODE", None)

    def test_invalid_value_falls_back_to_energy(self):
        os.environ["SHORTS_GAMEPLAY_CLUTCH_MODE"] = "bogus"
        try:
            s = Settings.from_env("_nonexistent.env")
            self.assertEqual(s.gameplay_clutch_mode, "energy")
        finally:
            os.environ.pop("SHORTS_GAMEPLAY_CLUTCH_MODE", None)


class RunnerRoutingTests(unittest.TestCase):
    """Verify the runner calls the right windows function based on clutch_mode."""

    def setUp(self):
        from shorts_clipper.core.observability import get_run_context

        get_run_context().reset()
        self._tmp = Path(tempfile.mkdtemp(prefix="emotion_route_"))

    def _base_settings(self, **overrides) -> Settings:
        defaults = dict(
            gameplay_mode=True,
            gameplay_scan_max_seconds=3600,
            gameplay_top_windows=5,
            gameplay_min_length=12.0,
            gameplay_max_length=60.0,
            gameplay_clutch_mode="energy",
            stream_audio_energy_enabled=False,
            output_dir=self._tmp / "outputs",
        )
        defaults.update(overrides)
        return Settings(**defaults)

    def _mocks(self):
        """Return a dict of commonly-mocked runner targets."""
        return {
            "fetch": mock.patch("shorts_clipper.pipeline.runner.fetch_subtitles"),
            "download_audio": mock.patch("shorts_clipper.pipeline.runner.download_audio"),
            "download_clip": mock.patch("shorts_clipper.pipeline.runner.download_clip"),
            "vertical": mock.patch("shorts_clipper.pipeline.runner.process_to_vertical"),
            "transcribe": mock.patch("shorts_clipper.pipeline.runner.transcribe_clip"),
        }

    def _wire_audio_mock(self, dl_mock):
        def _make_audio(url, path, **kwargs):
            Path(path).write_bytes(b"fake-audio")
            return Path(path)

        dl_mock.side_effect = _make_audio

    def _wire_clip_mocks(self, dc_mock, v_mock):
        dc_mock.side_effect = lambda *a, **k: (
            Path(a[1]).write_bytes(b"fake") or Path(a[1])
        )
        v_mock.side_effect = RuntimeError("stop-after-pass1")

    def test_energy_mode_calls_windows_and_peaks(self):
        mocks = self._mocks()
        mocks["fetch"].return_value = []
        self._wire_audio_mock(mocks["download_audio"])
        self._wire_clip_mocks(mocks["download_clip"], mocks["vertical"])
        mocks["transcribe"].return_value = []

        with mocks["fetch"], mocks["download_audio"], mocks["download_clip"], \
             mocks["vertical"], mocks["transcribe"], \
             mock.patch("shorts_clipper.attention.gameplay.windows_and_peaks_from_audio") as mock_wfa, \
             mock.patch("shorts_clipper.attention.emotion.windows_and_peaks_from_audio") as mock_emo:
            mock_wfa.return_value = [(mock.Mock(start=10.0, end=40.0), 20.0)]
            from shorts_clipper.pipeline import runner as r
            from shorts_clipper.core.exceptions import MediaProcessingError
            with self.assertRaises(MediaProcessingError):
                r.run("https://www.youtube.com/watch?v=abc123abc12",
                      settings=self._base_settings(gameplay_clutch_mode="energy"),
                      count=1)
            mock_wfa.assert_called_once()
            mock_emo.assert_not_called()

    def test_emotion_mode_calls_windows_and_peaks(self):
        mocks = self._mocks()
        mocks["fetch"].return_value = []
        self._wire_audio_mock(mocks["download_audio"])
        self._wire_clip_mocks(mocks["download_clip"], mocks["vertical"])
        mocks["transcribe"].return_value = []

        with mocks["fetch"], mocks["download_audio"], mocks["download_clip"], \
             mocks["vertical"], mocks["transcribe"], \
             mock.patch("shorts_clipper.attention.gameplay.windows_and_peaks_from_audio") as mock_wfa, \
             mock.patch("shorts_clipper.attention.emotion.windows_and_peaks_from_audio") as mock_emo:
            mock_emo.return_value = [(mock.Mock(start=10.0, end=40.0), 20.0)]
            from shorts_clipper.pipeline import runner as r
            from shorts_clipper.core.exceptions import MediaProcessingError
            with self.assertRaises(MediaProcessingError):
                r.run("https://www.youtube.com/watch?v=abc123abc12",
                      settings=self._base_settings(gameplay_clutch_mode="emotion"),
                      count=1)
            mock_emo.assert_called_once()
            mock_wfa.assert_not_called()

    def test_emotion_mode_decision_trace_has_clutch_mode(self):
        from shorts_clipper.core.observability import get_run_context

        mocks = self._mocks()
        mocks["fetch"].return_value = []
        self._wire_audio_mock(mocks["download_audio"])
        self._wire_clip_mocks(mocks["download_clip"], mocks["vertical"])
        mocks["transcribe"].return_value = []

        with mocks["fetch"], mocks["download_audio"], mocks["download_clip"], \
             mocks["vertical"], mocks["transcribe"], \
             mock.patch("shorts_clipper.attention.emotion.windows_and_peaks_from_audio") as mock_emo:
            mock_emo.return_value = [(mock.Mock(start=10.0, end=40.0), 20.0)]
            from shorts_clipper.pipeline import runner as r
            from shorts_clipper.core.exceptions import MediaProcessingError
            with self.assertRaises(MediaProcessingError):
                r.run("https://www.youtube.com/watch?v=abc123abc12",
                      settings=self._base_settings(gameplay_clutch_mode="emotion"),
                      count=1)
            trace = get_run_context().decision_trace
            self.assertEqual(trace.get("clutch_mode"), "emotion")
            self.assertEqual(trace.get("mode"), "gameplay")

    def test_energy_mode_decision_trace_has_clutch_mode_energy(self):
        from shorts_clipper.core.observability import get_run_context

        mocks = self._mocks()
        mocks["fetch"].return_value = []
        self._wire_audio_mock(mocks["download_audio"])
        self._wire_clip_mocks(mocks["download_clip"], mocks["vertical"])
        mocks["transcribe"].return_value = []

        with mocks["fetch"], mocks["download_audio"], mocks["download_clip"], \
             mocks["vertical"], mocks["transcribe"], \
             mock.patch("shorts_clipper.attention.gameplay.windows_and_peaks_from_audio") as mock_wfa:
            mock_wfa.return_value = [(mock.Mock(start=10.0, end=40.0), 20.0)]
            from shorts_clipper.pipeline import runner as r
            from shorts_clipper.core.exceptions import MediaProcessingError
            with self.assertRaises(MediaProcessingError):
                r.run("https://www.youtube.com/watch?v=abc123abc12",
                      settings=self._base_settings(gameplay_clutch_mode="energy"),
                      count=1)
            trace = get_run_context().decision_trace
            self.assertEqual(trace.get("clutch_mode"), "energy")
            self.assertEqual(trace.get("mode"), "gameplay")


if __name__ == "__main__":
    unittest.main()
