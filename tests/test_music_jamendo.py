"""Offline (mocked) tests for the Jamendo phonk source.

No network is touched: ``urllib.request.urlopen`` is patched at the module
level so both ``search_jamendo_tracks`` and the higher-level
``fetch_phonk_tracks`` source ordering can be exercised deterministically.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.downloader.music_scraper import (
    Track,
    fetch_phonk_tracks,
    search_jamendo_tracks,
)

_JAMENDO = "https://api.jamendo.com/v3.0/tracks/"


class _FakeResp:
    def __init__(self, payload):
        self._body = payload

    def read(self):
        import json

        return json.dumps(self._body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(payload):
    return mock.patch(
        "shorts_clipper.downloader.music_scraper.urllib.request.urlopen",
        return_value=_FakeResp(payload),
    )


def _item(name, artist, audio_url, duration, license_id):
    return {
        "id": "track0",
        "name": name,
        "artist_name": artist,
        "audio": [{"audio": audio_url, "audioformat": "mp32", "size": 2 ** 20}],
        "trackduration": duration,
        "licenses": [{"id": license_id}],
    }


class JamendoUrlTests(unittest.TestCase):
    def test_builds_url_with_license_filter_and_popularity(self):
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["url"] = req.full_url
            return _FakeResp({"results": []})

        with mock.patch(
            "shorts_clipper.downloader.music_scraper.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            search_jamendo_tracks(query="phonk", api_key="KEY1", max_tracks=10)

        self.assertTrue(captured["url"].startswith(_JAMENDO))
        import urllib.parse

        parts = urllib.parse.urlsplit(captured["url"])
        query = urllib.parse.parse_qs(parts.query)
        self.assertEqual(query["client_id"], ["KEY1"])
        self.assertEqual(query["search"], ["phonk"])
        self.assertEqual(query["limit"], ["10"])
        self.assertEqual(query["order"], ["popularity_week"])
        self.assertEqual(query["audioformat"], ["mp32"])
        self.assertEqual(query["include"], ["licenses"])
        self.assertEqual(query["license"], ["by,by-sa"])


class JamendoParseTests(unittest.TestCase):
    def test_parses_results_into_tracks(self):
        payload = {
            "results": [
                _item(
                    "Bounce",
                    "DJ Fast",
                    "https://prod.jamendo.com/bounce.mp3",
                    25.0,
                    "ccby",
                )
            ]
        }
        with _fake_urlopen(payload):
            tracks = search_jamendo_tracks(api_key="KEY1", max_tracks=5)

        self.assertEqual(len(tracks), 1)
        t = tracks[0]
        self.assertEqual(t.url, "https://prod.jamendo.com/bounce.mp3")
        self.assertEqual(t.name, "Bounce - DJ Fast")
        self.assertEqual(t.artist, "DJ Fast")
        self.assertEqual(t.license, "CC BY")
        self.assertEqual(t.duration, 25.0)

    def test_duration_skip_below_20_seconds(self):
        payload = {
            "results": [
                _item("Short Clip", "A", "https://x/short.mp3", 12.0, "ccby"),
                _item("Just Long Enough", "B", "https://x/ok.mp3", 20.0, "ccby"),
            ]
        }
        with _fake_urlopen(payload):
            tracks = search_jamendo_tracks(api_key="KEY1", max_tracks=5)
        self.assertEqual([t.name for t in tracks], ["Just Long Enough - B"])

    def test_skips_entries_without_audio(self):
        payload = {
            "results": [
                _item("Has Audio", "A", "https://x/a.mp3", 30.0, "ccby"),
                {"id": "noaudio", "name": "No Audio", "artist_name": "Z",
                 "licenses": [{"id": "ccby"}], "trackduration": 30.0, "audio": []},
            ]
        }
        with _fake_urlopen(payload):
            tracks = search_jamendo_tracks(api_key="KEY1", max_tracks=5)
        self.assertEqual([t.name for t in tracks], ["Has Audio - A"])

    def test_empty_api_key_returns_empty(self):
        self.assertEqual(search_jamendo_tracks(api_key=""), [])

    def test_http_error_returns_empty(self):
        with mock.patch(
            "shorts_clipper.downloader.music_scraper.urllib.request.urlopen",
            side_effect=RuntimeError("network down"),
        ):
            self.assertEqual(search_jamendo_tracks(api_key="KEY1"), [])

    def test_bad_json_returns_empty(self):
        class _BadResp(_FakeResp):
            def read(self):
                return b"<not json>"

        with mock.patch(
            "shorts_clipper.downloader.music_scraper.urllib.request.urlopen",
            return_value=_BadResp({}),
        ):
            self.assertEqual(search_jamendo_tracks(api_key="KEY1"), [])

    def test_bad_json_top_level_missing_results(self):
        with _fake_urlopen({"ok": True}):
            self.assertEqual(search_jamendo_tracks(api_key="KEY1"), [])


class JamendoLicenseTests(unittest.TestCase):
    def test_cc_by_sa_mapped(self):
        payload = {
            "results": [
                _item("Remix", "Band", "https://x/r.mp3", 30.0, "ccbysa"),
            ]
        }
        with _fake_urlopen(payload):
            tracks = search_jamendo_tracks(api_key="KEY1", max_tracks=5)
        self.assertEqual(tracks[0].license, "CC BY-SA")

    def test_cc_by_mapped(self):
        payload = {
            "results": [
                _item("Orig", "Band", "https://x/o.mp3", 30.0, "ccby"),
            ]
        }
        with _fake_urlopen(payload):
            tracks = search_jamendo_tracks(api_key="KEY1", max_tracks=5)
        self.assertEqual(tracks[0].license, "CC BY")


class FetchPhonkSourceOrderTests(unittest.TestCase):
    def test_jamendo_first_and_no_other_source_contacted(self):
        jamendo_track = Track(
            url="https://x/j.mp3",
            name="J - DJ",
            artist="DJ",
            license="CC BY",
            duration=25.0,
        )
        with mock.patch(
            "shorts_clipper.downloader.music_scraper.search_jamendo_tracks",
            return_value=[jamendo_track],
        ) as fake_jamendo, mock.patch(
            "shorts_clipper.downloader.music_scraper.search_pixabay_api",
            side_effect=AssertionError("pixabay api must not be contacted"),
        ) as fake_pix_api, mock.patch(
            "shorts_clipper.downloader.music_scraper.scrape_pixabay_urls",
            side_effect=AssertionError("pixabay scrape must not be contacted"),
        ) as fake_pix_scrape, mock.patch(
            "shorts_clipper.downloader.music_scraper.scrape_freestock_tracks",
            side_effect=AssertionError("freestock must not be contacted"),
        ) as fake_fs, mock.patch(
            "shorts_clipper.downloader.music_scraper._download", return_value=True
        ):
            with tempfile.TemporaryDirectory() as tmp:
                out = fetch_phonk_tracks(
                    Path(tmp), max_tracks=5, jamendo_api_key="JKEY"
                )

        fake_jamendo.assert_called_once_with(api_key="JKEY", max_tracks=5)
        fake_pix_api.assert_not_called()
        fake_pix_scrape.assert_not_called()
        fake_fs.assert_not_called()
        self.assertEqual([p.name for p in out], ["jamendo_J - DJ.mp3"])

    def test_jamendo_empty_falls_through_to_pixabay_api(self):
        pix_track = Track(
            url="https://x/p.mp3",
            name="P - Artist",
            artist="Artist",
            license="Pixabay Content License (free commercial use)",
        )
        with mock.patch(
            "shorts_clipper.downloader.music_scraper.search_jamendo_tracks",
            return_value=[],
        ) as fake_jamendo, mock.patch(
            "shorts_clipper.downloader.music_scraper.search_pixabay_api",
            return_value=[pix_track],
        ) as fake_pix_api, mock.patch(
            "shorts_clipper.downloader.music_scraper.scrape_pixabay_urls",
            side_effect=AssertionError("pixabay scrape must not be contacted"),
        ), mock.patch(
            "shorts_clipper.downloader.music_scraper.scrape_freestock_tracks",
            side_effect=AssertionError("freestock must not be contacted"),
        ), mock.patch(
            "shorts_clipper.downloader.music_scraper._download", return_value=True
        ):
            with tempfile.TemporaryDirectory() as tmp:
                out = fetch_phonk_tracks(
                    Path(tmp),
                    max_tracks=5,
                    jamendo_api_key="JKEY",
                    pixabay_api_key="PKEY",
                )

        fake_jamendo.assert_called_once_with(api_key="JKEY", max_tracks=5)
        fake_pix_api.assert_called_once_with(api_key="PKEY", max_tracks=5)
        self.assertTrue(out)
        self.assertTrue(out[0].name.startswith("pixabay_api_"))

    def test_jamendo_attribution_sidecar_formats(self):
        jamendo_track = Track(
            url="https://x/j.mp3",
            name="J - DJ",
            artist="DJ",
            license="CC BY-SA",
            duration=25.0,
        )
        with mock.patch(
            "shorts_clipper.downloader.music_scraper.search_jamendo_tracks",
            return_value=[jamendo_track],
        ), mock.patch(
            "shorts_clipper.downloader.music_scraper._download", return_value=True
        ):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                fetch_phonk_tracks(tmp_path, max_tracks=5, jamendo_api_key="JKEY")
                attr = list(tmp_path.glob("*.attribution.txt"))[0]
                text = attr.read_text(encoding="utf-8")

        self.assertIn("Track: J - DJ\n", text)
        self.assertIn("Source: https://x/j.mp3\n", text)
        self.assertIn("License: CC BY-SA\n", text)
        self.assertIn("Credit: Jamendo (DJ)\n", text)


if __name__ == "__main__":
    unittest.main()