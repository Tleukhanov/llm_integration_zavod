"""Tests for persistent stock script dedup."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_clipper.pipeline import stock_runner
from shorts_clipper.pipeline.stock_dedup import (
    DEFAULT_USED_PATH,
    choose_unused,
    load_used,
    normalize_script,
    record_used,
    script_hash,
)


class NormalizeHashTests(unittest.TestCase):
    """Verify normalization and hashing behavior."""

    def test_normalize_collapses_whitespace_and_case(self):
        """Equivalent texts normalize identically."""
        self.assertEqual(normalize_script("  Hello   World\n"), "hello world")
        self.assertEqual(normalize_script("HELLO WORLD"), "hello world")

    def test_hash_stable_for_equivalent_texts(self):
        """Formatting differences share one hash."""
        self.assertEqual(script_hash("Hello  World"), script_hash("  hello world\n"))

    def test_hash_differs_for_different_texts(self):
        """Distinct quotes hash differently."""
        self.assertNotEqual(script_hash("first quote"), script_hash("second quote"))

    def test_default_path_lives_under_gitignored_data(self):
        """Default state file stays out of version control."""
        self.assertEqual(DEFAULT_USED_PATH, Path("data/stock_used.json"))


class LoadRecordTests(unittest.TestCase):
    """Verify used-file roundtrip and tolerance."""

    def test_roundtrip(self):
        """Recorded hashes load back as a set."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "used.json"
            record_used(path, "aaa")
            record_used(path, "bbb")
            self.assertEqual(load_used(path), {"aaa", "bbb"})
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw, ["aaa", "bbb"])

    def test_missing_file_returns_empty(self):
        """Missing state file loads as empty."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_used(Path(d) / "nope.json"), set())

    def test_corrupt_file_returns_empty(self):
        """Corrupt state file loads as empty without raising."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "used.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_used(path), set())

    def test_wrong_shape_returns_empty(self):
        """Non-list payloads load as empty without raising."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "used.json"
            path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            self.assertEqual(load_used(path), set())

    def test_record_never_raises(self):
        """Unwritable targets never crash the caller."""
        with tempfile.TemporaryDirectory() as d:
            record_used(Path(d), "aaa")
            record_used(Path(d) / "sub" / "used.json", "")
            self.assertEqual(load_used(Path(d) / "sub" / "used.json"), set())


class ChooseUnusedTests(unittest.TestCase):
    """Verify unused selection and exhaustion signaling."""

    def test_returns_first_when_none_used(self):
        """Fresh pool returns the first candidate without reset."""
        script, reset = choose_unused(["aaa", "bbb"], set())
        self.assertEqual((script, reset), ("aaa", False))

    def test_skips_used(self):
        """Used scripts are skipped in pool order."""
        used = {script_hash("aaa")}
        script, reset = choose_unused(["aaa", "bbb", "ccc"], used)
        self.assertEqual((script, reset), ("bbb", False))

    def test_exhaustion_reuses_first_with_reset(self):
        """Full pool reuses the first candidate with reset set."""
        pool = ["aaa", "bbb"]
        used = {script_hash("aaa"), script_hash("bbb")}
        script, reset = choose_unused(pool, used)
        self.assertEqual((script, reset), ("aaa", True))

    def test_exhaustion_prefers_least_recently_used(self):
        """Ordered history reuses the oldest entry first."""
        pool = ["aaa", "bbb"]
        used = [script_hash("bbb"), script_hash("aaa")]
        script, reset = choose_unused(pool, used)
        self.assertEqual((script, reset), ("bbb", True))

    def test_empty_pool_raises(self):
        """Empty candidate pool raises ValueError."""
        with self.assertRaises(ValueError):
            choose_unused([], set())


class SelectHelperTests(unittest.TestCase):
    """Verify the runner selection helper across runs."""

    def _pool_file(self, directory: Path, lines: list[str]) -> Path:
        path = directory / "scripts.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_second_call_picks_different_script(self):
        """Two selections from one pool never repeat."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool_path = self._pool_file(tmp, ["alpha quote", "beta quote", "gamma quote"])
            used_path = tmp / "used.json"
            first = stock_runner._select_stock_script(
                pool_path,
                1,
                niche="self-growth",
                niche_dir=tmp,
                used_path=used_path,
            )
            second = stock_runner._select_stock_script(
                pool_path,
                2,
                niche="self-growth",
                niche_dir=tmp,
                used_path=used_path,
            )
            self.assertIn(first, ["alpha quote", "beta quote", "gamma quote"])
            self.assertIn(second, ["alpha quote", "beta quote", "gamma quote"])
            self.assertNotEqual(first, second)
            self.assertEqual(len(load_used(used_path)), 2)

    def test_exhaustion_resets_and_reuses(self):
        """Exhausted pool reuses with a fresh single-entry history."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool_path = self._pool_file(tmp, ["alpha quote", "beta quote"])
            used_path = tmp / "used.json"
            for seed in (1, 2):
                stock_runner._select_stock_script(
                    pool_path,
                    seed,
                    niche="self-growth",
                    niche_dir=tmp,
                    used_path=used_path,
                )
            third = stock_runner._select_stock_script(
                pool_path,
                3,
                niche="self-growth",
                niche_dir=tmp,
                used_path=used_path,
            )
            self.assertEqual(third, "alpha quote")
            self.assertEqual(load_used(used_path), {script_hash("alpha quote")})

    def test_single_script_pool_never_crashes(self):
        """Explicit single-script runs record gracefully."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool_path = self._pool_file(tmp, ["only quote"])
            used_path = tmp / "used.json"
            first = stock_runner._select_stock_script(
                pool_path,
                1,
                niche="self-growth",
                niche_dir=tmp,
                used_path=used_path,
            )
            second = stock_runner._select_stock_script(
                pool_path,
                2,
                niche="self-growth",
                niche_dir=tmp,
                used_path=used_path,
            )
            self.assertEqual(first, "only quote")
            self.assertEqual(second, "only quote")


