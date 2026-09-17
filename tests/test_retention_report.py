"""Tests for the ``retention-report`` decision-science CLI command.

No network access: every case exercises the aggregation against a temp
metrics DB seeded inline with fixed values, mirroring the sqlite-fixture
pattern from ``test_metrics.py``.
"""

import argparse
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from shorts_clipper.cli.retention_report import run_retention_report
from shorts_clipper.core.settings import Settings


def _past_iso(days: int) -> str:
    """ISO timestamp *days* in the past (UTC)."""
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _seed(db_path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE clips ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, source_url TEXT, "
        "platform TEXT, niche TEXT, published INTEGER DEFAULT 0, publish_ts TEXT, "
        "views INTEGER, likes INTEGER, comments INTEGER, hook_score REAL, energy REAL, "
        "UNIQUE(video_id))"
    )
    for i, row in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO clips (video_id, source_url, platform, niche, published, "
            "publish_ts, views, likes, comments, hook_score, energy) "
            "VALUES (?, 'u', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"v{i}",
                row.get("platform"),
                row.get("niche"),
                1 if row.get("published", True) else 0,
                row.get("publish_ts", _past_iso(1)),
                row.get("views"),
                row.get("likes"),
                row.get("comments"),
                row.get("hook_score"),
                row.get("energy"),
            ),
        )
    conn.commit()
    conn.close()


def _seed_legacy(db_path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE clips ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, source_url TEXT, "
        "channel TEXT, published INTEGER DEFAULT 0, publish_ts TEXT, "
        "views INTEGER, likes INTEGER, comments INTEGER, collected_at TEXT, "
        "UNIQUE(video_id))"
    )
    for i, row in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO clips (video_id, source_url, channel, published, publish_ts, "
            "views, likes, comments, collected_at) VALUES (?, 'u', 'c', ?, ?, ?, ?, ?, ?)",
            (
                f"v{i}",
                1 if row.get("published", True) else 0,
                row.get("publish_ts", _past_iso(1)),
                row.get("views"),
                row.get("likes"),
                row.get("comments"),
                row.get("publish_ts", _past_iso(1)),
            ),
        )
    conn.commit()
    conn.close()


def _args(**overrides) -> argparse.Namespace:
    base = {"platform": None, "days": 30, "niche": None, "out": None}
    base.update(overrides)
    return argparse.Namespace(**base)


def _run(settings: Settings, args: argparse.Namespace) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = run_retention_report(settings, args)
    return code, out.getvalue(), err.getvalue()


def _settings(db_path: Path) -> Settings:
    return Settings(metrics_path=db_path)


class RetentionReportMissingDbTests(unittest.TestCase):
    def test_missing_metrics_db_exits_one_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run(_settings(Path(tmp) / "nope.sqlite"), _args())
        self.assertEqual(code, 1)
        self.assertIn("Metrics database not found", err)

    def test_empty_metrics_db_exits_one_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            sqlite3.connect(str(db_path)).close()
            code, _, err = _run(_settings(db_path), _args())
        self.assertEqual(code, 1)
        self.assertIn("[ERR]", err)

    def test_metrics_db_with_clips_table_but_no_rows_exits_one_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            _seed(db_path, [])
            code, _, err = _run(_settings(db_path), _args())
        self.assertEqual(code, 1)
        self.assertIn("No clip records found", err)


