"""Tests for the synthesized-music fallback (``ensure_synthesized_track``).

Covers the guard rails that keep a render from shipping silent: it only fires
on an empty pool, is idempotent, honours ``SHORTS_SYNTHESIZE_MUSIC``, swallows
its own failures, and names its output so ``pick_track`` will actually pick it.
"""

import contextlib
import os
import random
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from shorts_clipper.audio import dark_industrial
from shorts_clipper.captions.music import (
    _is_generated_music,
    ensure_synthesized_track,
    list_tracks,
    pick_track,
    usable_tracks,
)


def _silence_wav(path: Path, seconds: float = 1.0, rate: int = 44100) -> Path:
    """Write a valid (silent) mono 16-bit WAV so track probing has real bytes."""
    nframes = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * nframes)
    return path


class SynthesizeWhenEmptyTests(unittest.TestCase):
    def test_synthesizes_into_empty_dir_and_pick_track_finds_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertEqual(list_tracks(d), [])

            out = ensure_synthesized_track(d, duration=1.0)

            self.assertIsNotNone(out)
            self.assertEqual(out, d / dark_industrial.DEFAULT_FILENAME)
            self.assertTrue(out.is_file())
            self.assertEqual([p.name for p in list_tracks(d)], [out.name])
            # pick_track must resolve to the new file on its own.
            self.assertEqual(pick_track(d, random.Random(0)), out)

    def test_is_idempotent_and_never_regenerates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            first = ensure_synthesized_track(d, duration=1.0)
            stamp = first.stat().st_mtime_ns
            payload = first.read_bytes()

            with mock.patch.object(
                dark_industrial, "make_dark_industrial", side_effect=AssertionError("re-render")
            ):
                second = ensure_synthesized_track(d, duration=1.0)

            self.assertIsNone(second)  # nothing to do -> no new track, no rewrite
            self.assertEqual(first.stat().st_mtime_ns, stamp)
            self.assertEqual(first.read_bytes(), payload)
            self.assertEqual(len(list_tracks(d)), 1)

    def test_real_downloaded_track_is_never_displaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            real = _silence_wav(d / "jamendo_real_track.mp3", seconds=90.0)
            before = real.read_bytes()

            self.assertIsNone(ensure_synthesized_track(d, duration=1.0))

            self.assertEqual([p.name for p in list_tracks(d)], [real.name])
            self.assertEqual(real.read_bytes(), before)
            self.assertEqual(pick_track(d, random.Random(3)), real)

    def test_empty_download_counts_as_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "truncated.mp3").write_bytes(b"")  # failed download left nothing
            self.assertEqual(list_tracks(d), [d / "truncated.mp3"])
            self.assertEqual(usable_tracks(d), [])

            out = ensure_synthesized_track(d, duration=1.0)

            self.assertIsNotNone(out)
            self.assertTrue(out.is_file())


class SynthesizeGateTests(unittest.TestCase):
    def _dir(self, tmp: str) -> Path:
        return Path(tmp)

    def test_disabled_flag_does_nothing_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp)
            with mock.patch.object(
                dark_industrial, "make_dark_industrial", side_effect=AssertionError("must not run")
            ):
                self.assertIsNone(ensure_synthesized_track(d, enabled=False))
            self.assertEqual(list_tracks(d), [])
            self.assertEqual(list(d.iterdir()), [])  # nothing written at all

    def test_env_gate_off_blocks_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp)
            with mock.patch.dict(os.environ, {"SHORTS_SYNTHESIZE_MUSIC": "0"}):
                self.assertIsNone(ensure_synthesized_track(d, duration=1.0))
            self.assertEqual(list_tracks(d), [])

    def test_env_gate_off_from_settings_pipeline(self):
        """The runner passes Settings.synthesize_music; off means no render."""
        from shorts_clipper.core.settings import Settings

        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp)
            with mock.patch.dict(os.environ, {"SHORTS_SYNTHESIZE_MUSIC": "false"}):
                settings = Settings.from_env("_nonexistent.env")
            self.assertFalse(settings.synthesize_music)
            with mock.patch.object(
                dark_industrial, "make_dark_industrial", side_effect=AssertionError("must not run")
            ):
                self.assertIsNone(ensure_synthesized_track(d, enabled=settings.synthesize_music))
            self.assertEqual(list_tracks(d), [])

    def test_gate_defaults_on(self):
        from shorts_clipper.core.settings import Settings

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHORTS_SYNTHESIZE_MUSIC", None)
            self.assertTrue(Settings.from_env("_nonexistent.env").synthesize_music)
            self.assertTrue(Settings().synthesize_music)

    def test_env_gate_on_values(self):
        for raw in ("1", "true", "TRUE", "yes", "on"):
            with tempfile.TemporaryDirectory() as tmp:
                d = self._dir(tmp)
                with mock.patch.dict(os.environ, {"SHORTS_SYNTHESIZE_MUSIC": raw}):
                    out = ensure_synthesized_track(d, duration=1.0)
                self.assertIsNotNone(out, msg=f"gate {raw!r} should allow synthesis")

    def test_env_gate_off_values(self):
        for raw in ("0", "false", "no", "off", "OFF"):
            with tempfile.TemporaryDirectory() as tmp:
                d = self._dir(tmp)
                with mock.patch.dict(os.environ, {"SHORTS_SYNTHESIZE_MUSIC": raw}):
                    out = ensure_synthesized_track(d, duration=1.0)
                self.assertIsNone(out, msg=f"gate {raw!r} should block synthesis")