class RunStockShortDedupTests(unittest.TestCase):
    """Verify run_stock_short prefers fresh scripts across runs."""

    def _settings(self, tmp: Path, pool_path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            niche="self-growth",
            stock_script_path=str(pool_path),
            niche_dir=str(tmp),
            stock_dir=str(tmp / "stock"),
            pexels_api_key="",
            stock_edit=False,
            stock_edit_bpm=132.0,
            video_codec="libx264",
            video_preset="veryfast",
            subtitle_style="Default",
            affiliate_enabled=False,
            bgm_mode="off",
            music_dir=str(tmp),
            bgm_volume=0.2,
            vo_rate="+0%",
            vo_pitch="+3Hz",
            output_dir=tmp / "outputs",
            publish_platforms=[],
        )

    def test_second_run_picks_different_script(self):
        """Two pipeline runs from one pool speak different quotes."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool_path = tmp / "scripts.txt"
            pool_path.write_text("alpha quote\nbeta quote\ngamma quote\n", encoding="utf-8")
            used_path = tmp / "used.json"
            seen: list[str] = []

            def fake_tts(script: str, vo_path: Path, **kwargs) -> tuple[Path, list]:
                seen.append(script)
                Path(vo_path).write_bytes(b"voice")
                return Path(vo_path), [("alpha", 0.0, 0.5), ("quote", 0.5, 1.0)]

            def fake_render(work_dir: Path, out_path: Path, duration: float, **kwargs) -> Path:
                Path(out_path).write_bytes(b"bg")
                return Path(out_path)

            def fake_burn(*args, **kwargs) -> Path:
                out = Path(kwargs["output_path"])
                out.write_bytes(b"video")
                return out

            settings = self._settings(tmp, pool_path)
            with mock.patch(
                "shorts_clipper.pipeline.stock_runner._refresh_retention_grades",
                return_value={},
            ):
                with mock.patch(
                    "shorts_clipper.audio.tts.synthesize_voiceover_boundaries",
                    side_effect=fake_tts,
                ):
                    with mock.patch(
                        "shorts_clipper.captions.music.track_duration",
                        return_value=8.0,
                    ):
                        with mock.patch(
                            "shorts_clipper.visual.stock.speech_window",
                            return_value=None,
                        ):
                            with mock.patch(
                                "shorts_clipper.visual.stock.list_stock_backgrounds",
                                return_value=[],
                            ):
                                with mock.patch(
                                    "shorts_clipper.visual.stock.render_procedural_background",
                                    side_effect=fake_render,
                                ):
                                    with mock.patch(
                                        "shorts_clipper.pipeline.stock_runner.generate_ass_file",
                                        return_value=tmp / "subs.ass",
                                    ):
                                        with mock.patch(
                                            "shorts_clipper.pipeline.stock_runner.burn_subtitles",
                                            side_effect=fake_burn,
                                        ):
                                            stock_runner.run_stock_short(
                                                settings=settings,
                                                count=1,
                                                used_path=used_path,
                                            )
                                            stock_runner.run_stock_short(
                                                settings=settings,
                                                count=1,
                                                used_path=used_path,
                                            )
            self.assertEqual(len(seen), 2)
            self.assertNotEqual(seen[0], seen[1])
            self.assertEqual(len(load_used(used_path)), 2)


if __name__ == "__main__":
    unittest.main()