class RetentionReportAggregationTests(unittest.TestCase):
    def _one_platform_context(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "metrics.sqlite"
        _seed(
            db_path,
            [
                {"platform": "youtube", "niche": "tech", "published": True,
                 "views": 1000, "likes": 50, "comments": 10,
                 "hook_score": 0.8, "energy": 0.5},
                {"platform": "youtube", "niche": "tech", "published": True,
                 "views": 2000, "likes": 100, "comments": 20,
                 "hook_score": 0.9, "energy": 0.7},
                {"platform": "youtube", "niche": "tech", "published": False,
                 "views": None, "likes": None, "comments": None},
                {"platform": "instagram", "niche": "tech", "published": True,
                 "views": 500, "likes": 40, "comments": 5,
                 "hook_score": 0.6, "energy": 0.9},
            ],
        )
        return db_path

    def test_populated_db_aggregates_one_platform_niche_and_grade(self):
        db_path = self._one_platform_context()
        out_path = Path(db_path).parent / "report.json"
        code, _, err = _run(_settings(db_path), _args(out=str(out_path)))

        self.assertEqual(code, 0, err)
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["command"], "retention-report")
        youtube = next(r for r in payload["rows"] if r["platform"] == "youtube")
        self.assertEqual(youtube["niche"], "tech")
        self.assertEqual(youtube["clips"], 3)
        self.assertEqual(youtube["published"], 2)
        self.assertEqual(youtube["publish_rate"], 0.6667)
        self.assertEqual(youtube["views_total"], 3000)
        self.assertEqual(youtube["likes_total"], 150)
        self.assertEqual(youtube["comments_total"], 30)
        self.assertEqual(youtube["median_views"], 1500.0)
        self.assertEqual(youtube["median_likes"], 75.0)
        self.assertEqual(youtube["median_comments"], 15.0)
        self.assertEqual(youtube["engagement"], 0.06)
        self.assertEqual(youtube["avg_hook_score"], 0.85)
        self.assertEqual(youtube["avg_energy"], 0.6)
        self.assertEqual(youtube["retention_score"], 0.04)
        self.assertEqual(youtube["retention_grade"], "D")

    def test_grade_maps_high_engagement_to_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            _seed(
                db_path,
                [
                    {"platform": "tiktok", "niche": "gaming", "views": 100,
                     "likes": 50, "comments": 25},
                    {"platform": "tiktok", "niche": "gaming", "views": 100,
                     "likes": 60, "comments": 30},
                ],
            )
            out_path = Path(tmp) / "report.json"
            code, _, err = _run(_settings(db_path), _args(out=str(out_path)))
            self.assertEqual(code, 0, err)
            row = json.loads(out_path.read_text(encoding="utf-8"))["rows"][0]
        # publish_rate=1.0, engagement=median(75,90)/100=0.825 -> score 0.825 -> A
        self.assertEqual(row["published"], 2)
        self.assertEqual(row["retention_grade"], "A")

    def test_no_views_falls_back_to_publish_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            _seed(
                db_path,
                [
                    {"platform": "youtube", "niche": "tech", "published": True,
                     "views": None},
                    {"platform": "youtube", "niche": "tech", "published": False,
                     "views": None},
                ],
            )
            out_path = Path(tmp) / "report.json"
            code, _, err = _run(_settings(db_path), _args(out=str(out_path)))
            self.assertEqual(code, 0, err)
            row = json.loads(out_path.read_text(encoding="utf-8"))["rows"][0]
        self.assertIsNone(row["engagement"])
        self.assertEqual(row["engagement_source"], "publish_rate_only (no row with views > 0)")
        self.assertEqual(row["retention_score"], 0.5)
        self.assertEqual(row["retention_grade"], "B")

    def test_filter_by_platform_returns_only_that_platform(self):
        db_path = self._one_platform_context()
        out_path = Path(db_path).parent / "report.json"
        code, _, err = _run(
            _settings(db_path), _args(platform="youtube", out=str(out_path))
        )
        self.assertEqual(code, 0, err)

        rows = json.loads(out_path.read_text(encoding="utf-8"))["rows"]
        self.assertEqual([r["platform"] for r in rows], ["youtube"])

    def test_filter_with_no_matches_exits_nonzero(self):
        db_path = self._one_platform_context()
        code, _, err = _run(_settings(db_path), _args(platform="tiktok"))
        self.assertEqual(code, 1)
        self.assertIn("No clip records match", err)

    def test_niche_filter_without_niche_column_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            _seed_legacy(db_path, [{"views": 10}])
            code, _, err = _run(_settings(db_path), _args(niche="tech"))
        self.assertEqual(code, 1)
        self.assertIn("no `niche` column", err)

    def test_days_window_excludes_stale_published_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            _seed(
                db_path,
                [
                    {"platform": "youtube", "publish_ts": _past_iso(5)},
                    {"platform": "youtube", "publish_ts": _past_iso(120)},
                    {"platform": "youtube", "publish_ts": None},
                ],
            )
            out_path = Path(tmp) / "report.json"
            code, _, err = _run(_settings(db_path), _args(out=str(out_path)))
            self.assertEqual(code, 0, err)
            row = json.loads(out_path.read_text(encoding="utf-8"))["rows"][0]
        self.assertEqual(row["clips"], 2)

    def test_markdown_report_written_with_expected_headers(self):
        db_path = self._one_platform_context()
        out_path = Path(db_path).parent / "report.json"
        code, _, err = _run(_settings(db_path), _args(out=str(out_path)))
        self.assertEqual(code, 0, err)

        md = out_path.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("# Retention Report", md)
        self.assertIn("| Platform | Niche |", md)
        self.assertIn("## Methodology", md)
        self.assertIn("A: retention_score >= 0.75", md)
        self.assertIn("D: retention_score < 0.25", md)

    def test_legacy_schema_marks_missing_columns_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            _seed_legacy(db_path, [{"views": 100, "likes": 5, "comments": 1}])
            out_path = Path(tmp) / "report.json"
            code, _, err = _run(_settings(db_path), _args(out=str(out_path)))
            self.assertEqual(code, 0, err)
            row = json.loads(out_path.read_text(encoding="utf-8"))["rows"][0]
        self.assertEqual(row["avg_hook_score"], "not_available")
        self.assertEqual(row["avg_energy"], "not_available")
        self.assertIsNone(row["niche"])
        self.assertEqual(row["engagement"], 0.06)

    def test_rows_sorted_by_platform_then_niche_then_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            _seed(
                db_path,
                [
                    {"platform": "instagram", "niche": "fitness", "views": 100,
                     "likes": 80, "comments": 30},
                    {"platform": "youtube", "niche": "tech", "views": 100,
                     "likes": 1, "comments": 0},
                    {"platform": "youtube", "niche": "food", "views": 10},
                ],
            )
            out_path = Path(tmp) / "report.json"
            code, _, err = _run(_settings(db_path), _args(out=str(out_path)))
            self.assertEqual(code, 0, err)
            rows = json.loads(out_path.read_text(encoding="utf-8"))["rows"]
        keys = [(r["platform"], r["niche"]) for r in rows]
        self.assertEqual(
            keys,
            [("instagram", "fitness"), ("youtube", "food"), ("youtube", "tech")],
        )
        self.assertEqual(rows[0]["retention_grade"], "A")


class RetentionReportCliWiringTests(unittest.TestCase):
    def test_cli_main_registers_and_runs_retention_report(self):
        import shorts_clipper.__main__ as cli

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            out_path = Path(tmp) / "report.json"
            _seed(db_path, [{"platform": "youtube", "niche": "tech", "views": 10}])
            with mock.patch.object(
                cli.Settings, "from_env", return_value=_settings(db_path)
            ):
                code = cli.main(
                    [
                        "retention-report",
                        "--platform",
                        "youtube",
                        "--days",
                        "30",
                        "--out",
                        str(out_path),
                    ]
                )
                self.assertEqual(code, 0)
                self.assertTrue(out_path.exists())
                self.assertTrue(out_path.with_suffix(".md").exists())

    def test_build_parser_includes_retention_report_options(self):
        import shorts_clipper.__main__ as cli

        parser = cli.build_parser()
        parsed = parser.parse_args(["retention-report", "--days", "7", "--platform", "x"])
        self.assertEqual(parsed.command, "retention-report")
        self.assertEqual(parsed.days, 7)
        self.assertEqual(parsed.platform, "x")


if __name__ == "__main__":
    unittest.main()