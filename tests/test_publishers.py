import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from shorts_clipper.core.settings import Settings
from shorts_clipper.publishers import (
    ClipMetadata,
    PublisherRegistry,
    PublishingEngine,
    PublishResult,
)
from shorts_clipper.publishers.base import Publisher


class MockYouTubePublisher(Publisher):
    def __init__(self):
        self.auth_called = False
        self.publish_called = False
        self.verify_called = False
        self.should_fail = False
        self.should_verify_fail = False

    @property
    def platform_name(self) -> str:
        return "youtube"

    def authenticate(self) -> None:
        self.auth_called = True
        if self.should_fail:
            raise RuntimeError("YouTube auth failed")

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        self.publish_called = True
        if self.should_fail:
            return PublishResult(self.platform_name, False, error_message="Upload failed")
        return PublishResult(self.platform_name, True, "http://yt", "yt123")

    def verify(self, platform_id: str) -> bool:
        self.verify_called = True
        if self.should_verify_fail:
            return False
        return True


class MockInstagramPublisher(Publisher):
    def __init__(self):
        self.auth_called = False
        self.publish_called = False
        self.verify_called = False
        self.should_fail = False
        self.fail_count = 0
        self.current_fails = 0

    @property
    def platform_name(self) -> str:
        return "instagram"

    def authenticate(self) -> None:
        self.auth_called = True

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        self.publish_called = True
        if self.should_fail or self.current_fails < self.fail_count:
            self.current_fails += 1
            import requests

            raise requests.exceptions.ConnectionError("timeout")
        return PublishResult(self.platform_name, True, "http://ig", "ig123")

    def verify(self, platform_id: str) -> bool:
        self.verify_called = True
        return True


@pytest.fixture(autouse=True)
def setup_registry():
    """Register mock publishers before each test, clear them after."""
    # Backup original publishers
    original = dict(PublisherRegistry._publishers)
    PublisherRegistry._publishers.clear()

    # Register mocks
    PublisherRegistry.register(MockYouTubePublisher)
    PublisherRegistry.register(MockInstagramPublisher)

    yield

    # Restore original publishers
    PublisherRegistry._publishers = original


@pytest.fixture(autouse=True)
def mock_r2():
    from unittest.mock import patch

    with patch("shorts_clipper.publishers.manager.R2Storage") as mock:
        instance = mock.return_value
        instance.upload.return_value = "mock_key"
        instance.generate_signed_url.return_value = "http://mock_signed_url"
        yield mock


@pytest.fixture(autouse=True)
def temp_metrics_path(tmp_path, monkeypatch):
    """Point the defensive metrics hook at a throwaway DB during tests."""
    monkeypatch.setenv("SHORTS_METRICS_PATH", str(tmp_path / "metrics.sqlite"))
    yield


def test_publisher_registry():
    # Verify that both are registered
    assert "youtube" in PublisherRegistry._publishers
    assert "instagram" in PublisherRegistry._publishers

    yt = PublisherRegistry.get_publisher("youtube")
    assert isinstance(yt, MockYouTubePublisher)
    assert yt.platform_name == "youtube"


def test_publishing_engine_success(tmp_path):
    engine = PublishingEngine(max_retries=1, base_backoff=0)
    video_path = tmp_path / "test_clip.mp4"
    video_path.touch()

    meta = ClipMetadata(title="Test", description="Test desc")

    results = engine.publish(video_path, meta, ["youtube", "instagram"])

    assert len(results) == 2
    assert results["youtube"].success
    assert results["instagram"].success

    manifest_path = tmp_path / "test_clip_publish_manifest.json"
    assert manifest_path.exists()

    with open(manifest_path) as f:
        manifest = json.load(f)

    assert manifest["overall_status"] == "SUCCESS"
    assert manifest["results"]["youtube"]["success"] is True
    assert manifest["results"]["instagram"]["success"] is True


def test_publishing_engine_partial_success(tmp_path):
    # Make instagram fail
    ig = PublisherRegistry.get_publisher("instagram")
    ig.should_fail = True
    PublisherRegistry._publishers["instagram"] = lambda: ig

    engine = PublishingEngine(max_retries=1, base_backoff=0)
    video_path = tmp_path / "test_clip2.mp4"
    video_path.touch()

    meta = ClipMetadata(title="Test", description="Test desc")

    results = engine.publish(video_path, meta, ["youtube", "instagram"])

    assert results["youtube"].success is True
    assert results["instagram"].success is False

    manifest_path = tmp_path / "test_clip2_publish_manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    assert manifest["overall_status"] == "PARTIAL_SUCCESS"


