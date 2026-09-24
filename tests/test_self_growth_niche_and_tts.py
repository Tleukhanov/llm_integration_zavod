import unittest

from shorts_clipper.audio.tts import (
    VOICE_BY_LANG,
    _detect_language,
    build_voiceover_text,
    pick_voice,
)
from shorts_clipper.scout.keywords import NICHE_KEYWORDS, build_queries, get_keywords


class SelfGrowthKeywordsTests(unittest.TestCase):
    def test_self_growth_niche_present(self):
        self.assertIn("self-growth", NICHE_KEYWORDS)
        self.assertIn("philosophy", NICHE_KEYWORDS)

    def test_get_keywords_includes_russian_self_growth(self):
        kws = get_keywords("self-growth")
        self.assertIn("стоицизм", kws)
        self.assertIn("внутренний голос", kws)
        self.assertIn("stoicism", kws)

    def test_philosophy_keywords_include_stoics(self):
        kws = get_keywords("philosophy")
        self.assertIn("marcus aurelius", kws)
        self.assertIn("seneca", kws)

    def test_build_queries_for_self_growth(self):
        queries = build_queries("self-growth", keyword=None, count=4)
        self.assertGreaterEqual(len(queries), 4)
        for q in queries:
            self.assertTrue(q.startswith("ytsearch15:"))


class VoiceLanguageTests(unittest.TestCase):
    def test_detect_russian_text(self):
        self.assertEqual(_detect_language("Стоицизм учит принимать то, что не зависит от нас"), "ru")

    def test_detect_english_text(self):
        self.assertEqual(_detect_language("Wisdom teaches us to accept what we cannot change"), "en")

    def test_pick_voice_auto_ru(self):
        self.assertEqual(pick_voice("Дисциплина освобождает разум", configured=None), "ru-RU-DmitryNeural")

    def test_pick_voice_auto_en(self):
        self.assertEqual(pick_voice("Discipline frees the mind", configured=None), "en-US-GuyNeural")

    def test_explicit_voice_wins(self):
        self.assertEqual(
            pick_voice("Любой текст", configured="en-US-ChristopherNeural"),
            "en-US-ChristopherNeural",
        )

    def test_voice_map_contains_ru(self):
        self.assertIn("ru", VOICE_BY_LANG)

    def test_build_voiceover_text_truncates(self):
        segs = [{"text": "word " * 60, "start": 0.0, "end": 5.0}]
        text = build_voiceover_text(segs, clip_duration=5.0, max_chars=220)
        self.assertIsNotNone(text)
        self.assertLessEqual(len(text), 221)


if __name__ == "__main__":
    unittest.main()