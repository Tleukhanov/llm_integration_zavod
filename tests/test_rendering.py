import unittest
from pathlib import Path

from shorts_clipper.cropping.geometry import compute_center_crop
from shorts_clipper.rendering.ffmpeg import (
    FfmpegRenderOptions,
    build_vertical_render_command,
)
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path


class CropGeometryTests(unittest.TestCase):
    def test_center_crop_wide_video_to_vertical_ratio(self):
        crop = compute_center_crop(width=1920, height=1080, target_width=1080, target_height=1920)

        self.assertEqual(crop.x, 656)
        self.assertEqual(crop.y, 0)
        self.assertEqual(crop.width, 608)
        self.assertEqual(crop.height, 1080)

    def test_center_crop_tall_video_to_vertical_ratio(self):
        crop = compute_center_crop(width=720, height=1600, target_width=1080, target_height=1920)

        self.assertEqual(crop.x, 0)
        self.assertEqual(crop.width, 720)
        self.assertEqual(crop.height, 1280)
        self.assertEqual(crop.y, 160)


class FfmpegCommandTests(unittest.TestCase):
    def test_builds_safe_argument_list_without_shell_interpolation(self):
        command = build_vertical_render_command(
            input_path=Path("input video.mp4"),
            output_path=Path("out.mp4"),
            start=12.5,
            end=42.5,
            source_width=1920,
            source_height=1080,
            options=FfmpegRenderOptions(video_codec="h264_nvenc", preset="fast"),
        )

        self.assertIsInstance(command, list)
        self.assertEqual(command[0], ffmpeg_path())
        self.assertTrue(Path(command[0]).is_file())
        self.assertIn("input video.mp4", command)
        self.assertIn("h264_nvenc", command)
        self.assertIn("crop=608:1080:656:0,scale=1080:1920", command)
        self.assertEqual(command[-1], "out.mp4")

    def test_rejects_invalid_time_window(self):
        with self.assertRaises(ValueError):
            build_vertical_render_command(
                input_path=Path("in.mp4"),
                output_path=Path("out.mp4"),
                start=10,
                end=5,
                source_width=1920,
                source_height=1080,
            )


if __name__ == "__main__":
    unittest.main()
