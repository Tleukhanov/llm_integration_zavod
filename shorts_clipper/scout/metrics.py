"""
Scout run metrics. One instance per scout run.
Writes to outputs/scout_metrics.json after each run.
Keeps last 100 entries.
"""

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# No shared METRICS_FILE constant anymore


@dataclass
class ScoutMetrics:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    niche: str = ""
    keyword: str = ""
    time_window_days: int = 0
    started_at: float = field(default_factory=time.time)

    # Discovery
    api_used: bool = False
    api_quota_charged: int = 0
    queries_fired: int = 0
    queries_with_results: int = 0
    video_ids_discovered: int = 0
    discovery_duration_s: float = 0.0

    # Stage filtering
    rejected_too_old: int = 0
    rejected_too_short: int = 0
    rejected_too_long: int = 0
    rejected_low_views: int = 0
    rejected_no_subtitles: int = 0
    rejected_timeout: int = 0
    rejected_low_quality: int = 0

    # Cache
    cache_hits: int = 0
    cache_misses: int = 0
    yt_dlp_calls: int = 0
    yt_dlp_timeouts: int = 0

    # Circuit breaker
    consecutive_failures_at_start: int = 0

    # Result
    winner_id: str = ""
    winner_title: str = ""
    winner_age_days: float = 0.0
    winner_virality_score: float = 0.0
    winning_query: str = ""
    total_duration_s: float = 0.0
    succeeded: bool = False
    failure_reason: str = ""

    # Evaluation performance
    avg_eval_time_s: float = 0.0
    slowest_eval_time_s: float = 0.0
    fastest_eval_time_s: float = 0.0

    # Subtitle fetch metrics
    subtitle_fetch_success: int = 0
    subtitle_fetch_failure: int = 0
    subtitle_429_count: int = 0

    def finish(self, winner: dict | None, failure_reason: str = "") -> None:
        self.total_duration_s = round(time.time() - self.started_at, 2)
        if winner:
            self.succeeded = True
            self.winner_id = winner.get("video_id") or winner.get("id", "")
            self.winner_title = winner.get("title", "")
        else:
            self.failure_reason = failure_reason

        metrics_file = Path(f"outputs/scout_metrics_{self.run_id}.json")
        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        metrics_file.write_text(json.dumps(asdict(self), indent=2))