def test_publishing_engine_retry_logic(tmp_path):
    ig = PublisherRegistry.get_publisher("instagram")
    ig.fail_count = 2  # Fail twice, succeed on third
    PublisherRegistry._publishers["instagram"] = lambda: ig

    engine = PublishingEngine(max_retries=3, base_backoff=0)
    video_path = tmp_path / "test_clip3.mp4"
    video_path.touch()

    meta = ClipMetadata(title="Test", description="Test desc")

    results = engine.publish(video_path, meta, ["instagram"])

    assert results["instagram"].success is True
    assert results["instagram"].retry_count == 2  # 0-indexed, so 0,1,2 = 3rd attempt


def test_publishing_engine_configuration_error(tmp_path):
    from shorts_clipper.core.exceptions import ConfigurationError

    class ConfigFailPublisher(Publisher):
        @property
        def platform_name(self) -> str:
            return "config_fail"

        def authenticate(self) -> None:
            pass

        def publish(self, vp, meta, signed_url=None, cb=None):
            raise ConfigurationError("PUBLIC_URL must be set")

        def verify(self, pid: str) -> bool:
            return True

    PublisherRegistry.register(ConfigFailPublisher)
    engine = PublishingEngine(max_retries=3, base_backoff=0)
    video_path = tmp_path / "test_config_fail.mp4"
    video_path.touch()

    meta = ClipMetadata(title="Test", description="Test desc")
    results = engine.publish(video_path, meta, ["config_fail"])

    assert results["config_fail"].success is False
    assert results["config_fail"].retry_count == 0
    assert "PUBLIC_URL must be set" in results["config_fail"].error_message


def test_failure_isolation(tmp_path):
    # Make youtube auth fail
    yt = PublisherRegistry.get_publisher("youtube")
    yt.should_fail = True
    PublisherRegistry._publishers["youtube"] = lambda: yt

    engine = PublishingEngine(max_retries=1, base_backoff=0)
    video_path = tmp_path / "test_clip4.mp4"
    video_path.touch()

    meta = ClipMetadata(title="Test", description="Test desc")

    results = engine.publish(video_path, meta, ["youtube", "instagram"])

    # YouTube should fail
    assert results["youtube"].success is False
    # Instagram should still succeed
    assert results["instagram"].success is True


class MockNewPublisher(Publisher):
    @property
    def platform_name(self) -> str:
        return "tiktok"

    def authenticate(self) -> None:
        pass

    def publish(self, vp, meta, signed_url=None, cb=None):
        return PublishResult("tiktok", True, platform_id="tik123")

    def verify(self, pid: str) -> bool:
        return True


def test_adding_new_publisher_without_modifying_pipeline(tmp_path):
    PublisherRegistry.register(MockNewPublisher)

    engine = PublishingEngine(max_retries=1, base_backoff=0)
    video_path = tmp_path / "test_clip5.mp4"
    video_path.touch()

    meta = ClipMetadata(title="Test", description="Test desc")

    results = engine.publish(video_path, meta, ["tiktok"])

    assert results["tiktok"].success is True


# ─────────────────────────────────────────────────────────────────────────────
# PUB-2: YouTube publish must NOT report success without a valid platform id
# ─────────────────────────────────────────────────────────────────────────────


def test_yt_extract_video_id_validation():
    from shorts_clipper.publishers.models import PublishValidationError
    from shorts_clipper.publishers.youtube.uploader import _extract_video_id

    assert _extract_video_id({"id": "aBcDeFgHiJk"}) == "aBcDeFgHiJk"
    assert _extract_video_id({"id": "abc-123_ABX"}) == "abc-123_ABX"

    with pytest.raises(PublishValidationError):
        _extract_video_id({})
    with pytest.raises(PublishValidationError):
        _extract_video_id({"id": None})
    with pytest.raises(PublishValidationError):
        _extract_video_id({"id": "short"})
    with pytest.raises(PublishValidationError):
        _extract_video_id({"id": "!!!!!!!!!!!invalid"})
    # Uploaded but no id came back (even though uploadStatus is present)
    with pytest.raises(PublishValidationError):
        _extract_video_id({"status": {"uploadStatus": "processed"}})


