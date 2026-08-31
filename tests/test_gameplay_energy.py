import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.attention.gameplay import select_energy_windows, windows_from_audio
from shorts_clipper.core.settings import Settings


class SelectEnergyWindowsTests(unittest.TestCase):
    def test_merges_consecutive_above_threshold(self):
        # 10 s, windows 1s each. Two loud runs: seconds 2-4 and 6-8.
        energy = [0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0]
        windows = select_energy_windows(
            energy,
            1.0,
            min_length=2.0,
            max_length=3.0,
            top_n=2,
            threshold=0.35,
        )
        self.assertEqual(len(windows), 2)
        # Both clusters present; aggregate energy determines order (equal length).
        starts = sorted(w.start for w in windows)
        self.assertEqual(starts, [2.0, 6.0])
        for w in windows:
            self.assertEqual(w.end - w.start, 3.0)

    def test_drops_clusters_shorter_than_min_length(self):
        # Seconds 4-5 is only 1s long (below min_length=3), so it is dropped.
        energy = [0.0] * 4 + [1.0, 1.0, 0.0] + [1.0, 1.0, 1.0, 0.0]
        windows = select_energy_windows(
            energy,
            1.0,
            min_length=3.0,
            max_length=3.0,
            top_n=5,
            threshold=0.35,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].start, 7.0)
        self.assertEqual(windows[0].end, 10.0)

    def test_expands_to_max_length_within_bounds(self):
        # Single energetic window at second 2; expand toward max_length=4s but
        # bounded by the array edges (0..10).
        energy = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        windows = select_energy_windows(
            energy,
            1.0,
            min_length=1.0,
            max_length=4.0,
            top_n=1,
            threshold=0.35,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].start, 1.0)
        self.assertEqual(windows[0].end, 5.0)
        self.assertEqual(windows[0].duration, 4.0)

    def test_top_n_returns_highest_energy_first(self):
        # Two clusters, second has higher aggregate energy.
        energy = [1.0, 1.0, 0.0] + [0.0, 0.0, 0.0] + [1.0, 1.0, 1.0, 0.0]
        windows = select_energy_windows(
            energy,
            1.0,
            min_length=2.0,
            max_length=3.0,
            top_n=5,
            threshold=0.35,
        )
        self.assertEqual(len(windows), 2)
        # First returned is the higher-energy cluster.
        self.assertEqual(windows[0].start, 6.0)
        self.assertEqual(windows[1].start, 0.0)

    def test_windows_are_non_overlapping_after_expansion(self):
        energy = [0.0, 1.0, 0.0, 1.0, 0.0]
        windows = select_energy_windows(
            energy,
            1.0,
            min_length=1.0,
            max_length=3.0,
            top_n=5,
            threshold=0.35,
        )
        windows.sort(key=lambda w: w.start)
        for a, b in zip(windows, windows[1:]):
            self.assertLessEqual(a.end, b.start + 1e-6)

    def test_empty_energy_returns_empty(self):
        self.assertEqual(
            select_energy_windows([], 1.0, min_length=2.0, max_length=60.0, top_n=3), []
        )

    def test_returns_plain_clip_windows(self):
        energy = [1.0, 1.0, 1.0]
        windows = select_energy_windows(
            energy, 1.0, min_length=2.0, max_length=60.0, top_n=1, threshold=0.35
        )
        self.assertEqual(windows[0].start, 0.0)
        self.assertEqual(windows[0].end, 3.0)


class WindowsFromAudioTests(unittest.TestCase):
    def _settings(self):
        from shorts_clipper.core.settings import Settings

        return Settings(
            gameplay_scan_max_seconds=3600,
            gameplay_top_windows=5,
            gameplay_min_length=12.0,
            gameplay_max_length=60.0,
        )

    @mock.patch("shorts_clipper.attention.gameplay.extract_audio_energy")
    def test_uses_monkeypatched_energy(self, mock_energy):
        # A 60s 'scan' represented as 60 windows; two energetic clusters.
        energy = [0.0] * 10 + [1.0] * 15 + [0.0] * 10 + [1.0] * 15 + [0.0] * 10
        mock_energy.return_value = energy
        windows = windows_from_audio(Path("fake.m4a"), 60.0, self._settings())
        self.assertTrue(mock_energy.called)
        # Both clusters are >= 12s, so both survive.
        self.assertEqual(len(windows), 2)
        for w in windows:
            self.assertGreaterEqual(w.duration, 12.0)
            self.assertLessEqual(w.duration, 60.0)

    @mock.patch("shorts_clipper.attention.gameplay.extract_audio_energy")
    def test_passes_scan_max_seconds_to_energy(self, mock_energy):
        mock_energy.return_value = [1.0] * 30
        windows_from_audio(Path("fake.m4a"), 60.0, self._settings())
        _, kwargs = mock_energy.call_args
        self.assertEqual(kwargs["max_seconds"], 3600)
        self.assertEqual(kwargs["window_seconds"], 1.0)

    @mock.patch("shorts_clipper.attention.gameplay.extract_audio_energy")
    def test_empty_energy_returns_empty(self, mock_energy):
        mock_energy.return_value = []
        windows = windows_from_audio(Path("fake.m4a"), 60.0, self._settings())
        self.assertEqual(windows, [])


class GameplaySettingsParseTests(unittest.TestCase):
    def test_defaults(self):
        s = Settings()
        self.assertFalse(s.gameplay_mode)
        self.assertEqual(s.gameplay_scan_max_seconds, 3600)
        self.assertEqual(s.gameplay_top_windows, 5)
        self.assertEqual(s.gameplay_min_length, 12.0)
        self.assertEqual(s.gameplay_max_length, 60.0)

    def test_env_parse(self):
        import os

        from shorts_clipper.core.settings import Settings

        os.environ["SHORTS_GAMEPLAY_MODE"] = "true"
        os.environ["SHORTS_GAMEPLAY_SCAN_MAX_SECONDS"] = "1800"
        os.environ["SHORTS_GAMEPLAY_TOP_WINDOWS"] = "7"
        os.environ["SHORTS_GAMEPLAY_MIN_LENGTH"] = "8.0"
        os.environ["SHORTS_GAMEPLAY_MAX_LENGTH"] = "45.0"
        try:
            s = Settings.from_env("_nonexistent.env")
            self.assertTrue(s.gameplay_mode)
            self.assertEqual(s.gameplay_scan_max_seconds, 1800)
            self.assertEqual(s.gameplay_top_windows, 7)
            self.assertEqual(s.gameplay_min_length, 8.0)
            self.assertEqual(s.gameplay_max_length, 45.0)
        finally:
            for key in (
                "SHORTS_GAMEPLAY_MODE",
                "SHORTS_GAMEPLAY_SCAN_MAX_SECONDS",
                "SHORTS_GAMEPLAY_TOP_WINDOWS",
                "SHORTS_GAMEPLAY_MIN_LENGTH",
                "SHORTS_GAMEPLAY_MAX_LENGTH",
            ):
                os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()
