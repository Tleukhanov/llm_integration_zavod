"""Tests for music-track selection, duration probing, and the phonk generator."""

import importlib.util
import random
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from shorts_clipper.captions.music import (
    LONG_TRACK_SECONDS,
    _is_junk_music,
    list_tracks,
    pick_track,
    track_duration,
    usable_tracks,
)


def _load_make_phonk():
    """Import scripts/make_phonk.py by path (not on the import path by default)."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "make_phonk.py"
    spec = importlib.util.spec_from_file_location("make_phonk", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_small_wav(path: Path, nframes: int = 44100 * 2, samprate: int = 44100):
    """Write a tiny but valid mono 16-bit WAV; returns its duration in seconds."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samprate)
        w.writeframes(struct.pack(f"<{nframes}h", *([0] * nframes)))
    return nframes / samprate


class ListTracksExtTests(unittest.TestCase):
    def test_includes_supported_and_ignores_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            names = ["a.mp3", "b.m4a", "c.ogg", "d.wav", "skip.txt", "e.xlsx"]
            for n in names:
                (d / n).write_bytes(b"x")
            got = {p.name for p in list_tracks(d)}
            self.assertEqual(got, {"a.mp3", "b.m4a", "c.ogg", "d.wav"})


class TrackDurationTests(unittest.TestCase):
    def test_wav_duration_without_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "tone.wav"
            dur = _write_small_wav(p, nframes=44100 * 3)
            self.assertAlmostEqual(track_duration(p), dur, places=3)

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(track_duration(Path(tmp) / "nope.wav"))

    def test_results_are_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "tone.wav"
            _write_small_wav(p, nframes=44100)
            from shorts_clipper.captions.music import _duration_cache

            key = str(p)
            _duration_cache.pop(key, None)
            first = track_duration(p)
            self.assertIsNotNone(first)
            self.assertIn(key, _duration_cache)


class PickTrackPrefersLongTests(unittest.TestCase):
    def test_prefers_long_tracks_over_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            long_a = d / "long_a.wav"
            long_b = d / "long_b.wav"
            (d / "short.wav").write_bytes(b"x")
            _write_small_wav(long_a, nframes=int(44100 * 90))
            _write_small_wav(long_b, nframes=int(44100 * 80))
            # Long tracks are >= LONG_TRACK_SECONDS.
            self.assertEqual(track_duration(long_a), 90.0)
            self.assertEqual(track_duration(long_b), 80.0)
            # Every pick across many seeds should land on a long track.
            for seed in range(40):
                picked = pick_track(d, random.Random(seed))
                self.assertIn(picked, {long_a, long_b})

    def test_unknown_duration_falls_back_to_random(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for i in range(3):
                (d / f"track_{i}.mp3").write_bytes(b"x")  # not real audio
            picked = {pick_track(d, random.Random(s)) for s in range(20)}
            self.assertEqual(len(picked), 3)


class JunkMusicFilterTests(unittest.TestCase):
    """Scratch audio must never be burned into a clip.

    A real run mixed `test_phonk_132.wav` over a published short: the leftover
    sat in the pool beside the intended industrial loop and `pick_track` chose
    between them at random. Unlike the `generated_*` family there is no operator
    scenario where a debug render is the BGM they want, so the filter is
    unconditional.
    """

    def test_junk_prefixes_are_never_picked(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            good = d / "dark_industrial_loop.wav"
            _write_small_wav(good, nframes=int(44100 * 90))
            for name in (
                "test_phonk_132.wav",
                "Test_Render.wav",
                "tmp_loop.wav",
                "debug_bgm.wav",
                "scratch_x.wav",
            ):
                _write_small_wav(d / name, nframes=int(44100 * 90))
            for seed in range(40):
                self.assertEqual(pick_track(d, random.Random(seed)), good)

    def test_junk_only_directory_is_empty_and_falls_back_to_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_small_wav(d / "test_phonk_132.wav", nframes=int(44100 * 90))
            # Visible to list_tracks (it only filters by extension)...
            self.assertEqual([p.name for p in list_tracks(d)], ["test_phonk_132.wav"])
            # ...but not mixable, and not "usable" -- so ensure_synthesized_track
            # renders a real loop instead of shipping the debug render.
            self.assertIsNone(pick_track(d, random.Random(0)))
            self.assertEqual(usable_tracks(d), [])

    def test_non_junk_names_survive_the_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ("dark_industrial_loop.wav", "latest_2024.wav", "attest.wav"):
                _write_small_wav(d / name, nframes=int(44100 * 90))
            for path in list_tracks(d):
                self.assertFalse(_is_junk_music(path), msg=path.name)


class MakePhonkGeneratorTests(unittest.TestCase):
    def test_wav_header_is_valid(self):
        mod = _load_make_phonk()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "phonk.wav"
            samples = mod.make_phonk(duration=1.0)
            mod.write_wav(out, samples)

            self.assertGreater(out.stat().st_size, 44)
            with open(out, "rb") as f:
                header = f.read(44)
            self.assertEqual(header[:4], b"RIFF")
            self.assertEqual(header[8:12], b"WAVE")

            with wave.open(str(out), "rb") as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getframerate(), mod.SAMPLE_RATE)
                nframes = w.getnframes()
                self.assertGreater(nframes, 0)
                self.assertAlmostEqual(nframes / mod.SAMPLE_RATE, 1.0, delta=0.01)
                # Non-silent content.
                import array

                data = array.array("h")
                data.frombytes(w.readframes(nframes))
                peak = max(abs(x) for x in data)
                self.assertGreater(peak, 0)

    def test_generates_requested_length(self):
        mod = _load_make_phonk()
        samples = mod.make_phonk(duration=2.0)
        self.assertEqual(len(samples), int(2.0 * mod.SAMPLE_RATE))

    def test_long_track_constant_exposed(self):
        # Guard: the threshold must match what we treat as "covers a clip".
        self.assertGreaterEqual(LONG_TRACK_SECONDS, 60.0)


if __name__ == "__main__":
    unittest.main()