def test_yt_publisher_fails_when_no_platform_id(tmp_path):
    from unittest.mock import patch

    from shorts_clipper.publishers.models import PublishValidationError
    from shorts_clipper.publishers.youtube.publisher import YouTubePublisher

    video_path = tmp_path / "x.mp4"
    video_path.touch()
    meta = ClipMetadata(title="T", description="D")

    with patch(
        "shorts_clipper.publishers.youtube.publisher.upload_short",
        side_effect=PublishValidationError("insert returned no valid id"),
    ):
        res = YouTubePublisher().publish(video_path, meta)

    assert res.success is False
    assert res.platform_id is None
    assert res.url is None
    assert "no valid id" in res.error_message


def test_yt_publisher_success_sets_short_url(tmp_path):
    from unittest.mock import patch

    from shorts_clipper.publishers.youtube.publisher import YouTubePublisher

    video_path = tmp_path / "x.mp4"
    video_path.touch()
    meta = ClipMetadata(title="T", description="D")
    video_id = "AbC-1_2xYz9"  # valid 11 chars

    with patch(
        "shorts_clipper.publishers.youtube.publisher.upload_short",
        return_value=video_id,
    ):
        res = YouTubePublisher().publish(video_path, meta)

    assert res.success is True
    assert res.platform_id == video_id
    assert res.url == f"https://youtube.com/shorts/{video_id}"


# ─────────────────────────────────────────────────────────────────────────────
# PUB-1: idempotent retries — never upload twice when the id was recovered
# ─────────────────────────────────────────────────────────────────────────────


class ThrowAfterUploadPublisher(Publisher):
    """Upload commits server-side, then the response is lost (RequestException)."""

    def __init__(self):
        self.publish_count = 0
        self.verify_calls = []
        self.known_id = "a" * 11

    @property
    def platform_name(self) -> str:
        return "throw_after_upload"

    def authenticate(self) -> None:
        pass

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        self.publish_count += 1
        err = requests.exceptions.RequestException("response lost after upload committed")
        err.platform_id = self.known_id
        raise err

    def verify(self, platform_id: str) -> bool:
        self.verify_calls.append(platform_id)
        return True


def test_idempotent_retry_verifies_before_reupload(tmp_path):
    pub = ThrowAfterUploadPublisher()
    PublisherRegistry._publishers["throw_after_upload"] = lambda: pub

    engine = PublishingEngine(max_retries=3, base_backoff=0)
    video_path = tmp_path / "test_idem.mp4"
    video_path.touch()

    meta = ClipMetadata(title="Test", description="Test desc")

    results = engine.publish(video_path, meta, ["throw_after_upload"])

    # The 2nd attempt must verify the already-uploaded id instead of re-uploading
    assert pub.publish_count == 1
    assert results["throw_after_upload"].success is True
    assert results["throw_after_upload"].platform_id == pub.known_id
    assert results["throw_after_upload"].retry_count == 1
    assert pub.verify_calls == [pub.known_id]


def test_budget_exhausted_with_unknown_id_no_reupload(tmp_path):
    """If a known id cannot be verified, stop (do not re-upload / duplicate)."""
    class UnverifiablePublisher(Publisher):
        def __init__(self):
            self.publish_count = 0

        @property
        def platform_name(self) -> str:
            return "unverifiable"

        def authenticate(self) -> None:
            pass

        def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
            self.publish_count += 1
            err = requests.exceptions.RequestException("lost response")
            err.platform_id = "b" * 11
            raise err

        def verify(self, platform_id: str) -> bool:
            raise RuntimeError("verify endpoint down")

    pub = UnverifiablePublisher()
    PublisherRegistry._publishers["unverifiable"] = lambda: pub

    engine = PublishingEngine(max_retries=3, base_backoff=0)
    video_path = tmp_path / "test_never_dup.mp4"
    video_path.touch()

    meta = ClipMetadata("T", "D")
    results = engine.publish(video_path, meta, ["unverifiable"])

    assert pub.publish_count == 1
    assert results["unverifiable"].success is False
    assert "duplicate" in results["unverifiable"].error_message.lower()


# ─────────────────────────────────────────────────────────────────────────────
# PUB-3: HTTP 429/quota/resumable errors must be classified as retryable
# ─────────────────────────────────────────────────────────────────────────────


