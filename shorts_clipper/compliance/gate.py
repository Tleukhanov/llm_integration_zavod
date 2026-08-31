"""Compliance gate — orchestrates rule-based + optional LLM review.

The gate never crashes the pipeline.  Any unexpected failure degrades to
``level="review"`` so publishing can continue with a warning.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from shorts_clipper.core.exceptions import ShortsClipperError
from shorts_clipper.core.settings import Settings

from .rules import ComplianceRules

log = logging.getLogger(__name__)


class ComplianceBlocked(ShortsClipperError):
    """Raised when the compliance gate blocks a clip from being published."""


_LEVEL_ORDER: dict[str, int] = {"pass": 0, "review": 1, "block": 2}


def _worst(a: str, b: str) -> str:
    return a if _LEVEL_ORDER.get(a, 0) >= _LEVEL_ORDER.get(b, 0) else b


def _parse_llm_verdict(raw: str) -> dict:
    """Robustly extract a JSON verdict from LLM output.

    Pattern mirrors ``hook_judge._parse_verdict``.
    """
    if not raw:
        return {"violations": [], "level": "review"}

    cleaned = raw.strip()

    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    brace_start = cleaned.find("{")
    brace_end = cleaned.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        cleaned = cleaned[brace_start : brace_end + 1]

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return {"violations": [], "level": "review"}

    if not isinstance(data, dict):
        return {"violations": [], "level": "review"}

    level = str(data.get("level", "review")).strip().lower()
    if level not in ("pass", "review", "block"):
        level = "review"

    violations = data.get("violations", [])
    if not isinstance(violations, list):
        violations = [str(violations)]

    return {"violations": violations, "level": level}


@dataclass(frozen=True, slots=True)
class ComplianceVerdict:
    """Outcome of a compliance check."""

    passed: bool
    level: str  # "pass" | "review" | "block"
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)


_LLM_PROMPT_TEMPLATE = """\
You are a content compliance reviewer for short-form video platforms
(YouTube Shorts, TikTok, Instagram Reels).

Review the following clip metadata for EXPLICIT policy violations only:
- Banned claims (guaranteed profits, risk-free money)
- Prohibited finance/gambling/adult content
- Copyright bait or impersonation
- Deceptive advertising or undisclosed sponsorships

Do NOT flag:
- Legitimate financial discussion with proper disclaimers
- General entertainment or educational content
- Normal social media calls-to-action

TITLE: {title}
DESCRIPTION: {description}
CTA: {cta}

