"""Tests for affiliate income: UTM decoration, revenue attribution events
and the ``revenue-report`` CLI command.

No network access: the revenue report is exercised against a temp metrics DB
seeded via ``record_affiliate_event`` with fixed amounts, mirroring the
sqlite-fixture pattern from ``test_metrics.py`` / ``test_retention_report.py``.
"""

import argparse
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from shorts_clipper.affiliate.partners import (
    AffiliatePartner,
    build_affiliate_description,
    decorate_affiliate_url,
)
from shorts_clipper.cli.revenue_report import run_revenue_report
from shorts_clipper.core.metrics import MetricsStore
from shorts_clipper.core.settings import Settings


class DecorateAffiliateUrlTests(unittest.TestCase):
    def test_append_utm_params_when_url_has_no_query(self):
        url = decorate_affiliate_url("https://skinfarm.com/r/en", "skin_farm", "clutch-1")
        self.assertEqual(
            url,
            "https://skinfarm.com/r/en"
            "?utm_source=shorts&utm_medium=affiliate"
            "&utm_campaign=skin_farm&utm_content=clutch-1",
        )

    def test_join_existing_query_with_ampersand_not_double_question(self):
        url = decorate_affiliate_url("https://skinfarm.com/r/en?ref=abc", "skin_farm", "clutch-1")
        self.assertEqual(
            url,
            "https://skinfarm.com/r/en?ref=abc"
            "&utm_source=shorts&utm_medium=affiliate"
            "&utm_campaign=skin_farm&utm_content=clutch-1",
        )
        self.assertEqual(url.count("?"), 1)

    def test_fragment_stays_after_query(self):
        url = decorate_affiliate_url("https://hub/en?ref=1#top", "p1", "clip")
        self.assertEqual(
            url,
            "https://hub/en?ref=1"
            "&utm_source=shorts&utm_medium=affiliate"
            "&utm_campaign=p1&utm_content=clip#top",
        )

    def test_partner_values_are_url_encoded(self):
        url = decorate_affiliate_url("https://hub/en", "my partner&x", "clip name")
        self.assertIn("utm_campaign=my+partner%26x", url)
        self.assertIn("utm_content=clip+name", url)

    def test_build_affiliate_description_decorates_when_clip_name_given(self):
        partner = AffiliatePartner(
            id="skin_farm", name="SkinFarm", link_en="https://skinfarm.com/r/en"
        )
        description = build_affiliate_description(
            {"description": "Nice clip"}, partner, "en", clip_name="clutch-1"
        )
        self.assertIn(
            "https://skinfarm.com/r/en?utm_source=shorts&utm_medium=affiliate"
            "&utm_campaign=skin_farm&utm_content=clutch-1",
            description,
        )

    def test_build_affiliate_description_keeps_raw_link_without_clip_name(self):
        partner = AffiliatePartner(
            id="p", name="SkinHub", link_en="https://hub/en", tag="#ad"
        )
        description = build_affiliate_description({"description": "Nice clip"}, partner, "en")
        self.assertEqual(description, "Nice clip\n\nSkinHub\nhttps://hub/en\n#ad")