class FakeGoogleHttpError(Exception):
    """Shape-alike of googleapiclient.errors.HttpError / ResumableUploadError."""

    def __init__(self, status, body, retry_after=None):
        self.status_code = status
        body_text = json.dumps(body) if not isinstance(body, str) else body
        super().__init__(
            f'<HttpError {status} when requesting <url> returned "Error". '
            f"Details: {body_text}"
        )

        class _Resp:
            def __init__(self, status, data, headers):
                self.status = status
                self.data = data
                self.headers = headers

            def get(self, name, default=None):
                return self.headers.get(name, default) if name in self.headers else default

        headers = {"retry-after": retry_after} if retry_after else {}
        self.resp = _Resp(status, body_text.encode("utf-8"), headers)


def test_error_classification_retriable_statuses():
    from shorts_clipper.publishers.manager import _is_retriable_error

    for code in (408, 429, 500, 502, 503, 504):
        err = FakeGoogleHttpError(code, {"error": {"message": "server error"}})
        assert _is_retriable_error(err) is True, f"status {code} should be retriable"


def test_error_classification_permanent_statuses():
    from shorts_clipper.publishers.manager import _is_retriable_error

    for code in (400, 401, 403, 404, 409, 422):
        err = FakeGoogleHttpError(code, {"error": {"message": "bad request"}})
        assert _is_retriable_error(err) is False, f"status {code} must be permanent"


def test_error_classification_quota_errors():
    from shorts_clipper.publishers.manager import _is_retriable_error

    quota = FakeGoogleHttpError(
        403,
        {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota exceeded"}},
    )
    assert _is_retriable_error(quota) is True

    daily = FakeGoogleHttpError(
        403, {"error": {"message": "The user has exceeded their dailyLimitExceeded quota"}}
    )
    assert _is_retriable_error(daily) is True

    user = FakeGoogleHttpError(
        403, {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}
    )
    assert _is_retriable_error(user) is True

    exhausted = FakeGoogleHttpError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})
    assert _is_retriable_error(exhausted) is True


class ResumableUploadError(Exception):
    pass


def test_error_classification_resumable_and_network():
    from shorts_clipper.publishers.manager import _is_retriable_error

    assert _is_retriable_error(ResumableUploadError("INSERT_FAILED")) is True
    assert _is_retriable_error(RuntimeError("resumableUploadError during upload")) is True
    assert _is_retriable_error(RuntimeError("connection reset by peer")) is True
    assert _is_retriable_error(RuntimeError("timed out waiting for response")) is True


def test_error_classification_structured_reasons():
    from shorts_clipper.publishers.manager import _is_retriable_error

    backend = FakeGoogleHttpError(
        403, {"error": {"errors": [{"reason": "backendError"}]}}
    )
    assert _is_retriable_error(backend) is True

    unavailable = FakeGoogleHttpError(503, {"error": {"status": "UNAVAILABLE"}})
    assert _is_retriable_error(unavailable) is True

    denied = FakeGoogleHttpError(
        403, {"error": {"errors": [{"reason": "permissionDenied"}]}}
    )
    assert _is_retriable_error(denied) is False


def test_error_classification_no_short_fragment_false_positives():
    from shorts_clipper.publishers.manager import _is_retriable_error

    collision = FakeGoogleHttpError(
        422,
        {"error": {"message": "video id 'vid500xyz' rejected, connection reset"}},
    )
    assert _is_retriable_error(collision) is False


def test_error_classification_requests_network_classes():
    import requests

    from shorts_clipper.publishers.manager import _is_retriable_error

    assert _is_retriable_error(requests.exceptions.ConnectionError("backend down")) is True
    assert _is_retriable_error(requests.exceptions.Timeout("timed out")) is True
    assert _is_retriable_error(requests.exceptions.ChunkedEncodingError("boom")) is True


def test_retry_after_parsing():
    from shorts_clipper.publishers.manager import _retry_after_seconds

    err = FakeGoogleHttpError(429, {"error": {"message": "rate limited"}}, retry_after="7")
    assert _retry_after_seconds(err) == 7

    class _Resp:
        status = 429

        def get(self, name, default=None):
            return None

    class _NoHeader(Exception):
        resp = _Resp()

    assert _retry_after_seconds(_NoHeader()) is None


