import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_clipper.captions.music import track_attribution
from shorts_clipper.downloader.music_scraper import (
    Track,
    _is_audible_bytes,
    ensure_phonk_tracks,
    fetch_phonk_tracks,
    scrape_freestock_tracks,
)


class TrackAttributionTests(unittest.TestCase):
    def _attr_file(self, content):
        tmp = Path(tempfile.gettempdir())
        track = tmp / "song.mp3"
        (tmp / "song.mp3.attribution.txt").write_text(content, encoding="utf-8")
        return track

    def test_reads_credit_and_source(self):
        track = self._attr_file(
            "Track: awesome-phonk\n"
            "Source: https://x.com/?a=1\n"
            "License: CC BY 4.0\n"
            "Credit: Royalty Free Music\n"
        )
        try:
            result = track_attribution(track)
            self.assertIn("Royalty Free Music", result)
            self.assertIn("x.com", result)
        finally:
            (track.parent / "song.mp3.attribution.txt").unlink(missing_ok=True)

    def test_none_when_no_attr_file(self):
        self.assertIsNone(track_attribution(Path("D:/shorts_music/nonexistent.wav")))

    def test_none_when_no_credit_line(self):
        track = self._attr_file("Some other line\n")
        try:
            self.assertIsNone(track_attribution(track))
        finally:
            (track.parent / "song.mp3.attribution.txt").unlink(missing_ok=True)


class AudibleBytesTests(unittest.TestCase):
    def test_mp3_with_id3(self):
        self.assertTrue(_is_audible_bytes(b"ID3\x03\x00", ".mp3"))

    def test_mp3_with_frame_sync(self):
        self.assertTrue(_is_audible_bytes(b"\xff\xfb\x90\x64", ".mp3"))

    def test_html_rejected(self):
        self.assertFalse(_is_audible_bytes(b"<!DOCTYPE html>...", ".mp3"))
        self.assertFalse(_is_audible_bytes(b""[:0], ".mp3"))

    def test_wav_riff(self):
        self.assertTrue(_is_audible_bytes(b"RIFF\x00\x00\x00\x00", ".wav"))

    def test_unknown_ext_accepts_any_nonempty(self):
        self.assertTrue(_is_audible_bytes(b"whatever", ".xyz"))


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
