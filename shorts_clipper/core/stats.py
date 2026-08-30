import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class OptimizerStats:
    def __init__(self, stats_file: Path = Path("outputs/optimizer_stats.json")):
        self.stats_file = stats_file
        self.stats = self._load()

    def _load(self) -> dict:
        if self.stats_file.exists():
            try:
                data = json.loads(self.stats_file.read_text(encoding="utf-8"))
                # Migrate older stats structures
                if "numeric_samples" not in data:
                    data["numeric_samples"] = data.get("total_runs", 0)
                    data["unknown_samples"] = 0
                    data["calibration_status"] = "NOT CALIBRATED"
                    data["coverage"] = 0.0
                    data["variants_generated"] = 0
                    data["detailed_wins"] = {}
                if data.get("total_confidence", 0.0) > data.get("numeric_samples", 0):
                    log.warning("Historical confidence statistics corrupted. Invalidating.")
                    data["total_confidence"] = 0.0
                    data["average_confidence"] = 0.0
                    data["numeric_samples"] = 0
                return data
            except Exception as e:
                log.warning("Failed to load optimizer stats: %s", e)
        return {
            "total_runs": 0,
            "variants_generated": 0,
            "wins_by_variant": {"base": 0, "optimized": 0, "custom": 0},
            "detailed_wins": {},
            "average_confidence": 0.0,
            "total_confidence": 0.0,
            "numeric_samples": 0,
            "unknown_samples": 0,
            "coverage": 0.0,
            "calibration_status": "NOT CALIBRATED",
        }

    def _save(self):
        self.stats_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.stats_file.write_text(
                json.dumps(self.stats, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            log.warning("Failed to save optimizer stats: %s", e)

    def record_run(
        self,
        winner_variant_id: str,
        confidence: float,
        variants_count: int = 0,
        runner_up_id: str = None,
        improvement_percentage: float = 0.0,
    ):
        self.stats["total_runs"] += 1
        self.stats["variants_generated"] += variants_count

        variant_type = "custom"
        if winner_variant_id == "base":
            variant_type = "base"
        elif (
            "start_at_hook" in winner_variant_id
            or "remove_dead_setup" in winner_variant_id
            or "remove_dead_air" in winner_variant_id
        ):
            variant_type = "optimized"
        elif "optim" in winner_variant_id.lower() or "variant" in winner_variant_id.lower():
            variant_type = "optimized"

        self.stats["wins_by_variant"][variant_type] = (
            self.stats["wins_by_variant"].get(variant_type, 0) + 1
        )
        self.stats["detailed_wins"][winner_variant_id] = (
            self.stats["detailed_wins"].get(winner_variant_id, 0) + 1
        )

        if "total_improvement" not in self.stats:
            self.stats["total_improvement"] = 0.0
            self.stats["average_improvement"] = 0.0
            self.stats["runner_ups"] = {}

        self.stats["total_improvement"] += improvement_percentage
        self.stats["average_improvement"] = (
            self.stats["total_improvement"] / self.stats["total_runs"]
        )

        if runner_up_id:
            self.stats["runner_ups"][runner_up_id] = (
                self.stats["runner_ups"].get(runner_up_id, 0) + 1
            )

        if isinstance(confidence, (int, float)):
            self.stats["total_confidence"] += float(confidence)
            self.stats["numeric_samples"] += 1
            self.stats["average_confidence"] = (
                self.stats["total_confidence"] / self.stats["numeric_samples"]
            )
        else:
            self.stats["unknown_samples"] += 1
            log.info("Recorded unknown confidence value. System is missing calibration data.")

        total_samples = self.stats["numeric_samples"] + self.stats["unknown_samples"]
        self.stats["coverage"] = (
            self.stats["numeric_samples"] / total_samples if total_samples > 0 else 0.0
        )
        self.stats["calibration_status"] = (
            "CALIBRATED" if self.stats["coverage"] > 0.5 else "NOT CALIBRATED"
        )

        self._save()

        avg_conf_display = (
            f"{self.stats['average_confidence'] * 100:.1f}%"
            if self.stats["numeric_samples"] > 0
            else "UNKNOWN"
        )
        log.info(
            "📊 Optimizer Stats Updated: %d runs, Base Wins: %d, Avg Confidence: %s, Calibration: %s",
            self.stats["total_runs"],
            self.stats["wins_by_variant"].get("base", 0),
            avg_conf_display,
            self.stats["calibration_status"],
        )


def get_optimizer_stats() -> OptimizerStats:
    return OptimizerStats()
