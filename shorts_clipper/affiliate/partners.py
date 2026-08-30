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


def build_affiliate_description(meta: dict, partner: AffiliatePartner, language: str) -> str:
    """Append the partner offer + link + disclosure tag to a description."""
    description = meta.get("description") or ""
    return f"{description}\n\n{partner.caption_text(language)}\n{partner.link(language)}\n{partner.tag}"