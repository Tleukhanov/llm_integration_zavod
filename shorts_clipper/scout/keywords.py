"""
Niche-aware keyword map for scout query generation.
Add new niches here. Do not use rotating random keywords.
Each niche maps to specific, relevant search terms.
"""

NICHE_KEYWORDS: dict[str, list[str]] = {
    "tech": [
        "AI news",
        "OpenAI",
        "Anthropic",
        "Apple",
        "Google",
        "Tesla",
        "robotics",
        "startups",
        "programming",
        "cybersecurity",
        "GPU",
        "semiconductor",
        "smartphone review",
        "software engineering",
        "machine learning",
        "open source",
    ],
    "gaming": [
        "gameplay",
        "boss fight",
        "speedrun",
        "game review",
        "tips tricks",
        "esports",
        "tournament",
        "game update",
        "patch notes",
        "walkthrough",
        "new release",
        "indie game",
    ],
    "finance": [
        "stock market",
        "investing",
        "crypto",
        "economy news",
        "earnings report",
        "Federal Reserve",
        "inflation",
        "day trading",
        "ETF",
        "portfolio",
        "recession",
        "interest rates",
    ],
    "fitness": [
        "workout routine",
        "diet plan",
        "weight loss",
        "muscle building",
        "nutrition",
        "cardio",
        "strength training",
        "running tips",
        "yoga",
        "recovery",
        "meal prep",
    ],
    "news": [
        "breaking news",
        "politics",
        "world news",
        "analysis",
        "interview",
        "election",
        "policy",
        "government",
        "geopolitics",
    ],
    "cooking": [
        "recipe",
        "cooking tips",
        "meal prep",
        "restaurant review",
        "baking",
        "cuisine",
        "food hack",
        "chef",
    ],
    "sports": [
        "highlights",
        "match recap",
        "transfer news",
        "analysis",
        "player interview",
        "training",
        "championship",
    ],
}


def _expand_niche_dynamically(niche: str) -> list[str]:
    """Use Gemini to semantically expand unknown niches."""
    try:
        from shorts_clipper.core.settings import Settings
        from shorts_clipper.providers.gemini import GeminiProvider

        settings = Settings.from_env()
        provider = GeminiProvider(api_key=settings.gemini_api_key)

        prompt = f"""
        Expand the following niche into exactly 6 highly relevant YouTube search keywords.
        Do not duplicate the niche name exactly.
        Example for "relationships": dating, breakups, attachment, marriage, communication, relationship advice
        
        Niche: "{niche}"
        Return ONLY a comma-separated list of keywords.
        """
        response = provider.generate_content(prompt)
        parts = [p.strip() for p in response.text.split(",") if p.strip()]
        if len(parts) >= 3:
            import time

            print("Sleeping 35 seconds after niche expansion to respect 2 RPM limit...")
            time.sleep(35)
            return parts
    except Exception:
        pass
    import time

    print("Sleeping 35 seconds after failed niche expansion to respect 2 RPM limit...")
    time.sleep(35)
    return ["tips", "story", "analysis", "news", "guide", "highlights"]


def get_keywords(niche: str) -> list[str]:
    """
    Return keyword list for a niche.
    Dynamically expands unknown niches.
    """
    niche_lower = niche.lower().strip()
    for key, kws in NICHE_KEYWORDS.items():
        if key in niche_lower or niche_lower in key:
            return kws

    # Unknown niche: generate semantic expansions
    return _expand_niche_dynamically(niche_lower)


def build_queries(niche: str, keyword: str | None, count: int = 4) -> list[str]:
    """
    Build search queries for discovery.
    Supports multiple keywords separated by commas, pipes, or semicolons.
    """
    import random
    import re

    if keyword:
        # Split on comma, pipe, or semicolon
        parts = [p.strip() for p in re.split(r"[,;|]", keyword) if p.strip()]
        queries = []
        for part in parts:
            queries.extend(
                [
                    f"ytsearch15:{part}",
                    f"ytsearch15:{part} interview",
                    f"ytsearch15:{part} podcast",
                ]
            )
        return queries

    kws = get_keywords(niche)
    selected = random.sample(kws, min(count, len(kws)))

    queries = []
    niche_lower = niche.lower().strip()
    for kw in selected:
        kw_lower = kw.lower()
        if niche_lower in kw_lower or kw_lower in niche_lower:
            queries.append(f"ytsearch15:{kw}")
        else:
            queries.append(f"ytsearch15:{niche} {kw}")

    # Final safety: remove absolute duplicates like "relationships relationships"
    cleaned = []
    for q in queries:
        words = q.replace("ytsearch15:", "").split()
        unique_words = []
        for w in words:
            if w.lower() not in [uw.lower() for uw in unique_words]:
                unique_words.append(w)
        cleaned.append(f"ytsearch15:{' '.join(unique_words)}")

    return cleaned
