import os
from datetime import UTC, datetime
from unittest import mock

from shorts_clipper.core.settings import Settings
from shorts_clipper.scout.metrics import ScoutMetrics
from shorts_clipper.scout.trending import _discover_via_api, get_trending_link


def test_settings_loads_youtube_api_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("YOUTUBE_API_KEY=test_key_123\n")

    with mock.patch.dict(os.environ, {}, clear=True):
        settings = Settings.from_env(env_file)
        assert settings.youtube_api_key == "test_key_123"
        assert os.environ.get("YOUTUBE_API_KEY") == "test_key_123"


@mock.patch("shorts_clipper.scout.trending.fetch_subtitles")
@mock.patch("shorts_clipper.scout.trending._discover_via_api")
@mock.patch("shorts_clipper.scout.trending.YouTubeAPIClient")
def test_scout_creates_client_and_executes_api_path(
    mock_client_cls, mock_discover_api, mock_fetch_subtitles
):
    mock_fetch_subtitles.return_value = []
    # Setup mock client
    mock_instance = mock.Mock()
    mock_instance.searches_available = True
    mock_client_cls.return_value = mock_instance

    # Mock api returning some results so it doesn't loop forever
    mock_discover_api.return_value = [
        {
            "id": "vid1",
            "_source": "youtube_api",
            "published_at": datetime.now(UTC).isoformat(),
        }
    ]

    with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": "valid_key"}):
        get_trending_link(niche="tech", max_age_days=7)

    # 2. Scout creates YouTubeAPIClient when key exists
    mock_client_cls.assert_called_with("valid_key")

    # 3. API discovery path executes
    assert mock_discover_api.call_count >= 1


@mock.patch("shorts_clipper.scout.trending._discover_via_ytdlp")
def test_fallback_still_works_when_key_absent(mock_discover_ytdlp):
    # Returning empty so we test it calls yt-dlp
    mock_discover_ytdlp.return_value = []

    # Ensure key is absent
    with mock.patch.dict(os.environ, {}, clear=True):
        get_trending_link(niche="tech", max_age_days=7)

    # 4. Fallback executes
    assert mock_discover_ytdlp.call_count >= 1


@mock.patch("shorts_clipper.scout.trending.YouTubeAPIClient")
def test_query_normalization_works(mock_client_cls):
    mock_client = mock.Mock()
    mock_client.search.return_value = []

    metrics = ScoutMetrics(niche="tech", keyword="", time_window_days=7)
    cutoff = datetime.now(UTC)

    # 5. Query normalization works
    _discover_via_api(mock_client, "ytsearch15:tech Apple", cutoff, metrics)
    mock_client.search.assert_called_with("tech Apple", published_after=cutoff)

    _discover_via_api(mock_client, "ytsearch:finance news", cutoff, metrics)
    mock_client.search.assert_called_with("finance news", published_after=cutoff)