class MetricsAffiliateEventTests(unittest.TestCase):
    def test_record_affiliate_event_writes_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MetricsStore(Path(tmp) / "metrics.sqlite")
            store.record_affiliate_event(
                "youtube", "tech", "skin_farm", "revenue", amount=12.5
            )
            row = store._conn.execute(
                "SELECT * FROM affiliate_events WHERE partner_id=?", ("skin_farm",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["platform"], "youtube")
            self.assertEqual(row["niche"], "tech")
            self.assertEqual(row["event_type"], "revenue")
            self.assertEqual(row["amount"], 12.5)
            self.assertEqual(row["stack"], "default")
            self.assertIsNotNone(row["published_at"])
            store.close()

    def test_record_affiliate_event_clicks_and_conversions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MetricsStore(Path(tmp) / "metrics.sqlite")
            store.record_affiliate_event("tiktok", "gaming", "nord", "click")
            store.record_affiliate_event("tiktok", "gaming", "nord", "conversion")
            counts = store._conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM affiliate_events "
                "GROUP BY event_type ORDER BY event_type"
            ).fetchall()
            self.assertEqual(
                [(r["event_type"], r["n"]) for r in counts],
                [("click", 1), ("conversion", 1)],
            )
            store.close()

    def test_record_affiliate_event_rejects_unknown_event_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MetricsStore(Path(tmp) / "metrics.sqlite")
            with self.assertRaises(ValueError):
                store.record_affiliate_event("youtube", "tech", "p", "impression")
            store.close()

    def test_legacy_metrics_db_still_opens_with_affiliate_events_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE clips ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, source_url TEXT, "
                "published INTEGER DEFAULT 0, publish_ts TEXT, views INTEGER, "
                "UNIQUE(video_id))"
            )
            conn.commit()
            conn.close()

            store = MetricsStore(db_path)
            try:
                tables = {
                    r["name"]
                    for r in store._conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("affiliate_events", tables)
            finally:
                store.close()


def _seed_events(db_path: Path) -> None:
    store = MetricsStore(db_path)
    try:
        store.record_affiliate_event("youtube", "tech", "skin_farm", "click")
        store.record_affiliate_event("youtube", "tech", "skin_farm", "conversion")
        store.record_affiliate_event("youtube", "tech", "skin_farm", "revenue", amount=12.5)
        store.record_affiliate_event("instagram", "fitness", "nord", "click")
        store.record_affiliate_event("instagram", "fitness", "nord", "revenue", amount=5.0)
    finally:
        store.close()


def _args(**overrides) -> argparse.Namespace:
    base = {"niche": None, "partner": None, "out": None}
    base.update(overrides)
    return argparse.Namespace(**base)


def _run(settings: Settings, args: argparse.Namespace) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = run_revenue_report(settings, args)
    return code, out.getvalue()


def _settings(db_path: Path) -> Settings:
    return Settings(metrics_path=db_path)


class RevenueReportEmptyTests(unittest.TestCase):
    def test_missing_metrics_db_exits_zero_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run(_settings(Path(tmp) / "nope.sqlite"), _args())
        self.assertEqual(code, 0)
        self.assertIn("No affiliate events yet", out)

    def test_db_without_affiliate_events_table_exits_zero_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            sqlite3.connect(str(db_path)).close()
            code, out = _run(_settings(db_path), _args())
        self.assertEqual(code, 0)
        self.assertIn("No affiliate events yet", out)

    def test_empty_affiliate_events_table_exits_zero_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            MetricsStore(db_path).close()
            code, out = _run(_settings(db_path), _args())
        self.assertEqual(code, 0)
        self.assertIn("No affiliate events yet", out)


class RevenueReportAggregationTests(unittest.TestCase):
    def _report(self, **overrides):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "metrics.sqlite"
        _seed_events(db_path)
        out_path = Path(tmp.name) / "report.md"
        code, out = _run(_settings(db_path), _args(out=str(out_path), **overrides))
        return code, out, out_path

    def test_aggregates_total_revenue_and_writes_markdown(self):
        code, out, out_path = self._report()
        self.assertEqual(code, 0, out)
        self.assertIn("$17.50", out)

        md = out_path.read_text(encoding="utf-8")
        self.assertIn("# Revenue Report", md)
        self.assertIn("Total revenue: **$17.50**", md)
        self.assertIn("Revenue by platform / niche", md)
        self.assertIn("Per-partner breakdown", md)

    def test_revenue_grouped_per_platform_niche(self):
        code, out, out_path = self._report()
        self.assertEqual(code, 0, out)
        md = out_path.read_text(encoding="utf-8")
        self.assertIn("youtube", md)
        self.assertIn("instagram", md)
        self.assertIn("| youtube | tech |", md)
        self.assertIn("| instagram | fitness |", md)

    def test_partner_breakdown_counts_clicks_conversions(self):
        code, out, _ = self._report()
        self.assertEqual(code, 0, out)
        # skin_farm: 1 click, 1 conversion, $12.50 revenue
        self.assertIn("| skin_farm | 1 | 1 | 1 | $12.50 |", out)
        # nord: 1 click, 0 conversions, $5.00 revenue
        self.assertIn("| nord | 1 | 0 | 1 | $5.00 |", out)

    def test_niche_filter_limits_rows(self):
        code, out, out_path = self._report(niche="tech")
        self.assertEqual(code, 0, out)
        md = out_path.read_text(encoding="utf-8")
        self.assertNotIn("instagram", md)
        self.assertIn("youtube", md)

    def test_partner_filter_limits_rows(self):
        code, out, _ = self._report(partner="nord")
        self.assertEqual(code, 0, out)
        self.assertNotIn("skin_farm", out)
        self.assertIn("nord", out)


class RevenueReportCliWiringTests(unittest.TestCase):
    def test_cli_main_registers_and_runs_revenue_report(self):
        import shorts_clipper.__main__ as cli

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            out_path = Path(tmp) / "report.md"
            _seed_events(db_path)
            with mock.patch.object(
                cli.Settings, "from_env", return_value=_settings(db_path)
            ):
                code = cli.main(
                    ["revenue-report", "--niche", "tech", "--out", str(out_path)]
                )
                self.assertEqual(code, 0)
                self.assertTrue(out_path.exists())
                md = out_path.read_text(encoding="utf-8")
                self.assertIn("# Revenue Report", md)
                self.assertIn("skin_farm", md)

    def test_build_parser_includes_revenue_report_options(self):
        import shorts_clipper.__main__ as cli

        parser = cli.build_parser()
        parsed = parser.parse_args(
            ["revenue-report", "--niche", "tech", "--partner", "nord"]
        )
        self.assertEqual(parsed.command, "revenue-report")
        self.assertEqual(parsed.niche, "tech")
        self.assertEqual(parsed.partner, "nord")


if __name__ == "__main__":
    unittest.main()