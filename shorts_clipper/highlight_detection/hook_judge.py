"""LLM-based hook quality judge for short-form moments.

Scores a candidate moment for whether it is a strong short-form hook
(intrigue + payoff + action) and gates weak windows.  Designed so it can
later be swapped to DeepSeek/OpenRouter via the providers interface —
see _PROVIDER_COMMENT for the swap point.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# --- Swap-point comment for future provider support ---
# To swap from Gemini to DeepSeek / OpenRouter, change the import and
# instantiation inside HookJudge.score() from:
#     from shorts_clipper.providers.gemini import GeminiProvider
#     provider = GeminiProvider(api_key=settings.gemini_api_key)
#     response = provider.generate_content(prompt)
# to:
#     from shorts_clipper.providers.openrouter import OpenRouterProvider
#     provider = OpenRouterProvider(api_key=settings.openrouter_api_key)
#     response = provider.generate_content(prompt)
# The rest of the module stays unchanged.

_PROMPT_TEMPLATE = """\
You are an expert judge for short-form video content (YouTube Shorts, TikTok, Reels).

Rate the following moment for its potential as a STRONG short-form hook.

CRITERIA for a strong hook:
- Immediate intrigue (viewer wants to know what happens next)
- Visible payoff or action (something concrete happens, not just setup)
- Emotionally charged (conflict, surprise, tension, humor, shock)
- Fits short-form (first 3 seconds must grab attention)

Weak hooks:
- Mid-monologue (no clear start point for a short)
- Pure exposition with no payoff
- Generic narration without emotional stakes
- Requires prior context to understand

Rate from 0.0 (terrible) to 1.0 (elite viral hook).

Return ONLY valid JSON, no commentary:
{{"score": <float 0.0-1.0>, "reason": "<short Russian reason, 1 sentence>"}}
"""

_SYSTEM_LANG_NOTE = (
    "Note: respond with a Russian-language reason if the moment text is in Russian."
)


def _parse_verdict(text: str) -> dict:
    """Extract a verdict dict from LLM response text.

    Handles plain JSON, fenced ```json blocks, and arbitrary surrounding text.
    Returns a safe default dict on any parse failure so the pipeline never breaks.
    """
    if not text:
        return {"score": 0.5, "reason": "empty response"}

    cleaned = text.strip()

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Locate first JSON object
    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        cleaned = cleaned[brace_start : brace_end + 1]

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return {"score": 0.5, "reason": "unparseable LLM response"}

    if not isinstance(data, dict):
        return {"score": 0.5, "reason": "unexpected non-dict response"}

    # Ensure score is a float in [0, 1]
    raw_score = data.get("score", 0.5)
    try:
        score = max(0.0, min(1.0, float(raw_score)))
    except (TypeError, ValueError):
        score = 0.5

    reason = str(data.get("reason", "")).strip() or "no reason provided"

    return {"score": score, "reason": reason}


def auto_threshold(settings) -> float:
    """Return the current hook min-score threshold from settings."""
    return getattr(settings, "hook_min_score", 0.5)


@dataclass(frozen=True, slots=True)
class HookVerdict:
    """Result of a hook quality evaluation."""

    score: float  # 0.0 – 1.0
    reason: str
    ok: bool  # True when score >= hook_min_score


class HookJudge:
    """LLM-powered hook quality judge.

    Usage::

        judge = HookJudge(settings)
        verdict = judge.score("This moment text...")
        if not verdict.ok:
            log.warning("Weak hook: %s", verdict.reason)
    """

    def __init__(self, settings) -> None:
        self._settings = settings
        self._min_score: float = auto_threshold(settings)

    def score(self, moment_text: str, context: dict | None = None) -> HookVerdict:
        """Score a single moment for hook quality.

        Returns a neutral (ok=True, score=0.5) verdict on any failure so the
        pipeline is never interrupted.
        """
        moment_text = (moment_text or "")[:600]
        if not moment_text.strip():
            return HookVerdict(0.5, "empty moment text — neutral", True)

        prompt = (
            _PROMPT_TEMPLATE
            + "\n"
            + _SYSTEM_LANG_NOTE
            + "\n\n--- MOMENT TEXT ---\n"
            + moment_text
        )

        try:
            # Lazy import: only pulls in google-genai when judge is actually used
            from shorts_clipper.providers.gemini import GeminiProvider

            provider = GeminiProvider(api_key=self._settings.gemini_api_key)
            response = provider.generate_content(prompt)
            raw_text = response.text
        except Exception as exc:
            log.warning("Hook judge LLM call failed: %s", exc)
            return HookVerdict(0.5, f"llm unavailable — neutral", True)

        parsed = _parse_verdict(raw_text)
        score = parsed["score"]
        reason = parsed["reason"]
        ok = score >= self._min_score

        return HookVerdict(score=score, reason=reason, ok=ok)

    def rank_moments(
        self, moments: list[dict], text_fn
    ) -> list[dict]:
        """Rank moments by hook score (descending).

        *moments* is a list of dicts, each containing at least an ``id`` key
        (or whatever key you choose — dicts are passed through as-is).
        *text_fn* is called as ``text_fn(moment) -> str`` to extract the
        moment's text for scoring.

        Returns a **new** list of dicts, each augmented with ``hook_score``,
        ``hook_reason``, ``hook_ok``.  Ties and neutral entries preserve the
        original insertion order (deterministic).
        """
        scored: list[tuple[int, float, HookVerdict, dict]] = []
        for idx, moment in enumerate(moments):
            try:
                text = text_fn(moment)
                verdict = self.score(text)
            except Exception as exc:
                log.warning("Hook judge failed for moment %d: %s", idx, exc)
                verdict = HookVerdict(0.5, "scoring failed — neutral", True)
            scored.append((idx, -verdict.score, verdict, moment))

        scored.sort(key=lambda t: (t[1], t[0]))

        result: list[dict] = []
        for _orig_idx, _neg_score, verdict, moment in scored:
            enriched = {
                **moment,
                "hook_score": verdict.score,
                "hook_reason": verdict.reason,
                "hook_ok": verdict.ok,
            }
            result.append(enriched)

        return result
