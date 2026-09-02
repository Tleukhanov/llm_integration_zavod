import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.downloader.music_scraper import (
    Track,
    ensure_phonk_tracks,
    fetch_phonk_tracks,
    scrape_freestock_tracks,
)


class TrackReprTests(unittest.TestCase):
    def test_track_fields(self):
        t = Track(url="https://x/y.mp3", name="y", artist=None, license="CC BY")
        self.assertEqual(t.name, "y")
        self.assertEqual(t.license, "CC BY")


class ScrapeFreestockTests(unittest.TestCase):
    @mock.patch("shorts_clipper.downloader.music_scraper._fetch_html")
    def test_parses_relative_and_absolute_mp3(self, fake_fetch):
        fake_fetch.return_value = """
        <audio src='/music/artist/mp3/artist-one.mp3'></audio>
        <a download href="https://www.free-stock-music.com/music/b/mp3/b-two.mp3">b</a>
        <audio src='/music/c/mp3/c-three.mp3'></audio>
        """
        tracks = scrape_freestock_tracks(max_results=10)
        urls = {t.url for t in tracks}
        self.assertIn("https://www.free-stock-music.com/music/artist/mp3/artist-one.mp3", urls)
        self.assertIn("https://www.free-stock-music.com/music/b/mp3/b-two.mp3", urls)
        self.assertIn("https://www.free-stock-music.com/music/c/mp3/c-three.mp3", urls)
        # All carry a CC license note by default.
        for t in tracks:
            self.assertIn("CC BY", t.license)

    @mock.patch("shorts_clipper.downloader.music_scraper._fetch_html")
    def test_empty_on_fetch_failure(self, fake_fetch):
        fake_fetch.side_effect = RuntimeError("network down")
        self.assertEqual(scrape_freestock_tracks(max_results=5), [])


class EnsurePhonkTests(unittest.TestCase):
    @mock.patch("shorts_clipper.downloader.music_scraper.fetch_phonk_tracks")
    def test_skips_when_sparse_min_met(self, fake_fetch):
        fake_files = [mock.Mock(spec=Path), mock.Mock(spec=Path), mock.Mock(spec=Path)]
        for f in fake_files:
            f.is_file.return_value = True
            f.suffix = Path("a.mp3").suffix  # .mp3
        path = mock.Mock(spec=Path)
        path.is_dir.return_value = True
        path.iterdir.return_value = fake_files
        with mock.patch("shorts_clipper.downloader.music_scraper.Path", return_value=path):
            ensure_phonk_tracks(Path("D:/shorts_music"), min_tracks=2)
        fake_fetch.assert_not_called()

    @mock.patch("shorts_clipper.downloader.music_scraper.fetch_phonk_tracks")
    def test_fetches_when_below_min(self, fake_fetch):
        one = mock.Mock(spec=Path)
        one.is_file.return_value = True
        one.suffix = ".mp3"
        path = mock.Mock(spec=Path)
        path.is_dir.return_value = True
        path.iterdir.return_value = [one]
        with mock.patch("shorts_clipper.downloader.music_scraper.Path", return_value=path):
            ensure_phonk_tracks(Path("D:/shorts_music"), min_tracks=3, fetch_count=5)
        fake_fetch.assert_called_once()
        self.assertEqual(fake_fetch.call_args.kwargs["max_tracks"], 5)

    @mock.patch("shorts_clipper.downloader.music_scraper.scrape_freestock_tracks")
    @mock.patch("shorts_clipper.downloader.music_scraper.scrape_pixabay_urls")
    def test_fetch_swallows_download_errors(self, fake_pix, fake_fs):
        # Pixabay empty -> falls to free-stock -> download fails -> no raise.
        fake_pix.return_value = []
        fake_fs.return_value = [
            Track(url="https://x/f.mp3", name="f", license="CC BY")
        ]
        with mock.patch(
            "shorts_clipper.downloader.music_scraper._download", return_value=False
        ):
            out = fetch_phonk_tracks(Path("D:/shorts_music"), max_tracks=5)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
