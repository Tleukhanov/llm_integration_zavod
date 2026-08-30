"""Tests for the config-driven affiliate partners module."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.affiliate.partners import (
    AffiliatePartner,
    build_affiliate_description,
    load_affiliate_partners,
    select_affiliate_partner,
    select_affiliate_transcript_text,
)
from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.core.settings import Settings


class AffiliateJsonLoadingTests(unittest.TestCase):
    def test_loads_valid_partners_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partners.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "skin_farm",
                            "name": "SkinFarm",
                            "link_en": "https://skinfarm.com/r/en",
                            "link_ru": "https://skinfarm.com/r/ru",
                            "banner_path": "banners/skinfarm.png",
                            "tag": "#ad",
                            "match_keywords": ["cs2", "skins"],
                            "enabled": True,
                        },
                        {
                            "id": "fan_odd",
                            "name": "FanOdd",
                            "link_en": "https://fanodd.com/r/en",
                            "match_keywords": [],
                        },
                    ]
                ),
                encoding="utf-8",
            )
            partners = load_affiliate_partners(Settings(affiliate_partners_path=str(path)))

            self.assertEqual(len(partners), 2)
            self.assertEqual(partners[0].id, "skin_farm")
            self.assertEqual(partners[0].link_ru, "https://skinfarm.com/r/ru")
            self.assertEqual(partners[0].banner_path, "banners/skinfarm.png")
            self.assertEqual(partners[0].match_keywords, ("cs2", "skins"))
            self.assertTrue(partners[0].enabled)
            self.assertEqual(partners[1].link_ru, None)
            self.assertEqual(partners[1].enabled, True)

    def test_missing_file_returns_empty_list(self):
        settings = Settings(affiliate_partners_path="does_not_exist.json")
        self.assertEqual(load_affiliate_partners(settings), [])

    def test_malformed_json_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partners.json"
            path.write_text("{not valid json", encoding="utf-8")
            partners = load_affiliate_partners(Settings(affiliate_partners_path=str(path)))
            self.assertEqual(partners, [])

    def test_invalid_entries_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partners.json"
            path.write_text(
                json.dumps([{"id": "missing_fields"}, {"name": "also_missing_link"}]),
                encoding="utf-8",
            )
            partners = load_affiliate_partners(Settings(affiliate_partners_path=str(path)))
            self.assertEqual(partners, [])


class AffiliateSelectorTests(unittest.TestCase):
    def setUp(self):
        self.keyword_partner = AffiliatePartner(
            id="cs",
            name="SkinMarket",
            link_en="https://example.com/r/en",
            match_keywords=("cs2", "skins"),
        )
        self.fallback_partner = AffiliatePartner(
            id="fn", name="FanOdd", link_en="https://example.com/r/fn"
        )

    def test_keyword_match_picks_first_matching_enabled_partner(self):
        partners = [self.keyword_partner, self.fallback_partner]
        result = select_affiliate_partner(
            partners, transcript_text="he said CS2 SKINS are cheap right now"
        )
        self.assertEqual(result.id, "cs")

    def test_keyword_match_requires_all_keywords(self):
        partners = [self.keyword_partner, self.fallback_partner]
        result = select_affiliate_partner(
            partners, transcript_text="only cs2 mentioned here", round_robin_index=1
        )
        self.assertEqual(result.id, "fn")

    def test_no_match_falls_back_to_round_robin(self):
        partners = [self.keyword_partner, self.fallback_partner]
        result = select_affiliate_partner(
            partners, transcript_text="nothing relevant in the clip", round_robin_index=1
        )
        self.assertEqual(result.id, "fn")

    def test_round_robin_cycles_on_index(self):
        partners = [self.keyword_partner, self.fallback_partner]
        first = select_affiliate_partner(partners, round_robin_index=0)
        second = select_affiliate_partner(partners, round_robin_index=1)
        self.assertEqual((first.id, second.id), ("cs", "fn"))

    def test_no_partners_returns_none(self):
        self.assertIsNone(
            select_affiliate_partner([], transcript_text="any text", round_robin_index=0)
        )

    def test_disabled_partners_are_skipped(self):
        disabled = AffiliatePartner(
            id="d", name="Off", link_en="https://example.com/r/d", match_keywords=("marketing",),
            enabled=False,
        )
        self.assertIsNone(select_affiliate_partner([disabled], transcript_text="marketing!"))

    def test_transcript_text_helper_lowercases_and_joins(self):
        segments = [
            TranscriptSegment(start=0, end=1, text="MP5 Skins"),
            TranscriptSegment(start=1, end=2, text="CS2 AWP"),
        ]
        self.assertEqual(select_affiliate_transcript_text(segments), "mp5 skins cs2 awp")


class AffiliateDescriptionTests(unittest.TestCase):
    def test_en_description_appends_offer_link_and_tag(self):
        partner = AffiliatePartner(
            id="p", name="SkinHub", link_en="https://hub/en", link_ru="https://hub/ru", tag="#ad"
        )
        meta = {"title": "T", "description": "Nice clip"}
        description = build_affiliate_description(meta, partner, "en")

        self.assertEqual(description, "Nice clip\n\nSkinHub\nhttps://hub/en\n#ad")

    def test_ru_description_uses_russian_link_when_available(self):
        partner = AffiliatePartner(
            id="p", name="SkinHub", link_en="https://hub/en", link_ru="https://hub/ru"
        )
        description = build_affiliate_description({"description": "Клип"}, partner, "ru")
        self.assertIn("https://hub/ru", description)
        self.assertNotIn("https://hub/en", description)

    def test_ru_falls_back_to_english_link_when_missing(self):
        partner = AffiliatePartner(id="p", name="SkinHub", link_en="https://hub/en")
        description = build_affiliate_description({"description": "Клип"}, partner, "ru-RU")
        self.assertIn("https://hub/en", description)


class BurnSubtitlesBannerTests(unittest.TestCase):
    @mock.patch("shorts_clipper.captions.generator.subprocess.run")
    def test_filter_complex_used_with_banner(self, mock_run):
        from shorts_clipper.captions.generator import burn_subtitles

        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            (tmp / "banner.png").write_bytes(b"fake")
            (tmp / "in.mp4").write_bytes(b"fake")
            segments = [TranscriptSegment(start=0, end=1, text="hello world")]

            burn_subtitles(
                tmp / "in.mp4",
                segments,
                0.0,
                tmp / "out.mp4",
                banner_image=tmp / "banner.png",
                banner_position="bottom_left",
            )

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd.count("-i"), 2)
            self.assertNotIn("-vf", cmd)
            self.assertIn("-filter_complex", cmd)
            filter_complex = cmd[cmd.index("-filter_complex") + 1]
            self.assertIn("overlay", filter_complex)
            self.assertIn("scale=200:-1", filter_complex)
            self.assertIn("[v0]", filter_complex)
            self.assertIn("[vout]", filter_complex)
            self.assertIn("H-h-300", filter_complex)

    @mock.patch("shorts_clipper.captions.generator.subprocess.run")
    def test_top_right_banner_position(self, mock_run):
        from shorts_clipper.captions.generator import burn_subtitles

        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            (tmp / "banner.png").write_bytes(b"fake")
            (tmp / "in.mp4").write_bytes(b"fake")
            segments = [TranscriptSegment(start=0, end=1, text="hello")]

            burn_subtitles(
                tmp / "in.mp4",
                segments,
                0.0,
                tmp / "out.mp4",
                banner_image=tmp / "banner.png",
                banner_position="top_right",
            )

            cmd = mock_run.call_args[0][0]
            filter_complex = cmd[cmd.index("-filter_complex") + 1]
            self.assertIn("overlay=W-w-40:40", filter_complex)

    @mock.patch("shorts_clipper.captions.generator.subprocess.run")
    def test_vf_path_unchanged_without_banner(self, mock_run):
        from shorts_clipper.captions.generator import burn_subtitles

        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            (tmp / "in.mp4").write_bytes(b"fake")
            segments = [TranscriptSegment(start=0, end=1, text="hello")]

            burn_subtitles(tmp / "in.mp4", segments, 0.0, tmp / "out.mp4")

            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd.count("-i"), 1)
            self.assertIn("-vf", cmd)
            self.assertNotIn("-filter_complex", cmd)

    @mock.patch("shorts_clipper.captions.generator.subprocess.run")
    def test_missing_banner_file_falls_back_to_vf(self, mock_run):
        from shorts_clipper.captions.generator import burn_subtitles

        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            (tmp / "in.mp4").write_bytes(b"fake")
            segments = [TranscriptSegment(start=0, end=1, text="hello")]

            burn_subtitles(
                tmp / "in.mp4",
                segments,
                0.0,
                tmp / "out.mp4",
                banner_image=tmp / "missing_banner.png",
            )

            cmd = mock_run.call_args[0][0]
            self.assertIn("-vf", cmd)
            self.assertNotIn("-filter_complex", cmd)


if __name__ == "__main__":
    unittest.main()