class FlakyHttpPublisher(Publisher):
    def __init__(self, fail_codes, fail_body=None):
        self.fail_codes = list(fail_codes)
        self.fail_body = fail_body or {"error": {"message": "Transient server error"}}
        self.calls = 0

    @property
    def platform_name(self) -> str:
        return "flaky"

    def authenticate(self) -> None:
        pass

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        self.calls += 1
        if self.fail_codes:
            code = self.fail_codes.pop(0)
            raise FakeGoogleHttpError(code, self.fail_body)
        return PublishResult(self.platform_name, True, "http://f", "fid123")

    def verify(self, platform_id: str) -> bool:
        return True


def test_http_429_retried_then_success(tmp_path):
    pub = FlakyHttpPublisher([429])
    PublisherRegistry._publishers["flaky"] = lambda: pub

    engine = PublishingEngine(max_retries=3, base_backoff=0)
    video_path = tmp_path / "test_429.mp4"
    video_path.touch()

    results = engine.publish(video_path, ClipMetadata("T", "D"), ["flaky"])

    assert pub.calls == 2
    assert results["flaky"].success is True
    assert results["flaky"].platform_id == "fid123"
    assert results["flaky"].retry_count == 1


def test_quota_403_retried_then_success(tmp_path):
    pub = FlakyHttpPublisher(
        [403],
        fail_body={"error": {"errors": [{"reason": "quotaExceeded"}],
                            "message": "quota exceeded"}},
    )
    PublisherRegistry._publishers["flaky"] = lambda: pub

    engine = PublishingEngine(max_retries=2, base_backoff=0)
    video_path = tmp_path / "test_quota.mp4"
    video_path.touch()

    results = engine.publish(video_path, ClipMetadata("T", "D"), ["flaky"])

    assert pub.calls == 2
    assert results["flaky"].success is True


def test_http_400_permanent_no_retry(tmp_path):
    pub = FlakyHttpPublisher([400, 400, 400])
    PublisherRegistry._publishers["flaky"] = lambda: pub

    engine = PublishingEngine(max_retries=3, base_backoff=0)
    video_path = tmp_path / "test_400.mp4"
    video_path.touch()

    results = engine.publish(video_path, ClipMetadata("T", "D"), ["flaky"])

    assert pub.calls == 1
    assert results["flaky"].success is False
    assert results["flaky"].error_message


def test_max_attempts_capped(tmp_path):
    pub = FlakyHttpPublisher([429, 429, 429, 429, 429])
    PublisherRegistry._publishers["flaky"] = lambda: pub

    engine = PublishingEngine(max_retries=2, base_backoff=0)
    video_path = tmp_path / "test_cap.mp4"
    video_path.touch()

    results = engine.publish(video_path, ClipMetadata("T", "D"), ["flaky"])

    assert pub.calls == 2
    assert results["flaky"].success is False


def test_retry_budget_exhausted_marks_permanent(tmp_path):
    pub = FlakyHttpPublisher([429, 429])
    PublisherRegistry._publishers["flaky"] = lambda: pub

    engine = PublishingEngine(max_retries=3, base_backoff=0, total_retry_time=0.0)
    video_path = tmp_path / "test_budget.mp4"
    video_path.touch()

    results = engine.publish(video_path, ClipMetadata("T", "D"), ["flaky"])

    assert pub.calls == 1
    assert results["flaky"].success is False
    assert "budget exhausted" in results["flaky"].error_message.lower()


class PermanentFailPublisher(Publisher):
    """Returns success=False explicitly (e.g. PUB-2) — must be permanent, no retry."""

    def __init__(self):
        self.calls = 0

    @property
    def platform_name(self) -> str:
        return "perm_fail"

    def authenticate(self) -> None:
        pass

    def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
        self.calls += 1
        return PublishResult(
            self.platform_name, False, error_message="no platform id returned"
        )

    def verify(self, platform_id: str) -> bool:
        return True


def test_explicit_failure_is_not_retried(tmp_path):
    pub = PermanentFailPublisher()
    PublisherRegistry._publishers["perm_fail"] = lambda: pub

    engine = PublishingEngine(max_retries=3, base_backoff=0)
    video_path = tmp_path / "test_perm.mp4"
    video_path.touch()

    results = engine.publish(video_path, ClipMetadata("T", "D"), ["perm_fail"])

    assert pub.calls == 1
    assert results["perm_fail"].success is False
    assert results["perm_fail"].error_message


# ─────────────────────────────────────────────────────────────────────────────
# unittest-discoverable coverage (also runs in the `python -m unittest` harness)
# ─────────────────────────────────────────────────────────────────────────────


