"""Tests for the SQLite metrics store and the new settings fields.

No network access: every case exercises the store locally against a temp DB
with fixed, injectable ``publish_ts`` values.
"""

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from shorts_clipper.core.metrics import (
    ClipRecord,
    MetricsStore,
    should_publish_today,
)
from shorts_clipper.core.settings import Settings


def _past_iso(hours: int) -> str:
    """ISO timestamp *hours* in the past (UTC)."""
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _today_iso(hour: int = 10) -> str:
    """ISO timestamp *hour* today (UTC)."""
    return f"{datetime.now(UTC).date().isoformat()}T{hour:02d}:00:00+00:00"


def _blank_env():
    """Empty .env file + a pristine os.environ for Settings.from_env."""
    return patch.dict(os.environ, {}, clear=True)


def _load_collect_metrics_module():
    """Load ``scripts/collect_metrics.py`` as a module (import-safe)."""
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "collect_metrics",
        repo_root / "scripts" / "collect_metrics.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class MetricsStoreTests(unittest.TestCase):
    def _store(self, temp_dir: str) -> MetricsStore:
        return MetricsStore(Path(temp_dir) / "metrics.sqlite")

    def test_sqlite_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            rec = ClipRecord(
                video_id="abc123",
                source_url="https://www.youtube.com/watch?v=abc123",
                title="Epic clutch",
                hook="WAIT FOR IT",
                affiliate_id="partner-1",
                channel="team_a",
                published=True,
                publish_ts=_past_iso(4),
                rendered_path="outputs/rendered_clip.mp4",
            )
            self.assertTrue(store.record_clip(rec))

            row = store._conn.execute(
                "SELECT * FROM clips WHERE video_id=?", ("abc123",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["source_url"], rec.source_url)
            self.assertEqual(row["title"], "Epic clutch")
            self.assertEqual(row["hook"], "WAIT FOR IT")
            self.assertEqual(row["affiliate_id"], "partner-1")
            self.assertEqual(row["channel"], "team_a")
            self.assertEqual(row["published"], 1)
            self.assertEqual(row["publish_ts"], rec.publish_ts)
            self.assertEqual(row["rendered_path"], "outputs/rendered_clip.mp4")
            store.close()

    def test_record_is_idempotent_on_duplicate_video_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            rec1 = ClipRecord(video_id="dup", source_url="https://youtu.be/dup", title="first")
            rec2 = ClipRecord(video_id="dup", source_url="https://youtu.be/dup", title="second")

            self.assertTrue(store.record_clip(rec1))
            self.assertFalse(store.record_clip(rec2))

            count = store._conn.execute("SELECT COUNT(*) AS n FROM clips").fetchone()["n"]
            self.assertEqual(count, 1)
            store.close()

    def test_mark_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_clip(ClipRecord(video_id="p1", source_url="https://youtu.be/p1"))

            store.mark_published("p1", publish_ts=_today_iso(12))
            row = store._conn.execute(
                "SELECT * FROM clips WHERE video_id=?", ("p1",)
            ).fetchone()
            self.assertEqual(row["published"], 1)
            self.assertEqual(row["publish_ts"], _today_iso(12))

            # A later mark with no timestamp keeps the existing publish_ts.
            store.mark_published("p1")
            row = store._conn.execute(
                "SELECT * FROM clips WHERE video_id=?", ("p1",)
            ).fetchone()
            self.assertEqual(row["publish_ts"], _today_iso(12))
            store.close()

    def test_record_publish_updates_metadata_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_clip(ClipRecord(video_id="pub1", source_url="https://youtu.be/pub1"))

            store.record_publish(
                "pub1",
                "youtube",
                platform_id="vid123",
                short_url="https://youtube.com/shorts/vid123",
            )
            row = store._conn.execute(
                "SELECT * FROM clips WHERE video_id=?", ("pub1",)
            ).fetchone()
            self.assertEqual(row["platform"], "youtube")
            self.assertEqual(row["platform_id"], "vid123")
            self.assertEqual(row["short_url"], "https://youtube.com/shorts/vid123")
            # A confirmed publish marks the row published and stamps a publish_ts.
            self.assertEqual(row["published"], 1)
            self.assertIsNotNone(row["publish_ts"])

            # Re-calling is idempotent: overwrites with the same shape, no error.
            store.record_publish(
                "pub1", "youtube", platform_id="vid999", short_url=None
            )
            row = store._conn.execute(
                "SELECT * FROM clips WHERE video_id=?", ("pub1",)
            ).fetchone()
            self.assertEqual(row["platform_id"], "vid999")
            self.assertIsNone(row["short_url"])
            self.assertEqual(row["published"], 1)

            # Unknown video_id is a silent no-op (no exception, no row).
            store.record_publish("missing", "youtube", platform_id="x")
            store.close()

    def test_recorded_ids_returns_every_video_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for vid in ("v1", "v2", "v3"):
                store.record_clip(ClipRecord(video_id=vid, source_url=f"https://youtu.be/{vid}"))
            self.assertEqual(store.recorded_ids(), {"v1", "v2", "v3"})
            store.close()

    def test_update_stats_coalesces(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_clip(ClipRecord(video_id="s1", source_url="https://youtu.be/s1"))

            store.update_stats("s1", views=100, likes=5, comments=2)
            row = store._conn.execute(
                "SELECT * FROM clips WHERE video_id=?", ("s1",)
            ).fetchone()
            self.assertEqual(row["views"], 100)
            self.assertEqual(row["likes"], 5)
            self.assertEqual(row["comments"], 2)
            self.assertIsNotNone(row["collected_at"])

            # None args must NOT clobber existing values.
            store.update_stats("s1", views=200, likes=None, comments=None)
            row = store._conn.execute(
                "SELECT * FROM clips WHERE video_id=?", ("s1",)
            ).fetchone()
            self.assertEqual(row["views"], 200)
            self.assertEqual(row["likes"], 5)
            self.assertEqual(row["comments"], 2)
            store.close()

    def test_unpublished_respects_min_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            # Old, never collected.
            store.record_clip(ClipRecord(video_id="old", source_url="https://youtu.be/old",
                                         publish_ts=_past_iso(6)))
            # Recent, never collected — excluded by an aggressive min_age.
            store.record_clip(ClipRecord(video_id="newish", source_url="https://youtu.be/new",
                                         publish_ts=_past_iso(1)))
            # Published but never collected — must appear.
            store.record_clip(ClipRecord(video_id="pub", source_url="https://youtu.be/pub",
                                         publish_ts=_past_iso(5)))
            store.mark_published("pub", publish_ts=_past_iso(5))
            # Published AND collected — excluded.
            store.record_clip(ClipRecord(video_id="done", source_url="https://youtu.be/done",
                                         published=True, publish_ts=_past_iso(1)))
            store.mark_published("done", publish_ts=_past_iso(1))
            store.update_stats("done", views=99)

            ids = {r["video_id"] for r in store.unpublished(min_age_seconds=2 * 3600)}
            self.assertEqual(ids, {"old", "pub"})

            ids = {r["video_id"] for r in store.unpublished(min_age_seconds=3 * 3600)}
            self.assertEqual(ids, {"old", "pub"})
            store.close()

    def test_top_hooks_groups_by_hook_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_clip(ClipRecord(video_id="h1", source_url="https://youtu.be/h1",
                                         hook="HYPE", channel="c1"))
            store.update_stats("h1", views=50)
            store.record_clip(ClipRecord(video_id="h2", source_url="https://youtu.be/h2",
                                         hook="HYPE", channel="c2"))
            store.update_stats("h2", views=30)
            store.record_clip(ClipRecord(video_id="h3", source_url="https://youtu.be/h3",
                                         hook="SLOW", channel="c1"))
            store.update_stats("h3", views=10)
            # No hook text — excluded from top hooks by design.
            store.record_clip(ClipRecord(video_id="h4", source_url="https://youtu.be/h4",
                                         hook=None, channel="c1"))
            store.update_stats("h4", views=200)

            hooks = store.top_hooks()
            self.assertEqual(hooks[0]["hook"], "HYPE")
            self.assertEqual(hooks[0]["avg_views"], 40.0)
            self.assertEqual(hooks[0]["count"], 2)
            self.assertEqual(hooks[1]["hook"], "SLOW")
            store.close()

    def test_top_partners_groups_by_affiliate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_clip(ClipRecord(video_id="h1", source_url="https://youtu.be/h1"))
            store.update_stats("h1", views=10)
            store.record_clip(ClipRecord(video_id="a", source_url="https://youtu.be/a",
                                         affiliate_id="partner-x"))
            store.update_stats("a", views=25)
            store.record_clip(ClipRecord(video_id="b", source_url="https://youtu.be/b",
                                         affiliate_id="partner-x"))
            store.update_stats("b", views=5)

            partners = store.top_partners()
            self.assertEqual(len(partners), 1)
            self.assertEqual(partners[0]["affiliate_id"], "partner-x")
            self.assertEqual(partners[0]["count"], 2)
            self.assertEqual(partners[0]["avg_views"], 15.0)
            store.close()

    def test_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_clip(ClipRecord(video_id="a", source_url="https://youtu.be/a",
                                         channel="c1", published=True,
                                         publish_ts=_today_iso(9)))
            store.update_stats("a", views=5)
            store.record_clip(ClipRecord(video_id="b", source_url="https://youtu.be/b",
                                         channel="c2", published=False))
            store.record_clip(ClipRecord(video_id="c", source_url="https://youtu.be/c",
                                         channel="c1", published=True,
                                         publish_ts=_today_iso(11)))
            store.update_stats("c", views=50)
            store.record_clip(ClipRecord(video_id="d", source_url="https://youtu.be/d",
                                         channel="c2"))
            store.update_stats("d", views=5)

            summary = store.summary()
            self.assertEqual(summary["produced"], 4)
            self.assertEqual(summary["published"], 2)
            self.assertEqual(summary["with_stats"], 3)
            self.assertEqual(summary["avg_views"], 20.0)  # (5 + 50 + 5) / 3
            self.assertEqual(summary["best_video_id"], "c")
            self.assertEqual(summary["best_title"], "")
            self.assertEqual(summary["best_views"], 50)
            store.close()

    def test_should_publish_today_counts_only_today_published_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            channel = "team_a"
            for i in range(4):
                store.record_clip(ClipRecord(
                    video_id=f"v{i}",
                    source_url=f"https://youtu.be/v{i}",
                    channel=channel,
                    published=True,
                    publish_ts=_today_iso(8 + i),
                ))
            # A published row from yesterday or another channel must not count.
            yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
            store.record_clip(ClipRecord(
                video_id="y", source_url="https://youtu.be/y", channel=channel,
                published=True, publish_ts=f"{yesterday}T09:00:00+00:00",
            ))
            store.record_clip(ClipRecord(
                video_id="other", source_url="https://youtu.be/other", channel="team_b",
                published=True, publish_ts=_today_iso(12),
            ))

            self.assertTrue(should_publish_today(store, channel, 5))
            self.assertFalse(should_publish_today(store, channel, 4))

            # Unpublished rows do not count toward the cap.
            store.record_clip(ClipRecord(
                video_id="unpub", source_url="https://youtu.be/unpub", channel=channel,
                published=False, publish_ts=_today_iso(13),
            ))
            self.assertTrue(should_publish_today(store, channel, 5))
            store.close()


class SettingsMetricsTests(unittest.TestCase):
    def _empty_env_path(self, temp_dir: str) -> Path:
        return Path(temp_dir) / ".env"

    def test_metrics_path_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_METRICS_PATH": "var/custom_metrics.sqlite"}):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.metrics_path, Path("var/custom_metrics.sqlite"))

    def test_metrics_path_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _blank_env():
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.metrics_path, Path("data/metrics.sqlite"))

    def test_factory_daily_cap_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_FACTORY_DAILY_CAP": "12"}):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.factory_daily_cap, 12)

    def test_factory_daily_cap_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _blank_env():
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.factory_daily_cap, 6)

    def test_factory_daily_cap_invalid_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_FACTORY_DAILY_CAP": "not-a-number"}):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.factory_daily_cap, 6)

    def test_title_variant_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_TITLE_VARIANT": "3"}):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.title_variant, 3)

    def test_title_variant_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _blank_env():
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.title_variant, -1)

    def test_title_variant_invalid_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_TITLE_VARIANT": "nope"}):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.title_variant, -1)

    def test_publish_hour_start_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_FACTORY_PUBLISH_HOUR_START": "22"}):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.factory_publish_hour_start, 22)

    def test_publish_hour_end_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_FACTORY_PUBLISH_HOUR_END": "6"}):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertEqual(settings.factory_publish_hour_end, 6)

    def test_publish_hour_default_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _blank_env():
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertIsNone(settings.factory_publish_hour_start)
            self.assertIsNone(settings.factory_publish_hour_end)

    def test_publish_hour_invalid_falls_back_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "SHORTS_FACTORY_PUBLISH_HOUR_START": "late",
                    "SHORTS_FACTORY_PUBLISH_HOUR_END": "evening",
                },
            ):
                settings = Settings.from_env(self._empty_env_path(tmp))
            self.assertIsNone(settings.factory_publish_hour_start)
            self.assertIsNone(settings.factory_publish_hour_end)


