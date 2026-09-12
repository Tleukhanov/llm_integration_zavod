"""Tests for the closed metrics -> scoring feedback loop.

Covers ``MetricsStore.channel_performance`` aggregation, the
``score_vod``/``rank_vods`` feedback bonus, and the ``auto_discover`` wiring
that threads a performance map into ranking. No network access: the metrics
DB is a temporary sqlite file and scout providers are mocked.
"""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_clipper.core.metrics import ClipRecord, MetricsStore
from shorts_clipper.scout.auto_batch import auto_discover
from shorts_clipper.scout.source_scout import SourceVideo
from shorts_clipper.scout.trends import feedback_bonus, rank_vods, score_vod

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


def make_video(
    video_id,
    *,
    title="",
    duration_seconds=None,
    view_count=None,
    upload_date=None,
    channel=None,
):
    extra = {}
    if view_count is not None:
        extra["view_count"] = view_count
    if upload_date is not None:
        extra["upload_date"] = upload_date
    if channel is not None:
        extra["channel"] = channel
    return SourceVideo(
        url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
        title=title,
        duration_seconds=duration_seconds,
        platform="youtube",
        license="",
        extra=extra,
    )


def _seed_channel_clip(db: MetricsStore, channel: str, views: int | None, published: bool) -> None:
    video_id = f"{channel}_{views}_{published}_{id(db)}"
    db.record_clip(
        ClipRecord(
            video_id=video_id,
            source_url=f"https://youtu.be/{video_id}",
            channel=channel,
            published=published,
        )
    )
    db.update_stats(video_id, views=views)