class _EngineTestCaseBase(unittest.TestCase):
    """Self-contained engine scaffold (works under pytest AND plain unittest)."""

    def setUp(self):
        self._orig_publishers = dict(PublisherRegistry._publishers)
        PublisherRegistry._publishers.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.video_path = Path(self._tmp.name) / "test_clip.mp4"
        self.video_path.touch()
        self._r2 = patch("shorts_clipper.publishers.manager.R2Storage")
        r2 = self._r2.start()
        self.addCleanup(self._r2.stop)
        r2.return_value.upload.return_value = "mock_key"
        r2.return_value.generate_signed_url.return_value = "http://mock_signed_url"
        self._metrics = patch.dict(
            os.environ,
            {"SHORTS_METRICS_PATH": str(Path(self._tmp.name) / "metrics.sqlite")},
        )
        self._metrics.start()
        self.addCleanup(self._metrics.stop)

    def tearDown(self):
        PublisherRegistry._publishers = self._orig_publishers


class W21VerifyFalseStopsReuploadTest(_EngineTestCaseBase):
    """W2-1: verify()==False in the idempotency guard must NOT re-upload."""

    def test_verify_false_prevents_second_publish(self):
        class VerifyFalsePublisher(Publisher):
            def __init__(self):
                self.publish_count = 0
                self.verify_ids = []

            @property
            def platform_name(self):
                return "verify_false"

            def authenticate(self):
                pass

            def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
                self.publish_count += 1
                return PublishResult(self.platform_name, True, "http://v", "vid123")

            def verify(self, platform_id: str) -> bool:
                self.verify_ids.append(platform_id)
                return False

        pub = VerifyFalsePublisher()
        PublisherRegistry._publishers["verify_false"] = lambda: pub

        engine = PublishingEngine(max_retries=3, base_backoff=0)
        results = engine.publish(self.video_path, ClipMetadata("T", "D"), ["verify_false"])

        # First attempt published + post-verify failed. The idempotency guard on
        # the 2nd attempt returned False -> must NOT fall through to a re-upload.
        assert pub.publish_count == 1
        assert results["verify_false"].success is False
        assert "re-upload skipped" in results["verify_false"].error_message.lower()


