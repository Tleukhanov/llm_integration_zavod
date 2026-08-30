import json
import logging
from typing import Any

from shorts_clipper.core.settings import Settings

log = logging.getLogger(__name__)


class SemanticRelevanceGate:
    def __init__(self, keyword: str, niche: str):
        self.keyword = keyword or ""
        self.niche = niche or ""
        self.settings = Settings.from_env()

    def filter_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []

        if not self.keyword and not self.niche:
            return candidates

        log.info(
            "Evaluating semantic relevance for %d candidates against Keyword: '%s', Niche: '%s'",
            len(candidates),
            self.keyword,
            self.niche,
        )

        # We only send ID, Title, and Channel to minimize tokens
        batch_input = []
        for c in candidates:
            batch_input.append(
                {
                    "id": c["id"],
                    "title": c.get("title", ""),
                    "channel": c.get("channel_title", "") or c.get("channel_id", ""),
                }
            )

        prompt = f"""
You are an expert Semantic Relevance Gatekeeper.
Evaluate the following YouTube videos for strict relevance to the given keyword and niche.

KEYWORD: "{self.keyword}"
NICHE: "{self.niche}"

Many unrelated videos bypass basic search algorithms (e.g., if searching "claude", unrelated shows like "Crime Patrol" or "Baalveer" might appear). 
You must identify and reject them.

For EACH video, provide:
- keyword_relevance (0-100): How relevant is the title/channel to the exact keyword?
- topic_relevance (0-100): How relevant is the title/channel to the broader topic?
- niche_relevance (0-100): How relevant is the title/channel to the specific niche?
- channel_relevance (0-100): Is the channel known for this content?

Return ONLY valid JSON in this exact structure:
[
  {{
    "id": "video_id",
    "keyword_relevance": 0,
    "topic_relevance": 0,
    "niche_relevance": 0,
    "channel_relevance": 0,
    "reason": "..."
  }}
]

Here are the videos:
{json.dumps(batch_input, indent=2)}
"""

        try:
            from google import genai

            from shorts_clipper.providers.gemini import GeminiProvider

            provider = GeminiProvider(api_key=self.settings.gemini_api_key)

            response = provider.generate_content(
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.0
                ),
            )
            result_text = response.text
            evaluations = json.loads(result_text)

            eval_map = {e["id"]: e for e in evaluations}
            passed_candidates = []

            # Strict Enforcement: A candidate may ONLY continue if relevance_score >= 50
            for c in candidates:
                vid = c["id"]
                if vid in eval_map:
                    ev = eval_map[vid]
                    c["_relevance"] = ev

                    # Do not trust LLM strings, verify mathematically
                    max_rel = max(
                        ev.get("keyword_relevance", 0),
                        ev.get("topic_relevance", 0),
                        ev.get("niche_relevance", 0),
                    )

                    if max_rel >= 50:
                        passed_candidates.append(c)
                    else:
                        log.info(
                            "\n[REJECTED] Video: %s\nMax Semantic Score: %d/100 (Threshold 50)\nReason: %s",
                            c.get("title"),
                            max_rel,
                            ev.get("reason", ""),
                        )
                else:
                    log.info(
                        "\n[REJECTED] Video: %s\nReason: Missing from LLM evaluation response (Unknown Relevance = Reject)",
                        c.get("title"),
                    )

            log.info(
                "Semantic Relevance Gate passed %d/%d candidates.",
                len(passed_candidates),
                len(candidates),
            )
            return passed_candidates

        except Exception as e:
            log.error(
                f"Semantic Relevance Gate API failed: {e}. ALLOWING ALL CANDIDATES as fallback."
            )
            return candidates
