import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from shorts_clipper.scout.channel_vods import YouTubeChannelVodsProvider
from shorts_clipper.scout.source_scout import SourceVideo, available_providers, scout
from shorts_clipper.scout.trends import rank_vods, score_vod

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


def make_video(video_id, *, title="", duration_seconds=None, view_count=None, upload_date=None):
    extra = {}
    if view_count is not None:
        extra["view_count"] = view_count
    if upload_date is not None:
        extra["upload_date"] = upload_date
    return SourceVideo(
        url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
        title=title,
        duration_seconds=duration_seconds,
        platform="youtube",
        license="",
        extra=extra,
    )


class ScoreVodTests(unittest.TestCase):
    def test_recency_full_marks_inside_three_days(self):
        uploaded = (NOW - timedelta(days=1)).strftime("%Y%m%d")
        video = make_video("a", upload_date=uploaded)
        self.assertEqual(score_vod(video, now=NOW), 50.0)

    def test_recency_vanishes_after_window(self):
        uploaded = (NOW - timedelta(days=30)).strftime("%Y%m%d")
        video = make_video("a", upload_date=uploaded)
        self.assertEqual(score_vod(video, now=NOW), 25.0)

    def test_recency_linear_decay(self):
        uploaded = (NOW - timedelta(days=8.5)).strftime("%Y%m%d")
        video = make_video("a", upload_date=uploaded)
        self.assertAlmostEqual(score_vod(video, now=NOW), 12.5 + 15 + 10, delta=1.5)

    def test_view_velocity_log_and_cap(self):
        low = make_video("low", view_count=1000)
        high = make_video("high", view_count=1_000_000)
        huge = make_video("huge", view_count=1_000_000_000)
        low_score = score_vod(low, now=NOW)
        high_score = score_vod(high, now=NOW)
        self.assertGreater(high_score, low_score)
        self.assertEqual(high_score, score_vod(huge, now=NOW))
        self.assertEqual(high_score, 50.0)  # recency 10 + velocity 30 + duration 10

    def test_missing_views_defaults(self):
        video = make_video("a")
        self.assertEqual(score_vod(video, now=NOW), 35.0)  # 10 recency + 15 views + 10 duration

    def test_keyword_bonus_with_emphasis(self):
        video = make_video("a", title="INSANE ACE CLUTCH!!!")
        self.assertEqual(score_vod(video, now=NOW), 53.0)  # 10 + 15 + 18 + 10

    def test_keyword_cap(self):
        title = "MAJOR QUALIFIER ACE 1v5 2016 FINAL LAST ROUND OVERTIME RECORD POWERFUL BEST INSANE"
        video = make_video("a", title=title)
        self.assertEqual(score_vod(video, now=NOW), 60.0)  # 10 + 15 + 25 + 10

    def test_duration_bands(self):
        cases = {
            900: 30.0,  # 15 min -> 5
            1800: 45.0,  # 30 min -> 20
            4500: 40.0,  # 75 min -> 15
            7200: 30.0,  # 120 min -> 5
            300: 25.0,  # 5 min -> 0
            None: 35.0,  # unknown -> 10
        }
        for duration, expected in cases.items():
            video = make_video("a", duration_seconds=duration)
            self.assertEqual(score_vod(video, now=NOW), expected, f"duration={duration}")

    def test_score_deterministic(self):
        video = make_video(
            "a", title="Clutch ace", duration_seconds=2400, view_count=50000,
            upload_date=(NOW - timedelta(days=2)).strftime("%Y%m%d"),
        )
        self.assertEqual(score_vod(video, now=NOW), score_vod(video, now=NOW))


class RankVodsTests(unittest.TestCase):
    def setUp(self):
        self.videos = [
            make_video("v_low", view_count=1000),
            make_video("v_high", view_count=2_000_000),
            make_video("v_mid", view_count=100_000),
        ]

    def test_orders_by_score_desc(self):
        ranked = rank_vods(self.videos)
        self.assertEqual([v.video_id for v in ranked], ["v_high", "v_mid", "v_low"])

    def test_ties_break_by_video_id(self):
        a = make_video("zzz", view_count=1000)
        b = make_video("aaa", view_count=1000)
        ranked = rank_vods([a, b])
        self.assertEqual([v.video_id for v in ranked], ["aaa", "zzz"])

    def test_min_recent_days_filters(self):
        fresh = make_video("fresh", upload_date=(datetime.now(UTC) - timedelta(days=1)).strftime("%Y%m%d"))
        stale = make_video("stale", upload_date=(datetime.now(UTC) - timedelta(days=30)).strftime("%Y%m%d"))
        no_date = make_video("nodate")
        ranked = rank_vods([stale, fresh, no_date], min_recent_days=7)
        self.assertEqual([v.video_id for v in ranked], ["fresh"])

    def test_max_results(self):
        ranked = rank_vods(self.videos, max_results=2)
        self.assertEqual(len(ranked), 2)


def _proc(stdout: str, returncode: int = 0) -> mock.Mock:
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


