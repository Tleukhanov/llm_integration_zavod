"""Piper voiceover engine: selection, graceful fallback, word boundaries."""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import wave
from pathlib import Path
from unittest import mock

import pytest

from shorts_clipper.audio import piper_tts
from shorts_clipper.audio.tts import (
    DEFAULT_VOICE,
    finalize_word_boundaries,
    normalize_word_boundaries,
    pick_voice,
    synthesize_voiceover,
    synthesize_voiceover_boundaries,
    voiceover_engine,
)
from shorts_clipper.core.settings import Settings

LINE = "Пятнадцать тысяч в месяц уходят на сервисы, которыми пользуются по минуте."


@pytest.fixture(autouse=True)
def _no_local_env(monkeypatch, tmp_path):
    """Isolate every test from the developer's real .env, models dir and network."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    for name in (
        "SHORTS_VO_ENGINE",
        "SHORTS_VO_PIPER_MODEL",
        "SHORTS_VO_VOICE",
        "SHORTS_VO_RATE",
        "SHORTS_MODELS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SHORTS_MODELS_DIR", "models")
    piper_tts.reset_voice_cache()
    # No unit test may reach the internet; the ones that exercise the download
    # path patch _download themselves.
    with mock.patch.object(
        piper_tts,
        "_download",
        side_effect=AssertionError("network access in a unit test"),
    ):
        yield
    piper_tts.reset_voice_cache()


def _write_wav(path: Path, seconds: float = 2.0, rate: int = 48000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


class _FakePiperVoice:
    """Stand-in for ``piper.PiperVoice`` that writes a real, silent WAV."""

    def __init__(self, seconds: float = 3.2, rate: int = 22050):
        self.seconds = seconds
        self.rate = rate
        self.calls: list = []

    def synthesize_wav(self, text, wav_file, syn_config=None):
        self.calls.append((text, syn_config))
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(self.rate)
        wav_file.writeframes(b"\x00\x00" * int(self.rate * self.seconds))


class _FakeSynthesisConfig:
    """Captures the length_scale every synthesis call was asked for."""

    created: list = []

    def __init__(self, length_scale=None, **kwargs):
        self.length_scale = length_scale
        self.__dict__.update(kwargs)
        _FakeSynthesisConfig.created.append(self)


@pytest.fixture(autouse=True)
def _reset_fake_configs():
    _FakeSynthesisConfig.created = []
    yield


def _fake_transcode(seconds: float = 3.2):
    """transcode_to_wav stand-in that materialises a readable 48 kHz WAV."""

    def _run(src, dest):
        _write_wav(Path(dest), seconds=seconds)
        return True

    return _run


@contextlib.contextmanager
def _piper_stub(voice=None, align=None, span=None, seconds: float = 3.2):
    """Patch every Piper dependency so no model/network is needed."""
    voice = voice if voice is not None else _FakePiperVoice(seconds=seconds)
    align = [] if align is None else align
    span = (0.0, seconds) if span is None else span
    with (
        mock.patch.object(piper_tts, "piper_installed", return_value=True),
        mock.patch.object(piper_tts, "ensure_model", return_value=True),
        mock.patch.object(piper_tts, "_load_voice", return_value=voice),
        mock.patch.object(piper_tts, "_synthesis_config", _FakeSynthesisConfig),
        mock.patch.object(piper_tts, "align_word_boundaries", return_value=align),
        mock.patch.object(piper_tts, "_speech_span", return_value=span),
        mock.patch(
            "shorts_clipper.audio.tts.transcode_to_wav", side_effect=_fake_transcode(seconds)
        ),
    ):
        yield voice


@contextlib.contextmanager
def _no_edge():
    """Force the edge-tts side to be deterministic and offline."""
    with (
        mock.patch(
            "shorts_clipper.audio.tts._edge_tts_library_available", return_value=False
        ),
        mock.patch("shorts_clipper.audio.tts._synthesize_edge", return_value=None) as edge,
    ):
        yield edge


# --------------------------------------------------------------------------
# Engine selection
# --------------------------------------------------------------------------


def test_piper_is_the_default_engine():
    assert Settings.from_env().vo_engine == "piper"
    assert voiceover_engine() == "piper"


def test_edge_engine_forced_via_setting(monkeypatch):
    monkeypatch.setenv("SHORTS_VO_ENGINE", "edge")
    assert Settings.from_env().vo_engine == "edge"
    assert voiceover_engine() == "edge"


@pytest.mark.parametrize("raw", ["edge", "EDGE", "edge-tts", "edgetts", " Edge "])
def test_edge_engine_aliases(monkeypatch, raw):
    monkeypatch.setenv("SHORTS_VO_ENGINE", raw)
    assert Settings.from_env().vo_engine == "edge"


@pytest.mark.parametrize("raw", ["", "piper", "PIPER", "nonsense", "banana"])
def test_unknown_engine_coerces_to_piper(monkeypatch, raw):
    """An unknown value must never mute a render: piper degrades to edge-tts."""
    monkeypatch.setenv("SHORTS_VO_ENGINE", raw)
    assert Settings.from_env().vo_engine == "piper"


def test_piper_used_when_engine_is_piper(monkeypatch, tmp_path):
    monkeypatch.setenv("SHORTS_VO_ENGINE", "piper")
    voice = _FakePiperVoice()
    with _piper_stub(voice=voice):
        with mock.patch(
            "shorts_clipper.audio.tts._synthesize_edge", side_effect=AssertionError("edge used")
        ):
            path = synthesize_voiceover(LINE, tmp_path / "vo.wav")
    assert path is not None and path.is_file()
    assert len(voice.calls) == 1
    assert voice.calls[0][0] == LINE


def test_edge_used_when_engine_forced(monkeypatch, tmp_path):
    monkeypatch.setenv("SHORTS_VO_ENGINE", "edge")
    with mock.patch.object(
        piper_tts, "synthesize", side_effect=AssertionError("piper used")
    ):
        with mock.patch(
            "shorts_clipper.audio.tts._synthesize_edge", return_value=None
        ) as edge:
            path = synthesize_voiceover(LINE, tmp_path / "vo.wav")
    assert path is None
    edge.assert_called_once()


def test_voice_selection_is_unchanged():
    """Piper must not disturb pick_voice / DEFAULT_VOICE behaviour."""
    assert pick_voice("Дисциплина освобождает разум", configured=None) == "ru-RU-DmitryNeural"
    assert pick_voice("Discipline frees the mind", configured=None) == DEFAULT_VOICE
    assert (
        pick_voice("Любой текст", configured="en-US-ChristopherNeural")
        == "en-US-ChristopherNeural"
    )
    assert Settings.from_env().vo_voice == DEFAULT_VOICE


def test_piper_model_follows_detected_language():
    assert piper_tts.resolve_model_name(LINE) == "ru_RU-denis-medium"
    assert piper_tts.resolve_model_name("Discipline frees the mind") == "en_US-amy-medium"


def test_configured_edge_voice_maps_to_same_language_piper_model():
    """An explicit ru edge voice must not silently switch to an English voice."""
    assert (
        piper_tts.resolve_model_name(LINE, configured="ru-RU-SvetlanaNeural")
        == "ru_RU-denis-medium"
    )
    assert (
        piper_tts.resolve_model_name("Discipline", configured="ru-RU-DmitryNeural")
        == "ru_RU-denis-medium"
    )
    # DEFAULT_VOICE means "auto", exactly as pick_voice treats it, so the
    # detected text language still wins.
    assert (
        piper_tts.resolve_model_name(LINE, configured=DEFAULT_VOICE)
        == "ru_RU-denis-medium"
    )


def test_configured_piper_model_id_wins():
    assert (
        piper_tts.resolve_model_name(LINE, configured="ru_RU-irina-medium")
        == "ru_RU-irina-medium"
    )


def test_piper_model_env_override(monkeypatch):
    monkeypatch.setenv("SHORTS_VO_PIPER_MODEL", "ru_RU-irina-medium")
    assert Settings.from_env().vo_piper_model == "ru_RU-irina-medium"
    assert piper_tts.resolve_model_name(LINE) == "ru_RU-irina-medium"


# --------------------------------------------------------------------------
# Fallback to edge-tts
# --------------------------------------------------------------------------


def _assert_sane(bounds):
    """The contract the caption path relies on (see test_stock_sync.py)."""
    assert bounds, "word boundaries must not be empty"
    assert all(len(b) == 3 for b in bounds)
    previous_start = -1.0
    for word, start, end in bounds:
        assert isinstance(word, str) and word.strip()
        assert start >= 0.0
        assert end > start, f"non-positive span for {word!r}"
        assert start >= previous_start - 1e-6, f"starts out of order at {word!r}"
        previous_start = start
    return bounds


def test_fallback_when_piper_not_installed(tmp_path, caplog):
    """No piper package on the box: degrade to edge-tts, never raise."""
    with mock.patch.object(piper_tts, "piper_installed", return_value=False):
        with _no_edge() as edge:
            with caplog.at_level(logging.WARNING):
                path, bounds = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert path is None
    assert bounds == []
    edge.assert_called_once()
    assert any("not installed" in r.message for r in caplog.records)
    assert any("falling back to edge-tts" in r.message for r in caplog.records)


def test_fallback_when_model_missing(tmp_path, caplog):
    """Piper installed but the voice is not cached and cannot be fetched."""
    with mock.patch.object(piper_tts, "piper_installed", return_value=True):
        with mock.patch.object(piper_tts, "ensure_model", return_value=False):
            with _no_edge() as edge:
                with caplog.at_level(logging.WARNING):
                    path, bounds = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert path is None
    assert bounds == []
    edge.assert_called_once()
    assert any("unavailable" in r.message for r in caplog.records)


def test_fallback_when_model_load_fails(tmp_path, caplog):
    with mock.patch.object(piper_tts, "piper_installed", return_value=True):
        with mock.patch.object(piper_tts, "ensure_model", return_value=True):
            with mock.patch.object(piper_tts, "_load_voice", return_value=None):
                with _no_edge() as edge:
                    with caplog.at_level(logging.WARNING):
                        path, bounds = synthesize_voiceover_boundaries(
                            LINE, tmp_path / "vo.wav"
                        )
    assert (path, bounds) == (None, [])
    edge.assert_called_once()
    assert any("could not be loaded" in r.message for r in caplog.records)


def test_fallback_when_piper_synthesis_raises(tmp_path, caplog):
    with _piper_stub():
        with mock.patch(
            "shorts_clipper.audio.tts.transcode_to_wav", side_effect=OSError("boom")
        ):
            with _no_edge() as edge:
                with caplog.at_level(logging.WARNING):
                    path, bounds = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert (path, bounds) == (None, [])
    edge.assert_called_once()
    assert any("Piper synthesis failed" in r.message for r in caplog.records)


def test_fallback_when_transcode_fails(tmp_path, caplog):
    with _piper_stub():
        with mock.patch(
            "shorts_clipper.audio.tts.transcode_to_wav", return_value=False
        ):
            with _no_edge() as edge:
                with caplog.at_level(logging.WARNING):
                    path, bounds = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert (path, bounds) == (None, [])
    edge.assert_called_once()


def test_fallback_when_piper_module_raises(tmp_path):
    """Even an ImportError-level bug inside piper_tts must not escape."""
    with mock.patch(
        "shorts_clipper.audio.piper_tts.synthesize",
        side_effect=RuntimeError("unexpected"),
    ):
        with _no_edge() as edge:
            path, bounds = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert (path, bounds) == (None, [])
    edge.assert_called_once()


def test_no_piper_no_model_no_edge_returns_none_without_raising(tmp_path):
    """The worst case: nothing installed at all."""
    with mock.patch.object(piper_tts, "piper_installed", return_value=False):
        with mock.patch(
            "shorts_clipper.audio.tts._edge_tts_library_available", return_value=False
        ):
            with mock.patch(
                "shorts_clipper.audio.tts._edge_tts_available", return_value=False
            ):
                path, bounds = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert (path, bounds) == (None, [])


def test_empty_text_short_circuits():
    assert synthesize_voiceover_boundaries("", Path("nope.wav")) == (None, [])
    assert synthesize_voiceover("   ", Path("nope.wav")) is None


# --------------------------------------------------------------------------
# Word boundaries
# --------------------------------------------------------------------------


def test_boundaries_from_piper_alignment_are_sane(tmp_path):
    bounds = [
        ("Пятнадцать", 0.10, 0.72),
        ("тысяч", 0.72, 1.10),
        ("в", 1.10, 1.18),
        ("месяц", 1.18, 1.66),
        ("уходят", 1.70, 2.20),
        ("на", 2.20, 2.34),
    ]
    with _piper_stub(align=bounds):
        with mock.patch(
            "shorts_clipper.audio.tts._synthesize_edge", side_effect=AssertionError("edge used")
        ):
            path, got = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert path is not None
    _assert_sane(got)
    assert got == bounds


def test_boundaries_from_proportional_fallback_are_sane(tmp_path):
    """No whisper alignment available: timings must still be usable."""
    with _piper_stub(align=[], span=(0.05, 3.05), seconds=3.2):
        with mock.patch(
            "shorts_clipper.audio.tts._synthesize_edge", side_effect=AssertionError("edge used")
        ):
            path, got = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert path is not None
    _assert_sane(got)
    assert len(got) == len(LINE.split())
    assert got[0][1] >= 0.05
    assert got[-1][2] <= 3.2


def test_boundaries_sane_when_edge_library_missing(tmp_path):
    """edge-tts CLI fallback with no boundary events still yields timings."""
    def _fake_edge(text, out_path, voice=None, rate="+8%", pitch=None):
        _write_wav(Path(out_path), seconds=2.0)
        return Path(out_path)

    with mock.patch(
        "shorts_clipper.audio.tts._edge_tts_library_available", return_value=False
    ):
        with mock.patch(
            "shorts_clipper.audio.tts._synthesize_edge", side_effect=_fake_edge
        ):
            with mock.patch(
                "shorts_clipper.audio.tts.speech_window", return_value=(0.0, 2.0)
            ):
                path, got = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert path is not None
    _assert_sane(got)
    assert len(got) == len(LINE.split())


def test_edge_word_events_are_normalised(tmp_path, monkeypatch):
    """Real edge-tts ticks come through untouched when they are already sane."""
    monkeypatch.setenv("SHORTS_VO_ENGINE", "edge")
    raw = [("Пятнадцать", 0.05, 0.51), ("тысяч", 0.51, 0.88), ("в", 0.9, 0.97)]
    with mock.patch(
        "shorts_clipper.audio.tts._edge_tts_library_available", return_value=True
    ):
        with mock.patch(
            "shorts_clipper.audio.tts._synthesize_audio_bytes",
            return_value=(b"fake-mp3", raw),
        ):
            with mock.patch(
                "shorts_clipper.audio.tts.transcode_to_wav", return_value=True
            ):
                with mock.patch(
                    "shorts_clipper.audio.tts._tts_wav_duration", return_value=4.0
                ):
                    path, got = synthesize_voiceover_boundaries(LINE, tmp_path / "vo.wav")
    assert path is not None
    _assert_sane(got)
    assert got == [("Пятнадцать", 0.05, 0.51), ("тысяч", 0.51, 0.88), ("в", 0.9, 0.97)]


def test_boundaries_repair_overlaps_and_inversions():
    got = normalize_word_boundaries(
        [
            ("second", 0.0, 0.5),
            ("first", 0.0, 0.5),
            ("zero", 1.0, 1.0),
            ("negative", -0.5, -0.1),
            ("late", 5.0, 4.0),
        ]
    )
    _assert_sane(got)
    # Sorted by start (ties keep input order), negatives clamped to 0,
    # zero/inverted spans widened.
    assert [w for w, _, _ in got] == ["negative", "second", "first", "zero", "late"]
    assert got[0][1] == 0.0
    by_word = {w: (s, e) for w, s, e in got}
    assert by_word["zero"][1] > by_word["zero"][0]
    assert by_word["late"][1] > by_word["late"][0]


def test_boundaries_clamped_to_audio_duration():
    got = normalize_word_boundaries([("a", 0.0, 99.0)], duration=2.0)
    assert got == [("a", 0.0, 2.0)]


def test_boundaries_drop_blank_and_malformed_entries():
    assert normalize_word_boundaries([]) == []
    assert normalize_word_boundaries([("  ", 0.0, 1.0)]) == []
    assert normalize_word_boundaries([("x", "bad", 1.0)]) == []
    assert normalize_word_boundaries([("x", 0.0)]) == []


def test_finalize_without_audio_returns_empty(tmp_path):
    assert finalize_word_boundaries(LINE, [], None) == []
    assert finalize_word_boundaries(LINE, [], tmp_path / "missing.wav") == []


def test_finalize_uses_proportional_when_boundaries_missing(tmp_path):
    wav = _write_wav(tmp_path / "vo.wav", seconds=2.0)
    with mock.patch("shorts_clipper.audio.tts.speech_window", return_value=(0.0, 2.0)):
        got = finalize_word_boundaries(LINE, [], wav)
    _assert_sane(got)
    assert got[-1][2] <= 2.0


def test_proportional_boundaries_are_monotonic_and_span_the_window():
    got = piper_tts.proportional_word_boundaries(LINE, 0.2, 4.2)
    _assert_sane(got)
    assert got[0][1] == pytest.approx(0.2)
    assert got[-1][2] == pytest.approx(4.2)
    # Longer words must get longer slices.
    by_word = {w: (e - s) for w, s, e in got}
    assert by_word["Пятнадцать"] > by_word["в"]


def test_proportional_boundaries_empty_text():
    assert piper_tts.proportional_word_boundaries("   ") == []


def test_alignment_keeps_source_words_not_whisper_tokens():
    """Whisper supplies TIMINGS only; the on-screen text is the script.

    Captured from a real ru_RU-denis-medium render: whisper returned
    ``... пользуются по | в поменутье.`` where the script said
    ``... пользуются | по минуте.``. Same word count, garbled tail — emitting
    the transcript would burn the mistake onto the screen.
    """
    source = "Пятнадцать тысяч в месяц уходят на сервисы, которыми пользуются по минуте.".split()
    aligned = [
        ("Пятнадцать", 0.000, 0.520),
        ("тысяч", 0.520, 1.020),
        ("в", 1.020, 1.200),
        ("месяц", 1.200, 1.420),
        ("уходят", 1.420, 1.860),
        ("на", 1.860, 2.000),
        ("сервисы,", 2.000, 2.560),
        ("которыми", 2.600, 2.960),
        ("пользуются", 2.960, 3.620),
        ("по", 3.620, 3.780),
        ("в", 3.780, 3.800),
        ("поменутье.", 3.800, 4.240),
    ]
    matched = piper_tts._pair_with_source_words(aligned, source)
    assert [w for w, _s, _e in matched] == source
    assert matched[-1][2] == pytest.approx(4.240)


def test_pairing_maps_onto_source_words_when_counts_differ():
    """Whisper merged two words: source words still get a real span each."""
    source = "Пятнадцать тысяч в месяц".split()
    aligned = [("Пятнадцать", 0.0, 0.5), ("тысяч", 0.5, 0.9), ("вмесяц", 0.9, 1.4)]
    got = piper_tts._pair_with_source_words(aligned, source)
    assert [w for w, _s, _e in got] == source
    _assert_sane(got)
    # "в месяц" both live inside the single "вмесяц" span.
    spans = {w: (s, e) for w, s, e in got}
    assert spans["в"] == pytest.approx((0.9, 1.4))
    assert spans["месяц"] == pytest.approx((0.9, 1.4))
    assert got[0][1] == 0.0


def test_pairing_handles_empty_inputs():
    assert piper_tts._pair_with_source_words([], ["a"]) == []
    assert piper_tts._pair_with_source_words([("a", 0.0, 1.0)], []) == []


def test_char_spans_tile_the_unit_interval():
    spans = piper_tts._char_spans(["ab", "c", "de"])
    assert spans[0][0] == 0.0
    assert spans[-1][1] == pytest.approx(1.0)
    for (_lo, hi), (lo_next, _hi_next) in zip(spans, spans[1:], strict=False):
        assert hi == pytest.approx(lo_next)


def test_alignment_model_drops_the_en_suffix_for_other_languages(monkeypatch):
    """tiny.en cannot transcribe Russian; the multilingual sibling is used."""
    pytest.importorskip("faster_whisper")
    monkeypatch.setenv("SHORTS_WHISPER_MODEL", "tiny.en")
    captured = {}

    class _FakeModel:
        def __init__(self, name, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs

    with mock.patch(
        "faster_whisper.WhisperModel", _FakeModel, create=True
    ):
        piper_tts._align_model_cache.clear()
        piper_tts._alignment_model("ru")
        assert captured["name"] == "tiny"
        piper_tts._align_model_cache.clear()
        piper_tts._alignment_model("en")
        assert captured["name"] == "tiny.en"
    piper_tts._align_model_cache.clear()


def test_alignment_needs_no_boundary_metdata_from_piper():
    """Piper itself exposes none — timings are recovered, never assumed."""
    aligned = [("раз", 0.0, 0.4), ("два", 0.4, 0.8)]
    got = piper_tts._pair_with_source_words(aligned, "раз два".split())
    assert got == [("раз", 0.0, 0.4), ("два", 0.4, 0.8)]


def test_segments_from_boundaries_feed_the_caption_path(tmp_path):
    """The end-to-end contract: boundaries -> TranscriptSegments -> ASS file."""
    from shorts_clipper.captions.generator import generate_ass_file
    from shorts_clipper.pipeline.stock_runner import _segments_from_word_bounds

    bounds = [
        ("Пятнадцать", 0.05, 0.55),
        ("тысяч", 0.55, 0.85),
        ("в", 0.85, 0.92),
        ("месяц", 0.92, 1.40),
        ("уходят", 1.45, 1.95),
        ("на", 1.95, 2.05),
    ]
    segments = _segments_from_word_bounds(bounds, seg_shift=0.0)
    assert segments
    assert sum(len(s.words or []) for s in segments) == len(bounds)
    assert all(s.start < s.end for s in segments)

    ass_path = tmp_path / "subs.ass"
    generate_ass_file(segments, start_offset=0.0, output_path=ass_path)
    ass = ass_path.read_text(encoding="utf-8")
    assert "ПЯТНАДЦАТЬ" in ass.upper()
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert dialogues
    # The boundary timestamps must survive into the ASS dialogue rows.
    assert "0:00:00.92" in ass
    assert ass.index("0:00:00.00") < ass.index("0:00:00.92")


# --------------------------------------------------------------------------
# Model cache location
# --------------------------------------------------------------------------


def test_model_cache_lives_under_models_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_MODELS_DIR", str(tmp_path / "custom-models"))
    expected = tmp_path / "custom-models" / "piper"
    assert piper_tts.model_dir() == expected
    assert Settings.from_env().piper_model_dir == expected
    onnx, config = piper_tts.model_paths("ru_RU-denis-medium")
    assert onnx.parent == expected
    assert onnx.name == "ru_RU-denis-medium.onnx"
    assert config.name == "ru_RU-denis-medium.onnx.json"


def test_model_cache_is_not_in_the_repo_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_MODELS_DIR", "models")
    model_dir = piper_tts.model_dir()
    assert model_dir.name == "piper"
    assert model_dir.parent.name == "models"
    assert not model_dir.is_absolute()
    # "models" is relative to cwd, so the cache can only land in the models dir.
    assert model_dir == Path("models") / "piper"


def test_model_cache_dir_is_gitignored():
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "models/" in gitignore
    assert "*.onnx" in gitignore


def test_model_present_requires_both_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_MODELS_DIR", str(tmp_path / "m"))
    onnx, config = piper_tts.model_paths("ru_RU-denis-medium")
    assert not piper_tts.model_present("ru_RU-denis-medium")
    onnx.parent.mkdir(parents=True, exist_ok=True)
    onnx.write_bytes(b"weights")
    assert not piper_tts.model_present("ru_RU-denis-medium")
    config.write_text("{}")
    assert piper_tts.model_present("ru_RU-denis-medium")


def test_ensure_model_skips_download_when_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_MODELS_DIR", str(tmp_path / "m"))
    onnx, config = piper_tts.model_paths("ru_RU-denis-medium")
    onnx.parent.mkdir(parents=True, exist_ok=True)
    onnx.write_bytes(b"weights")
    config.write_text("{}")
    with mock.patch.object(piper_tts, "_download", side_effect=AssertionError("download")):
        assert piper_tts.ensure_model("ru_RU-denis-medium") is True


def test_ensure_model_download_failure_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("SHORTS_MODELS_DIR", str(tmp_path / "m"))
    with mock.patch.object(piper_tts, "_download", side_effect=OSError("offline")):
        assert piper_tts.ensure_model("ru_RU-denis-medium") is False
    assert not (tmp_path / "m" / "piper" / "ru_RU-denis-medium.onnx.part").exists()


def test_hot_path_makes_no_network_calls_when_cached(tmp_path, monkeypatch):
    """Once cached, synthesis must not touch the network."""
    monkeypatch.setenv("SHORTS_MODELS_DIR", str(tmp_path / "m"))
    onnx, config = piper_tts.model_paths("ru_RU-denis-medium")
    onnx.parent.mkdir(parents=True, exist_ok=True)
    onnx.write_bytes(b"weights")
    config.write_text("{}")
    with mock.patch.object(piper_tts, "_download", side_effect=AssertionError("network")):
        assert piper_tts.ensure_model() is True


def test_hf_relative_paths():
    assert (
        piper_tts._hf_relative_path("ru_RU-denis-medium", "")
        == "ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx"
    )
    assert (
        piper_tts._hf_relative_path("en_US-amy-medium", ".json")
        == "en/en_US/amy/medium/en_US-amy-medium.onnx.json"
    )
    with pytest.raises(ValueError):
        piper_tts._hf_relative_path("garbage", "")


# --------------------------------------------------------------------------
# rate -> length_scale mapping
# --------------------------------------------------------------------------


def test_rate_parsing():
    assert piper_tts.parse_rate_percent("+8%") == 8.0
    assert piper_tts.parse_rate_percent("-15%") == -15.0
    assert piper_tts.parse_rate_percent("+8") == 8.0
    assert piper_tts.parse_rate_percent("") == 0.0
    assert piper_tts.parse_rate_percent(None) == 0.0
    assert piper_tts.parse_rate_percent("fast") == 0.0
    assert piper_tts.parse_rate_percent("+999%") == piper_tts.RATE_PERCENT_MAX
    assert piper_tts.parse_rate_percent("-999%") == piper_tts.RATE_PERCENT_MIN


def test_length_scale_mapping_is_inverted():
    """edge "+12%" (faster) must give a length_scale BELOW 1.0."""
    assert piper_tts.rate_to_length_scale("+12%") == pytest.approx(1 / 1.12, rel=1e-6)
    assert piper_tts.rate_to_length_scale("+12%") < 1.0
    assert piper_tts.rate_to_length_scale("+0%") == pytest.approx(1.0)
    assert piper_tts.rate_to_length_scale("-25%") > 1.0
    assert piper_tts.rate_to_length_scale("") == pytest.approx(1.0)


def test_length_scale_is_monotonic():
    """Every faster request must produce a smaller length_scale."""
    rates = ["-50%", "-25%", "+0%", "+10%", "+25%", "+50%"]
    scales = [piper_tts.rate_to_length_scale(r) for r in rates]
    assert scales == sorted(scales, reverse=True)
    assert len(set(scales)) == len(scales)


def test_length_scale_is_clamped_against_chipmunks():
    for absurd in ("+200%", "+1000%", "+100%", "+30%"):
        scale = piper_tts.rate_to_length_scale(absurd)
        assert scale == pytest.approx(piper_tts.LENGTH_SCALE_MIN)
        # At most ~1.3x faster, never the 3x edge-tts would have asked for.
        assert scale >= 2.0 / 3.0
    for slow in ("-200%", "-100%", "-1000%", "-30%"):
        scale = piper_tts.rate_to_length_scale(slow)
        assert scale == pytest.approx(piper_tts.LENGTH_SCALE_MAX)
        assert scale <= 1.5
    assert piper_tts.LENGTH_SCALE_MIN == pytest.approx(1 / 1.3)
    assert piper_tts.LENGTH_SCALE_MAX == pytest.approx(1 / 0.7)


def test_length_scale_bounds_hold_for_every_rate():
    for pct in range(-400, 401, 7):
        scale = piper_tts.rate_to_length_scale(f"{pct:+d}%")
        assert piper_tts.LENGTH_SCALE_MIN - 1e-9 <= scale <= piper_tts.LENGTH_SCALE_MAX + 1e-9


def test_render_inputs_are_reproducible():
    """Everything feeding a render is a pure function of text + settings.

    Piper's samples themselves are not bit-reproducible (VITS samples its
    duration predictor / noise and onnxruntime exposes no seed hook), so the
    guarantee is on the inputs and on the timing recovery, not on the PCM.
    """
    assert [piper_tts.rate_to_length_scale(r) for r in ("-30%", "+0%", "+30%")] == [
        piper_tts.rate_to_length_scale(r) for r in ("-30%", "+0%", "+30%")
    ]
    assert piper_tts.resolve_model_name(LINE) == piper_tts.resolve_model_name(LINE)
    raw = [("a", 0.2, 0.5), ("b", 0.1, 0.3), ("c", 0.5, 0.5)]
    once = normalize_word_boundaries(raw, duration=2.0)
    twice = normalize_word_boundaries(raw, duration=2.0)
    assert once == twice
    # Normalisation is idempotent: re-running it changes nothing.
    assert normalize_word_boundaries(once, duration=2.0) == once


def test_piper_receives_the_mapped_length_scale(tmp_path):
    voice = _FakePiperVoice()
    with _piper_stub(voice=voice):
        path, bounds = piper_tts.synthesize(
            LINE, tmp_path / "vo.wav", rate="+25%", pitch="+3Hz"
        )
    assert path is not None
    config = _FakeSynthesisConfig.created[-1]
    # edge "+25%" (1.25x faster) -> length_scale 1/1.25, i.e. SHORTER audio.
    assert config.length_scale == pytest.approx(1 / 1.25)
    # VITS has no pitch control: accepted, never fatal.
    assert voice.calls and voice.calls[0][0] == LINE
    assert bounds


def test_real_synthesis_config_pins_noise_defaults():
    """Noise levels are pinned so output never shifts with a model config."""
    pytest.importorskip("piper")
    config = piper_tts._synthesis_config(0.8)
    assert config.length_scale == pytest.approx(0.8)
    assert config.noise_scale == pytest.approx(piper_tts.PIPER_NOISE_SCALE)
    assert config.noise_w_scale == pytest.approx(piper_tts.PIPER_NOISE_W_SCALE)


# --------------------------------------------------------------------------
# Lazy import guarantee
# --------------------------------------------------------------------------


def test_importing_tts_does_not_import_piper():
    """A machine without piper must be able to import the whole package."""
    import os
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys;"
        "import shorts_clipper.audio.tts as t;"
        "import shorts_clipper.audio.piper_tts as p;"
        "assert 'piper' not in sys.modules, 'piper imported eagerly';"
        "assert p.DEFAULT_PIPER_MODEL;"
        "print('ok')"
    )
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_piper_installed_is_a_probe_not_an_import():
    with mock.patch.object(importlib.util, "find_spec", return_value=None):
        assert piper_tts.piper_installed() is False


def test_piper_synthesize_reports_missing_package(tmp_path, caplog):
    with mock.patch.object(piper_tts, "piper_installed", return_value=False):
        with caplog.at_level(logging.WARNING):
            assert piper_tts.synthesize(LINE, tmp_path / "vo.wav") == (None, [])
    assert any("not installed" in r.message for r in caplog.records)