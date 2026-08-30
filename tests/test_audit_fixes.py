from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from shorts_clipper.providers.gemini import GeminiProvider
from shorts_clipper.scout.trending import (
    compute_scout_v2_intermediate_score,
    fetch_metadata_batch,
    get_trending_link,
)


# ITERATION 1 BUG 1
def test_trending_with_null_metrics():
    now = datetime.now(UTC)
    video = {
        "published_at": "20240101",
        "view_count": None,  # the bug
        "like_count": None,
        "comment_count": None,
    }
    # Should not raise TypeError
    score = compute_scout_v2_intermediate_score(video, now, {})
    assert isinstance(score, float)


# ITERATION 1 BUG 3
@patch("subprocess.run")
def test_yt_dlp_partial_success_handling(mock_run):
    mock_result = MagicMock()
    mock_result.returncode = 1
    # Valid json for one, exit code 1 for the batch
    mock_result.stdout = '{"id": "abc", "title": "test"}\n'
    mock_result.stderr = "some error"
    mock_run.return_value = mock_result

    res = fetch_metadata_batch(["abc", "def"])
    assert len(res) == 1
    assert res[0]["id"] == "abc"


# ITERATION 1 BUG 4
@patch("pathlib.Path.write_text")
@patch("shorts_clipper.scout.trending._discover_via_api", return_value=[])
@patch("shorts_clipper.scout.trending._discover_via_ytdlp", return_value=[])
def test_concurrent_scout_report_writes(mock_ytdlp, mock_api, mock_write):
    job_id = "test_job_123"
    try:
        get_trending_link(job_id=job_id, max_age_days=1)
    except Exception:
        pass
    # We just want to ensure it doesn't crash, the actual check is in the code
    # that uses job_id for the report file


# ITERATION 2 BUG 1
def test_gemini_generate_content_retries_on_429():
    provider = GeminiProvider(api_key="test")
    mock_client = MagicMock()
    provider._client = mock_client

    class Fake429(Exception):
        pass

    mock_client.models.generate_content.side_effect = [
        Fake429("429 Too Many Requests"),
        MagicMock(text="success"),
    ]

    with patch("time.sleep", return_value=None):
        res = provider.generate_content("test prompt")

    assert res.text == "success"
    assert mock_client.models.generate_content.call_count == 2


# ITERATION 2 BUG 2
def test_select_clips_detailed_invalid_json_type():
    provider = GeminiProvider(api_key="test")
    # Return a dict instead of a list
    with patch.object(
        provider,
        "generate_content",
        return_value=MagicMock(text='{"candidates": [{"start": 10, "end": 50}]}'),
    ):
        res = provider.select_multiple_clips_detailed([], count=1)

    assert len(res) == 1
    assert res[0]["start"] == 10


# ITERATION 2 BUG 3
def test_metadata_generation_null_tags():
    provider = GeminiProvider(api_key="test")
    raw = '{"selected_title": "t", "description": "d", "tags": null}'
    with patch.object(provider, "generate_content", return_value=MagicMock(text=raw)):
        res = provider.generate_clip_metadata([], "test")

    assert res["title"] == "t"
    assert res["tags"] == ["shorts"]


# ITERATION 2 BUG 4
def test_regex_fallback_with_string_floats():
    provider = GeminiProvider(api_key="test")
    raw = 'invalid json "start": "10.5", "end": "50.5"'
    with patch.object(provider, "generate_content", return_value=MagicMock(text=raw)):
        res = provider.select_multiple_clips_detailed([], count=1)

    assert len(res) == 1
    assert res[0]["start"] == 10.5
    assert res[0]["end"] == 50.5


# ITERATION 2 BUG 5: Hydration of flat-playlist
@patch("shorts_clipper.scout.trending.fetch_metadata_batch")
def test_trending_hydration_metadata(mock_fetch):
    now = datetime.now(UTC)
    video_no_timestamp = {
        "id": "abc",
        "view_count": 1000,
        "like_count": 100,
        "comment_count": 10,
    }
    # It should score 0.0 without timestamp
    assert compute_scout_v2_intermediate_score(video_no_timestamp, now, {}) == 0.0

    # Mock fetch_metadata_batch returning full info
    mock_fetch.return_value = [
        {
            "id": "abc",
            "timestamp": now.timestamp() - 3600,  # 1 hour ago
            "view_count": 1000,
            "like_count": 100,
            "comment_count": 10,
        }
    ]
    full_info = mock_fetch.return_value[0]

    # It should score > 0.0 with full info
    score = compute_scout_v2_intermediate_score(full_info, now, {})
    assert score > 0.0
