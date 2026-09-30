"""Stock-path quality gates and affiliate suppression."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.core.settings import STOCK_MOTIVATION_NICHES, Settings
from shorts_clipper.pipeline.stock_runner import stock_affiliate_allowed

_ENV_KEYS = (
    "SHORTS_VISUAL_MODE",
    "SHORTS_HOOK_JUDGE_ENABLED",
    "SHORTS_HOOK_MIN_SCORE",
    "SHORTS_BGM_MODE",
    "SHORTS_STOCK_AFFILIATE_CARDS_ENABLED",
    "SHORTS_AFFILIATE_AD_CARD",
    "SHORTS_NICHE",
    "AFFILIATE_ENABLED",
    "SHORTS_CHANNEL",
)


def _load(env_text: str) -> Settings:
    """Load settings from an isolated temp env file."""
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text(env_text, encoding="utf-8")
        return Settings.from_env(env_path=env_path)


def _clean() -> mock._patch_dict:
    """Patch environ and drop gate-related keys."""
    patcher = mock.patch.dict(os.environ, {}, clear=False)
    patched = patcher.start()
    for key in _ENV_KEYS:
        patched.pop(key, None)
    return patcher


class StockJudgeTests(unittest.TestCase):
    def test_judge_on_by_default_for_stock(self):
        """Stock enables the hook judge without explicit config."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\n")
            self.assertTrue(settings.hook_judge_enabled)
        finally:
            patcher.stop()

    def test_judge_explicit_off_respected_for_stock(self):
        """Explicit file opt-out keeps the judge off for stock."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\nSHORTS_HOOK_JUDGE_ENABLED=false\n")
            self.assertFalse(settings.hook_judge_enabled)
        finally:
            patcher.stop()

    def test_judge_explicit_off_env_respected_for_stock(self):
        """Explicit environ opt-out keeps the judge off for stock."""
        patcher = _clean()
        try:
            os.environ["SHORTS_HOOK_JUDGE_ENABLED"] = "false"
            settings = _load("SHORTS_VISUAL_MODE=stock\n")
            self.assertFalse(settings.hook_judge_enabled)
        finally:
            patcher.stop()

    def test_judge_stays_off_for_clip(self):
        """Clip path keeps the historical judge default."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=clip\n")
            self.assertFalse(settings.hook_judge_enabled)
        finally:
            patcher.stop()

    def test_stock_min_score_default(self):
        """Stock uses a strict min-score default."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\n")
            self.assertAlmostEqual(settings.hook_min_score, 0.6)
        finally:
            patcher.stop()

    def test_stock_min_score_explicit_respected(self):
        """Explicit min-score wins over the stock default."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\nSHORTS_HOOK_MIN_SCORE=0.8\n")
            self.assertAlmostEqual(settings.hook_min_score, 0.8)
        finally:
            patcher.stop()


class StockBgmTests(unittest.TestCase):
    def test_bgm_defaults_mix50_for_stock(self):
        """Unset BGM mode becomes mix50 for stock."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\n")
            self.assertEqual(settings.bgm_mode, "mix50")
        finally:
            patcher.stop()

    def test_bgm_explicit_off_stays_off_for_stock(self):
        """Explicit off never flips to mix50 for stock."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\nSHORTS_BGM_MODE=off\n")
            self.assertEqual(settings.bgm_mode, "off")
        finally:
            patcher.stop()

    def test_bgm_explicit_mode_respected_for_stock(self):
        """Explicit BGM mode wins over the stock default."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=stock\nSHORTS_BGM_MODE=music\n")
            self.assertEqual(settings.bgm_mode, "music")
        finally:
            patcher.stop()

    def test_bgm_stays_off_for_clip(self):
        """Clip path keeps the historical BGM default."""
        patcher = _clean()
        try:
            settings = _load("SHORTS_VISUAL_MODE=clip\n")
            self.assertEqual(settings.bgm_mode, "off")
        finally:
            patcher.stop()


class StockAffiliateTests(unittest.TestCase):
    def test_motivation_niches_covered(self):
        """Expected motivation niches match the stock set."""
        self.assertEqual(
            set(STOCK_MOTIVATION_NICHES),
            {"self-growth", "philosophy", "money", "relationships"},
        )

    def test_cards_off_for_motivation_niches_by_default(self):
        """Motivation niches suppress affiliate overlays by default."""
        for niche in ("self-growth", "philosophy", "money", "relationships"):
            with self.subTest(niche=niche):
                settings = Settings(niche=niche, stock_affiliate_cards_enabled=False)
                self.assertFalse(stock_affiliate_allowed(settings, niche))

    def test_opt_in_reenables_motivation_niches(self):
        """Explicit opt-in re-enables affiliate overlays for stock."""
        patcher = _clean()
        try:
            for niche in ("self-growth", "philosophy", "money", "relationships"):
                with self.subTest(niche=niche):
                    settings = _load(
                        "SHORTS_VISUAL_MODE=stock\n"
                        "SHORTS_STOCK_AFFILIATE_CARDS_ENABLED=true\n"
                        f"SHORTS_NICHE={niche}\n"
                    )
                    self.assertTrue(settings.stock_affiliate_cards_enabled)
                    self.assertTrue(stock_affiliate_allowed(settings, niche))
        finally:
            patcher.stop()

    def test_non_motivation_niche_allowed(self):
        """Niches outside the motivation set stay eligible."""
        settings = Settings(niche="gaming", stock_affiliate_cards_enabled=False)
        self.assertTrue(stock_affiliate_allowed(settings, "gaming"))

    def test_clip_path_unaffected(self):
        """Clip affiliate flags are untouched by stock defaults."""
        patcher = _clean()
        try:
            settings = _load(
                "SHORTS_VISUAL_MODE=clip\nSHORTS_AFFILIATE_AD_CARD=true\n"
            )
            self.assertTrue(settings.affiliate_ad_card)
            self.assertFalse(settings.stock_affiliate_cards_enabled)
        finally:
            patcher.stop()


if __name__ == "__main__":
    unittest.main()