class GeminiTitleCandidatesTests(unittest.TestCase):
    def test_generate_clip_metadata_exposes_candidates(self):
        from unittest.mock import MagicMock

        from shorts_clipper.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="test")
        mock_response = MagicMock()
        mock_response.text = (
            '{"candidates": ['
            '  {"title": "First title", "total": 90},'
            '  {"title": "Second title", "total": 80},'
            '  {"title": "Third title", "total": 70}'
            '], "selected_title": "Second title",'
            ' "description": "A description.", "tags": ["cs2", "shorts"]}'
        )
        with patch.object(provider, "generate_content", return_value=mock_response):
            meta = provider.generate_clip_metadata([])

        self.assertEqual(
            meta["candidates"], ["First title", "Second title", "Third title"]
        )
        self.assertEqual(meta["title"], "Second title")

    def test_generate_clip_metadata_candidates_empty_when_absent(self):
        from unittest.mock import MagicMock

        from shorts_clipper.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="test")
        mock_response = MagicMock()
        mock_response.text = (
            '{"selected_title": "Only title",'
            ' "description": "A description.", "tags": ["cs2"]}'
        )
        with patch.object(provider, "generate_content", return_value=mock_response):
            meta = provider.generate_clip_metadata([])

        self.assertEqual(meta["candidates"], [])
        self.assertEqual(meta["title"], "Only title")