class ChannelVodsProviderTests(unittest.TestCase):
    def test_parses_flat_playlist_lines(self):
        items = [
            {
                "id": "c123",
                "title": "CS2 Clutch",
                "duration": 5400,
                "view_count": 12000,
                "upload_date": "20260911",
                "channel": "Pimp",
            },
            {"id": "c456", "title": "No durations"},
        ]
        stdout = "\n".join(json.dumps(it) for it in items)
        with mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            return_value=_proc(stdout),
        ) as fake_run:
            results = YouTubeChannelVodsProvider().search(
                "cs2", limit=10, channel_urls=["https://www.youtube.com/@Pimpmuckl/videos"]
            )

        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first.video_id, "c123")
        self.assertEqual(first.url, "https://www.youtube.com/watch?v=c123")
        self.assertEqual(first.title, "CS2 Clutch")
        self.assertEqual(first.duration_seconds, 5400.0)
        self.assertEqual(first.extra["channel"], "Pimp")
        self.assertEqual(first.extra["view_count"], 12000)
        self.assertEqual(first.extra["upload_date"], "20260911")
        cmd = fake_run.call_args.args[0]
        self.assertIn("--flat-playlist", cmd)
        self.assertIn("https://www.youtube.com/@Pimpmuckl/videos", cmd)

    def test_injected_channels_skip_config(self):
        def booom(*args, **kwargs):
            raise AssertionError("config must not be read when channel_urls provided")

        with mock.patch("shorts_clipper.scout.channel_vods._config_path", side_effect=booom), mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            return_value=_proc(""),
        ):
            results = YouTubeChannelVodsProvider().search(
                "cs2", limit=5, channel_urls=["https://www.youtube.com/@X/videos"]
            )
        self.assertEqual(results, [])

    def test_config_loading_from_file(self):
        config = {
            "sources": [
                {
                    "title": "Pimp",
                    "platform": "youtube",
                    "uri": "https://www.youtube.com/@Pimpmuckl/videos",
                    "enabled": True,
                    "tags": ["cs2"],
                },
                {
                    "title": "Off",
                    "platform": "youtube",
                    "uri": "https://www.youtube.com/@off/videos",
                    "enabled": False,
                    "tags": [],
                },
                {"title": "Twitch", "platform": "twitch", "uri": "https://twitch.tv/x/videos", "enabled": True},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vod_sources.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch("shorts_clipper.scout.channel_vods._config_path", return_value=path), mock.patch(
                "shorts_clipper.scout.channel_vods.subprocess.run",
                return_value=_proc(json.dumps({"id": "c1", "title": "T", "channel": "Pimp"})),
            ) as fake_run:
                results = YouTubeChannelVodsProvider().search("cs2", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].video_id, "c1")
        self.assertEqual(results[0].extra["tags"], ["cs2"])
        used_uri = fake_run.call_args.args[0][-1]
        self.assertEqual(used_uri, "https://www.youtube.com/@Pimpmuckl/videos")

    def test_dedupes_across_channels_first_seen_wins(self):
        line = json.dumps({"id": "dup", "title": "Same VOD", "channel": "C"})
        uris = ["https://www.youtube.com/@A/videos", "https://www.youtube.com/@B/videos"]
        with mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            return_value=_proc(line),
        ) as fake_run:
            results = YouTubeChannelVodsProvider().search("cs2", limit=10, channel_urls=uris)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].extra["channel"], "C")
        self.assertEqual(fake_run.call_count, 2)

    def test_respects_limit_across_channels(self):
        line = json.dumps({"id": "v", "title": "VOD"})
        uris = ["https://www.youtube.com/@A/videos", "https://www.youtube.com/@B/videos"]
        with mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            return_value=_proc(line),
        ):
            results = YouTubeChannelVodsProvider().search("cs2", limit=1, channel_urls=uris)
        self.assertEqual(len(results), 1)

    def test_exclude_ids_skipped(self):
        line = json.dumps({"id": "seen", "title": "Already processed"})
        with mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            return_value=_proc(line),
        ):
            results = YouTubeChannelVodsProvider().search(
                "cs2", limit=5, channel_urls=["https://www.youtube.com/@A/videos"],
                exclude_ids={"seen"},
            )
        self.assertEqual(results, [])

    def test_timeout_returns_empty(self):
        with mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 60),
        ):
            results = YouTubeChannelVodsProvider().search(
                "cs2", limit=5, channel_urls=["https://www.youtube.com/@A/videos"]
            )
        self.assertEqual(results, [])

    def test_nonzero_returncode_returns_empty(self):
        with mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            return_value=_proc("bogus", returncode=1),
        ):
            results = YouTubeChannelVodsProvider().search(
                "cs2", limit=5, channel_urls=["https://www.youtube.com/@A/videos"]
            )
        self.assertEqual(results, [])

    def test_registered_and_scoutable(self):
        self.assertIn("youtube_channels", available_providers())
        line = json.dumps({"id": "scout1", "title": "Scouted VOD", "channel": "C"})
        with mock.patch(
            "shorts_clipper.scout.channel_vods.subprocess.run",
            return_value=_proc(line),
        ):
            results = scout(
                "cs2",
                providers=("youtube_channels",),
                limit=5,
                channel_urls=["https://www.youtube.com/@A/videos"],
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://www.youtube.com/watch?v=scout1")


if __name__ == "__main__":
    unittest.main()
