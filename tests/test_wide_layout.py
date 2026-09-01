"""Tests for optional 16:9 wide layout support."""
import os
import unittest

from shorts_clipper.cropping.geometry import compute_center_crop
from shorts_clipper.rendering.crop import _build_wide_crop_filter, _WIDE_W, _WIDE_H
from shorts_clipper.core.settings import Settings


class WideCropFilterTests(unittest.TestCase):
    def test_wide_filter_from_1920x1080_source(self):
        vf = _build_wide_crop_filter(1920, 1080, "crop_center")
        self.assertIn("scale=1920:1080", vf)
        self.assertIn(f"crop={_WIDE_W}:{_WIDE_H}", vf)
        self.assertIn("setsar=1", vf)

    def test_wide_filter_from_1080x1920_portrait_source(self):
        vf = _build_wide_crop_filter(1080, 1920, "crop_center")
        self.assertIn(f"crop={_WIDE_W}:{_WIDE_H}", vf)
        self.assertIn("setsar=1", vf)
        self.assertIn("scale=", vf)

    def test_wide_filter_crop_left(self):
        vf = _build_wide_crop_filter(1920, 1080, "crop_left")
        self.assertIn(f"crop={_WIDE_W}:{_WIDE_H}:0:0", vf)
        self.assertIn("setsar=1", vf)

    def test_wide_filter_crop_right(self):
        vf = _build_wide_crop_filter(1920, 1080, "crop_right")
        self.assertIn(f"crop={_WIDE_W}:{_WIDE_H}", vf)
        self.assertIn("setsar=1", vf)

    def test_wide_filter_crop_right_portrait(self):
        vf = _build_wide_crop_filter(1080, 1920, "crop_right")
        self.assertIn(f"crop={_WIDE_W}:{_WIDE_H}", vf)
        self.assertIn("setsar=1", vf)
        parts = f"crop={_WIDE_W}:{_WIDE_H}:".split()
        self.assertIn("crop=", vf)

    def test_wide_filter_720p_source(self):
        vf = _build_wide_crop_filter(1280, 720, "crop_center")
        self.assertIn(f"crop={_WIDE_W}:{_WIDE_H}", vf)
        self.assertIn("scale=", vf)

    def test_wide_filter_dimensions_are_1920x1080(self):
        self.assertEqual(_WIDE_W, 1920)
        self.assertEqual(_WIDE_H, 1080)

    def test_center_crop_wide_geometry(self):
        crop = compute_center_crop(
            width=1080, height=1920, target_width=1920, target_height=1080
        )
        self.assertEqual(crop.width, 1080)
        self.assertEqual(crop.height, 608)


def _settings_with_env(**env_vars):
    saved = {k: os.environ.get(k) for k in env_vars}
    try:
        for k in env_vars:
            os.environ.pop(k, None)
        s = Settings.from_env(".env.does.not.exist")
        return s
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _settings_with_env_set(**env_vars):
    saved = {k: os.environ.get(k) for k in env_vars}
    try:
        for k, v in env_vars.items():
            os.environ[k] = v
        s = Settings.from_env(".env.does.not.exist")
        return s
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_default_output_aspect():
    s = _settings_with_env(SHORTS_OUTPUT_ASPECT="")
    assert s.output_aspect == "vertical"


def test_output_aspect_wide():
    s = _settings_with_env_set(SHORTS_OUTPUT_ASPECT="wide")
    assert s.output_aspect == "wide"


def test_output_aspect_both():
    s = _settings_with_env_set(SHORTS_OUTPUT_ASPECT="both")
    assert s.output_aspect == "both"


def test_output_aspect_invalid_falls_back():
    s = _settings_with_env_set(SHORTS_OUTPUT_ASPECT="invalid_value")
    assert s.output_aspect == "vertical"


def test_output_aspect_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SHORTS_OUTPUT_ASPECT=both\n",
        encoding="utf-8",
    )
    s = Settings.from_env(env_file)
    assert s.output_aspect == "both"


def test_output_aspect_case_insensitive():
    s = _settings_with_env_set(SHORTS_OUTPUT_ASPECT="WIDE")
    assert s.output_aspect == "wide"