class SynthesizeFailureIsSafeTests(unittest.TestCase):
    def test_render_error_is_swallowed_and_logs_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(
                dark_industrial,
                "make_dark_industrial",
                side_effect=RuntimeError("numpy exploded"),
            ):
                with self.assertLogs("shorts_clipper.captions.music", level="WARNING") as logs:
                    out = ensure_synthesized_track(d, duration=1.0)

            self.assertIsNone(out)  # run continues without BGM
            self.assertTrue(any("numpy exploded" in m for m in logs.output))
            self.assertEqual(list_tracks(d), [])

    def test_write_error_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(
                dark_industrial,
                "write_wav",
                side_effect=OSError("disk full"),
            ):
                out = ensure_synthesized_track(d, duration=1.0)

            self.assertIsNone(out)
            self.assertEqual(list_tracks(d), [])
            self.assertEqual(list(d.iterdir()), [])  # .tmp sidecar cleaned up

    def test_missing_dependency_error_is_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(
                dark_industrial,
                "make_dark_industrial",
                side_effect=ImportError("No module named 'numpy'"),
            ):
                with self.assertLogs("shorts_clipper.captions.music", level="WARNING") as logs:
                    out = ensure_synthesized_track(d, duration=1.0)
            self.assertIsNone(out)
            self.assertTrue(any("numpy" in m for m in logs.output))
            self.assertEqual(list_tracks(d), [])

    def test_unusable_music_dir_is_swallowed(self):
        """A music_dir that is not a directory must not break the render."""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "music"
            blocker.write_bytes(b"not a directory")
            with self.assertLogs("shorts_clipper.captions.music", level="WARNING"):
                out = ensure_synthesized_track(blocker, duration=1.0)
            self.assertIsNone(out)
            self.assertEqual(list_tracks(blocker), [])


class GeneratedNameSurvivesPickTrackTests(unittest.TestCase):
    def test_default_filename_is_not_treated_as_generated(self):
        name = dark_industrial.DEFAULT_FILENAME
        self.assertFalse(name.startswith("generated_"))
        self.assertFalse(_is_generated_music(Path(name)))

    def test_pick_track_returns_it_without_allow_procedural(self):
        os.environ.pop("SHORTS_ALLOW_PROCEDURAL_MUSIC", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                out = ensure_synthesized_track(d, duration=1.0)
                self.assertFalse(_is_generated_music(out))
                self.assertEqual(pick_track(d, random.Random(11)), out)
        finally:
            os.environ.pop("SHORTS_ALLOW_PROCEDURAL_MUSIC", None)


class PipelineHookTests(unittest.TestCase):
    def test_runner_hooks_the_fallback_next_to_the_scraper(self):
        import inspect

        from shorts_clipper.pipeline import runner

        src = inspect.getsource(runner.run)
        self.assertIn("ensure_phonk_tracks", src)
        self.assertIn("ensure_synthesized_track", src)
        # Both live under the same "bgm enabled" guard.
        self.assertIn('if settings.bgm_mode != "off":', src)
        self.assertLess(
            src.index("ensure_phonk_tracks"), src.index("ensure_synthesized_track")
        )

    def test_stock_runner_also_guards_against_silence(self):
        import inspect

        from shorts_clipper.pipeline import stock_runner

        src = inspect.getsource(stock_runner.run_stock_short)
        self.assertIn("ensure_synthesized_track", src)


class DarkIndustrialModuleTests(unittest.TestCase):
    def test_cli_wrapper_delegates_to_the_package_module(self):
        """One copy of the DSP: the script must not define it itself."""
        import importlib.util

        script = Path(__file__).resolve().parents[1] / "scripts" / "make_dark_industrial.py"
        src = script.read_text(encoding="utf-8")
        self.assertIn("from shorts_clipper.audio.dark_industrial import", src)
        self.assertNotIn("def _tv_biquad", src)
        self.assertNotIn("def make_dark_industrial", src)

        spec = importlib.util.spec_from_file_location("make_dark_industrial_cli", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIs(mod.make_dark_industrial, dark_industrial.make_dark_industrial)
        self.assertIs(mod.write_wav, dark_industrial.write_wav)

    def test_cli_writes_requested_duration(self):
        import importlib.util

        script = Path(__file__).resolve().parents[1] / "scripts" / "make_dark_industrial.py"
        spec = importlib.util.spec_from_file_location("make_dark_industrial_cli", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.wav"
            buf = []
            with contextlib.redirect_stdout(type("S", (), {"write": lambda s, t: buf.append(t)})()):
                self.assertEqual(mod.main(["--duration", "2", "--out", str(out)]), 0)
            with wave.open(str(out), "rb") as w:
                self.assertEqual(w.getframerate(), dark_industrial.SAMPLE_RATE)
                self.assertAlmostEqual(
                    w.getnframes() / dark_industrial.SAMPLE_RATE, 2.0, delta=0.01
                )
                peak = max(
                    abs(x)
                    for x in dark_industrial.np.frombuffer(
                        w.readframes(w.getnframes()), dtype="<i2"
                    )
                )
            self.assertGreater(peak, 0)

    def test_render_is_deterministic_and_at_target_peak(self):
        first = dark_industrial.make_dark_industrial(duration=1.0)
        second = dark_industrial.make_dark_industrial(duration=1.0)
        self.assertEqual(len(first), int(1.0 * dark_industrial.SAMPLE_RATE))
        # Fixed seed -> byte-identical renders.
        self.assertTrue((first == second).all())
        self.assertAlmostEqual(
            float(abs(first).max()), dark_industrial.PEAK, places=6
        )


if __name__ == "__main__":
    unittest.main()
