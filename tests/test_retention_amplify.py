"""Tests for the retention-driven publish amplification gate.

Covers settings parsing, the shared A/B/C/D grade computation (reused from the
retention-report CLI), the decision function, the manager's publish-path gate
(including the per-pair amplify factor budget) and the runner one-time grade
refresh. No network access; publishes use throwaway mock publishers.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from shorts_clipper.core.settings import Settings
from shorts_clipper.editorial.profiles import EditorialProfile
from shorts_clipper.editorial.retention_amplify import (
    PublishDecision,
    RetentionGrade,
    current_retention_grades,
    decide_publish,
)
from shorts_clipper.pipeline.runner import _refresh_retention_grades
from shorts_clipper.publishers import (
    ClipMetadata,
    PublisherRegistry,
    PublishingEngine,
    PublishResult,
)
from shorts_clipper.publishers.base import Publisher


def _past_iso(days: int) -> str:
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


def _blank_env():
    """Empty .env file + a pristine os.environ for Settings.from_env."""
    return patch.dict(os.environ, {}, clear=True)


class _YtPublisher(Publisher):
    @property
    def platform_name(self) -> str:
        return "youtube"

    def authenticate(self) -> None:
        pass

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        return PublishResult("youtube", True, "http://yt", "yt123")

    def verify(self, platform_id: str) -> bool:
        return True


class _IgPublisher(Publisher):
    @property
    def platform_name(self) -> str:
        return "instagram"

    def authenticate(self) -> None:
        pass

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        return PublishResult("instagram", True, "http://ig", "ig123")

    def verify(self, platform_id: str) -> bool:
        return True


@pytest.fixture
def mock_registry():
    original = dict(PublisherRegistry._publishers)
    PublisherRegistry._publishers.clear()
    PublisherRegistry.register(_YtPublisher)
    PublisherRegistry.register(_IgPublisher)
    yield
    PublisherRegistry._publishers = original


@pytest.fixture
def mock_r2():
    with patch("shorts_clipper.publishers.manager.R2Storage") as mock:
        instance = mock.return_value
        instance.upload.return_value = "mock_key"
        instance.generate_signed_url.return_value = "http://mock_signed_url"
        yield mock


@pytest.fixture
def amp_settings(tmp_path):
    """Settings with the amplifier ON and compliance off."""
    return Settings(
        compliance_enabled=False,
        metrics_path=str(tmp_path / "metrics.sqlite"),
        retention_amplify=True,
        retention_min_grade="B",
        retention_amplify_factor=1.5,
    )


def _seed_grades(db_path: Path) -> dict[tuple[str | None, str | None], RetentionGrade]:
    _seed(
        db_path,
        [
            # (tech, youtube): median engagement 0.9 -> publish_rate 1.0 * 0.9 = A
            {"platform": "youtube", "niche": "tech", "published": True,
             "views": 100, "likes": 90, "comments": 0},
            # (tech, instagram): engagement 0.3 -> C (below B -> skip)
            {"platform": "instagram", "niche": "tech", "published": True,
             "views": 100, "likes": 30, "comments": 0},
        ],
    )
    return current_retention_grades(Settings(metrics_path=db_path))


class RetentionAmplifySettingsTests(unittest.TestCase):
    def _empty_env_path(self, temp_dir: str) -> Path:
        return Path(temp_dir) / ".env"

    def test_retention_settings_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _blank_env():
                s = Settings.from_env(self._empty_env_path(tmp))
        self.assertFalse(s.retention_amplify)
        self.assertEqual(s.retention_min_grade, "B")
        self.assertEqual(s.retention_amplify_factor, 1.5)

    def test_retention_amplify_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_RETENTION_AMPLIFY": "true"}):
                s = Settings.from_env(self._empty_env_path(tmp))
        self.assertTrue(s.retention_amplify)

    def test_retention_amplify_from_env_falsey(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_RETENTION_AMPLIFY": "false"}):
                s = Settings.from_env(self._empty_env_path(tmp))
        self.assertFalse(s.retention_amplify)

    def test_retention_min_grade_from_env_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_RETENTION_MIN_GRADE": "a"}):
                s = Settings.from_env(self._empty_env_path(tmp))
        self.assertEqual(s.retention_min_grade, "A")

    def test_retention_min_grade_invalid_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_RETENTION_MIN_GRADE": "Q"}):
                with self.assertRaises(ValueError):
                    Settings.from_env(self._empty_env_path(tmp))

    def test_retention_amplify_factor_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_RETENTION_AMPLIFY_FACTOR": "2.25"}):
                s = Settings.from_env(self._empty_env_path(tmp))
        self.assertEqual(s.retention_amplify_factor, 2.25)

    def test_retention_amplify_factor_clamped_at_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_RETENTION_AMPLIFY_FACTOR": "0.2"}):
                s = Settings.from_env(self._empty_env_path(tmp))
        self.assertEqual(s.retention_amplify_factor, 1.0)

    def test_retention_amplify_factor_invalid_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SHORTS_RETENTION_AMPLIFY_FACTOR": "nope"}):
                s = Settings.from_env(self._empty_env_path(tmp))
        self.assertEqual(s.retention_amplify_factor, 1.5)


class RetentionAmplifyDecisionTests(unittest.TestCase):
    def test_missing_grade_allows_neutrally(self):
        decision = decide_publish(
            EditorialProfile(name="d"),
            "tech",
            "youtube",
            {},
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.amplify_factor, 1.0)

    def test_grade_a_amplifies(self):
        decision = decide_publish(
            EditorialProfile(name="d"),
            "tech",
            "youtube",
            {("tech", "youtube"): RetentionGrade("A", 0.9)},
            min_grade="B",
            amplify_factor=1.5,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.amplify_factor, 1.5)
        self.assertIn("retention grade A", decision.reason)

    def test_grade_b_allowed_neutral(self):
        decision = decide_publish(
            EditorialProfile(name="d"),
            "tech",
            "youtube",
            {("tech", "youtube"): RetentionGrade("B", 0.6)},
            min_grade="B",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.amplify_factor, 1.0)

    def test_grade_c_below_min_grade_skipped(self):
        decision = decide_publish(
            EditorialProfile(name="d"),
            "tech",
            "instagram",
            {("tech", "instagram"): RetentionGrade("C", 0.3)},
            min_grade="B",
        )
        self.assertFalse(decision.allowed)
        self.assertIn("below min_grade", decision.reason)

    def test_grade_d_always_below_min_grade(self):
        decision = decide_publish(
            EditorialProfile(name="d"),
            "tech",
            "youtube",
            {("tech", "youtube"): RetentionGrade("D", 0.1)},
            min_grade="D",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.amplify_factor, 1.0)

    def test_floor_blocks_despite_a_letter(self):
        decision = decide_publish(
            EditorialProfile(name="d", retention_floor=0.95),
            "tech",
            "youtube",
            {("tech", "youtube"): RetentionGrade("A", 0.9)},
            min_grade="B",
        )
        self.assertFalse(decision.allowed)
        self.assertIn("below floor", decision.reason)

    def test_floor_satisfied_allows(self):
        decision = decide_publish(
            EditorialProfile(name="d", retention_floor=0.8),
            "tech",
            "youtube",
            {("tech", "youtube"): RetentionGrade("A", 0.9)},
            min_grade="B",
        )
        self.assertTrue(decision.allowed)

    def test_decisions_are_frozen_dataclasses(self):
        decision = PublishDecision(gate="retention", allowed=True, reason="ok", amplify_factor=2.0)
        self.assertEqual(
            decision,
            PublishDecision(gate="retention", allowed=True, reason="ok", amplify_factor=2.0),
        )


class RetentionAmplifyGradesTests(unittest.TestCase):
    def test_current_grades_from_seeded_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "metrics.sqlite"
            grades = _seed_grades(db)
        self.assertEqual(grades[("tech", "youtube")].letter, "A")
        self.assertGreaterEqual(grades[("tech", "youtube")].value, 0.75)
        self.assertEqual(grades[("tech", "instagram")].letter, "C")
        self.assertLess(grades[("tech", "instagram")].value, 0.5)

    def test_missing_db_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            grades = current_retention_grades(Settings(metrics_path=Path(tmp) / "nope.sqlite"))
        self.assertEqual(grades, {})

    def test_empty_db_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "metrics.sqlite"
            sqlite3.connect(str(db)).close()
            grades = current_retention_grades(Settings(metrics_path=db))
        self.assertEqual(grades, {})

    def test_stale_rows_excluded_from_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "metrics.sqlite"
            _seed(
                db,
                [
                    {"platform": "youtube", "niche": "tech", "publish_ts": _past_iso(60),
                     "views": 100, "likes": 90},
                ],
            )
            grades = current_retention_grades(Settings(metrics_path=db))
        self.assertEqual(grades, {})


class RetentionAmplifyManagerTests:
    def test_gate_skips_below_grade_and_amplifies_a(
        self, tmp_path, mock_registry, mock_r2, amp_settings, monkeypatch
    ):
        monkeypatch.setattr(
            "shorts_clipper.publishers.manager.Settings.from_env", lambda: amp_settings
        )
        grades = _seed_grades(amp_settings.metrics_path)

        engine = PublishingEngine(max_retries=1, base_backoff=0)
        video_path = tmp_path / "clip.mp4"
        video_path.touch()
        meta = ClipMetadata(title="Test", description="Test desc")

        results = engine.publish(
            video_path,
            meta,
            ["youtube", "instagram"],
            niche="tech",
            retention_grades=grades,
        )

        # Instagram graded C (below B) is skipped; youtube A is published.
        assert "youtube" in results
        assert "instagram" not in results
        assert results["youtube"].success

        # The A pair gets the amplified daily-cap budget; the C pair keeps base.
        assert engine.amplify_factor_for("tech", "youtube") == amp_settings.retention_amplify_factor
        assert engine.amplify_factor_for("tech", "instagram") == 1.0
        assert engine.effective_daily_cap(6, "tech", "youtube") == 9.0
        assert engine.effective_daily_cap(6, "tech", "instagram") == 6.0

    def test_gate_respects_profile_floor(
        self, tmp_path, mock_registry, mock_r2, amp_settings, monkeypatch
    ):
        monkeypatch.setattr(
            "shorts_clipper.publishers.manager.Settings.from_env", lambda: amp_settings
        )
        grades = {
            ("tech", "youtube"): RetentionGrade("A", 0.9),
            ("tech", "instagram"): RetentionGrade("A", 0.9),
        }

        engine = PublishingEngine(max_retries=1, base_backoff=0)
        video_path = tmp_path / "clip.mp4"
        video_path.touch()
        meta = ClipMetadata(title="Test", description="Test desc")

        results = engine.publish(
            video_path,
            meta,
            ["youtube", "instagram"],
            niche="tech",
            retention_grades=grades,
            profile=EditorialProfile(name="strict", retention_floor=0.95),
        )

        # Floor 0.95 > 0.9 value: both blocked despite A letter.
        assert results == {}
        assert engine.amplify_factor_for("tech", "youtube") == 1.0

    def test_missing_grades_allow_all(self, tmp_path, mock_registry, mock_r2, amp_settings, monkeypatch):
        monkeypatch.setattr(
            "shorts_clipper.publishers.manager.Settings.from_env", lambda: amp_settings
        )

        engine = PublishingEngine(max_retries=1, base_backoff=0)
        video_path = tmp_path / "clip.mp4"
        video_path.touch()
        meta = ClipMetadata(title="Test", description="Test desc")

        results = engine.publish(
            video_path,
            meta,
            ["youtube", "instagram"],
            niche="tech",
        )

        assert "youtube" in results
        assert "instagram" in results
        assert engine.amplify_factor_for("tech", "youtube") == 1.0

    def test_feature_off_is_noop(self, tmp_path, mock_registry, mock_r2, monkeypatch):
        monkeypatch.setattr(
            "shorts_clipper.publishers.manager.Settings.from_env",
            lambda: Settings(compliance_enabled=False),
        )

        engine = PublishingEngine(max_retries=1, base_backoff=0)
        video_path = tmp_path / "clip.mp4"
        video_path.touch()
        meta = ClipMetadata(title="Test", description="Test desc")

        results = engine.publish(
            video_path,
            meta,
            ["youtube", "instagram"],
            niche="tech",
            retention_grades={
                ("tech", "instagram"): RetentionGrade("C", 0.3),
            },
        )

        assert "youtube" in results
        assert "instagram" in results
        assert results["youtube"].success
        assert results["instagram"].success


class RetentionAmplifyRunnerTests(unittest.TestCase):
    def test_refresh_returns_empty_when_feature_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(compliance_enabled=False, retention_amplify=False,
                                metrics_path=Path(tmp) / "metrics.sqlite")
            self.assertEqual(_refresh_retention_grades(settings), {})

    def test_refresh_loads_grades_when_feature_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "metrics.sqlite"
            _seed_grades(db)
            settings = Settings(compliance_enabled=False, retention_amplify=True,
                                metrics_path=db)
            grades = _refresh_retention_grades(settings)
        self.assertIn(("tech", "youtube"), grades)

    def test_refresh_missing_db_falls_back_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(compliance_enabled=False, retention_amplify=True,
                                metrics_path=Path(tmp) / "nope.sqlite")
            self.assertEqual(_refresh_retention_grades(settings), {})