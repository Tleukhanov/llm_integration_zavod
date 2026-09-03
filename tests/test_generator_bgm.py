"""Rendering tests for the optional background-music feature."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import av

from shorts_clipper.captions.generator import burn_subtitles
from shorts_clipper.core.models import TranscriptSegment, TranscriptWord
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path


def _make_sample(segments):
    return [
        TranscriptSegment(
            start=seg["start"],
            end=seg["end"],
            text=seg["text"],
            words=[
                TranscriptWord(start=w[0], end=w[1], word=w[2])
                for w in seg.get("words", [])
            ],
        )
        for seg in segments
    ]


def _build_tiny_mp4(path: Path, width=160, height=288, seconds=2.0, fps=10):
    """Create a tiny video+audio file with PyAV (avi/pcm for easy encoding)."""
    fmt = av.open(str(path), mode="w")
    stream = fmt.add_stream("mpeg4", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"

    a_stream = fmt.add_stream("pcm_s16le", rate=44100)
    a_stream.layout = "mono"

    for i in range(int(seconds * fps)):
        import numpy as np

        rgb = np.full((height, width, 3), (i * 40) % 255, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        for packet in stream.encode(frame):
            fmt.mux(packet)

        aframe = av.AudioFrame(format="s16", layout="mono", samples=4410)
        aframe.sample_rate = 44100
        aframe.pts = int(i * 44100 / fps)
        for packet in a_stream.encode(aframe):
            fmt.mux(packet)

    for packet in stream.encode():
        fmt.mux(packet)
    for packet in a_stream.encode():
        fmt.mux(packet)
    fmt.close()


class BurnSubtitleBgmTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="bgm_test_")
        self.tmp = Path(self._tmp.name)
        self.video = self.tmp / "input.avi"
        _build_tiny_mp4(self.video)
        self.music = self.tmp / "phonk.mp3"
        # Only needs to exist — ffmpeg run is mocked in the assertions.
        self.music.write_bytes(b"ID3\x03\x00\x00\x00fake-music-bytes")
        self.segments = _make_sample(
            [
                {
                    "start": 0.0,
                    "end": 1.8,
                    "text": "hello world this is great",
                    "words": [
                        (0.0, 0.5, "hello"),
                        (0.6, 3.0, "world"),
                    ],
                }
            ]
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_without_bgm_command_matches_shape(self):
        out = self.tmp / "no_bgm.mp4"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            return R()

        with mock.patch("shorts_clipper.captions.generator.subprocess.run", fake_run):
            burn_subtitles(
                self.video, self.segments, start_offset=0.0, output_path=out,
            )
        cmd = captured["cmd"]
        self.assertEqual(cmd[0], ffmpeg_path())
        # no -filter_complex (uses -vf), no music input
        self.assertIn("-vf", cmd)
        self.assertNotIn("-filter_complex", cmd)
        self.assertNotIn(str(self.music), cmd)
        # no bgm audio args
        self.assertIn("-af", cmd)

    def test_with_bgm_mixes_audio_and_keeps_ass(self):
        out = self.tmp / "with_bgm.mp4"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            return R()

        with mock.patch("shorts_clipper.captions.generator.subprocess.run", fake_run):
            r = burn_subtitles(
                self.video,
                self.segments,
                start_offset=0.0,
                output_path=out,
                bgm_audio=self.music,
                bgm_volume=0.30,
            )

        cmd = captured["cmd"]
        self.assertEqual(cmd[0], ffmpeg_path())
        joined = " ".join(cmd)
        # music path present as input
        self.assertIn(str(self.music), cmd)
        self.assertIn("-filter_complex", cmd)
        self.assertIn("amix=inputs=2", joined)
        self.assertIn("volume=0.300", joined)
        self.assertIn("ass=", joined)


class HookBannerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hook_test_")
        self.tmp = Path(self._tmp.name)
        self.video = self.tmp / "input.avi"
        _build_tiny_mp4(self.video)
        self.segments = _make_sample(
            [
                {
                    "start": 0.0,
                    "end": 1.8,
                    "text": "hello world",
                    "words": [
                        (0.0, 0.5, "hello"),
                        (0.6, 1.8, "world"),
                    ],
                }
            ]
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _capture_cmd(self, **burn_kwargs):
        out = self.tmp / "out.mp4"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd

            class R:
                returncode = 0
                stderr = ""
                stdout = ""

            return R()

        with mock.patch("shorts_clipper.captions.generator.subprocess.run", fake_run):
            burn_subtitles(
                self.video,
                self.segments,
                start_offset=0.0,
                output_path=out,
                **burn_kwargs,
            )
        return captured["cmd"]

    def test_hook_enabled_includes_drawtext(self):
        cmd = self._capture_cmd(hook_banner_text="WAIT FOR IT...")
        joined = " ".join(cmd)
        self.assertIn("drawtext=", joined)
        self.assertIn("WAIT FOR IT...", joined)

    def test_hook_disabled_no_drawtext(self):
        cmd = self._capture_cmd(hook_banner_text=None)
        joined = " ".join(cmd)
        self.assertNotIn("WAIT FOR IT...", joined)

    def test_hook_empty_string_no_drawtext(self):
        cmd = self._capture_cmd(hook_banner_text="")
        joined = " ".join(cmd)
        self.assertNotIn("drawtext=", joined)

    def test_hook_with_bgm_includes_drawtext(self):
        music = self.tmp / "phonk.mp3"
        music.write_bytes(b"ID3\x03\x00\x00\x00fake-music-bytes")
        cmd = self._capture_cmd(
            hook_banner_text="CLICK THIS",
            bgm_audio=music,
            bgm_volume=0.30,
        )
        joined = " ".join(cmd)
        self.assertIn("drawtext=", joined)
        self.assertIn("CLICK THIS", joined)
        self.assertIn("-filter_complex", joined)

    def test_hook_fade_out_alpha_expression(self):
        cmd = self._capture_cmd(hook_banner_text="HOOK")
        joined = " ".join(cmd)
        # Verify the fade-out alpha expression is present
        self.assertIn("alpha=", joined)
        self.assertIn("between(t,0,1.0)", joined)


class HookSettingsTests(unittest.TestCase):
    def test_defaults(self):
        from shorts_clipper.core.settings import Settings

        s = Settings()
        self.assertTrue(s.hook_banner_enabled)
        self.assertEqual(s.hook_banner_text, "WAIT FOR IT\u2026")

    def test_env_parse(self):
        import os

        from shorts_clipper.core.settings import Settings

        os.environ["SHORTS_HOOK_BANNER_ENABLED"] = "false"
        os.environ["SHORTS_HOOK_BANNER_TEXT"] = "WATCH NOW"
        try:
            s = Settings.from_env("_nonexistent.env")
            self.assertFalse(s.hook_banner_enabled)
            self.assertEqual(s.hook_banner_text, "WATCH NOW")
        finally:
            os.environ.pop("SHORTS_HOOK_BANNER_ENABLED", None)
            os.environ.pop("SHORTS_HOOK_BANNER_TEXT", None)


if __name__ == "__main__":
    unittest.main()
