"""Tests for RU+EN multilingual support: Unicode word counting, bilingual
trigger vocabularies, and whisper language settings."""

import re

from shorts_clipper.captions.generator import EMOTIONAL_TRIGGERS
from shorts_clipper.core.settings import Settings
from shorts_clipper.highlight_detection.scoring import (
    EMOTION_WORDS,
    HOOK_PATTERNS,
    VIRAL_WORDS,
)

UNICODE_WORD_RE = re.compile(r"[\w']+")

RUSSIAN_SENTENCE = "Смотри, как невероятно это работает, стоп!"
ENGLISH_SENTENCE = "Look how amazing this works!"


def test_unicode_word_regex_counts_russian_sentence():
    words = UNICODE_WORD_RE.findall(RUSSIAN_SENTENCE.lower())
    assert words == ["смотри", "как", "невероятно", "это", "работает", "стоп"]
    assert len(words) == 6


def test_unicode_word_regex_still_matches_english():
    words = UNICODE_WORD_RE.findall(ENGLISH_SENTENCE.lower())
    assert words == ["look", "how", "amazing", "this", "works"]
    assert len(words) == 5


def test_trigger_lists_include_russian_words():
    assert any(p for p in HOOK_PATTERNS if re.search(r"[а-яё]", p))
    assert any(w for w in EMOTION_WORDS if re.search(r"[а-яё]", w))
    assert any(w for w in VIRAL_WORDS if re.search(r"[а-яё]", w))
    assert any(w for w in EMOTIONAL_TRIGGERS if re.search(r"[А-ЯЁ]", w))


def test_settings_from_env_picks_up_whisper_language(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SHORTS_WHISPER_LANGUAGE=ru\n", encoding="utf-8")
    monkeypatch.delenv("SHORTS_WHISPER_LANGUAGE", raising=False)
    settings = Settings.from_env(env_file)
    assert settings.whisper_language == "ru"


def test_settings_whisper_language_defaults_to_none(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("SHORTS_WHISPER_LANGUAGE", raising=False)
    settings = Settings.from_env(env_file)
    assert settings.whisper_language is None
