"""Config-driven affiliate partners.

Partners are loaded from a JSON file (see ``Settings.affiliate_partners_path``),
selected deterministically against the clip transcript, and their offer is
appended to the generated metadata description.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AffiliatePartner:
    id: str
    name: str
    link_en: str
    link_ru: str | None = None
    banner_path: str | None = None
    tag: str = "#ad"
    match_keywords: tuple[str, ...] = ()
    enabled: bool = True

    def caption_text(self, language: str) -> str:
        return self.name

    def link(self, language: str) -> str:
        if language.lower().startswith("ru") and self.link_ru:
            return self.link_ru
        return self.link_en


def load_affiliate_partners(settings) -> list[AffiliatePartner]:
    """Load partners from JSON. Missing/corrupt config degrades to an empty list."""
    path = Path(settings.affiliate_partners_path)
    if not path.exists():
        log.warning("Affiliate partners file not found: %s", path)
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to parse affiliate partners file %s: %s", path, exc)
        return []

    if not isinstance(raw, list):
        log.warning("Affiliate partners file %s must contain a JSON list", path)
        return []

    partners: list[AffiliatePartner] = []
    for item in raw:
        if not isinstance(item, dict):
            log.warning("Skipping invalid affiliate partner entry: %r", item)
            continue
        try:
            raw_enabled = item.get("enabled", True)
            if isinstance(raw_enabled, bool):
                enabled = raw_enabled
            else:
                enabled = str(raw_enabled).lower() in {"1", "true", "yes", "on"}
            partners.append(
                AffiliatePartner(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    link_en=str(item["link_en"]),
                    link_ru=item.get("link_ru"),
                    banner_path=item.get("banner_path"),
                    tag=str(item.get("tag", "#ad")),
                    match_keywords=tuple(str(k) for k in (item.get("match_keywords") or ())),
                    enabled=enabled,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Skipping invalid affiliate partner entry %r: %s", item, exc)
    return partners


def select_affiliate_transcript_text(segments) -> str:
    """Build a searchable lowercased text blob from transcript segments."""
    return " ".join(getattr(s, "text", "") for s in segments).lower()


def select_affiliate_partner(
    partners: list[AffiliatePartner],
    *,
    transcript_text: str = "",
    language: str = "en",
    round_robin_index: int = 0,
) -> AffiliatePartner | None:
    """Pick the first enabled partner whose keywords appear in the transcript.

    Falls back to a deterministic round-robin pick when nothing matches.
    Returns None when there are no (enabled) partners.
    """
    enabled = [p for p in partners if p.enabled]
    if not enabled:
        return None

    haystack = transcript_text.lower()
    for partner in enabled:
        if partner.match_keywords and all(
            keyword.lower() in haystack for keyword in partner.match_keywords
        ):
            return partner

    return enabled[round_robin_index % len(enabled)]


def import_from_research(
    research_path: str | Path = "config/partners.json",
    output_path: str | Path = "affiliate_partners.json",
) -> list[AffiliatePartner]:
    """Convert the research ``config/partners.json`` list into operational
    ``AffiliatePartner`` records and write them as a JSON array.

    Returns the converted list; on any error an empty list is returned after
    logging a warning so callers never crash.
    """
    research_path = Path(research_path)
    output_path = Path(output_path)

    if not research_path.exists():
        log.warning("Research partners file not found: %s", research_path)
        return []

    try:
        raw = json.loads(research_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to parse research partners file %s: %s", research_path, exc)
        return []

    programs = raw.get("programs") if isinstance(raw, dict) else None
    if not programs:
        log.warning("No 'programs' list found in %s", research_path)
        return []

    # --- keyword generation heuristics -----------------------------------
    _KEYWORD_MAP: dict[str, list[str]] = {
        "razer": ["razer", "peripherals", "setup", "gear", "keyboard", "mouse"],
        "logitech": ["logitech", "logi", "peripherals", "setup", "gear", "mouse", "headset"],
        "nordvpn": ["vpn", "nordvpn", "ping", "lag", "ddos", "security"],
        "exitlag": ["exitlag", "lag", "ping", "fps", "optimise", "optimize", "connection"],
        "secretlab": ["secretlab", "chair", "setup", "desk", "ergonomic"],
        "humble": ["humble", "humble bundle", "games", "deals", "bundle"],
        "green man": ["green man gaming", "gmg", "games", "keys", "store"],
        "cs.trade": ["cs.trade", "cs2", "skins", "trade", "trading", "marketplace"],
        "tradeupspy": ["tradeupspy", "trade up", "trade-up", "skins", "odds", "contract"],
        "impact": ["impact", "network", "affiliate", "programs"],
    }

    _NICHE_KEYWORDS: list[str] = [
        "cs2", "gaming", "setup", "peripherals", "skins", "competitive",
        "esports", "streaming", "content creator",
    ]

    def _keywords_for(entry: dict) -> tuple[str, ...]:
        name_lower = (entry.get("name") or "").lower()
        type_lower = (entry.get("type") or "").lower()
        niche_lower = (entry.get("niche_fit") or "").lower()
        kw: list[str] = []

        for pattern, words in _KEYWORD_MAP.items():
            if pattern in name_lower:
                kw.extend(words)
                break

        if "digital" in type_lower:
            kw.extend(["digital", "software", "download"])
        if "network" in type_lower:
            kw.extend(["network", "marketplace"])
        if "brand" in type_lower:
            kw.extend(["brand", "official"])

        for nk in _NICHE_KEYWORDS:
            if nk in niche_lower and nk not in kw:
                kw.append(nk)

        # always include the short name slug so basic matching works
        slug = _slugify(entry.get("name") or "partner")
        if slug and slug not in kw:
            kw.append(slug)

        # de-duplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for k in kw:
            k = k.lower().strip()
            if k and k not in seen:
                seen.add(k)
                unique.append(k)
        return tuple(unique)

    def _slugify(name: str) -> str:
        slug = "".join(c if c.isalnum() else "-" for c in name.lower())
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug.strip("-")

    partners: list[AffiliatePartner] = []
    for entry in programs:
        if not isinstance(entry, dict):
            log.warning("Skipping non-dict entry in research programs: %r", entry)
            continue
        try:
            slug = _slugify(entry.get("name") or "partner")
            partner = AffiliatePartner(
                id=slug,
                name=str(entry.get("name") or slug),
                link_en=str(entry.get("signup_url") or ""),
                tag="#ad",
                match_keywords=_keywords_for(entry),
                enabled=True,
            )
            partners.append(partner)
        except (TypeError, ValueError) as exc:
            log.warning("Skipping research entry %r: %s", entry.get("name"), exc)

    # Write the operational JSON array
    try:
        output_data = [
            {
                "id": p.id,
                "name": p.name,
                "link_en": p.link_en,
                "tag": p.tag,
                "match_keywords": list(p.match_keywords),
                "enabled": p.enabled,
            }
            for p in partners
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info("Wrote %d affiliate partners to %s", len(partners), output_path)
    except OSError as exc:
        log.warning("Failed to write affiliate partners to %s: %s", output_path, exc)

    return partners


def auto_cta_text(partner: AffiliatePartner) -> str:
    """Build a default CTA for a partner's mid-roll ad card."""
    return f"{partner.name} — скины CS2, ссылка в описании"


def build_affiliate_description(meta: dict, partner: AffiliatePartner, language: str) -> str:
    """Append the partner offer + link + disclosure tag to a description."""
    description = meta.get("description") or ""
    return f"{description}\n\n{partner.caption_text(language)}\n{partner.link(language)}\n{partner.tag}"