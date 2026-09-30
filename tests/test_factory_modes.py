"""Factory visual-mode routing without VOD discovery for stock."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _load_factory_module():
    """Load scripts/factory.py as a standalone module."""
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "factory_script_modes", repo_root / "scripts" / "factory.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_run_clips_module():
    """Load scripts/run_clips.py as a standalone module."""
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "run_clips_script_modes", repo_root / "scripts" / "run_clips.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _make_settings(tmp: str):
    """Build minimal settings namespace for factory rounds."""
    return SimpleNamespace(
        hook_banner_enabled=False,
        hook_banner_text="",
        niche="self-growth",
        factory_publish_hour_start=0,
        factory_publish_hour_end=24,
        metrics_path=str(Path(tmp) / "metrics.sqlite"),
    )


class FactoryStockModeTests(unittest.TestCase):
    def test_stock_calls_stock_runner_and_skips_discovery(self):
        """Stock mode renders via stock runner without auto_discover."""
        factory = _load_factory_module()
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(tmp)
            out = Path(tmp) / "stock.mp4"
            out.touch()
            store = mock.MagicMock()
            fake_stock = mock.MagicMock()
            fake_stock.run_stock_short.return_value = [out]
            with mock.patch.dict(
                sys.modules,
                {"shorts_clipper.pipeline.stock_runner": fake_stock},
            ):
                with mock.patch(
                    "shorts_clipper.scout.auto_batch.auto_discover",
                    side_effect=AssertionError("must not discover"),
                ) as mock_discover:
                    discovered, clipped, errors = factory._run_round(
                        settings,
                        query=None,
                        providers=("youtube",),
                        count=1,
                        max_videos=3,
                        publish=False,
                        store=store,
                        channel="test",
                        niche="self-growth",
                        daily_cap=6,
                        visual_mode="stock",
                    )
            self.assertEqual(mock_discover.call_count, 0)
            self.assertEqual(fake_stock.run_stock_short.call_count, 1)
            self.assertEqual(discovered, 1)
            self.assertEqual(clipped, 1)
            self.assertEqual(errors, 0)

    def test_stock_passes_requested_niche(self):
        """Stock mode forwards the requested niche to the runner."""
        factory = _load_factory_module()
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(tmp)
            out = Path(tmp) / "stock.mp4"
            out.touch()
            store = mock.MagicMock()
            fake_stock = mock.MagicMock()
            fake_stock.run_stock_short.return_value = [out]
            with mock.patch.dict(
                sys.modules,
                {"shorts_clipper.pipeline.stock_runner": fake_stock},
            ):
                with mock.patch(
                    "shorts_clipper.scout.auto_batch.auto_discover",
                    side_effect=AssertionError("must not discover"),
                ):
                    factory._run_round(
                        settings,
                        query=None,
                        providers=("youtube",),
                        count=2,
                        max_videos=3,
                        publish=False,
                        store=store,
                        channel="test",
                        niche="money",
                        daily_cap=6,
                        visual_mode="stock",
                    )
            _, kwargs = fake_stock.run_stock_short.call_args
            self.assertEqual(kwargs.get("niche"), "money")
            self.assertEqual(kwargs.get("count"), 2)

    def test_stock_defaults_to_self_growth(self):
        """Stock mode falls back to self-growth without a niche."""
        factory = _load_factory_module()
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(tmp)
            settings.niche = ""
            out = Path(tmp) / "stock.mp4"
            out.touch()
            store = mock.MagicMock()
            fake_stock = mock.MagicMock()
            fake_stock.run_stock_short.return_value = [out]
            with mock.patch.dict(
                sys.modules,
                {"shorts_clipper.pipeline.stock_runner": fake_stock},
            ):
                with mock.patch(
                    "shorts_clipper.scout.auto_batch.auto_discover",
                    side_effect=AssertionError("must not discover"),
                ):
                    factory._run_round(
                        settings,
                        query=None,
                        providers=("youtube",),
                        count=1,
                        max_videos=3,
                        publish=False,
                        store=store,
                        channel="test",
                        niche=None,
                        daily_cap=6,
                        visual_mode="stock",
                    )
            _, kwargs = fake_stock.run_stock_short.call_args
            self.assertEqual(kwargs.get("niche"), "self-growth")

    def test_stock_metrics_use_stock_id(self):
        """Stock metrics never reference a VOD video_id."""
        factory = _load_factory_module()
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(tmp)
            out = Path(tmp) / "stock.mp4"
            out.touch()
            store = mock.MagicMock()
            fake_stock = mock.MagicMock()
            fake_stock.run_stock_short.return_value = [out]
            with mock.patch.dict(
                sys.modules,
                {"shorts_clipper.pipeline.stock_runner": fake_stock},
            ):
                with mock.patch(
                    "shorts_clipper.scout.auto_batch.auto_discover",
                    side_effect=AssertionError("must not discover"),
                ):
                    factory._run_round(
                        settings,
                        query=None,
                        providers=("youtube",),
                        count=1,
                        max_videos=3,
                        publish=False,
                        store=store,
                        channel="test",
                        niche="philosophy",
                        daily_cap=6,
                        visual_mode="stock",
                    )
            self.assertTrue(store.record_clip.called)
            for call in store.record_clip.call_args_list:
                rec = call.args[0]
                self.assertTrue(str(rec.video_id).startswith("stock:philosophy:"))


class FactoryClipModeTests(unittest.TestCase):
    def test_clip_uses_discovery_and_skips_stock(self):
        """Clip mode discovers VODs and never touches the stock runner."""
        factory = _load_factory_module()
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(tmp)
            out = Path(tmp) / "clip.mp4"
            out.touch()
            store = mock.MagicMock()
            video = {
                "url": "https://www.youtube.com/watch?v=vod123",
                "video_id": "vod123",
                "title": "Some VOD",
                "channel": "SomeChannel",
            }
            fake_runner = mock.MagicMock()
            fake_runner.run.return_value = [out]
            fake_stock = mock.MagicMock()
            fake_stock.run_stock_short.side_effect = AssertionError(
                "must not run stock"
            )
            with mock.patch.dict(
                sys.modules,
                {
                    "shorts_clipper.pipeline.runner": fake_runner,
                    "shorts_clipper.pipeline.stock_runner": fake_stock,
                },
            ):
                with mock.patch(
                    "shorts_clipper.scout.auto_batch.auto_discover",
                    return_value=[video],
                ) as mock_discover:
                    discovered, clipped, errors = factory._run_round(
                        settings,
                        query="cs2",
                        providers=("youtube",),
                        count=1,
                        max_videos=3,
                        publish=False,
                        store=store,
                        channel="test",
                        niche=None,
                        daily_cap=6,
                        visual_mode="clip",
                    )
            self.assertEqual(mock_discover.call_count, 1)
            self.assertEqual(fake_runner.run.call_count, 1)
            self.assertEqual(fake_stock.run_stock_short.call_count, 0)
            self.assertEqual(discovered, 1)
            self.assertEqual(clipped, 1)
            self.assertEqual(errors, 0)


class RunClipsParserTests(unittest.TestCase):
    def test_parser_accepts_niche_and_visual_mode(self):
        """run_clips parser exposes niche and visual-mode flags."""
        run_clips = _load_run_clips_module()
        args = run_clips._build_parser().parse_args(
            [
                "--url",
                "https://www.youtube.com/watch?v=vod123",
                "--niche",
                "money",
                "--visual-mode",
                "stock",
            ]
        )
        self.assertEqual(args.niche, "money")
        self.assertEqual(args.visual_mode, "stock")

    def test_batch_passthrough_forwards_niche(self):
        """Batch helper forwards the requested niche to run."""
        run_clips = _load_run_clips_module()
        args = run_clips._build_parser().parse_args(
            [
                "--niche",
                "relationships",
                "--visual-mode",
                "clip",
                "--count",
                "1",
            ]
        )
        args.upload = False
        args.privacy = "private"
        args.channel = None
        args.dateafter = None
        settings = SimpleNamespace()
        fake_runner = mock.MagicMock()
        fake_runner.run.return_value = [Path("a.mp4")]
        with mock.patch.dict(
            sys.modules, {"shorts_clipper.pipeline.runner": fake_runner}
        ):
            run_clips._process_batch_items(
                ["https://www.youtube.com/watch?v=vod123"], args, settings
            )
        _, kwargs = fake_runner.run.call_args
        self.assertEqual(kwargs.get("niche"), "relationships")


if __name__ == "__main__":
    unittest.main()
