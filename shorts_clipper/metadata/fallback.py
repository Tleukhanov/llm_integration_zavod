import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def generate_fallback_metadata(
    segments: list[Any], source_title: str = "", source_channel: str = "", niche: str = ""
) -> dict[str, Any]:
    """
    Generates intelligent metadata using Gemini Flash.
    Used as a fallback when Gemini Pro is unavailable or quota is exhausted.
    """
    log.info("[FALLBACK] generating metadata using Flash Semantic Engine")

    from shorts_clipper.core.settings import Settings

    settings = Settings.from_env()

    source_title = source_title or "Unknown"
    source_channel = source_channel or "Unknown"
    niche = niche or ""

    full_text = " ".join([s.text.strip() for s in segments])

    prompt = f"""
You are a Viral Packaging Expert.
Analyze the following transcript excerpt and generate viral metadata.

Do NOT use generic word frequencies.
Identify the true entities, concepts, subjects, and themes.

Source Title: {source_title}
Source Channel: {source_channel}
Niche: {niche}
Transcript:
{full_text}

1. Extract the core semantic topics.
2. Generate 5 highly clickable, curious, and specific title candidates based on the actual tension in the text.
3. Score each candidate (0-100) based on curiosity, clarity, novelty, emotion, and specificity.
4. Select the best one.
5. Write a brief description containing what happened and why it matters.
6. Generate up to 15 relevant tags.

Return ONLY valid JSON in this exact structure:
{{
  "candidates": [
    {{"title": "Candidate 1", "score": 90}},
    {{"title": "Candidate 2", "score": 85}}
  ],
  "best_title": "The Winning Title",
  "description": "...",
  "tags": ["tag1", "tag2"]
}}
"""

    try:
        from google import genai

        from shorts_clipper.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key=settings.gemini_api_key)

        response = provider.generate_content(
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.7
            ),
        )

        res = json.loads(response.text)
        log.info("\n[FALLBACK TITLE CANDIDATES]")
        for c in res.get("candidates", []):
            log.info("Candidate (Score: %d): %s", c.get("score", 0), c.get("title", ""))

        selected_title = res.get("best_title", "Viral Clip")
        log.info("Selected: %s\n", selected_title)

        return {
            "title": selected_title,
            "description": res.get("description", ""),
            "tags": res.get("tags", []),
        }
    except Exception as e:
        log.warning("Flash Semantic Metadata failed: %s. Using basic static fallback.", e)
        # Absolute failsafe
        return {
            "title": f"The Truth About {source_title[:50]}...",
            "description": f"A powerful moment from {source_channel}.",
            "tags": ["shorts", niche, "viral"],
        }
