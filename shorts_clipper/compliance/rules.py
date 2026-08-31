"""Regex-based compliance rules for clip titles, descriptions and CTAs.

The lists at the top of this file are the single source of truth for
blacklisted / required-disclaimer patterns.  Edit them directly — the
scanner recompiles nothing; ``re.compile`` is called once at import time.
"""

from __future__ import annotations

import re
from typing import Final

# ── Hard-block patterns (finance / gambling / adult verticals) ────────────
# Compiled with IGNORECASE so "КАЗИНО", "Casino", "CASINO" all match.
HARD_BLOCK_PATTERNS: Final[list[re.Pattern[str]]] = [
    # ── Finance / get-rich-quick (Russian) ──
    re.compile(r"гарантированн", re.I),
    re.compile(r"заработай", re.I),
    re.compile(r"быстр(?:ый|ого) заработок", re.I),
    re.compile(r"доход за день", re.I),
    re.compile(r"прибыль\b", re.I),
    re.compile(r"пассивн(?:ый|ого) доход", re.I),
    re.compile(r"бесплатн(?:о|ые|ый)\s+(?:деньг[иае]|денег)", re.I),
    re.compile(r"богате?й за", re.I),
    re.compile(r"миллион(?:ер)? за", re.I),
    re.compile(r"доход без вложений", re.I),
    re.compile(r"легк(?:ие|их|ий) деньг", re.I),
    re.compile(r"деньги из ниоткуда", re.I),
    re.compile(r"Ⲛагрузка\s*не\s*нужна", re.I),
    # ── Finance / get-rich-quick (English) ──
    re.compile(r"get rich (?:quick|fast)", re.I),
    re.compile(r"make money (?:fast|quick|easy|online)", re.I),
    re.compile(r"easy money", re.I),
    re.compile(r"passive income", re.I),
    re.compile(r"guaranteed profit", re.I),
    re.compile(r"risk[- ]free (?:profit|income|money)", re.I),
    re.compile(r"double your money", re.I),
    re.compile(r"100[%] profit", re.I),
    re.compile(r"earn \$\d", re.I),
    re.compile(r"no.?risk", re.I),
    # ── Gambling (Russian) ──
    re.compile(r"казин[оа]", re.I),
    re.compile(r"ставк[аиуе]", re.I),
    re.compile(r"букмекер", re.I),
    re.compile(r"беттинг", re.I),
    re.compile(r"aviator\s*casino", re.I),
    re.compile(r" слот[ыаие]", re.I),
    re.compile(r"джекпот", re.I),
    re.compile(r"рулетк", re.I),
    re.compile(r"покер[^а]", re.I),
    # ── Gambling (English) ──
    re.compile(r"\bcasino\b", re.I),
    re.compile(r"\bbetting\b", re.I),
    re.compile(r"\bslots?\b.*(?:win|real money)", re.I),
    re.compile(r"online gambling", re.I),
    re.compile(r"blackjack", re.I),
    re.compile(r"roulette", re.I),
    re.compile(r"jackpot", re.I),
    # ── Adult / NSFW (Russian) ──
    re.compile(r"\bсекс\b", re.I),
    re.compile(r"интим", re.I),
    re.compile(r"порн[оаие]", re.I),
    re.compile(r"эротик", re.I),
    # ── Adult / NSFW (English) ──
    re.compile(r"\bporn(?:o|ography)?\b", re.I),
    re.compile(r"\bxxx\b", re.I),
    re.compile(r"\bnude\b", re.I),
    re.compile(r"\bnaked\b", re.I),
    re.compile(r"\bnsfw\b", re.I),
]

# ── Finance / trading topic triggers ──────────────────────────────────────
_FinanceTopic: Final = re.compile(
    r"forex|трейдинг|инвестиц|trade[sr]?|инфопродукт|обучени[ея]|"
    r"курс(?:а|ов|ы)?|крипто(?:валют|монет)|bitcoin|crypto|"
    r"акци[яиу]|фондов|бирж|маркетмейк",
    re.I,
)

# ── Disclaimer presence indicators ────────────────────────────────────────
_DisclaimerPresent: Final = re.compile(
    r"риск|не является индивидуальной инвестиционной рекомендацией|"
    r"реклама|#реклама|#ad\b|партнёр|partner|disclaimer|"
    r"не является финансовой рекомендацией|invest.?at your own risk",
    re.I,
)

# ── Ad-disclosure required indicators ─────────────────────────────────────
_AdDisclosurePresent: Final = re.compile(
    r"реклама|#реклама|#ad\b|партнёр|partner|sponsored|affil(?:iate|liated)|коммерческ",
    re.I,
)

# ── Affiliate CTA / link indicators ───────────────────────────────────────
_AffiliateLinkPresent: Final = re.compile(
    r"https?://.*(?:partner|ref=|aff=|promo|utm_source)|"
    r"ссылк[аиуе]\s*(?:в|на)\s*описани|"
    r"кликните|нажмите|перейдите|follow.*link|link.*bio",
    re.I,
)


class ComplianceRules:
    """Rules-based scanner for compliance text checks."""

    def scan_text(self, text: str) -> list[str]:
        """Return list of violated hard-rule descriptions; empty = ok."""
        violations: list[str] = []
        for pat in HARD_BLOCK_PATTERNS:
            match = pat.search(text)
            if match:
                violations.append(
                    f"hard_block: matched '{match.group()}' with pattern {pat.pattern}"
                )
        return violations

    def check_finance(self, text: str) -> bool:
        """Return True if *text* discusses finance / trading / investing topics."""
        return bool(_FinanceTopic.search(text))

    def has_disclaimer(self, text: str) -> bool:
        """Return True if *text* already contains a compliance disclaimer."""
        return bool(_DisclaimerPresent.search(text))

    def has_ad_disclosure(self, text: str) -> bool:
        """Return True if *text* contains an ad/affiliate disclosure."""
        return bool(_AdDisclosurePresent.search(text))

    def has_affiliate_link(self, text: str) -> bool:
        """Return True if *text* contains an affiliate link or CTA."""
        return bool(_AffiliateLinkPresent.search(text))
