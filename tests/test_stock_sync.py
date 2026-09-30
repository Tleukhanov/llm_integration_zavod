"""Sync tests for stock subtitles and TTS tail."""

from unittest import mock

from shorts_clipper.audio.tts import resolve_speech_end, speech_window
from shorts_clipper.pipeline.stock_runner import _segments_from_word_bounds
from shorts_clipper.visual.stock import build_word_segments


def test_word_bounds_single_shift():
    """Word bounds already carry silence so shift stays zero."""
    bounds = [
        ("hello", 0.5, 0.9),
        ("world", 0.9, 1.3),
        ("today", 1.3, 1.7),
        ("again", 1.7, 2.1),
    ]
    segments = _segments_from_word_bounds(bounds, seg_shift=0.0)
    assert segments
    first = segments[0]
    assert abs(first.start - 0.5) < 1e-6
    assert abs(first.words[0].start - 0.5) < 1e-6
    assert abs(first.words[0].end - 0.9) < 1e-6
    double = _segments_from_word_bounds(bounds, seg_shift=0.5)
    assert abs(double[0].start - 1.0) < 1e-6
    assert abs(first.start - 0.5) < 1e-6


def test_uniform_consistent_single_shift():
    """Uniform fallback shifts once and matches word bounds start."""
    bounds = [
        ("hello", 0.5, 0.9),
        ("world", 0.9, 1.3),
        ("today", 1.3, 1.7),
    ]
    word_segs = _segments_from_word_bounds(bounds, seg_shift=0.0)
    shift = 0.5
    effective = 4.45
    uniform = build_word_segments("hello world today again", duration=effective)
    assert uniform
    shifted_starts = [s.start + shift for s in uniform]
    shifted_ends = [s.end + shift for s in uniform]
    assert abs(shifted_starts[0] - shift) < 1e-6
    assert abs(shifted_starts[0] - word_segs[0].start) < 1e-6
    assert abs(shifted_ends[-1] - (shift + effective)) < 1e-6


def test_tail_no_trailing_keeps_full_voice():
    """Missing trailing silence extends to file end minus epsilon."""
    intervals = [(0.0, 0.5), (2.0, 2.5)]
    got = resolve_speech_end(intervals, 5.0, epsilon=0.05)
    assert abs(got - 4.95) < 1e-6
    assert got > 4.0


def test_tail_trailing_trims():
    """Trailing silence keeps legacy trim at last silence start."""
    intervals = [(0.0, 0.5), (2.0, 2.5), (4.5, 5.0)]
    got = resolve_speech_end(intervals, 5.0, epsilon=0.05)
    assert abs(got - 4.5) < 1e-6


def test_boundaries_monotonic():
    """Segments and words stay ordered with start below end."""
    bounds = [
        ("one", 0.5, 0.8),
        ("two", 0.8, 1.1),
        ("three", 1.1, 1.4),
        ("four", 1.4, 1.7),
        ("five", 1.7, 2.0),
    ]
    segments = _segments_from_word_bounds(bounds, seg_shift=0.0)
    assert segments
    prev_end = -1.0
    for seg in segments:
        assert seg.start < seg.end
        assert seg.start >= prev_end - 1e-6
        prev_end = seg.end
        wprev = seg.start - 1e-6
        for w in seg.words:
            assert w.start < w.end
            assert w.start >= wprev - 1e-6
            assert w.start >= seg.start - 1e-6
            assert w.end <= seg.end + 1e-6
            wprev = w.start
    uniform = build_word_segments("one two three four five", duration=4.5)
    shifted = [(s.start + 0.5, s.end + 0.5) for s in uniform]
    for start, end in shifted:
        assert start < end
    assert shifted == sorted(shifted)


def test_speech_window_no_trailing_uses_duration():
    """Speech window extends past last mid pause when tail is voiced."""
    stderr = (
        "[silencedetect] silence_start: 0\n"
        "[silencedetect] silence_end: 0.5 | silence_duration: 0.5\n"
        "[silencedetect] silence_start: 2\n"
        "[silencedetect] silence_end: 2.5 | silence_duration: 0.5\n"
    )
    fake = mock.Mock()
    fake.returncode = 0
    fake.stderr = stderr
    with mock.patch("shorts_clipper.audio.tts.subprocess.run", return_value=fake):
        with mock.patch(
            "shorts_clipper.audio.tts._tts_wav_duration", return_value=5.0
        ):
            got = speech_window("dummy.wav")
    assert got is not None
    start, end = got
    assert abs(start - 0.5) < 1e-6
    assert abs(end - 4.95) < 1e-6


def test_speech_window_trailing_trims():
    """Speech window trims trailing silence like legacy behavior."""
    stderr = (
        "[silencedetect] silence_start: 0\n"
        "[silencedetect] silence_end: 0.5 | silence_duration: 0.5\n"
        "[silencedetect] silence_start: 4.5\n"
        "[silencedetect] silence_end: 5 | silence_duration: 0.5\n"
    )
    fake = mock.Mock()
    fake.returncode = 0
    fake.stderr = stderr
    with mock.patch("shorts_clipper.audio.tts.subprocess.run", return_value=fake):
        with mock.patch(
            "shorts_clipper.audio.tts._tts_wav_duration", return_value=5.0
        ):
            got = speech_window("dummy.wav")
    assert got is not None
    start, end = got
    assert abs(start - 0.5) < 1e-6
    assert abs(end - 4.5) < 1e-6
