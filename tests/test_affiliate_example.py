"""Tests that the example partners config loads and that robustness holds."""

import json
import tempfile
import unittest
from pathlib import Path

from shorts_clipper.affiliate.partners import (
    build_affiliate_description,
    load_affiliate_partners,
    select_affiliate_partner,
)
from shorts_clipper.core.settings import Settings

EXAMPLE_PATH = Path(__file__).parent.parent / "affiliate_partners.example.json"


class AffiliateExampleFileTests(unittest.TestCase):
    def test_loads_three_example_partners(self):
        partners = load_affiliate_partners(
            Settings(affiliate_partners_path=str(EXAMPLE_PATH))
        )
        self.assertEqual(len(partners), 3)

    def test_disabled_partner_is_flagged(self):
        partners = load_affiliate_partners(
            Settings(affiliate_partners_path=str(EXAMPLE_PATH))
        )
        by_id = {p.id: p for p in partners}
        self.assertTrue(by_id["skin_case_example"].enabled)
        self.assertTrue(by_id["knife_gallery_example"].enabled)
        self.assertFalse(by_id["cool_shop_example"].enabled)

    def test_ru_and_en_link_helpers(self):
        partners = load_affiliate_partners(
            Settings(affiliate_partners_path=str(EXAMPLE_PATH))
        )
        by_id = {p.id: p for p in partners}
        skin = by_id["skin_case_example"]
        self.assertEqual(skin.link("ru"), "https://example.com/skincase/STREAMER_ID_RU")
        self.assertEqual(skin.link("en"), "https://example.com/skincase/STREAMER_ID_EN")
        knife = by_id["knife_gallery_example"]
        self.assertIsNone(knife.link_ru)
        self.assertEqual(knife.link("ru"), "https://example.com/knifegallery/STREAMER")
        self.assertEqual(knife.link("en"), "https://example.com/knifegallery/STREAMER")


class AffiliateExampleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.partners = load_affiliate_partners(
            Settings(affiliate_partners_path=str(EXAMPLE_PATH))
        )

    def test_keyword_match_picks_skin_case(self):
        result = select_affiliate_partner(
            self.partners, transcript_text="открыл кейс и забрал cs2 скины"
        )
        self.assertEqual(result.id, "skin_case_example")

    def test_keyword_match_requires_all_keywords(self):
        result = select_affiliate_partner(
            self.partners, transcript_text="упомянут кейс, но нет cs2", round_robin_index=1
        )
        self.assertEqual(result.id, "knife_gallery_example")

    def test_no_match_falls_back_to_round_robin(self):
        result = select_affiliate_partner(
            self.partners, transcript_text="совсем ничего релевантного",
            round_robin_index=0,
        )
        self.assertEqual(result.id, "skin_case_example")

    def test_all_disabled_returns_none(self):
        disabled = [p for p in self.partners if not p.enabled]
        self.assertIsNone(
            select_affiliate_partner(disabled, transcript_text="кейс cs2")
        )


class AffiliateExampleRobustnessTests(unittest.TestCase):
    def test_malformed_json_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partners.json"
            path.write_text("{not valid json", encoding="utf-8")
            partners = load_affiliate_partners(
                Settings(affiliate_partners_path=str(path))
            )
            self.assertEqual(partners, [])

    def test_missing_file_returns_empty_list(self):
        settings = Settings(affiliate_partners_path=str(Path("does_not_exist.json")))
        self.assertEqual(load_affiliate_partners(settings), [])

    def test_object_instead_of_list_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partners.json"
            path.write_text(json.dumps({"id": "not_a_list"}), encoding="utf-8")
            partners = load_affiliate_partners(
                Settings(affiliate_partners_path=str(path))
            )
            self.assertEqual(partners, [])


class AffiliateExampleDescriptionTests(unittest.TestCase):
    def test_build_appends_offer_link_and_tag(self):
        partners = load_affiliate_partners(
            Settings(affiliate_partners_path=str(EXAMPLE_PATH))
        )
        skin = partners[0]
        meta = {"description": "Nice clip"}
        description = build_affiliate_description(meta, skin, "en")
        self.assertEqual(
            description,
            "Nice clip\n\nSkinCase (example)\n"
            "https://example.com/skincase/STREAMER_ID_EN\n#ad",
        )


if __name__ == "__main__":
    unittest.main()
