"""Game-agnostic factory-as-a-service: SHORTS_GAME_NAME / LABEL / HASHTAGS.

Verifies the settings surface, that no user-facing published text is pinned
to "Counter-Strike 2", and that scout discovery follows the configured game.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_clipper.affiliate.partners import (
    AffiliatePartner,
    auto_cta_text,
    build_affiliate_description,
)
from shorts_clipper.core.settings import Settings
from shorts_clipper.scout.auto_batch import auto_discover
from shorts_clipper.scout.keywords import build_queries, get_keywords


class GamePortSettingsTests(unittest.TestCase):
    def test_defaults(self):
        settings = Settings()
        self.assertEqual(settings.game_name, "cs2")
        self.assertEqual(settings.game_label, "Counter-Strike 2")
        self.assertEqual(settings.game_hashtags, ["#cs2", "#counterstrike2"])

    def test_env_file_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "SHORTS_GAME_NAME=valorant",
                        "SHORTS_GAME_LABEL=Valorant",
                        "SHORTS_GAME_HASHTAGS=#valorant,#valorantclips",
                    ]
                ),
                encoding="utf-8",
            )
            settings = Settings.from_env(env_path)
            self.assertEqual(settings.game_name, "valorant")
            self.assertEqual(settings.game_label, "Valorant")
            self.assertEqual(settings.game_hashtags, ["#valorant", "#valorantclips"])

    def test_env_var_override(self):
        with mock.patch.dict(
            "os.environ",
            {
                "SHORTS_GAME_NAME": "gtarp",
                "SHORTS_GAME_LABEL": "GTA RP",
                "SHORTS_GAME_HASHTAGS": "#gtarp,#gta",
            },
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.game_name, "gtarp")
        self.assertEqual(settings.game_label, "GTA RP")
        self.assertEqual(settings.game_hashtags, ["#gtarp", "#gta"])


class GamePortPublishedTextTests(unittest.TestCase):
    def test_auto_cta_text_uses_game_label(self):
        partner = AffiliatePartner(id="p", name="SkinHub", link_en="https://x/en")
        cta = auto_cta_text(partner, "Valorant")
        self.assertIn("Valorant", cta)
        self.assertNotIn("Counter-Strike", cta)

    def test_published_caption_description_hashtags_follow_game(self):
        settings = Settings(
            game_name="valorant",
            game_label="Valorant",
            game_hashtags=["#valorant", "#valorantclips"],
        )
        partner = AffiliatePartner(id="p", name="SkinHub", link_en="https://x/en")
        meta = {"title": "Round Win", "description": "Clutch moment"}

        description = build_affiliate_description(meta, partner, "en")
        cta = auto_cta_text(partner, settings.game_label)
        tags_str = " ".join(settings.game_hashtags)
        caption = "\n\n".join([meta["title"], description, cta, tags_str])

        self.assertIn("Valorant", caption)
        self.assertNotIn("Counter-Strike", caption)
        self.assertIn("#valorant", caption)
        self.assertNotIn("#cs2", caption)


class GamePortScoutTests(unittest.TestCase):
    def test_get_keywords_expands_game_name(self):
        kws = get_keywords("valorant", game_name="valorant")
        self.assertGreaterEqual(len(kws), 4)
        for kw in kws:
            self.assertTrue(kw.startswith("valorant"))
        # The exact-match game branch never leaks generic CS2 terms.
        self.assertNotIn("cs2", " ".join(kws))

    def test_build_queries_game_name(self):
        queries = build_queries("fortnite", keyword=None, count=3, game_name="fortnite")
        self.assertGreaterEqual(len(queries), 3)
        for q in queries:
            self.assertTrue(q.startswith("ytsearch15:"))
            self.assertIn("fortnite", q)

    def test_auto_discover_default_query_uses_game_name(self):
        settings = SimpleNamespace(
            processed_videos_path="does-not-exist.json",
            metrics_path=None,
            game_name="valorant",
        )
        with mock.patch("shorts_clipper.scout.auto_batch.scout", return_value=[]) as scout_patch:
            auto_discover(settings, providers=("youtube",), max_results=3)
            called_query = scout_patch.call_args.args[0]
        self.assertIn("valorant gameplay", called_query)


if __name__ == "__main__":
    unittest.main()