import subprocess
import unittest
from pathlib import Path

from shorts_clipper.attention.audio_energy import bias_toward_energetic, extract_audio_energy
from shorts_clipper.attention.judges import StreamAudioEnergyJudge
from shorts_clipper.attention.models import FeatureSet
from shorts_clipper.core.models import TranscriptSegment
from shorts_clipper.core.settings import Settings
from shorts_clipper.utils.ffmpeg_path import ffmpeg_path


def _make_sine_clip(path: Path, seconds: int = 3) -> Path:
    cmd = [
        ffmpeg_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=880:duration={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size=160x120:rate=10",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


def _seg(start: float, end: float) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text="x")


class AudioEnergyExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(__file__).parent / "_tmp_clip.mp4"
        cls.tmp = tmp
        if not tmp.exists():
            _make_sine_clip(tmp)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp.exists():
            cls.tmp.unlink()

    def test_extract_returns_normalized_values(self):
        energy = extract_audio_energy(self.tmp, window_seconds=0.5)
        self.assertTrue(energy)
        self.assertTrue(all(0.0 <= e <= 1.0 for e in energy))
        self.assertAlmostEqual(max(energy), 1.0)

    def test_empty_or_missing_file_returns_empty(self):
        missing = Path(__file__).parent / "_does_not_exist.mp4"
        self.assertEqual(extract_audio_energy(missing), [])

    def test_silent_file_returns_empty_or_zeros(self):
        silent = Path(__file__).parent / "_silent.mp4"
        if not silent.exists():
            subprocess.run(
                [
                    ffmpeg_path(),
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=16000:cl=mono",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=duration=1:size=160x120:rate=10",
                    "-t",
                    "1",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    str(silent),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        energy = extract_audio_energy(silent, window_seconds=0.5)
        if energy:
            self.assertTrue(all(e == 0.0 for e in energy))
        silent.unlink(missing_ok=True)


class StreamEnergyJudgeTests(unittest.TestCase):
    def test_empty_energy_is_neutral(self):
        features = FeatureSet(
            text="", word_count=0, words_per_second=0.0, questions=0, exclamations=0,
            emotion_hits=0, viral_hits=0, hook_hits=0, story_arc_markers=0,
            contradictions=0, numbers=0, money_references=0, time_references=0,
            pause_density=0.0, speaker_changes=0, sentiment=0.0, repetition=0,
            visual_dependency_markers=0, raw_words=[], duration=1.0,
            start_time=0.0, end_time=1.0, semantic_segments=[],
        )
        result = StreamAudioEnergyJudge().evaluate(features)
        self.assertEqual(result.score, 0.0)

    def test_energetic_energy_scores_above_neutral(self):
        features = FeatureSet(
            text="", word_count=0, words_per_second=0.0, questions=0, exclamations=0,
            emotion_hits=0, viral_hits=0, hook_hits=0, story_arc_markers=0,
            contradictions=0, numbers=0, money_references=0, time_references=0,
            pause_density=0.0, speaker_changes=0, sentiment=0.0, repetition=0,
            visual_dependency_markers=0, raw_words=[], duration=5.0,
            start_time=0.0, end_time=5.0, semantic_segments=[],
            audio_energy=[1.0, 1.0, 1.0, 1.0, 1.0],
        )
        result = StreamAudioEnergyJudge().evaluate(features)
        self.assertGreaterEqual(result.score, 0.4)


class BiasTowardEnergeticTests(unittest.TestCase):
    def test_prefers_energetic_window(self):
        # 20 seconds, mostly dead air with a loud 2s crowd reaction in the middle
        segments = [_seg(i, i + 1.0) for i in range(20)]
        energy = [0.0] * 9 + [1.0, 1.0] + [0.0] * 9
        chosen = bias_toward_energetic(segments, energy, threshold=0.15, window_seconds=1.0)
        self.assertIsNot(segments, chosen)
        self.assertGreaterEqual(chosen[0].start, 9.0)
        self.assertLessEqual(chosen[-1].end, 11.0)

    def test_returns_original_when_already_energetic(self):
        segments = [_seg(i, i + 1.0) for i in range(5)]
        energy = [1.0] * 5
        chosen = bias_toward_energetic(segments, energy, threshold=0.15, window_seconds=1.0)
        self.assertIs(chosen, segments)

    def test_empty_energy_returns_fallback(self):
        segments = [_seg(i, i + 1.0) for i in range(3)]
        chosen = bias_toward_energetic(segments, [], fallback="FB", threshold=0.15)
        self.assertEqual(chosen, "FB")


class SettingsParseTests(unittest.TestCase):
    def test_defaults(self):
        s = Settings()
        self.assertTrue(s.stream_audio_energy_enabled)
        self.assertEqual(s.stream_energy_window_seconds, 1.0)
        self.assertEqual(s.stream_energy_threshold, 0.15)

    def test_env_parse(self):
        import os

        os.environ["SHORTS_STREAM_AUDIO_ENERGY"] = "false"
        os.environ["SHORTS_STREAM_ENERGY_WINDOW"] = "2.0"
        os.environ["SHORTS_STREAM_ENERGY_THRESHOLD"] = "0.4"
        try:
            s = Settings.from_env("_nonexistent.env")
            self.assertFalse(s.stream_audio_energy_enabled)
            self.assertEqual(s.stream_energy_window_seconds, 2.0)
            self.assertEqual(s.stream_energy_threshold, 0.4)
        finally:
            os.environ.pop("SHORTS_STREAM_AUDIO_ENERGY", None)
            os.environ.pop("SHORTS_STREAM_ENERGY_WINDOW", None)
            os.environ.pop("SHORTS_STREAM_ENERGY_THRESHOLD", None)


if __name__ == "__main__":
    unittest.main()
