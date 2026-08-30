import pathlib
import sqlite3
from unittest.mock import MagicMock, patch

from shorts_clipper.scout.trending import get_trending_link


def test_sqlite_connection_leak_on_error():
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = sqlite3.OperationalError("Simulated DB error")

    with (
        patch.object(pathlib.Path, "exists", return_value=True),
        patch("sqlite3.connect", return_value=mock_conn),
        patch("shorts_clipper.scout.trending._discover_via_ytdlp", return_value=[]),
        patch("shorts_clipper.scout.trending._discover_via_api", return_value=[]),
    ):
        try:
            get_trending_link(niche="gaming", keyword="minecraft", max_age_days=1)
        except Exception:
            pass

        mock_conn.close.assert_called()
