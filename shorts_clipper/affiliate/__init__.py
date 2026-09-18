"""Config-driven affiliate partners: loading, selection, and description formatting."""

from shorts_clipper.affiliate.partners import (
    AffiliatePartner,
    auto_cta_text,
    build_affiliate_description,
    decorate_affiliate_url,
    load_affiliate_partners,
    select_affiliate_partner,
    select_affiliate_transcript_text,
)

__all__ = [
    "AffiliatePartner",
    "auto_cta_text",
    "build_affiliate_description",
    "decorate_affiliate_url",
    "load_affiliate_partners",
    "select_affiliate_partner",
    "select_affiliate_transcript_text",
]