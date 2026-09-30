"""Tests for stock sidecar persistence and deterministic seeds."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_clipper.pipeline import stock_runner


class NextFreeStablePathTests(unittest.TestCase):
    """Verify unique stable output paths."""

    def test_returns_base_when_free(self):
        """Return base path when nothing exists."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "stock_short_1.mp4"
            self.assertEqual(stock_runner._next_free_stable_path(base), base)

    def test_appends_suffix_when_occupied(self):
        """Append numeric suffix when base paths exist."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "stock_short_1.mp4"
            base.write_bytes(b"v1")
            nxt = stock_runner._next_free_stable_path(base)
            self.assertEqual(nxt, Path(d) / "stock_short_1_2.mp4")
            nxt.write_bytes(b"v2")
            (Path(d) / "stock_short_1_2.json").write_text("{}", encoding="utf-8")
            nxt2 = stock_runner._next_free_stable_path(base)
            self.assertEqual(nxt2, Path(d) / "stock_short_1_3.mp4")

    def test_json_only_blocks_base(self):
        """Treat lone sidecar as occupying the base path."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "stock_short_1.mp4"
            (Path(d) / "stock_short_1.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                stock_runner._next_free_stable_path(base),
                Path(d) / "stock_short_1_2.mp4",
            )


class SidecarRefreshTests(unittest.TestCase):
    """Verify fresh sidecars never reuse stale metadata."""

    def test_second_run_never_overwrites_and_refreshes(self):
        """Persist twice and keep both videos with fresh sidecars."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            base = out / "stock_short_1.mp4"
            src1 = out / "src1.mp4"
            src1.write_bytes(b"video-one")
            meta1 = {"title": "first", "script": "one"}
            stable1 = stock_runner._next_free_stable_path(base)
            stock_runner._persist_file_atomic(src1, stable1)
            stock_runner._write_json_atomic(stable1.with_suffix(".json"), meta1)
            src2 = out / "src2.mp4"
            src2.write_bytes(b"video-two")
            meta2 = {"title": "second", "script": "two"}
            stable2 = stock_runner._next_free_stable_path(base)
            stock_runner._persist_file_atomic(src2, stable2)
            stock_runner._write_json_atomic(stable2.with_suffix(".json"), meta2)
            self.assertNotEqual(stable1, stable2)
            self.assertEqual(stable1.read_bytes(), b"video-one")
            self.assertEqual(stable2.read_bytes(), b"video-two")
            got1 = json.loads(stable1.with_suffix(".json").read_text(encoding="utf-8"))
            got2 = json.loads(stable2.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(got1["title"], "first")
            self.assertEqual(got2["title"], "second")

    def test_write_json_atomic_replaces(self):
        """Atomic json write replaces previous payload."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "stock_short_1.json"
            stock_runner._write_json_atomic(path, {"title": "old"})
            stock_runner._write_json_atomic(path, {"title": "new"})
            got = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(got["title"], "new")


class BgmSeedStabilityTests(unittest.TestCase):
    """Verify BGM seed is stable across processes."""

    def test_same_inputs_same_output(self):
        """Same seed and path give same RNG draws."""
        bg = Path("background.mp4")
        r1 = stock_runner._bgm_seed(123, bg)
        r2 = stock_runner._bgm_seed(123, bg)
        self.assertEqual(r1.random(), r2.random())
        r3 = stock_runner._bgm_seed(999, bg)
        self.assertNotEqual(r1.random(), r3.random())

    def test_uses_stable_digest_not_hash(self):
        """Seed material derives from a stable digest."""
        import inspect

        src = inspect.getsource(stock_runner._bgm_seed)
        self.assertIn("sha256", src)
        runner_src = inspect.getsource(stock_runner.run_stock_short)
        self.assertNotIn("hash(str(", runner_src)

    def test_stable_across_processes(self):
        """Seeded draws match across interpreter runs."""
        code = (
            "from pathlib import Path; "
            "from shorts_clipper.pipeline.stock_runner import _bgm_seed; "
            "print(_bgm_seed(77, Path('background.mp4')).random())"
        )
        outs = []
        for hash_seed in ("0", "12345"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = hash_seed
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            self.assertEqual(proc.returncode, 0)
            outs.append(proc.stdout.strip())
        self.assertEqual(outs[0], outs[1])


class ZeroShortsTests(unittest.TestCase):
    """Verify loud failure when nothing is produced."""

    def test_guard_raises_on_empty(self):
        """Empty output list raises RuntimeError."""
        with self.assertRaises(RuntimeError):
            stock_runner._ensure_outputs_or_raise([])

    def test_guard_passes_on_outputs(self):
        """Non-empty output list does not raise."""
        stock_runner._ensure_outputs_or_raise([Path("x.mp4")])

    def test_run_raises_when_vo_fails(self):
        """Run with failing voiceover raises RuntimeError."""
        settings = SimpleNamespace(
            niche="self-growth",
            stock_script_path=None,
            niche_dir=None,
            stock_dir=Path("none"),
            pexels_api_key=None,
            stock_edit=False,
            stock_edit_bpm=132.0,
            video_codec="libx264",
            video_preset="veryfast",
            subtitle_style="Default",
            affiliate_enabled=False,
            bgm_mode="off",
            music_dir=Path("none"),
            bgm_volume=0.2,
            vo_rate="+0%",
            vo_pitch="+3Hz",
            output_dir=Path(tempfile.gettempdir()) / "stock_sidecar_test_out",
            publish_platforms=[],
        )
        with mock.patch(
            "shorts_clipper.pipeline.stock_runner._refresh_retention_grades",
            return_value={},
        ):
            with mock.patch(
                "shorts_clipper.visual.stock.load_stock_script",
                return_value="quote text",
            ):
                with mock.patch(
                    "shorts_clipper.audio.tts.synthesize_voiceover_boundaries",
                    return_value=(None, []),
                ):
                    with self.assertRaises(RuntimeError):
                        stock_runner.run_stock_short(settings=settings, count=1)


if __name__ == "__main__":
    unittest.main()
