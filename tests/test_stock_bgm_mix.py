"""Stock BGM mix: voice-forward bed at a stock-specific volume."""

from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_clipper.core.settings import Settings
from shorts_clipper.pipeline import runner, stock_runner

STOCK_VOLUME_DEFAULT = 0.16
CLIP_VOLUME_DEFAULT = 0.30

_ENV_KEYS = (
    "SHORTS_VISUAL_MODE",
    "SHORTS_BGM_MODE",
    "SHORTS_BGM_VOLUME",
    "SHORTS_STOCK_BGM_VOLUME",
)


def _clean() -> mock._patch_dict:
    """Patch environ and drop BGM-related keys."""
    patcher = mock.patch.dict(os.environ, {}, clear=False)
    patched = patcher.start()
    for key in _ENV_KEYS:
        patched.pop(key, None)
    return patcher


def _load(env_text: str) -> Settings:
    """Load settings from an isolated temp env file."""
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text(env_text, encoding="utf-8")
        return Settings.from_env(env_path=env_path)


def _stock_settings(tmp: Path, **overrides) -> SimpleNamespace:
    values = dict(
        niche="self-growth",
        stock_script_path=None,
        niche_dir=str(tmp),
        stock_dir=str(tmp / "stock"),
        pexels_api_key="",
        stock_edit=False,
        stock_edit_bpm=132.0,
        video_codec="libx264",
        video_preset="veryfast",
        subtitle_style="Default",
        affiliate_enabled=False,
        bgm_mode="mix50",
        music_dir=str(tmp),
        bgm_volume=CLIP_VOLUME_DEFAULT,
        stock_bgm_volume=STOCK_VOLUME_DEFAULT,
        vo_rate="+0%",
        vo_pitch="+3Hz",
        output_dir=tmp / "outputs",
        publish_platforms=[],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _run_stock(settings, tmp: Path, track: Path | None) -> dict:
    """Run one stock short with mocked IO; return burn_subtitles kwargs."""
    captured: dict = {}

    def fake_tts(script: str, vo_path: Path, **kwargs):
        Path(vo_path).write_bytes(b"voice")
        return Path(vo_path), [("alpha", 0.0, 0.5), ("quote", 0.5, 1.0)]

    def fake_render(work_dir: Path, out_path: Path, duration: float, **kwargs):
        Path(out_path).write_bytes(b"bg")
        return Path(out_path)

    def fake_burn(*args, **kwargs):
        captured.update(kwargs)
        out = Path(kwargs["output_path"])
        out.write_bytes(b"video")
        return out

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
                    "shorts_clipper.audio.tts.speech_window",
                    return_value=None,
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
                                        with mock.patch(
                                            "shorts_clipper.pipeline.stock_runner.should_use_bgm",
                                            return_value=True,
                                        ):
                                            with mock.patch(
                                                "shorts_clipper.pipeline.stock_runner.pick_track",
                                                return_value=track,
                                            ):
                                                stock_runner.run_stock_short(
                                                    settings=settings,
                                                    count=1,
                                                    used_path=tmp / "used.json",
                                                )
    return captured


class StockBgmMixTests(unittest.TestCase):
    def test_stock_bgm_kwargs_voice_forward_and_stock_volume(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            track = tmp / "track.mp3"
            track.write_bytes(b"ID3")
            settings = _stock_settings(tmp)
            captured = _run_stock(settings, tmp, track)
            self.assertIs(captured["bgm_music_forward"], True)
            self.assertAlmostEqual(captured["bgm_volume"], STOCK_VOLUME_DEFAULT)
            self.assertEqual(captured["bgm_audio"], track)

    def test_stock_bgm_volume_env_override_reaches_burn(self):
        patcher = _clean()
        try:
            settings = _load(
                "SHORTS_VISUAL_MODE=stock\n"
                "SHORTS_OUTPUT_DIR=out\n"
                "SHORTS_NICHE_DIR=niches\n"
                "SHORTS_STOCK_BGM_VOLUME=0.22\n"
            )
            self.assertAlmostEqual(settings.stock_bgm_volume, 0.22)
            self.assertAlmostEqual(settings.bgm_volume, CLIP_VOLUME_DEFAULT)
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                track = tmp / "track.mp3"
                track.write_bytes(b"ID3")
                captured = _run_stock(settings, tmp, track)
                self.assertIs(captured["bgm_music_forward"], True)
                self.assertAlmostEqual(captured["bgm_volume"], 0.22)
        finally:
            patcher.stop()

    def test_stock_bgm_volume_defaults(self):
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\n")
            self.assertAlmostEqual(settings.stock_bgm_volume, STOCK_VOLUME_DEFAULT)
            self.assertAlmostEqual(settings.bgm_volume, CLIP_VOLUME_DEFAULT)
        finally:
            patcher.stop()

    def test_stock_bgm_volume_environ_beats_file(self):
        patcher = _clean()
        try:
            os.environ["SHORTS_STOCK_BGM_VOLUME"] = "0.25"
            settings = _load(
                "SHORTS_VISUAL_MODE=stock\nSHORTS_STOCK_BGM_VOLUME=0.22\n"
            )
            self.assertAlmostEqual(settings.stock_bgm_volume, 0.25)
        finally:
            patcher.stop()

    def test_off_mode_passes_no_bgm_kwargs(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            settings = _stock_settings(tmp, bgm_mode="off")
            captured = _run_stock(settings, tmp, None)
            self.assertNotIn("bgm_audio", captured)
            self.assertNotIn("bgm_volume", captured)
            self.assertNotIn("bgm_music_forward", captured)

    def test_missing_track_passes_no_bgm_kwargs(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            settings = _stock_settings(tmp)
            captured = _run_stock(settings, tmp, None)
            self.assertNotIn("bgm_audio", captured)
            self.assertNotIn("bgm_volume", captured)
            self.assertNotIn("bgm_music_forward", captured)


class ClipPathRegressionTests(unittest.TestCase):
    def test_clip_path_keeps_flat_mix_and_clip_volume(self):
        src = inspect.getsource(runner.run)
        self.assertIn('"bgm_music_forward": music_forward', src)
        self.assertIn('"bgm_volume": bgm_vol', src)
        self.assertIn("bgm_vol = settings.bgm_volume", src)
        self.assertNotIn("stock_bgm_volume", src)

    def test_stock_path_uses_voice_forward_and_stock_volume(self):
        src = inspect.getsource(stock_runner.run_stock_short)
        self.assertIn('"bgm_music_forward": True', src)
        self.assertIn('"bgm_volume": settings.stock_bgm_volume', src)


if __name__ == "__main__":
    unittest.main()