class W24YouTubeTransientReraiseTest(unittest.TestCase):
    """W2-4: transient YT errors must be re-raised, only validation is permanent."""

    def _make_video(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "x.mp4"
        path.touch()
        return path

    def test_http_429_is_raised_not_returned(self):
        from shorts_clipper.publishers.youtube.publisher import YouTubePublisher

        with patch(
            "shorts_clipper.publishers.youtube.publisher.upload_short",
            side_effect=FakeGoogleHttpError(429, {"error": {"message": "quota exceeded"}}),
        ):
            with self.assertRaises(FakeGoogleHttpError):
                YouTubePublisher().publish(self._make_video(), ClipMetadata("T", "D"))

    def test_http_500_is_raised_not_returned(self):
        from shorts_clipper.publishers.youtube.publisher import YouTubePublisher

        with patch(
            "shorts_clipper.publishers.youtube.publisher.upload_short",
            side_effect=FakeGoogleHttpError(503, {"error": {"message": "backend"}}),
        ):
            with self.assertRaises(FakeGoogleHttpError):
                YouTubePublisher().publish(self._make_video(), ClipMetadata("T", "D"))

    def test_validation_error_returns_permanent_result(self):
        from shorts_clipper.publishers.models import PublishValidationError
        from shorts_clipper.publishers.youtube.publisher import YouTubePublisher

        with patch(
            "shorts_clipper.publishers.youtube.publisher.upload_short",
            side_effect=PublishValidationError("insert returned no valid id"),
        ):
            res = YouTubePublisher().publish(self._make_video(), ClipMetadata("T", "D"))

        assert res.success is False
        assert res.platform_id is None
        assert "no valid id" in res.error_message

    def test_request_exception_is_raised_not_returned(self):
        from shorts_clipper.publishers.youtube.publisher import YouTubePublisher

        err = requests.exceptions.ConnectionError("timed out")
        with patch(
            "shorts_clipper.publishers.youtube.publisher.upload_short",
            side_effect=err,
        ):
            with self.assertRaises(requests.exceptions.ConnectionError):
                YouTubePublisher().publish(self._make_video(), ClipMetadata("T", "D"))


class W24YouTubeRetryCycleTest(_EngineTestCaseBase):
    """W2-4: transient YT-style errors burn the retry budget in the manager."""

    def test_transient_429_exhausts_budget_and_returns_retriable_failure(self):
        class Yt429Publisher(Publisher):
            def __init__(self):
                self.calls = 0

            @property
            def platform_name(self):
                return "yt429"

            def authenticate(self):
                pass

            def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
                self.calls += 1
                raise FakeGoogleHttpError(429, {"error": {"message": "quota exceeded"}})

            def verify(self, platform_id: str) -> bool:
                return True

        pub = Yt429Publisher()
        PublisherRegistry._publishers["yt429"] = lambda: pub

        engine = PublishingEngine(max_retries=3, base_backoff=0, total_retry_time=30.0)
        results = engine.publish(self.video_path, ClipMetadata("T", "D"), ["yt429"])

        assert pub.calls == 3
        assert results["yt429"].success is False
        assert results["yt429"].error_message

    def test_transient_429_then_success(self):
        class Yt429ThenSuccess(Publisher):
            def __init__(self):
                self.calls = 0

            @property
            def platform_name(self):
                return "yt429s"

            def authenticate(self):
                pass

            def publish(self, video_path, metadata, signed_url=None, progress_callback=None):
                self.calls += 1
                if self.calls == 1:
                    raise FakeGoogleHttpError(429, {"error": {"message": "quota exceeded"}})
                return PublishResult(self.platform_name, True, "http://yt", "yt12345")

            def verify(self, platform_id: str) -> bool:
                return True

        pub = Yt429ThenSuccess()
        PublisherRegistry._publishers["yt429s"] = lambda: pub

        engine = PublishingEngine(max_retries=3, base_backoff=0, total_retry_time=30.0)
        results = engine.publish(self.video_path, ClipMetadata("T", "D"), ["yt429s"])

        assert pub.calls == 2
        assert results["yt429s"].success is True
        assert results["yt429s"].retry_count == 1


class W26TikTokVerifyIdTest(unittest.TestCase):
    """W2-6: yt verify() must check with publish_id (what publish() returned)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _setup_publisher(self, status_by_publish_id):
        from shorts_clipper.publishers.tiktok import publisher as tiktok_mod

        settings = SimpleNamespace(
            tiktok_client_key="k",
            tiktok_client_secret="s",
            tiktok_access_token="tok",
            tiktok_open_id="open.1",
        )
        settings_patcher = patch.object(Settings, "from_env", return_value=settings)
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)

        def fake_request_json(method, url, headers, payload=None, timeout=60):
            if method == "POST":
                return {"data": {"publish_id": "pub_123"}}
            url = str(url)
            for pid, body in status_by_publish_id.items():
                if f"publish_id={pid}" in url:
                    return body
            return {"data": {"status": "UNKNOWN"}}

        req_patcher = patch.object(tiktok_mod, "_request_json", side_effect=fake_request_json)
        req_patcher.start()
        self.addCleanup(req_patcher.stop)
        return tiktok_mod

    def test_publish_returns_publish_id_as_platform_id(self):
        tiktok_mod = self._setup_publisher(
            {
                "pub_123": {
                    "data": {"status": "PUBLISH_COMPLETE", "video_id": "vid_999"}
                }
            }
        )
        video = Path(self._tmp.name) / "x.mp4"
        res = tiktok_mod.TikTokPublisher().publish(
            video, ClipMetadata("T", "D"), signed_url="http://signed/url"
        )

        assert res.success is True
        assert res.platform_id == "pub_123"
        assert res.url.endswith("/video/vid_999")

    def test_verify_correct_publish_id_returns_true(self):
        tiktok_mod = self._setup_publisher(
            {
                "pub_123": {
                    "data": {"status": "PUBLISH_COMPLETE", "video_id": "vid_999"}
                }
            }
        )
        assert tiktok_mod.TikTokPublisher().verify("pub_123") is True

    def test_verify_unknown_id_returns_false(self):
        tiktok_mod = self._setup_publisher(
            {
                "pub_123": {
                    "data": {"status": "PUBLISH_COMPLETE", "video_id": "vid_999"}
                }
            }
        )
        assert tiktok_mod.TikTokPublisher().verify("vid_999") is False
        assert tiktok_mod.TikTokPublisher().verify("does_not_exist") is False
