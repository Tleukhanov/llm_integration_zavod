"""Config-driven affiliate partners: loading, selection, and description formatting."""

from shorts_clipper.affiliate.partners import (
    AffiliatePartner,
    build_affiliate_description,
    load_affiliate_partners,
    select_affiliate_partner,
    select_affiliate_transcript_text,
)

__all__ = [
    "AffiliatePartner",
    "build_affiliate_description",
    "load_affiliate_partners",
    "select_affiliate_partner",
    "select_affiliate_transcript_text",
]