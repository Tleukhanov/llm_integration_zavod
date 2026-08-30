"""Gemini AI highlight provider.

Uses Gemini 2.5 Pro (thinking model) to select the best clip window.
Implements HighlightProvider ABC so it can be swapped for any other LLM.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Sequence
from typing import Any

from shorts_clipper.core.exceptions import ProviderError
from shorts_clipper.core.models import ClipWindow, TranscriptSegment
from shorts_clipper.providers.base import HighlightProvider
from shorts_clipper.transcription.formatting import format_transcript

log = logging.getLogger(__name__)


class GeminiQuotaExhaustedError(Exception):
    """Exception raised when Gemini API quota is exhausted."""

    pass


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
SYSTEM ROLE

You are Scout.

You are not a clip extractor.

You are not a transcript analyzer.

You are not an educational content curator.

You are an Attention Prediction Engine.

Your sole objective is to predict whether a human will voluntarily continue watching a short-form video.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MISSION

Do not maximize:

* clip count
* upload volume
* information density
* educational value
* transcript quality

Maximize:

* retention
* curiosity
* emotional engagement
* tension
* payoff
* shareability

Success:

Viewer stops scrolling.

Failure:

Viewer swipes away.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CORE TRUTH

Humans do not watch information.

Humans watch:

* curiosity
* conflict
* surprise
* emotion
* embarrassment
* tension
* risk
* status shifts
* reactions
* reveals
* victories
* failures

Do not search for interesting topics.

Search for interesting moments.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CANDIDATE DISCOVERY

Analyze the transcript below.

{transcript}

Aggressively search for:

* arguments
* disagreements
* debates
* emotional reactions
* confessions
* shocking statements
* dramatic reveals
* failures
* victories
* audience reactions
* laughter
* tension followed by payoff
* social risk
* unexpected outcomes
* impossible claims
* controversial opinions
* emotional breakthroughs

Avoid:

* introductions
* greetings
* context building
* setup phases
* generic explanations
* educational monologues
* static conversations
* charts
* statistics
* filler dialogue
* low-energy discussions
* long pauses

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOOK ANALYSIS

The first 3 seconds determine survival.

Ask:

Would a complete stranger stop scrolling?

Strong hooks:

* conflict already in progress
* surprising statement
* shocking visual
* emotional reaction
* impossible claim
* unresolved question
* tension already active

Weak hooks:

* greetings
* introductions
* slow context
* background explanation
* generic narration

Hook Score:
0-20

Any candidate with Hook Score below 15 is automatically rejected.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ATTENTION SCORING

Score every candidate.

1. Curiosity Gap (0-20)

Does the viewer immediately want an answer?

2. Emotional Intensity (0-20)

Does somebody visibly care?

3. Emotional Delta (0-20)

Does emotional state change?

Examples:

calm → angry
confident → embarrassed
skeptical → convinced
losing → winning
serious → laughing

Static emotion scores low.

Emotional transformation scores high.

4. Tension (0-20)

Is there:

* uncertainty
* conflict
* risk
* anticipation
* pressure
* embarrassment
* disagreement
* danger

5. Payoff (0-20)

Does the clip deliver something satisfying?

6. Context Independence (0-20)

Can a new viewer instantly understand it?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EXPLANATION DENSITY PENALTY

Subtract 0-20 points.

Penalize:

* teaching
* explaining
* lecturing
* reasoning chains
* information dumps
* slow breakdowns
* long setup before payoff

High explanation density is strongly negative.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SHAREABILITY TEST

Ask:

Would somebody send this to a friend?

Would somebody quote this?

Would somebody argue about this?

Would somebody comment on this?

Would somebody replay this?

If no:

Reduce score.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POSTABILITY TEST

Ask:

Would I upload this on my own channel?

Would I stop scrolling for this?

Would I watch until the end?

Would this outperform an average clip in the niche?

If no:

Reduce score significantly.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BONUS SIGNALS

Add positive weight:

* visible reactions
* audience reactions
* laughter
* surprise
* shock
* conflict
* challenge-response dynamics
* emotional escalation
* dramatic reversal
* unexpected outcome
* powerful one-liner
* confession
* social embarrassment
* high emotional stakes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HARD REJECTS

Reject immediately if:

* requires prior context
* starts slowly
* no emotional change
* no payoff
* mostly explanation
* mostly educational
* mostly statistics
* mostly charts
* mostly setup
* feels like filler
* completely predictable

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MULTI-STAGE EVALUATION

Stage 1:
Generate 10 candidates.

Stage 2:
Score every candidate.

Stage 3:
Keep only Top 3.

Stage 4:
Perform head-to-head comparison.

Ask:

Which clip would generate the highest retention?

Which clip would generate the most comments?

Which clip would generate the most replays?

Which clip would generate the most shares?

Select one winner.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT FORMAT

Return ONLY valid JSON. No markdown. No commentary.
Return the single winning candidate:

{{
  "timestamp_start": <float seconds>,
  "timestamp_end": <float seconds>,
  "layout": "<crop_center | crop_left | crop_right>",
  "hook_score": 0,
  "curiosity_gap": 0,
  "emotional_intensity": 0,
  "emotional_delta": 0,
  "tension": 0,
  "payoff": 0,
  "context_independence": 0,
  "explanation_penalty": 0,
  "final_score": 0,
  "reasoning": "..."
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

QUALITY THRESHOLD

90-100 = Elite
85-89 = Publish
70-84 = Borderline
Below 70 = Reject

If no clip reaches 85:

RETURN:

NO_CLIP_FOUND

Publishing nothing is better than publishing weak content.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINAL OBJECTIVE

Think like:

* a creator
* a viewer
* a recommendation algorithm

Do not find clips.

Find moments worth watching.

If uncertain:

Reject.

If mediocre:

Reject.

If average:

Reject.

Attention is scarce.

Protect it.
"""