class CollectMetricsUrlTests(unittest.TestCase):
    def test_short_url_preferred_over_source_url(self):
        mod = _load_collect_metrics_module()
        row = {
            "source_url": "https://www.youtube.com/watch?v=SOURCE_ID",
            "short_url": "https://youtube.com/shorts/PUBLISHED_ID",
        }
        self.assertEqual(mod._stats_url(row), "https://youtube.com/shorts/PUBLISHED_ID")

    def test_non_http_short_url_ignored(self):
        mod = _load_collect_metrics_module()
        row = {
            "source_url": "https://youtu.be/SOURCE_ID",
            "short_url": "youtube.com/shorts/abc",
        }
        self.assertEqual(mod._stats_url(row), "https://youtu.be/SOURCE_ID")

    def test_unpublished_falls_back_to_source_url(self):
        mod = _load_collect_metrics_module()
        row = {"source_url": "https://youtu.be/SOURCE_ID", "short_url": ""}
        self.assertEqual(mod._stats_url(row), "https://youtu.be/SOURCE_ID")


class RunnerMarkProcessedTests(unittest.TestCase):
    """W2-5/W2-2: ``processed_check_enabled`` is the single gate for the
    processed-mark, and the canonical source ``video_id`` is threaded into
    ``engine.publish`` so ``record_publish`` lands on the same metrics row
    ``record_clip`` created."""

    URL = "https://www.youtube.com/watch?v=dummy123"

    def _settings(self, processed_check_enabled: bool):
        from dataclasses import replace

        from shorts_clipper.core.settings import Settings

        return replace(
            Settings.from_env(),
            processed_check_enabled=processed_check_enabled,
            bgm_mode="off",
            stream_audio_energy_enabled=False,
        )

    def _preselected(self):
        from shorts_clipper.core.models import ClipWindow

        return [(ClipWindow(start=10.0, end=20.0), "center")]

    def _open_mocks(self, *, mock_engine: bool):
        from contextlib import ExitStack

        from shorts_clipper.core.models import TranscriptSegment

        segments = [TranscriptSegment(start=0.0, end=10.0, text="Text", words=[])]
        stack = ExitStack()
        stack.enter_context(
            patch("shorts_clipper.pipeline.runner.fetch_subtitles", return_value=segments)
        )
        stack.enter_context(patch("shorts_clipper.pipeline.runner.download_audio"))
        stack.enter_context(
            patch("shorts_clipper.pipeline.runner.transcribe_clip", return_value=segments)
        )
        stack.enter_context(patch("shorts_clipper.pipeline.runner.download_clip"))
        stack.enter_context(patch("shorts_clipper.pipeline.runner.process_to_vertical"))
        stack.enter_context(patch("shorts_clipper.pipeline.runner.burn_subtitles"))
        stack.enter_context(patch("shorts_clipper.rendering.thumbnailer.extract_thumbnail"))
        gem = stack.enter_context(patch("shorts_clipper.pipeline.runner.GeminiProvider"))
        finisher = stack.enter_context(
            patch("shorts_clipper.pipeline.runner.EditorialFinisher")
        )
        store = stack.enter_context(
            patch("shorts_clipper.core.processed_store.ProcessedStore")
        )
        engine = (
            stack.enter_context(patch("shorts_clipper.pipeline.runner.PublishingEngine"))
            if mock_engine
            else None
        )
        return stack, gem, finisher, store, engine

    def _seed(self, gem, finisher) -> None:
        from shorts_clipper.core.models import ClipWindow

        gem.return_value.generate_clip_metadata.return_value = {
            "title": "Title",
            "description": "Description",
            "tags": ["cs2"],
        }
        finisher.return_value.snap_boundaries.side_effect = (
            lambda start, end, *args, **kwargs: ClipWindow(start=start, end=end)
        )

    def test_mark_processed_skipped_when_flag_disabled(self):
        from shorts_clipper.pipeline.runner import run

        stack, gem, finisher, store, _ = self._open_mocks(mock_engine=False)
        with stack:
            self._seed(gem, finisher)
            result = run(
                self.URL,
                settings=self._settings(processed_check_enabled=False),
                count=1,
                upload=False,
                preselected_clips=self._preselected(),
            )

        self.assertIsInstance(result, Path)
        # Gate disabled: neither the entry check nor the mark touches the store.
        store.from_path.assert_not_called()

    def test_mark_processed_recorded_when_flag_enabled(self):
        from shorts_clipper.pipeline.runner import run

        stack, gem, finisher, store, _ = self._open_mocks(mock_engine=False)
        with stack:
            self._seed(gem, finisher)
            store.from_path.return_value.is_processed.return_value = False
            result = run(
                self.URL,
                settings=self._settings(processed_check_enabled=True),
                count=1,
                upload=False,
                preselected_clips=self._preselected(),
            )

        self.assertIsInstance(result, Path)
        store.from_path.return_value.mark_processed.assert_called_once_with(
            "dummy123", self.URL, title=None
        )

    def test_mark_processed_skipped_on_publish_failure(self):
        from shorts_clipper.pipeline.runner import run

        stack, gem, finisher, store, engine = self._open_mocks(mock_engine=True)
        with stack:
            self._seed(gem, finisher)
            store.from_path.return_value.is_processed.return_value = False
            engine.return_value.publish.side_effect = RuntimeError("upload boom")
            result = run(
                self.URL,
                settings=self._settings(processed_check_enabled=True),
                count=1,
                upload=True,
                preselected_clips=self._preselected(),
            )

        self.assertIsInstance(result, Path)
        engine.return_value.publish.assert_called_once()
        self.assertEqual(
            engine.return_value.publish.call_args.kwargs["video_id"], "dummy123"
        )
        # A failed publish prevents the processed-mark so the video can retry.
        store.from_path.return_value.mark_processed.assert_not_called()

    def test_runner_publish_passes_canonical_video_id(self):
        from shorts_clipper.pipeline.runner import run
        from shorts_clipper.publishers.models import PublishResult

        stack, gem, finisher, store, engine = self._open_mocks(mock_engine=True)
        with stack:
            self._seed(gem, finisher)
            store.from_path.return_value.is_processed.return_value = False
            engine.return_value.publish.return_value = {
                "youtube": PublishResult(
                    "youtube",
                    True,
                    "https://youtube.com/shorts/dummy123",
                    "dummy123",
                    "2026-01-01T00:00:00Z",
                )
            }
            result = run(
                self.URL,
                settings=self._settings(processed_check_enabled=True),
                count=1,
                upload=True,
                preselected_clips=self._preselected(),
            )

        self.assertIsInstance(result, Path)
        engine.return_value.publish.assert_called_once()
        video_id = engine.return_value.publish.call_args.kwargs["video_id"]
        self.assertEqual(video_id, "dummy123")
        self.assertTrue(video_id)

    def test_record_publish_matches_row_inserted_by_record_clip(self):
        from shorts_clipper.core.settings import Settings as CoreSettings
        from shorts_clipper.publishers import manager as mgr
        from shorts_clipper.publishers.models import PublishResult

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            store = MetricsStore(db_path)
            store.record_clip(ClipRecord(video_id="dummy123", source_url=self.URL))
            store.close()

            engine = mgr.PublishingEngine()
            engine._video_id = "dummy123"
            result = PublishResult(
                "youtube",
                True,
                "https://youtube.com/shorts/dummy123",
                "dummy123",
                "2026-01-01T00:00:00Z",
            )
            with patch.object(
                mgr.Settings, "from_env", return_value=CoreSettings(metrics_path=db_path)
            ):
                engine._record_publish("youtube", result)

            reader = MetricsStore(db_path)
            try:
                row = reader._conn.execute(
                    "SELECT * FROM clips WHERE video_id=?", ("dummy123",)
                ).fetchone()
            finally:
                reader.close()
            self.assertIsNotNone(row)
            self.assertEqual(row["platform_id"], "dummy123")
            self.assertEqual(row["short_url"], "https://youtube.com/shorts/dummy123")
            self.assertEqual(row["published"], 1)


if __name__ == "__main__":
    unittest.main()