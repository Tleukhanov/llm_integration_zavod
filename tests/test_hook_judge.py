"""Tests for the hook judge module — hermetic, no live API calls."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.core.settings import Settings
from shorts_clipper.highlight_detection.hook_judge import (
    HookJudge,
    HookVerdict,
    _parse_verdict,
    auto_threshold,
)


# ---------------------------------------------------------------------------
# _parse_verdict tests
# ---------------------------------------------------------------------------


class TestParseVerdict(unittest.TestCase):
    def test_plain_json(self):
        raw = '{"score": 0.9, "reason": "strong hook"}'
        result = _parse_verdict(raw)
        self.assertAlmostEqual(result["score"], 0.9)
        self.assertEqual(result["reason"], "strong hook")

    def test_fenced_json(self):
        raw = '```json\n{"score": 0.7, "reason": "ok"}\n```'
        result = _parse_verdict(raw)
        self.assertAlmostEqual(result["score"], 0.7)

    def test_fenced_without_json_tag(self):
        raw = '```\n{"score": 0.6, "reason": "plain"}\n```'
        result = _parse_verdict(raw)
        self.assertAlmostEqual(result["score"], 0.6)

    def test_surrounded_by_text(self):
        raw = 'Here is my answer: {"score": 0.8, "reason": "great"} — done.'
        result = _parse_verdict(raw)
        self.assertAlmostEqual(result["score"], 0.8)
        self.assertEqual(result["reason"], "great")

    def test_garbage_returns_neutral(self):
        result = _parse_verdict("this is not json at all!!!")
        self.assertAlmostEqual(result["score"], 0.5)
        self.assertIn("unparseable", result["reason"])

    def test_empty_string_returns_neutral(self):
        result = _parse_verdict("")
        self.assertAlmostEqual(result["score"], 0.5)

    def test_missing_score_field(self):
        result = _parse_verdict('{"reason": "no score here"}')
        self.assertAlmostEqual(result["score"], 0.5)
        self.assertEqual(result["reason"], "no score here")

    def test_missing_reason_field(self):
        result = _parse_verdict('{"score": 0.3}')
        self.assertAlmostEqual(result["score"], 0.3)
        self.assertIn("no reason", result["reason"])

    def test_non_dict_returns_neutral(self):
        result = _parse_verdict('[1, 2, 3]')
        self.assertAlmostEqual(result["score"], 0.5)

    def test_score_clamped_above_one(self):
        result = _parse_verdict('{"score": 5.0, "reason": "high"}')
        self.assertAlmostEqual(result["score"], 1.0)

    def test_score_clamped_below_zero(self):
        result = _parse_verdict('{"score": -3.0, "reason": "neg"}')
        self.assertAlmostEqual(result["score"], 0.0)

    def test_non_numeric_score(self):
        result = _parse_verdict('{"score": "notanumber", "reason": "err"}')
        self.assertAlmostEqual(result["score"], 0.5)


# ---------------------------------------------------------------------------
# auto_threshold tests
# ---------------------------------------------------------------------------


class TestAutoThreshold(unittest.TestCase):
    def test_default(self):
        s = Settings()
        self.assertAlmostEqual(auto_threshold(s), 0.5)


# ---------------------------------------------------------------------------
# Settings parse tests
# ---------------------------------------------------------------------------


class TestHookJudgeSettings(unittest.TestCase):
    def test_defaults(self):
        s = Settings()
        self.assertFalse(s.hook_judge_enabled)
        self.assertAlmostEqual(s.hook_min_score, 0.5)

    def test_env_parse_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "SHORTS_HOOK_JUDGE_ENABLED=true\nSHORTS_HOOK_MIN_SCORE=0.7\n",
                encoding="utf-8",
            )
            s = Settings.from_env(env_path=env_path)
            self.assertTrue(s.hook_judge_enabled)
            self.assertAlmostEqual(s.hook_min_score, 0.7)

    def test_env_parse_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("SHORTS_HOOK_JUDGE_ENABLED=false\n", encoding="utf-8")
            s = Settings.from_env(env_path=env_path)
            self.assertFalse(s.hook_judge_enabled)

    def test_bad_min_score_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("SHORTS_HOOK_MIN_SCORE=not_a_number\n", encoding="utf-8")
            s = Settings.from_env(env_path=env_path)
            self.assertAlmostEqual(s.hook_min_score, 0.5)


# ---------------------------------------------------------------------------
# HookJudge.score tests
# ---------------------------------------------------------------------------


class TestHookJudgeScore(unittest.TestCase):
    def _settings(self, min_score: float = 0.5) -> Settings:
        return Settings(
            gemini_api_key="fake-key",
            hook_judge_enabled=True,
            hook_min_score=min_score,
        )

    def test_neutral_when_provider_raises(self):
        judge = HookJudge(self._settings())

        # Patch the GeminiProvider class so that constructing it raises
        raising_cls = mock.MagicMock()
        raising_cls.side_effect = RuntimeError("API down")

        with mock.patch.dict(
            "sys.modules",
            {
                "shorts_clipper.providers.gemini": mock.MagicMock(
                    GeminiProvider=raising_cls
                ),
                "shorts_clipper.providers": mock.MagicMock(),
            },
        ):
            verdict = judge.score("Some moment text here")
            self.assertTrue(verdict.ok)
            self.assertAlmostEqual(verdict.score, 0.5)
            self.assertIn("llm unavailable", verdict.reason)

    def test_neutral_when_provider_returns_garbage(self):
        judge = HookJudge(self._settings())

        mock_prov = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.text = "!!not json!!"
        mock_prov.generate_content.return_value = mock_resp

        with mock.patch.dict(
            "sys.modules",
            {
                "shorts_clipper.providers.gemini": mock.MagicMock(
                    GeminiProvider=mock.MagicMock(return_value=mock_prov)
                ),
                "shorts_clipper.providers": mock.MagicMock(),
            },
        ):
            verdict = judge.score("some text")
            self.assertTrue(verdict.ok)
            self.assertAlmostEqual(verdict.score, 0.5)

    def test_ok_true_when_above_threshold(self):
        settings = self._settings(min_score=0.4)
        judge = HookJudge(settings)

        mock_prov = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.text = '{"score": 0.5, "reason": "decent"}'
        mock_prov.generate_content.return_value = mock_resp

        with mock.patch.dict(
            "sys.modules",
            {
                "shorts_clipper.providers.gemini": mock.MagicMock(
                    GeminiProvider=mock.MagicMock(return_value=mock_prov)
                ),
                "shorts_clipper.providers": mock.MagicMock(),
            },
        ):
            verdict = judge.score("Good hook moment")
            self.assertTrue(verdict.ok)
            self.assertAlmostEqual(verdict.score, 0.5)

    def test_ok_false_when_below_threshold(self):
        settings = self._settings(min_score=0.6)
        judge = HookJudge(settings)

        mock_prov = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.text = '{"score": 0.5, "reason": "mediocre"}'
        mock_prov.generate_content.return_value = mock_resp

        with mock.patch.dict(
            "sys.modules",
            {
                "shorts_clipper.providers.gemini": mock.MagicMock(
                    GeminiProvider=mock.MagicMock(return_value=mock_prov)
                ),
                "shorts_clipper.providers": mock.MagicMock(),
            },
        ):
            verdict = judge.score("Weak hook moment")
            self.assertFalse(verdict.ok)
            self.assertAlmostEqual(verdict.score, 0.5)

    def test_empty_text_returns_neutral(self):
        judge = HookJudge(self._settings())
        verdict = judge.score("")
        self.assertTrue(verdict.ok)
        self.assertAlmostEqual(verdict.score, 0.5)

    def test_text_truncated_to_600_chars(self):
        settings = self._settings()
        judge = HookJudge(settings)

        captured = {}

        def fake_generate_content(contents):
            captured["prompt"] = contents
            mock_resp = mock.MagicMock()
            mock_resp.text = '{"score": 0.8, "reason": "ok"}'
            return mock_resp

        mock_prov_cls = mock.MagicMock()
        mock_prov_cls.return_value.generate_content = fake_generate_content

        with mock.patch.dict(
            "sys.modules",
            {
                "shorts_clipper.providers.gemini": mock.MagicMock(
                    GeminiProvider=mock_prov_cls
                ),
                "shorts_clipper.providers": mock.MagicMock(),
            },
        ):
            long_text = "x" * 1000
            judge.score(long_text)
            # The prompt should contain at most 600 chars of the moment text
            prompt = captured["prompt"]
            # Find the moment text section
            marker = "--- MOMENT TEXT ---"
            if marker in prompt:
                moment_section = prompt.split(marker)[1]
                self.assertLessEqual(len(moment_section.strip()), 601)


# ---------------------------------------------------------------------------
# rank_moments tests
# ---------------------------------------------------------------------------


class TestRankMoments(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(
            gemini_api_key="fake-key",
            hook_judge_enabled=True,
            hook_min_score=0.5,
        )

    def _make_judge_with_scores(self, scores: list[float]) -> HookJudge:
        """Create a HookJudge that returns predetermined scores in order."""
        judge = HookJudge(self._settings())
        call_idx = {"n": 0}

        import types

        def bound_score(self_inner, moment_text, context=None):
            n = call_idx["n"]
            call_idx["n"] = n + 1
            s = scores[min(n, len(scores) - 1)]
            return HookVerdict(score=s, reason=f"reason-{s}", ok=s >= 0.5)

        judge.score = types.MethodType(bound_score, judge)
        return judge

    def test_sorts_descending(self):
        moments = [
            {"id": "a", "text": "bad moment"},
            {"id": "b", "text": "great moment"},
            {"id": "c", "text": "ok moment"},
        ]
        judge = self._make_judge_with_scores([0.2, 0.9, 0.6])
        result = judge.rank_moments(moments, text_fn=lambda m: m["text"])
        self.assertEqual(result[0]["id"], "b")
        self.assertEqual(result[1]["id"], "c")
        self.assertEqual(result[2]["id"], "a")

    def test_augmentation(self):
        moments = [{"id": "x", "text": "hello"}]
        judge = self._make_judge_with_scores([0.75])
        result = judge.rank_moments(moments, text_fn=lambda m: m["text"])
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["hook_score"], 0.75)
        self.assertIn("hook_reason", result[0])
        self.assertTrue(result[0]["hook_ok"])

    def test_tie_preserves_original_order(self):
        moments = [
            {"id": "first", "text": "same"},
            {"id": "second", "text": "same"},
            {"id": "third", "text": "same"},
        ]
        judge = self._make_judge_with_scores([0.5, 0.5, 0.5])
        result = judge.rank_moments(moments, text_fn=lambda m: m["text"])
        self.assertEqual([m["id"] for m in result], ["first", "second", "third"])

    def test_empty_list(self):
        judge = self._make_judge_with_scores([])
        result = judge.rank_moments([], text_fn=lambda m: "")
        self.assertEqual(result, [])

    def test_scoring_failure_gives_neutral(self):
        moments = [{"id": "fail", "text": "x"}]
        judge = HookJudge(self._settings())

        def bad_text_fn(m):
            raise ValueError("boom")

        result = judge.rank_moments(moments, text_fn=bad_text_fn)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["hook_score"], 0.5)
        self.assertTrue(result[0]["hook_ok"])


# ---------------------------------------------------------------------------
# HookVerdict dataclass
# ---------------------------------------------------------------------------


class TestHookVerdict(unittest.TestCase):
    def test_fields(self):
        v = HookVerdict(score=0.8, reason="strong", ok=True)
        self.assertAlmostEqual(v.score, 0.8)
        self.assertEqual(v.reason, "strong")
        self.assertTrue(v.ok)

    def test_frozen(self):
        v = HookVerdict(score=0.3, reason="weak", ok=False)
        with self.assertRaises(AttributeError):
            v.score = 0.9  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