_MULTI_PROMPT_TEMPLATE = """\
SYSTEM ROLE

You are Scout.

You are not a clip extractor.

You are not a transcript analyzer.

You are not an educational content curator.

You are an Attention Prediction Engine.

Your sole objective is to predict whether a human will voluntarily continue watching a short-form video.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MISSION

Do not maximize:

* clip count
* upload volume
* information density
* educational value
* transcript quality

Maximize:

* retention
* curiosity
* emotional engagement
* tension
* payoff
* shareability

Success:

Viewer stops scrolling.

Failure:

Viewer swipes away.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CORE TRUTH

Humans do not watch information.

Humans watch:

* curiosity
* conflict
* surprise
* emotion
* embarrassment
* tension
* risk
* status shifts
* reactions
* reveals
* victories
* failures

Do not search for interesting topics.

Search for interesting moments.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CANDIDATE DISCOVERY

Analyze the transcript below.

{transcript}

Generate up to {count} candidate windows (generate 10 candidate windows if {count} is large, else up to {count}).
Do not assume the first candidate is best.

Aggressively search for:

* arguments
* disagreements
* debates
* emotional reactions
* confessions
* shocking statements
* dramatic reveals
* failures
* victories
* audience reactions
* laughter
* tension followed by payoff
* social risk
* unexpected outcomes
* impossible claims
* controversial opinions
* emotional breakthroughs

Avoid:

* introductions
* greetings
* context building
* setup phases
* generic explanations
* educational monologues
* static conversations
* charts
* statistics
* filler dialogue
* low-energy discussions
* long pauses

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOOK ANALYSIS

The first 3 seconds determine survival.

Ask:

Would a complete stranger stop scrolling?

Strong hooks:

* conflict already in progress
* surprising statement
* shocking visual
* emotional reaction
* impossible claim
* unresolved question
* tension already active

Weak hooks:

* greetings
* introductions
* slow context
* background explanation
* generic narration

Hook Score:
0-20

Any candidate with Hook Score below 15 is automatically rejected.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ATTENTION SCORING

Score every candidate.

1. Curiosity Gap (0-20)

Does the viewer immediately want an answer?

2. Emotional Intensity (0-20)

Does somebody visibly care?

3. Emotional Delta (0-20)

Does emotional state change?

Examples:

calm → angry
confident → embarrassed
skeptical → convinced
losing → winning
serious → laughing

Static emotion scores low.

Emotional transformation scores high.

4. Tension (0-20)

Is there:

* uncertainty
* conflict
* risk
* anticipation
* pressure
* embarrassment
* disagreement
* danger

5. Payoff (0-20)

Does the clip deliver something satisfying?

6. Context Independence (0-20)

Can a new viewer instantly understand it?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EXPLANATION DENSITY PENALTY

Subtract 0-20 points.

Penalize:

* teaching
* explaining
* lecturing
* reasoning chains
* information dumps
* slow breakdowns
* long setup before payoff

High explanation density is strongly negative.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SHAREABILITY TEST

Ask:

Would somebody send this to a friend?

Would somebody quote this?

Would somebody argue about this?

Would somebody comment on this?

Would somebody replay this?

If no:

Reduce score.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POSTABILITY TEST

Ask:

Would I upload this on my own channel?

Would I stop scrolling for this?

Would I watch until the end?

Would this outperform an average clip in the niche?

If no:

Reduce score significantly.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BONUS SIGNALS

Add positive weight:

* visible reactions
* audience reactions
* laughter
* surprise
* shock
* conflict
* challenge-response dynamics
* emotional escalation
* dramatic reversal
* unexpected outcome
* powerful one-liner
* confession
* social embarrassment
* high emotional stakes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HARD REJECTS

Reject immediately if:

* requires prior context
* starts slowly
* no emotional change
* no payoff
* mostly explanation
* mostly educational
* mostly statistics
* mostly charts
* mostly setup
* feels like filler
* completely predictable

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MULTI-STAGE EVALUATION

Stage 1:
Generate 10 candidates.

Stage 2:
Score every candidate.

Stage 3:
Keep only Top {count}.

Stage 4:
Perform head-to-head comparison.

Ask:

Which clip would generate the highest retention?

Which clip would generate the most comments?

Which clip would generate the most replays?

Which clip would generate the most shares?

Select up to {count} winners.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT FORMAT

Return ONLY valid JSON as a list/array of objects. No markdown. No commentary.
Return the selected candidate(s):

[
  {{
    "timestamp_start": <float seconds>,
    "timestamp_end": <float seconds>,
    "layout": "<crop_center | crop_left | crop_right>",
    "title": "<short engaging viral title>",
    "hook_score": 0,
    "curiosity_gap": 0,
    "emotional_intensity": 0,
    "emotional_delta": 0,
    "tension": 0,
    "payoff": 0,
    "context_independence": 0,
    "explanation_penalty": 0,
    "final_score": 0,
    "reasoning": "..."
  }}
]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

QUALITY THRESHOLD

90-100 = Elite
85-89 = Publish
70-84 = Borderline
Below 70 = Reject

If no clip reaches 85:

RETURN:

NO_CLIP_FOUND

Publishing nothing is better than publishing weak content.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINAL OBJECTIVE

Think like:

* a creator
* a viewer
* a recommendation algorithm

Do not find clips.

Find moments worth watching.

If uncertain:

Reject.

If mediocre:

Reject.

If average:

Reject.

Attention is scarce.

Protect it.
"""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class GeminiProvider(HighlightProvider):
    """Uses Gemini 2.5 Flash to select the best clip window from a transcript."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "gemini-3.1-flash-lite",
        fallback_window: tuple[float, float] = (60.0, 95.0),
        fallback_layout: str = "crop_center",
    ) -> None:
        self._model = model
        self._fallback_window = fallback_window
        self._fallback_layout = fallback_layout

        from google import genai  # type: ignore[import]

        from shorts_clipper.core.settings import Settings

        settings = Settings.from_env()
        self._client = genai.Client(
            api_key=api_key or settings.gemini_api_key or os.environ.get("GEMINI_API_KEY")
        )

    def generate_content(
        self, contents: any, max_retries: int = 5, initial_delay: float = 5.0, **kwargs
    ) -> any:
        """Call generate_content with exponential backoff on transient errors."""
        import time

        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            try:
                return self._client.models.generate_content(
                    model=self._model, contents=contents, **kwargs
                )
            except Exception as exc:
                exc_str = str(exc)
                is_quota = "quota exceeded" in exc_str.lower() or "out of quota" in exc_str.lower()
                is_rate_limit = (
                    "429" in exc_str
                    or "RESOURCE_EXHAUSTED" in exc_str
                    or getattr(exc, "code", None) == 429
                    or getattr(exc, "status_code", None) == 429
                )
                if is_rate_limit:
                    log.warning("Gemini Rate Limit (429) hit. Will retry.")
                    import re

                    match = re.search(r"retry in (\d+(?:\.\d+)?)s", exc_str)
                    if match:
                        delay = float(match.group(1)) + 5.0
                    else:
                        delay = 65.0
                elif is_quota:
                    log.error("GEMINI QUOTA EXHAUSTED\nSWITCHING TO FALLBACK")
                    raise GeminiQuotaExhaustedError(exc_str) from exc

                is_auth_error = (
                    "400" in exc_str
                    or "INVALID_ARGUMENT" in exc_str
                    or "API_KEY_INVALID" in exc_str
                    or "API key not valid" in exc_str
                )
                if is_auth_error:
                    log.error("GEMINI API KEY INVALID OR MISSING: %s", exc_str)
                    raise ProviderError(f"Gemini API key is invalid or missing: {exc_str}") from exc

                if attempt == max_retries:
                    log.error(
                        "Gemini generate_content failed after %d attempts: %s",
                        max_retries,
                        exc,
                    )
                    raise
                log.warning(
                    "Gemini API call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt,
                    max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)

    def select_clip(
        self, segments: Sequence[TranscriptSegment], allow_fallback: bool = True
    ) -> ClipWindow:
        """Return the best clip window. Falls back gracefully on any failure."""
        transcript_text = format_transcript(segments)
        prompt = _PROMPT_TEMPLATE.format(transcript=transcript_text)

        log.info("\n--- CONSULTING %s ---", self._model)
        try:
            response = self.generate_content(prompt)
            raw = response.text.strip()
            log.debug("Gemini raw response: %r", raw)

            if "NO_CLIP_FOUND" in raw:
                raise ProviderError(
                    "Gemini returned NO_CLIP_FOUND. No candidates reached the threshold."
                )

            # Strip markdown code fences if present
            json_str = raw
            json_match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()

            try:
                data = json.loads(json_str)
                if not isinstance(data, dict):
                    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                        data = data[0]
                    else:
                        raise ValueError(f"Expected dict, got {type(data)}")
            except Exception as exc:
                raise ProviderError(f"Gemini returned unparseable JSON: {raw!r}") from exc

            start = float(data.get("timestamp_start", data.get("start", 0.0)))
            end = float(data.get("timestamp_end", data.get("end", 0.0)))
            layout = str(data.get("layout", self._fallback_layout)).strip()
            score = int(data.get("final_score", data.get("virality_score", 0)))

            if score < 85:
                raise ProviderError(
                    f"Selected clip score ({score}) is below the minimum threshold of 85."
                )

            # Enforce duration bounds — if Gemini goes out of spec, clamp it
            duration = end - start
            if duration < 30:
                log.warning(
                    "Gemini returned %.1fs clip (< 30s). Extending end to %.1fs.",
                    duration,
                    start + 35,
                )
                end = start + 35
            elif duration > 65:
                log.warning(
                    "Gemini returned %.1fs clip (> 65s). Trimming to %.1fs.",
                    duration,
                    start + 55,
                )
                end = start + 55

            log.info(
                "✅ Gemini selected: %.1fs → %.1fs [%s] | score=%d | curiosity=%s",
                start,
                end,
                layout,
                score,
                data.get("curiosity_level", "?"),
            )
            log.info(
                "   Tension: %s | Payoff: %s",
                data.get("tension_level", "?"),
                data.get("payoff_level", "?"),
            )
            log.info("   Why:  %s", data.get("reason", ""))

            window = ClipWindow(start=start, end=end)
            return window, layout  # type: ignore[return-value]

        except Exception as exc:  # noqa: BLE001
            if not allow_fallback:
                raise
            fb_start, fb_end = self._fallback_window
            log.warning(
                "Gemini failed (%s). Using fallback %.1fs→%.1fs [%s]",
                exc,
                fb_start,
                fb_end,
                self._fallback_layout,
            )
            return ClipWindow(start=fb_start, end=fb_end), self._fallback_layout  # type: ignore[return-value]

    def select_clip_raw(
        self, segments: Sequence[TranscriptSegment], allow_fallback: bool = True
    ) -> tuple[ClipWindow, str]:
        """Same as select_clip but always returns (ClipWindow, layout_str)."""
        result = self.select_clip(segments, allow_fallback=allow_fallback)
        if isinstance(result, tuple):
            return result
        return result, self._fallback_layout

    def select_multiple_clips(
        self,
        segments: Sequence[TranscriptSegment],
        count: int = 1,
        allow_fallback: bool = True,
    ) -> list[tuple[ClipWindow, str]]:
        """Return the best clip windows (up to count) from the transcript."""
        if count <= 1:
            win, lay = self.select_clip_raw(segments, allow_fallback=allow_fallback)
            return [(win, lay)]

        transcript_text = format_transcript(segments)
        prompt = _MULTI_PROMPT_TEMPLATE.format(count=count, transcript=transcript_text)

        log.info("\n--- CONSULTING %s FOR MULTIPLE CLIPS (up to %d) ---", self._model, count)
        try:
            response = self.generate_content(prompt)
            raw = response.text.strip()
            log.debug("Gemini raw response: %r", raw)

            if "NO_CLIP_FOUND" in raw:
                raise ProviderError(
                    "Gemini returned NO_CLIP_FOUND. No high-quality clips selected."
                )

            json_str = raw
            json_match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()

            try:
                items = json.loads(json_str)
            except json.JSONDecodeError as exc:
                raise ProviderError(f"Gemini returned unparseable JSON array: {raw!r}") from exc

            if not isinstance(items, list):
                if isinstance(items, dict):
                    extracted = None
                    for val in items.values():
                        if isinstance(val, list):
                            extracted = val
                            break
                    items = extracted if extracted is not None else [items]
                else:
                    raise ProviderError(f"Gemini response did not return a JSON list: {raw!r}")

            results: list[tuple[ClipWindow, str]] = []
            for item in items[:count]:
                try:
                    start = float(item.get("timestamp_start", item.get("start", 0.0)))
                    end = float(item.get("timestamp_end", item.get("end", 0.0)))
                    layout = str(item.get("layout", self._fallback_layout)).strip()
                    score = int(item.get("final_score", item.get("virality_score", 0)))

                    if score < 85:
                        log.warning("Skipping selected clip with low score: %d (< 85)", score)
                        continue

                    # Clamp duration
                    duration = end - start
                    if duration < 30:
                        end = start + 35
                    elif duration > 65:
                        end = start + 55

                    log.info(
                        "✅ Gemini selected candidate: %.1fs → %.1fs [%s] | score=%d | curiosity=%s",
                        start,
                        end,
                        layout,
                        score,
                        item.get("curiosity_level", "?"),
                    )
                    results.append((ClipWindow(start=start, end=end), layout))
                except Exception as item_exc:
                    log.warning("Failed parsing individual clip candidate: %s", item_exc)

            if not results:
                raise ProviderError("No high-quality clips selected by Gemini.")

            return results

        except Exception as exc:
            if not allow_fallback:
                raise
            log.warning("Gemini multi-clip selection failed (%s). Using fallback.", exc)
            win, lay = self.select_clip_raw(segments, allow_fallback=allow_fallback)
            return [(win, lay)]

    def select_multiple_clips_detailed(
        self, segments: Sequence[TranscriptSegment], count: int = 1
    ) -> list[dict[str, Any]]:
        """Return a detailed list of dictionaries representing the best clips from Gemini."""
        transcript_text = format_transcript(segments)
        prompt = _MULTI_PROMPT_TEMPLATE.format(count=count, transcript=transcript_text)
        try:
            response = self.generate_content(prompt)
            raw = response.text.strip()

            if "NO_CLIP_FOUND" in raw:
                return []

            json_str = raw
            json_match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()

            try:
                items = json.loads(json_str)
                if not isinstance(items, list):
                    if isinstance(items, dict):
                        extracted = None
                        for val in items.values():
                            if isinstance(val, list):
                                extracted = val
                                break
                        items = extracted if extracted is not None else [items]
                    else:
                        items = []

                if not items:
                    raise ValueError("Parsed JSON resulted in an empty items list")
            except (json.JSONDecodeError, ValueError) as exc:
                log.info("[GEMINI] JSON parse failed, attempting timestamp extraction")
                start, end = None, None

                start_match = re.search(r'"start":\s*"?(\d+\.?\d*)"?', raw)
                end_match = re.search(r'"end":\s*"?(\d+\.?\d*)"?', raw)
                if start_match and end_match:
                    start = float(start_match.group(1))
                    end = float(end_match.group(1))
                else:
                    start_match = re.search(r'"timestamp_start":\s*"?(\d+\.?\d*)"?', raw)
                    end_match = re.search(r'"timestamp_end":\s*"?(\d+\.?\d*)"?', raw)
                    if start_match and end_match:
                        start = float(start_match.group(1))
                        end = float(end_match.group(1))
                    else:
                        range_match = re.search(r"(\d+\.?\d*)\s*(?:→|-|to)\s*(\d+\.?\d*)", raw)
                        if range_match:
                            start = float(range_match.group(1))
                            end = float(range_match.group(2))

                if start is not None and end is not None and start < end:
                    log.info(
                        "[GEMINI] Extracted timestamps: start=%s end=%s (saved second call)",
                        start,
                        end,
                    )
                    items = [
                        {
                            "timestamp_start": start,
                            "timestamp_end": end,
                            "layout": self._fallback_layout,
                            "final_score": 85,
                            "reasoning": "Extracted via regex fallback",
                        }
                    ]
                else:
                    log.info("[GEMINI] Extraction failed, falling back to second call")
                    raise exc

            sanitized = []
            for item in items[:count]:
                start = float(item.get("timestamp_start", item.get("start", 0.0)))
                end = float(item.get("timestamp_end", item.get("end", 0.0)))
                layout = str(item.get("layout", self._fallback_layout)).strip()
                score = int(item.get("final_score", item.get("virality_score", 85)))
                reason = str(
                    item.get(
                        "reasoning",
                        item.get("reason", "Highly engaging interaction segment."),
                    )
                )

                # New keys
                hook = int(item.get("hook_score", 0))
                curiosity = int(item.get("curiosity_gap", item.get("curiosity_level", 0)))
                emotion = int(item.get("emotional_intensity", 0))
                delta = int(item.get("emotional_delta", 0))
                tension = int(item.get("tension", item.get("tension_level", 0)))
                payoff = int(item.get("payoff", item.get("payoff_level", 0)))
                independence = int(item.get("context_independence", 0))
                penalty = int(item.get("explanation_penalty", 0))
                title = str(item.get("title", "Engaging Highlight")).strip()

                # Clamp duration
                duration = end - start
                if duration < 30:
                    end = start + 35
                elif duration > 65:
                    end = start + 55

                sanitized.append(
                    {
                        "start": start,
                        "end": end,
                        "layout": layout,
                        "virality_score": score,
                        "hook_score": hook,
                        "curiosity_gap": curiosity,
                        "emotional_intensity": emotion,
                        "emotional_delta": delta,
                        "tension": tension,
                        "payoff": payoff,
                        "context_independence": independence,
                        "explanation_penalty": penalty,
                        "title": title,
                        "reason": reason,
                        "duration": round(end - start, 1),
                    }
                )
            return sanitized
        except Exception as e:
            if isinstance(e, GeminiQuotaExhaustedError):
                raise
            log.warning("Detailed Gemini highlights fetch failed: %s. Using simple fallback.", e)
            try:
                simple_clips = self.select_multiple_clips(segments, count=count)
                return [
                    {
                        "start": win.start,
                        "end": win.end,
                        "layout": lay,
                        "virality_score": 85,
                        "hook_score": 15,
                        "curiosity_gap": 15,
                        "emotional_intensity": 15,
                        "emotional_delta": 15,
                        "tension": 15,
                        "payoff": 15,
                        "context_independence": 15,
                        "explanation_penalty": 0,
                        "title": f"Clip Highlight #{i + 1}",
                        "reason": "AI Selected engaging segment.",
                        "duration": round(win.end - win.start, 1),
                    }
                    for i, (win, lay) in enumerate(simple_clips)
                ]
            except Exception:
                return []

    def generate_clip_metadata(
        self,
        segments: Sequence[TranscriptSegment],
        source_title: str = "",
        source_channel: str = "",
    ) -> dict:
        """Generate production-grade viral metadata for a clip using Gemini.

        Generates 5 candidate titles, scores them, and selects the best.
        Generates a keyword-rich description with hashtags.

        Raises:
            ProviderError: If metadata generation fails completely.
        """
        transcript_text = format_transcript(segments)
        import datetime

        current_year = datetime.datetime.now().year
        prompt = f"""\
