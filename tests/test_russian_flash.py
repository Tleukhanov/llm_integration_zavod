import re
import unicodedata
import unittest

from shorts_clipper.visual import stock as stock_visual

# The rendered short this contract was written against: a price-objection script
# whose payoff used to be covered by the stock phrase "НЕ ОСТАНОВЛЯЙСЯ".
MONEY_SCRIPT = (
    "Из ста клиентов двадцать говорят «дорого». Кажется, что виновата цена. "
    "Чаще всего не виновата: за два часа до звонка не задан ни один вопрос. "
    "Дорого звучит не цена. Дорого звучит непонятно."
)


def _spoken_candidates(script: str) -> set[str]:
    """Every uppercased sentence of *script* as the flash could render it."""
    parts = re.split(r"(?<=[.!?…])\s+|[\r\n]+", script)
    return {
        re.sub(r"\s+", " ", part).strip(" \t«»\"'“”„‘’—–-,;:.").upper()
        for part in parts
        if part.strip()
    }


class RussianFlashTests(unittest.TestCase):
    def test_flash_phrases_are_cyrillic(self):
        flashes = stock_visual.edit_flash_schedule(9.0)
        self.assertTrue(flashes)
        for flash in flashes:
            letters = [ch for ch in flash["text"] if ch.isalpha()]
            self.assertTrue(letters)
            for ch in letters:
                name = unicodedata.name(ch, "")
                self.assertIn("CYRILLIC", name)
                self.assertNotIn("LATIN", name)
            self.assertNotRegex(flash["text"], re.compile(r"[A-Za-z]"))

    def test_flash_text_comes_from_the_script(self):
        """Pins the NEW contract, replacing the old "static bank has 4 entries".

        The flash is the short's own payoff line, not a stock battle cry, so
        nothing on screen may be an unrelated canned phrase.
        """
        spoken = _spoken_candidates(MONEY_SCRIPT)
        flashes = stock_visual.edit_flash_schedule(9.0, script=MONEY_SCRIPT, seed=7)
        self.assertTrue(flashes)
        for flash in flashes:
            self.assertIn(flash["text"], spoken)
            self.assertNotIn(flash["text"], stock_visual._EDIT_FLASH_PHRASES)

    def test_flash_slot_count_unchanged(self):
        """The cadence work (bars=2) already landed — this only guards slots."""
        self.assertEqual(len(stock_visual.edit_flash_schedule(9.0, script=MONEY_SCRIPT)), 4)
        self.assertEqual(len(stock_visual.edit_flash_schedule(30.0, bars=2, script=MONEY_SCRIPT)), 8)

    def test_flash_schedule_timing_unchanged(self):
        flashes = stock_visual.edit_flash_schedule(9.0)
        bar = 4 * stock_visual.beat_seconds(132)
        self.assertTrue(flashes)
        for flash in flashes:
            self.assertAlmostEqual(
                flash["start"], round(flash["start"] / bar) * bar, places=3
            )
            self.assertLess(flash["start"], flash["end"])
            self.assertLessEqual(flash["end"], 9.0)

    def test_flash_schedule_deterministic(self):
        self.assertEqual(
            stock_visual.edit_flash_schedule(9.0, seed=2),
            stock_visual.edit_flash_schedule(9.0, seed=2),
        )


