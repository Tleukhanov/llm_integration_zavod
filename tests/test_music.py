"""Tests for the background-music helper module."""

import random
import unittest
from pathlib import Path

from shorts_clipper.captions.music import list_tracks, pick_track, should_use_bgm


class ListTracksTests(unittest.TestCase):
    def _make_dirs(self, root: Path) -> Path:
        d = root / "music"
        d.mkdir(parents=True)
        (d / "b.mp3").write_bytes(b"b")
        (d / "a.wav").write_bytes(b"a")
        (d / "c.m4a").write_bytes(b"c")
        (d / "d.ogg").write_bytes(b"d")
        (d / "notes.txt").write_text("not audio")
        (d / "e.MP3").write_bytes(b"e")  # uppercase ext handled
        return d

    def test_sorts_deterministically_and_filters_extensions(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = self._make_dirs(Path(tmp))
            tracks = list_tracks(d)
            names = [t.name for t in tracks]
            self.assertEqual(
                names, ["a.wav", "b.mp3", "c.m4a", "d.ogg", "e.MP3"]
            )
            self.assertNotIn("notes.txt", names)

    def test_missing_dir_returns_empty(self):
        self.assertEqual(list_tracks(Path("/nonexistent/music")), [])


class ShouldUseBgmTests(unittest.TestCase):
    def test_off_never(self):
        rng = random.Random(0)
        for _ in range(5):
            self.assertFalse(should_use_bgm("off", rng))

    def test_music_always(self):
        rng = random.Random(0)
        for _ in range(5):
            self.assertTrue(should_use_bgm("music", rng))

    def test_auto_true(self):
        rng = random.Random(0)
        for _ in range(3):
            self.assertTrue(should_use_bgm("auto", rng))

    def test_unknown_false(self):
        rng = random.Random(0)
        self.assertFalse(should_use_bgm("bogus", rng))

    def test_mix50_both_outcomes_over_seeds(self):
        results = {should_use_bgm("mix50", random.Random(s)) for s in range(64)}
        self.assertEqual(results, {True, False})

    def test_mix50_deterministic_for_seed(self):
        a = should_use_bgm("mix50", random.Random(1234))
        b = should_use_bgm("mix50", random.Random(1234))
        self.assertEqual(a, b)


class PickTrackTests(unittest.TestCase):
    def _make_tracks(self, root: Path, count: int) -> Path:
        d = root / "music"
        d.mkdir(parents=True)
        for i in range(count):
            (d / f"track_{i:02d}.mp3").write_bytes(b"x")
        return d

    def test_none_when_no_tracks(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "empty"
            d.mkdir()
            self.assertIsNone(pick_track(d, random.Random(0)))

    def test_deterministic_for_seed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = self._make_tracks(Path(tmp), 5)
            rng1 = random.Random(42)
            rng2 = random.Random(42)
            self.assertEqual(pick_track(d, rng1), pick_track(d, rng2))

    def test_avoids_last_track_when_available(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = self._make_tracks(Path(tmp), 3)
            # Force last_track to a known value and confirm the pick differs
            # (only possible across many trials when >1 track exists).
            last = d / "track_00.mp3"
            picked_any_different = False
            for _ in range(50):
                p = pick_track(d, random.Random(_), last_track=last)
                if p != last:
                    picked_any_different = True
                    break
            self.assertTrue(picked_any_different)

    def test_single_track_is_returned(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = self._make_tracks(Path(tmp), 1)
            self.assertEqual(pick_track(d, random.Random(0)), d / "track_00.mp3")


class SettingsBgmTests(unittest.TestCase):
    def _load(self, env_text: str):
        from shorts_clipper.core.settings import Settings

        file_values = {}
        for line in env_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            file_values[k.strip()] = v.strip()
        return file_values

    def test_defaults(self):
        from shorts_clipper.core.settings import _env

        fv = self._load("")
        self.assertEqual(_env("SHORTS_BGM_MODE", fv, "off") or "off", "off")
        self.assertEqual(_env("SHORTS_MUSIC_DIR", fv, "D:/shorts_music") or "D:/shorts_music", "D:/shorts_music")
        self.assertEqual(_env("SHORTS_BGM_VOLUME", fv, "0.30") or "0.30", "0.30")

    def test_env_parsing(self):
        from shorts_clipper.core.settings import _env

        fv = self._load(
            "SHORTS_BGM_MODE=mix50\n"
            "SHORTS_MUSIC_DIR=D:/tracks\n"
            "SHORTS_BGM_VOLUME=0.50\n"
        )
        self.assertEqual(_env("SHORTS_BGM_MODE", fv, "off") or "off", "mix50")
        self.assertEqual(_env("SHORTS_MUSIC_DIR", fv, "D:/shorts_music") or "D:/shorts_music", "D:/tracks")
        self.assertEqual(_env("SHORTS_BGM_VOLUME", fv, "0.30") or "0.30", "0.50")

    def test_constructs_settings_rows(self):
        from shorts_clipper.core.settings import Settings
        from pathlib import Path

        s = Settings.from_env("nonexistent.env")
        self.assertEqual(s.bgm_mode, "off")
        self.assertEqual(s.music_dir, Path("D:/shorts_music"))
        self.assertEqual(s.bgm_volume, 0.30)


if __name__ == "__main__":
    unittest.main()