Return ONLY valid JSON:
{{"violations": ["..."], "level": "pass"|"review"|"block"}}
"""


class ComplianceGate:
    """Pre-publish compliance checker combining rules + optional LLM review.

    Parameters are read from ``settings`` at init time.  The gate is
    **cheap to construct** (no I/O in ``__init__``) and safe to call even
    when compliance is disabled — in that case it always returns PASS.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled: bool = getattr(settings, "compliance_enabled", True)
        self._llm_enabled: bool = getattr(settings, "compliance_llm", True)
        self._finance_strict: bool = getattr(settings, "compliance_finance_strict", False)
        self._report_dir: Path = Path(
            getattr(settings, "compliance_report_dir", Path("outputs/compliance"))
        )
        self._gemini_key: str | None = getattr(settings, "gemini_api_key", None)
        self._rules = ComplianceRules()
        self._settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        title: str,
        description: str,
        cta_text: str = "",
    ) -> ComplianceVerdict:
        """Run the full compliance pipeline and return a verdict."""
        if not self._enabled:
            log.debug("Compliance gate disabled — PASS unconditionally")
            return ComplianceVerdict(passed=True, level="pass", checks={"disabled": True})

        combined_text = f"{title}\n{description}\n{cta_text}"
        checks: dict = {}
        reasons: list[str] = []
        worst_level = "pass"

        # ── 1. Rules-based scan ──────────────────────────────────────
        hard_violations = self._rules.scan_text(combined_text)
        checks["rules_hard_block"] = hard_violations
        if hard_violations:
            worst_level = "block"
            reasons.extend(hard_violations)

        # ── 2. Finance disclaimer check ──────────────────────────────
        is_finance = self._rules.check_finance(combined_text)
        checks["finance_topic"] = is_finance
        if is_finance:
            has_disc = self._rules.has_disclaimer(combined_text)
            checks["disclaimer_present"] = has_disc
            if not has_disc:
                if self._finance_strict:
                    worst_level = _worst(worst_level, "block")
                    reasons.append("finance_topic_detected: missing required disclaimer (strict mode)")
                else:
                    worst_level = _worst(worst_level, "review")
                    reasons.append("finance_topic_detected: missing disclaimer (non-strict → review)")

        # ── 3. Ad disclosure check ───────────────────────────────────
        has_affiliate = self._rules.has_affiliate_link(combined_text)
        checks["affiliate_link"] = has_affiliate
        if has_affiliate:
            has_ad = self._rules.has_ad_disclosure(combined_text)
            checks["ad_disclosure"] = has_ad
            if not has_ad:
                worst_level = _worst(worst_level, "review")
                reasons.append("affiliate_link_detected: missing ad disclosure")

        # ── 4. LLM reviewer ──────────────────────────────────────────
        llm_level = "pass"
        if self._llm_enabled and self._gemini_key:
            llm_level, llm_reasons, llm_checks = self._run_llm_review(
                title, description, cta_text
            )
            checks["llm"] = llm_checks
            if llm_level == "block":
                worst_level = "block"
                reasons.extend(llm_reasons)
            elif llm_level == "review":
                worst_level = _worst(worst_level, "review")
                reasons.extend(llm_reasons)
        elif self._llm_enabled and not self._gemini_key:
            checks["llm"] = "skipped: no gemini key"

        final_level = worst_level
        passed = final_level != "block"

        return ComplianceVerdict(
            passed=passed,
            level=final_level,
            reasons=reasons,
            checks=checks,
        )

    def suggest_description(
        self,
        base_description: str,
        is_finance: bool,
        affiliate_enabled: bool,
    ) -> tuple[str, str]:
        """Build a SAFE description by appending required disclaimers.

        Returns ``(safe_description, note)`` where *note* is a short
        human-readable summary of what was added (empty string = nothing
        was appended).
        """
        additions: list[str] = []

        if affiliate_enabled and not self._rules.has_ad_disclosure(base_description):
            additions.append("#реклама")

        if is_finance and not self._rules.has_disclaimer(base_description):
            additions.append(
                "Материал носит информационный характер и не является "
                "индивидуальной инвестиционной рекомендацией. "
                "Торговля на финансовых рынках сопряжена с высоким риском потери капитала."
            )

        if not additions:
            return base_description, ""

        suffix = "\n\n" + "\n".join(additions)
        note = f"appended {len(additions)} disclaimer(s)"
        return base_description + suffix, note

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_llm_review(
        self, title: str, description: str, cta_text: str
    ) -> tuple[str, list[str], dict]:
        """Call Gemini for a second-opinion compliance review.

        Returns ``(level, reasons, checks_dict)``.  On any error,
        degrades to ``"review"`` so the pipeline never crashes.
        """
        try:
            from shorts_clipper.providers.gemini import GeminiProvider

            provider = GeminiProvider(api_key=self._gemini_key)
            prompt = _LLM_PROMPT_TEMPLATE.format(
                title=title, description=description, cta=cta_text
            )
            t0 = time.monotonic()
            response = provider.generate_content(prompt, max_retries=2, initial_delay=2.0)
            elapsed = time.monotonic() - t0

            raw_text = response.text
            verdict = _parse_llm_verdict(raw_text)

            checks = {
                "level": verdict["level"],
                "violations": verdict["violations"],
                "latency_s": round(elapsed, 2),
            }

            reasons: list[str] = []
            for v in verdict.get("violations", []):
                reasons.append(f"llm: {v}")

            return verdict["level"], reasons, checks

        except Exception as exc:
            log.warning("Compliance LLM review failed (degrading to REVIEW): %s", exc)
            return "review", [f"llm_unavailable: {exc}"], {"error": str(exc)}

    def write_block_report(
        self,
        video_path: Path | str | None,
        title: str,
        description: str,
        verdict: ComplianceVerdict,
    ) -> Path:
        """Persist a BLOCK report to disk.  Returns the report path."""
        self._report_dir.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", title)[:60].strip("_")
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"blocked_{ts}_{slug}.json"
        report_path = self._report_dir / filename

        report = {
            "timestamp": datetime.now(UTC).isoformat(),
            "video_path": str(video_path) if video_path else None,
            "title": title,
            "description": description,
            "level": verdict.level,
            "reasons": verdict.reasons,
            "checks": verdict.checks,
        }

        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            log.info("Compliance block report written: %s", report_path)
        except Exception as exc:
            log.error("Failed to write compliance report: %s", exc)

        return report_path
