import pytest

from shorts_clipper.compliance.gate import ComplianceBlocked
from shorts_clipper.core.settings import Settings
from shorts_clipper.publishers import ClipMetadata, PublisherRegistry, PublishingEngine, PublishResult
from shorts_clipper.publishers.base import Publisher


class _MockPublishPublisher(Publisher):
    def __init__(self):
        self.publish_called = False

    @property
    def platform_name(self) -> str:
        return "youtube"

    def authenticate(self) -> None:
        pass

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        self.publish_called = True
        return PublishResult(self.platform_name, True, "http://yt", "yt123")

    def verify(self, platform_id: str) -> bool:
        return True


@pytest.fixture
def mock_registry():
    original = dict(PublisherRegistry._publishers)
    PublisherRegistry._publishers.clear()
    PublisherRegistry.register(_MockPublishPublisher)
    yield
    PublisherRegistry._publishers = original


@pytest.fixture(autouse=True)
def mock_r2():
    from unittest.mock import patch

    with patch("shorts_clipper.publishers.manager.R2Storage") as mock:
        instance = mock.return_value
        instance.upload.return_value = "mock_key"
        instance.generate_signed_url.return_value = "http://mock_signed_url"
        yield mock


def _settings(report_dir) -> Settings:
    return Settings(
        compliance_enabled=True,
        compliance_auto_disclaimers=True,
        affiliate_enabled=False,
        affiliate_cta_text="",
        compliance_report_dir=str(report_dir),
    )


def test_block_raises_and_writes_report(tmp_path, mock_registry, monkeypatch):
    monkeypatch.setattr(
        "shorts_clipper.publishers.manager.Settings.from_env",
        lambda: _settings(tmp_path),
    )

    engine = PublishingEngine(max_retries=1, base_backoff=0)
    video_path = tmp_path / "blocked.mp4"
    video_path.touch()
    meta = ClipMetadata(
        title="Заработай миллион на ставках",
        description="Быстрый заработок за день",
    )

    with pytest.raises(ComplianceBlocked):
        engine.publish(video_path, meta, ["youtube"])

    reports = list(tmp_path.glob("blocked_*.json"))
    assert len(reports) == 1


def test_review_publishes(tmp_path, mock_registry, monkeypatch):
    monkeypatch.setattr(
        "shorts_clipper.publishers.manager.Settings.from_env",
        lambda: _settings(tmp_path),
    )

    engine = PublishingEngine(max_retries=1, base_backoff=0)
    video_path = tmp_path / "review.mp4"
    video_path.touch()
    # Finance topic without disclaimer in non-strict mode -> REVIEW (still publishes)
    meta = ClipMetadata(
        title="Обучение трейдингу",
        description="Учимся торговать на форексе",
    )

    results = engine.publish(video_path, meta, ["youtube"])
    assert results["youtube"].success is True


def test_pass_publishes(tmp_path, mock_registry, monkeypatch):
    monkeypatch.setattr(
        "shorts_clipper.publishers.manager.Settings.from_env",
        lambda: _settings(tmp_path),
    )

    engine = PublishingEngine(max_retries=1, base_backoff=0)
    video_path = tmp_path / "pass.mp4"
    video_path.touch()
    meta = ClipMetadata(title="Простое видео", description="Обычное описание")

    results = engine.publish(video_path, meta, ["youtube"])
    assert results["youtube"].success is True