class ScriptFlashSelectionTests(unittest.TestCase):
    """The flash picks the strongest short line out of the script itself."""

    def test_payoff_line_wins_over_the_setup(self):
        self.assertEqual(
            stock_visual.flash_phrases_from_script(MONEY_SCRIPT)[0],
            "ДОРОГО ЗВУЧИТ НЕПОНЯТНО",
        )

    def test_only_flash_sized_sentences_are_offered(self):
        phrases = stock_visual.flash_phrases_from_script(MONEY_SCRIPT)
        self.assertTrue(phrases)
        for phrase in phrases:
            words = phrase.split()
            self.assertGreaterEqual(len(words), stock_visual._EDIT_FLASH_MIN_WORDS)
            self.assertLessEqual(len(words), stock_visual._EDIT_FLASH_MAX_WORDS)
            self.assertLessEqual(len(phrase), stock_visual._EDIT_FLASH_MAX_CHARS)
        self.assertNotIn("ИЗ СТА КЛИЕНТОВ ДВАДЦАТЬ ГОВОРЯТ ДОРОГО", phrases)

    def test_pool_is_capped_and_deduplicated(self):
        script = "Раз. Два. Три. Четыре. Пять. Шесть. Семь. Восемь."
        phrases = stock_visual.flash_phrases_from_script(script)
        self.assertLessEqual(len(phrases), stock_visual._EDIT_FLASH_POOL_MAX)
        self.assertEqual(len(phrases), len(set(phrases)))

    def test_distinct_slots_get_distinct_script_lines(self):
        flashes = stock_visual.edit_flash_schedule(30.0, bars=2, script=MONEY_SCRIPT, seed=1)
        texts = [f["text"] for f in flashes]
        self.assertGreater(len(set(texts)), 1)
        for prev, cur in zip(texts, texts[1:], strict=False):
            self.assertNotEqual(prev, cur)

    def test_no_phrase_repeats_inside_a_three_slot_window(self):
        """The reported bug was the same line 7s (2 slots) apart."""
        pool = stock_visual.flash_phrases_from_script(MONEY_SCRIPT)
        texts = [
            f["text"]
            for f in stock_visual.edit_flash_schedule(60.0, bars=2, script=MONEY_SCRIPT)
        ]
        window = min(len(pool), 3)
        self.assertGreaterEqual(len(texts), 2 * len(pool))
        for i in range(len(texts)):
            for j in range(i + 1, min(i + window, len(texts))):
                self.assertNotEqual(texts[i], texts[j], msg=f"slots {i} and {j} repeat")

    def test_every_pool_line_gets_used_on_a_long_clip(self):
        pool = stock_visual.flash_phrases_from_script(MONEY_SCRIPT)
        texts = [
            f["text"]
            for f in stock_visual.edit_flash_schedule(60.0, bars=2, script=MONEY_SCRIPT)
        ]
        self.assertEqual(set(texts), set(pool))

    def test_script_flash_is_deterministic(self):
        self.assertEqual(
            stock_visual.edit_flash_schedule(30.0, bars=2, script=MONEY_SCRIPT, seed=5),
            stock_visual.edit_flash_schedule(30.0, bars=2, script=MONEY_SCRIPT, seed=5),
        )

    def test_latin_script_is_quoted_not_translated(self):
        script = "Your price is fine. Confusion is what sounds expensive. Clarity closes it."
        flashes = stock_visual.edit_flash_schedule(30.0, bars=2, script=script, seed=2)
        self.assertTrue(flashes)
        spoken = _spoken_candidates(script)
        for flash in flashes:
            self.assertIn(flash["text"], spoken)


class ScriptFlashFallbackTests(unittest.TestCase):
    """Precedence: explicit pool > script's own lines > static bank > nothing.

    The static bank is Russian, so it is only reachable when no script was
    supplied at all. A script that yields no flash-sized line gets NO flash
    rather than an unrelated Russian battle-cry burned over it -- which is the
    exact defect this replaced ("НЕ ОСТАНОВЛЯЙСЯ" over a sales script).
    """

    def test_oversized_script_gets_no_flash_not_the_static_bank(self):
        script = " ".join(f"слово{i}" for i in range(40))
        self.assertEqual(stock_visual.flash_phrases_from_script(script), [])
        self.assertEqual(stock_visual.edit_flash_schedule(9.0, script=script), [])

    def test_no_script_at_all_falls_back_to_the_static_bank(self):
        flashes = stock_visual.edit_flash_schedule(9.0, script=None)
        self.assertTrue(flashes)
        for flash in flashes:
            self.assertIn(flash["text"], stock_visual._EDIT_FLASH_PHRASES)

    def test_non_cyrillic_script_never_gets_russian_static_phrases(self):
        """Regression guard for the reported defect, in the English case."""
        script = (
            "Most SaaS teams lose their best deal in the last four minutes of the call "
            "because nobody asked the obvious question before the demo started."
        )
        self.assertEqual(stock_visual.flash_phrases_from_script(script), [])
        flashes = stock_visual.edit_flash_schedule(9.0, script=script)
        self.assertEqual(flashes, [])
        for flash in flashes:
            self.assertNotIn(flash["text"], stock_visual._EDIT_FLASH_PHRASES)

    def test_unusable_script_does_not_crash(self):
        """Nothing flash-sized is a valid outcome for degenerate input."""
        for script in ("", "   ", "\n\n", "— — —", "04:11"):
            with self.subTest(script=script):
                self.assertEqual(stock_visual.flash_phrases_from_script(script), [])
                self.assertEqual(stock_visual.edit_flash_schedule(9.0, script=script), [])

    def test_explicit_phrases_still_win_over_the_script(self):
        flashes = stock_visual.edit_flash_schedule(
            6.0, phrases=["ONE", "TWO"], script=MONEY_SCRIPT, seed=1
        )
        self.assertTrue(flashes)
        self.assertTrue(all(f["text"] in {"ONE", "TWO"} for f in flashes))


if __name__ == "__main__":
    unittest.main()
