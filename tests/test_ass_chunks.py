"""Fast-speech ASS chunk guards."""

import unittest

from shorts_clipper.captions.generator import _build_ass_chunks
from shorts_clipper.core.models import TranscriptSegment, TranscriptWord


def _fast_words() -> list[TranscriptWord]:
    """Build eight words at 30ms each."""
    texts = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
    words: list[TranscriptWord] = []
    for i, text in enumerate(texts):
        start = i * 0.03
        words.append(TranscriptWord(start=start, end=start + 0.03, word=text))
    return words


def _fast_segments() -> list[TranscriptSegment]:
    """Build one fast-speech segment."""
    words = _fast_words()
    return [TranscriptSegment(start=0.0, end=0.24, text=" ".join(w.word for w in words), words=words)]


def _overlap_segments() -> list[TranscriptSegment]:
    """Build segments where the second ends before the first."""
    first = [
        TranscriptWord(start=0.0, end=0.5, word="alpha"),
        TranscriptWord(start=0.5, end=1.0, word="beta"),
        TranscriptWord(start=1.0, end=1.5, word="gamma"),
        TranscriptWord(start=1.5, end=2.0, word="delta"),
    ]
    second = [
        TranscriptWord(start=0.2, end=0.4, word="fast"),
        TranscriptWord(start=0.4, end=0.6, word="talk"),
    ]
    return [
        TranscriptSegment(start=0.0, end=2.0, text="alpha beta gamma delta", words=first),
        TranscriptSegment(start=0.2, end=0.6, text="fast talk", words=second),
    ]


def _normal_segments() -> list[TranscriptSegment]:
    """Build one normal-pacing segment."""
    words = [
        TranscriptWord(start=0.0, end=0.5, word="never"),
        TranscriptWord(start=0.5, end=1.0, word="give"),
        TranscriptWord(start=1.0, end=1.5, word="up"),
        TranscriptWord(start=1.5, end=2.0, word="bro"),
    ]
    return [TranscriptSegment(start=0.0, end=2.0, text="never give up bro", words=words)]


class FastSpeechTests(unittest.TestCase):
    def test_loses_zero_words(self):
        """Every fast word survives in exactly one chunk."""
        chunks = _build_ass_chunks(_fast_segments(), 0.0)
        expected = [w.word.upper() for w in _fast_words()]
        found: list[str] = []
        for chunk in chunks:
            found.extend(chunk["text"].split())
        self.assertEqual(found, expected)

    def test_monotonic_start_lt_end(self):
        """Chunks stay ordered with positive duration."""
        chunks = _build_ass_chunks(_fast_segments(), 0.0)
        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            self.assertLess(chunk["start"], chunk["end"])
        for prev, cur in zip(chunks, chunks[1:], strict=False):
            self.assertGreaterEqual(cur["start"], prev["end"])
            self.assertGreaterEqual(cur["end"], prev["end"])


class ClampMergeTests(unittest.TestCase):
    def test_merge_path_warns_and_keeps_words(self):
        """Degenerate chunks merge with a warning and keep all words."""
        segments = _overlap_segments()
        with self.assertLogs("shorts_clipper.captions.generator", level="WARNING") as logs:
            chunks = _build_ass_chunks(segments, 0.0)
        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            self.assertLess(chunk["start"], chunk["end"])
        expected = ["ALPHA", "BETA", "GAMMA", "DELTA", "FAST", "TALK"]
        found: list[str] = []
        for chunk in chunks:
            found.extend(chunk["text"].split())
        self.assertEqual(found, expected)
        combined = "\n".join(logs.output)
        self.assertIn("Clamped", combined)
        self.assertIn("merged", combined)

    def test_fast_speech_exercises_clamping(self):
        """Dense words trigger the overlap clamp warning."""
        with self.assertLogs("shorts_clipper.captions.generator", level="WARNING") as logs:
            chunks = _build_ass_chunks(_fast_segments(), 0.0)
        self.assertGreater(len(chunks), 0)
        self.assertIn("Clamped", "\n".join(logs.output))


class NormalPacingTests(unittest.TestCase):
    def test_normal_pacing_output_unchanged(self):
        """Normal pacing keeps a single exact chunk."""
        chunks = _build_ass_chunks(_normal_segments(), 0.0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "NEVER GIVE UP BRO")
        self.assertAlmostEqual(chunks[0]["start"], 0.0)
        self.assertAlmostEqual(chunks[0]["end"], 2.0)


if __name__ == "__main__":
    unittest.main()
