import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class RunContext:
    """Singleton tracking context and artifacts for a single pipeline run."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.reset()
        return cls._instance

    def reset(self):
        self.run_id = datetime.utcnow().strftime("run_%Y%m%d_%H%M%S")
        self.decision_trace = {}
        self.attention_reports = {}
        self.variant_reports = []
        self.score_breakdowns = {}
        self.pipeline_metrics = {}
        self.final_metadata = {}
        self.editorial_summaries = {}
        self.run_dir = None

    def set_run_dir(self, output_dir: Path):
        self.run_dir = output_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"Initialized RunContext at {self.run_dir}")
        return self.run_dir

    def add_decision_trace(self, trace_data: dict):
        self.decision_trace.update(trace_data)

    def add_attention_report(self, clip_id: str, report_data: dict):
        self.attention_reports[clip_id] = report_data

    def add_variant(self, variant_data: dict):
        self.variant_reports.append(variant_data)

    def add_score_breakdown(self, clip_id: str, breakdown: dict):
        self.score_breakdowns[clip_id] = breakdown

    def add_pipeline_metrics(self, metrics: dict):
        self.pipeline_metrics.update(metrics)

    def add_final_metadata(self, clip_id: str, metadata: dict):
        self.final_metadata[clip_id] = metadata

    def set_editorial_summary(self, clip_id: str, summary_md: str):
        self.editorial_summaries[clip_id] = summary_md

    def export_all(self):
        if not self.run_dir:
            log.warning("Run directory not set. Skipping export.")
            return

        def _dump(filename: str, data: Any):
            if not data:
                return
            path = self.run_dir / filename
            try:
                if isinstance(data, str):
                    path.write_text(data, encoding="utf-8")
                else:
                    path.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
            except Exception as e:
                log.error(f"Failed to write {filename}: {e}")

        _dump("decision_trace.json", self.decision_trace)
        _dump("attention_report.json", self.attention_reports)
        _dump("variant_report.json", self.variant_reports)
        _dump("score_breakdown.json", self.score_breakdowns)
        _dump("pipeline_metrics.json", self.pipeline_metrics)
        _dump("final_metadata.json", self.final_metadata)

        try:
            from shorts_clipper.core.stats import get_optimizer_stats

            _dump("benchmark_summary.json", get_optimizer_stats().stats)
        except Exception as e:
            log.warning("Could not export benchmark summary: %s", e)

        for clip_id, summary in self.editorial_summaries.items():
            _dump(
                f"editorial_summary_{clip_id}.md"
                if len(self.editorial_summaries) > 1
                else "editorial_summary.md",
                summary,
            )

    def verify_run(self) -> bool:
        """Self verification hook."""
        issues = []
        if not self.variant_reports:
            issues.append("No variants generated.")
        if not self.decision_trace:
            issues.append("No decision trace recorded.")
        if not self.score_breakdowns:
            issues.append("Score transformations not recorded.")

        if issues:
            log.error("❌ RUN UNVERIFIED. Issues: %s", ", ".join(issues))
            if self.run_dir:
                (self.run_dir / "UNVERIFIED").touch()
            return False

        # Automated regression detection
        try:
            from shorts_clipper.core.stats import get_optimizer_stats

            stats_obj = get_optimizer_stats()
            avg_confidence = stats_obj.stats.get("average_confidence", 0.0)
            if avg_confidence > 0:
                current_conf = self.decision_trace.get("confidence", 0)
                if (
                    isinstance(current_conf, (int, float))
                    and current_conf > 0
                    and current_conf < avg_confidence * 0.8
                ):
                    log.warning(
                        "📉 REGRESSION DETECTED: Confidence (%.2f) is significantly below historical average (%.2f)",
                        current_conf,
                        avg_confidence,
                    )
                    if self.run_dir:
                        (self.run_dir / "REGRESSION").touch()
        except Exception as e:
            log.warning("Regression detection failed: %s", e)

        log.info("✅ Run self-verification passed.")
        return True


def get_run_context() -> RunContext:
    return RunContext()