class ChannelPerformanceTests(unittest.TestCase):
    def _store(self, tmp: str) -> MetricsStore:
        return MetricsStore(Path(tmp) / "metrics.sqlite")

    def test_aggregates_and_orders_by_avg_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            # hit channel: published clips with views, one published without
            # stats, and one unpublished-but-collected clip.
            _seed_channel_clip(store, "hit", 100, True)
            _seed_channel_clip(store, "hit", 300, True)
            store.record_clip(ClipRecord(
                video_id="hit_nostats", source_url="https://youtu.be/hit_nostats",
                channel="hit", published=True,
            ))
            _seed_channel_clip(store, "hit", 999, False)
            # weak channel: two published, low-view clips.
            _seed_channel_clip(store, "weak", 5, True)
            _seed_channel_clip(store, "weak", 7, True)

            rows = store.channel_performance()
            self.assertEqual([r["channel"] for r in rows], ["hit", "weak"])

            hit = rows[0]
            self.assertEqual(hit["produced"], 4)
            self.assertEqual(hit["published"], 3)
            self.assertEqual(hit["with_stats"], 3)
            self.assertEqual(hit["views"], 1399)  # 100 + 300 + 999 (sum over all rows)
            self.assertEqual(hit["likes"], 0)
            self.assertEqual(hit["comments"], 0)
            # avg/median only over published clips with non-NULL views.
            self.assertEqual(hit["avg_views"], 200.0)
            self.assertEqual(hit["median_views"], 200.0)

            weak = rows[1]
            self.assertEqual(weak["published"], 2)
            self.assertEqual(weak["avg_views"], 6.0)
            self.assertEqual(weak["median_views"], 6.0)
            store.close()

    def test_median_for_even_and_odd_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for views in (10, 30, 40, 50):
                _seed_channel_clip(store, "even_c", views, True)
            for views in (10, 30, 50):
                _seed_channel_clip(store, "odd_c", views, True)

            rows = {r["channel"]: r for r in store.channel_performance()}
            self.assertEqual(rows["even_c"]["avg_views"], 32.5)
            self.assertEqual(rows["even_c"]["median_views"], 35.0)  # (30 + 40) / 2
            self.assertEqual(rows["odd_c"]["avg_views"], 30.0)
            self.assertEqual(rows["odd_c"]["median_views"], 30.0)
            store.close()

    def test_limit_caps_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for name, views in (("c1", 100), ("c2", 200), ("c3", 300)):
                _seed_channel_clip(store, name, views, True)
            rows = store.channel_performance(limit=2)
            self.assertEqual([r["channel"] for r in rows], ["c3", "c2"])
            store.close()

    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertEqual(store.channel_performance(), [])
            store.close()

    def test_unpublished_and_null_views_never_dilute_avg(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            _seed_channel_clip(store, "c", 100, True)
            _seed_channel_clip(store, "c", 1000, False)
            store.record_clip(ClipRecord(
                video_id="no_views", source_url="https://youtu.be/no_views",
                channel="c", published=True,
            ))
            row = store.channel_performance()[0]
            self.assertEqual(row["avg_views"], 100.0)
            self.assertEqual(row["median_views"], 100.0)
            store.close()


class ScoreVodFeedbackTests(unittest.TestCase):
    def test_strong_channel_gets_full_cap_bonus(self):
        video = make_video("a", channel="hit")
        base = score_vod(video, now=NOW)  # no feedback: 10 + 15 + 10 = 35
        self.assertEqual(base, 35.0)
        perf = {"hit": 200_000.0}
        self.assertEqual(score_vod(video, now=NOW, performance=perf), base + 10.0)
        self.assertEqual(feedback_bonus("hit", perf), 10.0)

    def test_partial_bonus_scales_with_avg_views(self):
        perf = {"weak": 5_000.0}
        self.assertEqual(feedback_bonus("weak", perf), 0.5)  # 10 * 5000 / 100000
        self.assertEqual(feedback_bonus("ok", {"ok": 50_000.0}), 5.0)

    def test_unknown_channel_and_empty_maps_give_zero(self):
        video = make_video("a", channel="proven")
        base = score_vod(video, now=NOW)
        self.assertEqual(feedback_bonus("unknown", {"proven": 200_000.0}), 0.0)
        self.assertEqual(feedback_bonus("proven", {}), 0.0)
        self.assertEqual(feedback_bonus("proven", None), 0.0)
        self.assertEqual(
            score_vod(video, now=NOW, performance={"other": 200_000.0}), base
        )

    def test_backward_compatible_defaults(self):
        video = make_video("a", title="CLUTCH", view_count=50_000, duration_seconds=2400)
        no_args = score_vod(video, now=NOW)
        self.assertEqual(no_args, score_vod(video, now=NOW, performance=None))
        self.assertEqual(no_args, score_vod(video, now=NOW, performance={}))
        self.assertEqual(no_args, score_vod(video, now=NOW, performance={"nope": 999}))

    def test_bonus_never_exceeds_cap(self):
        self.assertEqual(feedback_bonus("c", {"c": float(10**9)}), 10.0)
        self.assertLessEqual(feedback_bonus("c", {"c": 2_000_000.0}), 10.0)

    def test_total_score_is_clamped_at_100(self):
        title = "MAJOR QUALIFIER ACE 1v5 2016 FINAL LAST ROUND OVERTIME RECORD POWERFUL BEST INSANE"
        video = make_video(
            "a",
            title=title,
            duration_seconds=2400,
            view_count=1_000_000_000,
            upload_date=(NOW - timedelta(days=1)).strftime("%Y%m%d"),
            channel="hit",
        )
        self.assertEqual(score_vod(video, now=NOW), 100.0)
        self.assertEqual(score_vod(video, now=NOW, performance={"hit": 200_000.0}), 100.0)


class RankVodsFeedbackTests(unittest.TestCase):
    def test_rank_applies_performance_bonus(self):
        videos = [
            make_video("a_vod", channel="unknown", view_count=50_000),
            make_video("b_vod", channel="hit", view_count=50_000),
        ]
        # Without feedback both tie at 50 -> a_vod wins on video_id.
        plain = rank_vods(videos)
        self.assertEqual([v.video_id for v in plain], ["a_vod", "b_vod"])
        # With feedback the proven channel overtakes despite the id order.
        ranked = rank_vods(videos, performance={"hit": 200_000.0})
        self.assertEqual([v.video_id for v in ranked], ["b_vod", "a_vod"])


class AutoBatchFeedbackTests(unittest.TestCase):
    def _settings(self, processed_path: Path, metrics_path=None):
        return SimpleNamespace(
            processed_videos_path=str(processed_path),
            metrics_path=str(metrics_path) if metrics_path else None,
        )

    def _seed_metrics(self, db_path: Path, channel: str, views: int) -> None:
        store = MetricsStore(db_path)
        try:
            for i in range(3):
                video_id = f"m{i}"
                store.record_clip(ClipRecord(
                    video_id=video_id,
                    source_url=f"https://youtu.be/{video_id}",
                    channel=channel,
                    published=True,
                ))
                store.update_stats(video_id, views=views)
        finally:
            store.close()

    def test_auto_discover_threads_performance_into_ranking(self):
        videos = [
            make_video("b_vod", channel="hit_channel", view_count=50_000),
            make_video("a_vod", channel="unknown_channel", view_count=50_000),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            self._seed_metrics(db_path, "hit_channel", 200_000)
            settings = self._settings(Path(tmp) / "processed.json", metrics_path=db_path)
            with mock.patch("shorts_clipper.scout.auto_batch.scout", return_value=videos):
                out = auto_discover(settings, query="cs2", providers=("youtube",), max_results=10)
            self.assertEqual([item["video_id"] for item in out], ["b_vod", "a_vod"])
            self.assertGreater(out[0]["score"], out[1]["score"])
            self.assertEqual(out[0]["score"], round(score_vod(videos[0], performance={"hit_channel": 200_000.0}), 1))

    def test_auto_discover_scores_tie_without_performance(self):
        videos = [
            make_video("b_vod", channel="hit_channel", view_count=50_000),
            make_video("a_vod", channel="unknown_channel", view_count=50_000),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp) / "processed.json")  # no metrics_path
            with mock.patch("shorts_clipper.scout.auto_batch.scout", return_value=videos):
                out = auto_discover(settings, query="cs2", providers=("youtube",), max_results=10)
            self.assertEqual([item["video_id"] for item in out], ["a_vod", "b_vod"])
            self.assertEqual(out[0]["score"], out[1]["score"])

    def test_auto_discover_missing_metrics_db_is_graceful(self):
        videos = [make_video("a_vod", channel="hit_channel", view_count=50_000)]
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp) / "processed.json")
            with mock.patch("shorts_clipper.scout.auto_batch.scout", return_value=videos):
                out = auto_discover(
                    settings,
                    query="cs2",
                    providers=("youtube",),
                    max_results=10,
                    metrics_db=str(Path(tmp) / "missing.sqlite"),
                )
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["video_id"], "a_vod")
            self.assertEqual(out[0]["score"], round(score_vod(videos[0]), 1))

    def test_auto_discover_empty_metrics_db_is_graceful(self):
        videos = [make_video("a_vod", channel="hit_channel", view_count=50_000)]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "metrics.sqlite"
            store = MetricsStore(db_path)  # empty table
            store.close()
            settings = self._settings(Path(tmp) / "processed.json", metrics_path=db_path)
            with mock.patch("shorts_clipper.scout.auto_batch.scout", return_value=videos):
                out = auto_discover(settings, query="cs2", providers=("youtube",), max_results=10)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["score"], round(score_vod(videos[0]), 1))


if __name__ == "__main__":
    unittest.main()