SYSTEM ROLE

You are a YouTube Shorts metadata strategist.

You have generated over 10,000 viral Shorts titles.

Your titles consistently outperform generic titles by 300-500%.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SOURCE CONTEXT
Original Video Title: {source_title}
Original Channel: {source_channel}
Current Year: {current_year}

Use this context to accurately align the short's title and description with the broader topic. Do not hallucinate dates or years relative to the current year. Do not just use generic words if specific entities are implied.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TRANSCRIPT

{transcript_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TITLE GENERATION

Generate exactly 5 candidate titles for this YouTube Shorts clip.

Rules:

* Maximum 65 characters (excluding hashtags)
* Do NOT summarize the content
* Do NOT use generic phrases like "Watch This" or "You Won't Believe"
* Maximize curiosity gap — make the viewer NEED to watch
* Use specificity — vague titles fail
* Use emotional triggers — surprise, conflict, shock, disbelief
* Use pattern interrupts — unexpected framing
* Include 1-2 relevant hashtags at the end (e.g. #shorts #tech)

Title quality spectrum:

TERRIBLE: "Tesla Hits Utility Box"
BAD: "Tesla Accident Caught on Camera"
MEDIOCRE: "This Tesla Crash Was Insane"
GOOD: "This Tesla Did Something Nobody Expected"
EXCELLENT: "The Moment This Tesla Driver Realized It Was Too Late"

TERRIBLE: "Market Share Analysis"
BAD: "Big Companies Control Everything"
MEDIOCRE: "Why One Company Dominates"
GOOD: "Why One Industry Controls Everything"
EXCELLENT: "The Industry That Secretly Runs Your Life"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TITLE SCORING

Score each candidate title (0-100):

* Curiosity (0-25): Does the viewer NEED to know what happens?
* Click potential (0-25): Would you click this in a feed of 100 Shorts?
* Emotional impact (0-25): Does it trigger an emotion (shock, curiosity, anger, awe)?
* Specificity (0-15): Is it specific enough to feel real, not generic?
* Shareability (0-10): Would someone send this title to a friend?

Select the highest-scoring title as the winner.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DESCRIPTION GENERATION

Generate a description (150-300 characters) that:

* Provides brief context about the clip content
* Contains 2-3 searchable keywords naturally embedded
* Contains 3-5 relevant hashtags
* Does NOT say "generated by" or "automatically created" or mention any tool
* Ends with a soft call-to-action (e.g. "Follow for more" or "Drop a comment")

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TAGS

Generate 5-8 relevant tags for YouTube search discovery.
Tags should be specific to the content, not generic filler.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT FORMAT

Return ONLY valid JSON. No markdown. No commentary.

{{
  "candidates": [
    {{"title": "...", "curiosity": 0, "click_potential": 0, "emotional_impact": 0, "specificity": 0, "shareability": 0, "total": 0}},
    {{"title": "...", "curiosity": 0, "click_potential": 0, "emotional_impact": 0, "specificity": 0, "shareability": 0, "total": 0}},
    {{"title": "...", "curiosity": 0, "click_potential": 0, "emotional_impact": 0, "specificity": 0, "shareability": 0, "total": 0}},
    {{"title": "...", "curiosity": 0, "click_potential": 0, "emotional_impact": 0, "specificity": 0, "shareability": 0, "total": 0}},
    {{"title": "...", "curiosity": 0, "click_potential": 0, "emotional_impact": 0, "specificity": 0, "shareability": 0, "total": 0}}
  ],
  "selected_title": "...",
  "description": "...",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]
}}
"""
        log.info("🧠 Generating production metadata (5 candidate titles)...")
        response = self.generate_content(prompt)
        raw = response.text.strip()

        json_str = raw
        json_match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()

        try:
            data = json.loads(json_str)
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict, got {type(data)}")
        except Exception as exc:
            log.error("Gemini returned unparseable metadata JSON: %r", raw)
            raise ProviderError(f"Metadata JSON parse failed: {raw!r}") from exc

        # Extract the winning title
        title = str(data.get("selected_title", "")).strip()

        # If no selected_title, pick highest-scoring candidate
        if not title and data.get("candidates"):
            candidates = data["candidates"]
            best = max(candidates, key=lambda c: c.get("total", 0))
            title = str(best.get("title", "")).strip()
            log.info(
                "📊 Title candidates scored: %s",
                [(c.get("title", "?")[:40], c.get("total", 0)) for c in candidates],
            )

        # Clean title: remove wrapping quotes
        if title.startswith('"') and title.endswith('"'):
            title = title[1:-1].strip()

        description = str(data.get("description", "")).strip()
        raw_tags = data.get("tags")
        if raw_tags is None:
            raw_tags = []
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]

        # Validate — refuse to return empty metadata
        if not title:
            raise ProviderError("Gemini returned empty title in metadata response.")
        if not description:
            raise ProviderError("Gemini returned empty description in metadata response.")
        if not tags:
            tags = ["shorts"]

        log.info("✅ Metadata generated — Title: %s", title)
        log.info(
            "   Description: %s",
            description[:80] + ("..." if len(description) > 80 else ""),
        )
        log.info("   Tags: %s", tags)

        return {
            "title": title,
            "description": description,
            "tags": tags,
        }
