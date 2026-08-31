"""Tests for the timed mid-roll affiliate ad card feature."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import av
import numpy as np

from shorts_clipper.affiliate.partners import (
    AffiliatePartner,
    auto_cta_text,
)
from shorts_clipper.captions.generator import burn_subtitles
from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.core.settings import Settings


def _make_vertical_mp4(path: Path) -> Path:
    """Generate a tiny vertical 1080x1920 mp4 via PyAV with audio."""
    container = av.open(str(path), mode="w")
    vstream = container.add_stream("mpeg4", rate=1)
    vstream.width = 108
    vstream.height = 192
    vstream.pix_fmt = "yuv420p"

    astream = container.add_stream("aac", rate=8000)
    astream.layout = "mono"

    for _ in range(4):
        frame = av.VideoFrame.from_ndarray(
            np.zeros((192, 108, 3), dtype=np.uint8), format="rgb24"
        )
        for packet in vstream.encode(frame):
            container.mux(packet)

    for _ in range(4):
        # 0.5s of silent audio
        samples = np.zeros(4000, dtype=np.int16)
        aframe = av.AudioFrame.from_ndarray(
            samples.reshape(1, -1), format="s16", layout="mono"
        )
        aframe.sample_rate = 8000
        for packet in astream.encode(aframe):
            container.mux(packet)

    for packet in vstream.encode():
        container.mux(packet)
    for packet in astream.encode():
        container.mux(packet)
    container.close()
    return path


SEGMENTS = [
    TranscriptSegment(start=0.0, end=0.5, text="hello world"),
    TranscriptSegment(start=0.5, end=1.0, text="cs2 skins"),
]


class AutoCtaTextTests(unittest.TestCase):
    def test_uses_partner_name(self):
        partner = AffiliatePartner(
            id="p", name="SkinFarm", link_en="https://x/en"
        )
        self.assertIn("SkinFarm", auto_cta_text(partner))

    def test_matches_expected_default(self):
        partner = AffiliatePartner(
            id="p", name="FanOdd", link_en="https://x/en"
        )
        self.assertEqual(auto_cta_text(partner), "FanOdd — скины CS2, ссылка в описании")


class SettingsParseTests(unittest.TestCase):
    def test_defaults(self):
        settings = Settings()
        self.assertFalse(settings.affiliate_ad_card)
        self.assertEqual(settings.affiliate_ad_start_fraction, 0.45)
        self.assertEqual(settings.affiliate_ad_duration_sec, 4.0)
        self.assertEqual(settings.affiliate_cta_text, "")

    def test_env_parse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "SHORTS_AFFILIATE_AD_CARD=true",
                        "SHORTS_AFFILIATE_AD_START_FRACTION=0.6",
                        "SHORTS_AFFILIATE_AD_DURATION_SEC=5.0",
                        "SHORTS_AFFILIATE_CTA_TEXT=Check it out",
                    ]
                ),
                encoding="utf-8",
            )
            settings = Settings.from_env(env_path)
            self.assertTrue(settings.affiliate_ad_card)
            self.assertEqual(settings.affiliate_ad_start_fraction, 0.6)
            self.assertEqual(settings.affiliate_ad_duration_sec, 5.0)
            self.assertEqual(settings.affiliate_cta_text, "Check it out")

    def test_invalid_fraction_clamped_to_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "SHORTS_AFFILIATE_AD_START_FRACTION=9.9\n", encoding="utf-8"
            )
            settings = Settings.from_env(env_path)
            self.assertEqual(settings.affiliate_ad_start_fraction, 0.45)


class BurnSubtitlesAdCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.video = _make_vertical_mp4(self.tmpdir / "in.mp4")
        self.ad_img = self.tmpdir / "ad.png"
        self.ad_img.write_bytes(b"fakeimage")
        self.banner = self.tmpdir / "banner.png"
        self.banner.write_bytes(b"fakebanner")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, **kwargs):
        with mock.patch("shorts_clipper.captions.generator.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stderr="")
            out = self.tmpdir / "out.mp4"
            burn_subtitles(self.video, SEGMENTS, 0.0, out, **kwargs)
            return mock_run.call_args[0][0]

    def test_no_ad_params_identical_to_baseline(self):
        base_cmd = self._run()
        other_cmd = self._run(ad_card_text=None, ad_card_image=None)
        # Both commands use unique temp dirs; normalize those paths before comparing
        def _norm(cmd):
            import re
            return [re.sub(r"ass_[^/]+/subs\.ass", "ass_TMP/subs.ass", c) for c in cmd]

        self.assertEqual(_norm(base_cmd), _norm(other_cmd))
        self.assertNotIn("enable='between(t,", " ".join(base_cmd))
        self.assertNotIn("drawtext", " ".join(base_cmd))
        self.assertIn("-vf", base_cmd)
        self.assertNotIn("-filter_complex", base_cmd)

    def test_ad_card_with_image_and_text_uses_filter_complex(self):
        cmd = self._run(
            ad_card_image=self.ad_img,
            ad_card_text="Buy now",
            ad_card_start=2.0,
            ad_card_duration=2.5,
        )
        joined = " ".join(cmd)
        self.assertIn("-i", joined)
        self.assertEqual(cmd.count("-i"), 2)
        self.assertIn("-filter_complex", cmd)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("enable='between(t,2.0,4.5)'", fc)
        self.assertIn("[card]", fc)
        self.assertIn("min(ih,300)", fc)
        self.assertIn("drawtext", fc)
        self.assertIn("textfile=", fc)
        self.assertNotIn("-vf", cmd)

    def test_until_end_of_clip_uses_large_end(self):
        cmd = self._run(
            ad_card_image=self.ad_img,
            ad_card_text="Buy now",
            ad_card_start=1.5,
            ad_card_duration=None,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("enable='between(t,1.5,999999.0)'", fc)

    def test_text_only_uses_no_extra_input(self):
        cmd = self._run(ad_card_text="Just text", ad_card_start=0.5)
        self.assertEqual(cmd.count("-i"), 1)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("drawtext", fc)
        self.assertIn("enable='between(t,0.5,999999.0)'", fc)

    def test_banner_and_ad_card_chain(self):
        cmd = self._run(
            banner_image=self.banner,
            banner_position="bottom_left",
            ad_card_image=self.ad_img,
            ad_card_text="Buy now",
            ad_card_start=1.0,
            ad_card_duration=3.0,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertEqual(cmd.count("-i"), 3)
        self.assertIn("[1:v]scale=200:-1[logo]", fc)
        self.assertIn("[2:v]scale=-2:'min(ih,300)'[card]", fc)
        self.assertIn("overlay=40:H-h-300", fc)
        self.assertIn("overlay=(W-w)/2:H-h-320", fc)
        self.assertIn("enable='between(t,1.0,4.0)'", fc)
        # order: banner then card
        self.assertLess(fc.index("[logo]"), fc.index("[card]"))

    def test_ad_card_only_image(self):
        cmd = self._run(
            ad_card_image=self.ad_img,
            ad_card_start=0.0,
            ad_card_duration=2.0,
        )
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertEqual(cmd.count("-i"), 2)
        # no banner, so ad image is input 1
        self.assertIn("[1:v]scale=-2:'min(ih,300)'[card]", fc)
        self.assertNotIn("drawtext", fc)


if __name__ == "__main__":
    unittest.main()
