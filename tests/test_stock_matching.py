"""Tests for semantic script↔visual matching and within-run anti-repeat.

Covers the three pieces that replaced the old bare ``random.choice``:
tag inference for background clips (``shorts_clipper.visual.stock_tags``),
tag-based pool filtering (``shorts_clipper.visual.stock``), and the
theme-aware repeat guard (``shorts_clipper.pipeline.stock_dedup``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from shorts_clipper.pipeline import stock_runner
from shorts_clipper.pipeline.stock_dedup import (
    RecentScripts,
    choose_fresh,
    choose_unused,
    load_used,
    script_hash,
    theme_hash,
    theme_similarity,
    theme_tokens,
)
from shorts_clipper.visual import stock as stock_visual
from shorts_clipper.visual import stock_tags


class VisualTagVocabularyTests(unittest.TestCase):
    """Verify the curated tag vocabulary stays coherent."""

    def test_every_script_tag_exists_in_visual_vocabulary(self):
        """A script tag with no clip counterpart could never match anything."""
        self.assertEqual(
            sorted(set(stock_tags.SCRIPT_TAG_KEYWORDS) - set(stock_tags.VISUAL_TAG_TOKENS)), []
        )

    def test_every_visual_tag_has_script_keywords(self):
        """A clip tag with no script keywords could never match anything."""
        self.assertEqual(
            sorted(set(stock_tags.VISUAL_TAG_TOKENS) - set(stock_tags.SCRIPT_TAG_KEYWORDS)), []
        )

    def test_tags_are_lowercase_snake_case(self):
        for tag in stock_tags.visual_tag_names():
            self.assertEqual(tag, tag.strip().lower().replace(" ", "_"), tag)

    def test_lexicon_tokens_are_unique(self):
        for table in (stock_tags.VISUAL_TAG_TOKENS, stock_tags.SCRIPT_TAG_KEYWORDS):
            for tag, entries in table.items():
                lowered = [e.lower() for e in entries]
                self.assertEqual(len(lowered), len(set(lowered)), f"{tag}: duplicate keyword")


class BackgroundTagTests(unittest.TestCase):
    """Verify filename and sidecar tag inference."""

    def test_filename_tokens_map_to_tags(self):
        cases = {
            "city_night_rain.mp4": {"city", "city_night", "night", "rain"},
            "forest_morning.mp4": {"forest", "morning"},
            "ocean_waves.mp4": {"ocean", "water"},
            "highway_car.mp4": {"highway", "car"},
            "office_smoke.mp4": {"office", "smoke"},
            "sunrise_alps.mp4": {"sunrise"},
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertTrue(expected.issubset(stock_tags.background_tags(name)), name)

    def test_hyphenated_and_spaced_names(self):
        for name in ("city-night.mp4", "city night.mp4", "CITY_NIGHT.mp4"):
            with self.subTest(name=name):
                self.assertIn("city_night", stock_tags.background_tags(name))

    def test_untagged_clip_has_no_tags(self):
        self.assertEqual(stock_tags.background_tags("clip_01.mp4"), frozenset())
        self.assertEqual(stock_tags.background_tags("a7f3c2.mp4"), frozenset())

    def test_pexels_cache_folder_supplies_semantics(self):
        """``00.mp4`` is meaningless; the query folder it was fetched for is not."""
        tags = stock_tags.background_tags("data/stock/_pexels_cache/morning-sunrise-forest-calm/00.mp4")
        self.assertEqual(tags, frozenset({"morning", "sunrise", "forest", "calm"}))

    def test_parent_folder_is_always_part_of_the_token_source(self):
        """The folder is read too — that is how Pexels query slugs get through."""
        self.assertEqual(stock_tags.background_tags("data/stock/self-growth/00.mp4"), frozenset())
        self.assertEqual(stock_tags.background_tags("data/stock/money/00.mp4"), frozenset({"money"}))

    def test_tags_txt_sidecar_overrides_filename(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "generic_01.mp4"
            clip.write_bytes(b"x")
            (Path(d) / "generic_01.tags.txt").write_text(
                "# explicit\ncity_night\nrain, smoke\n", encoding="utf-8"
            )
            self.assertEqual(
                stock_tags.background_tags(clip), frozenset({"city_night", "rain", "smoke"})
            )

    def test_json_sidecar_list_and_string(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "a.mp4"
            clip.write_bytes(b"x")
            (Path(d) / "a.json").write_text('{"tags": ["ocean", "Sunset"]}', encoding="utf-8")
            self.assertEqual(stock_tags.background_tags(clip), frozenset({"ocean", "sunset"}))
            (Path(d) / "a.json").write_text('{"tags": "forest, rain"}', encoding="utf-8")
            self.assertEqual(stock_tags.background_tags(clip), frozenset({"forest", "rain"}))
            (Path(d) / "a.json").write_text('["city night"]', encoding="utf-8")
            self.assertEqual(stock_tags.background_tags(clip), frozenset({"city_night"}))

    def test_corrupt_sidecar_is_ignored_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "forest.mp4"
            clip.write_bytes(b"x")
            (Path(d) / "forest.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(stock_tags.background_tags(clip), frozenset({"forest"}))

    def test_empty_sidecar_falls_back_to_filename(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "ocean.mp4"
            clip.write_bytes(b"x")
            (Path(d) / "ocean.json").write_text('{"tags": []}', encoding="utf-8")
            self.assertEqual(stock_tags.background_tags(clip), frozenset({"ocean"}))

    def test_unknown_sidecar_tags_are_kept_verbatim(self):
        """An operator tag is trusted even when it is outside the vocabulary."""
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "x.mp4"
            clip.write_bytes(b"x")
            (Path(d) / "x.tags.txt").write_text("my_own_tag\n", encoding="utf-8")
            self.assertEqual(stock_tags.background_tags(clip), frozenset({"my_own_tag"}))

    def test_pool_tags_use_the_primary_clip_only(self):
        """The clip that opens the short sets the topic of the whole montage."""
        clips = [Path("a/rain.mp4"), Path("b/desert.mp4")]
        self.assertEqual(stock_tags.background_pool_tags(clips), frozenset({"rain"}))
        self.assertEqual(stock_tags.background_pool_tags([]), frozenset())
        self.assertEqual(stock_tags.background_pool_tags(None), frozenset())


class ScriptTagTests(unittest.TestCase):
    """Verify keyword tagging of script text."""

    def test_keyword_stems_match_inflections(self):
        self.assertIn("morning", stock_tags.script_tags("Тело встало утром и взяло будильник."))
        self.assertIn("night", stock_tags.script_tags("В 23:00 было выбрано «ещё чуть-чуть»."))
        self.assertIn("gym", stock_tags.script_tags("Пять утра. Двенадцать подтягиваний."))
        self.assertIn("couple", stock_tags.script_tags("Он скучает по себе тому, каким был до тебя."))

    def test_unrelated_text_has_no_tags(self):
        self.assertEqual(stock_tags.script_tags("абракадабра"), frozenset())
        self.assertEqual(stock_tags.script_tags(""), frozenset())

    def test_generic_word_is_not_a_false_positive(self):
        """"любой" must not read as the couple tag."""
        self.assertNotIn("couple", stock_tags.script_tags("Любой другой вариант тоже подходит."))

    def test_mountain_does_not_swallow_city(self):
        self.assertNotIn("mountain", stock_tags.script_tags("Городские улицы пусты к утру."))
        self.assertIn("city", stock_tags.script_tags("Городские улицы пусты к утру."))

    def test_filter_keeps_only_matching_lines(self):
        pool = ["Пять утра. Двенадцать подтягиваний.", "Зарплата шестьдесят тысяч."]
        matched, fell_back = stock_tags.filter_scripts_by_tags(pool, frozenset({"morning"}))
        self.assertEqual(matched, ["Пять утра. Двенадцать подтягиваний."])
        self.assertFalse(fell_back)

    def test_filter_reports_full_pool_fallback(self):
        pool = ["Зарплата шестьдесят тысяч."]
        matched, fell_back = stock_tags.filter_scripts_by_tags(pool, frozenset({"forest"}))
        self.assertEqual(matched, pool)
        self.assertTrue(fell_back)

    def test_filter_without_tags_returns_pool_untouched(self):
        pool = ["a", "b"]
        matched, fell_back = stock_tags.filter_scripts_by_tags(pool, frozenset())
        self.assertEqual(matched, pool)
        self.assertFalse(fell_back)


class LoadStockScriptMatchingTests(unittest.TestCase):
    """Verify the public script loader honours the visual."""

    def _pool(self, directory: Path) -> Path:
        path = directory / "pool.txt"
        path.write_text(
            "\n".join(
                [
                    "Пять утра. Двенадцать подтягиваний.",
                    "Зарплата шестьдесят тысяч.",
                    "Телефон убран — и через пятнадцать минут выясняется.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_background_narrows_the_pool(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool(tmp)
            for seed in range(30):
                picked = stock_visual.load_stock_script(
                    pool, seed, background="forest_morning.mp4"
                )
                self.assertEqual(picked, "Пять утра. Двенадцать подтягиваний.")

    def test_explicit_tags_and_background_path_agree(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool(tmp)
            by_path = stock_visual.load_stock_script(pool, 5, background="office_money.mp4")
            by_tags = stock_visual.load_stock_script(
                pool, 5, background_tags=stock_tags.background_tags("office_money.mp4")
            )
            self.assertEqual(by_path, by_tags)
            self.assertEqual(by_path, "Зарплата шестьдесят тысяч.")

    def test_no_visual_keeps_the_whole_pool(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool(tmp)
            for seed in range(30):
                picked = stock_visual.load_stock_script(pool, seed)
                self.assertIn(
                    picked,
                    [
                        "Пять утра. Двенадцать подтягиваний.",
                        "Зарплата шестьдесят тысяч.",
                        "Телефон убран — и через пятнадцать минут выясняется.",
                    ],
                )

    def test_deterministic_for_a_given_seed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool(tmp)
            first = [stock_visual.load_stock_script(pool, s, background="night_city.mp4") for s in range(10)]
            second = [stock_visual.load_stock_script(pool, s, background="night_city.mp4") for s in range(10)]
            self.assertEqual(first, second)

    def test_script_path_precedence_survives_matching(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "niches"
            (tmp / "money").mkdir(parents=True)
            (tmp / "money" / "scripts.txt").write_text("Зарплата из профиля.\n", encoding="utf-8")
            explicit = Path(d) / "explicit.txt"
            explicit.write_text("Зарплата из явного пути.\n", encoding="utf-8")
            self.assertEqual(
                stock_visual.load_stock_script(
                    explicit, seed=0, niche="money", niche_dir=tmp, background="office.mp4"
                ),
                "Зарплата из явного пути.",
            )
            self.assertEqual(
                stock_visual.load_stock_script(
                    None, seed=0, niche="money", niche_dir=tmp, background="office_money.mp4"
                ),
                "Зарплата из профиля.",
            )

    def test_builtin_bank_still_used_and_filterable(self):
        with tempfile.TemporaryDirectory() as d:
            for seed in range(10):
                picked = stock_visual.load_stock_script(
                    None, seed, niche="no-such-niche", niche_dir=d, background="forest_morning.mp4"
                )
                self.assertIn(picked, stock_visual._DEFAULT_SCRIPTS)
                self.assertIn("morning", stock_tags.script_tags(picked))

    def test_falls_back_to_full_pool_when_nothing_matches(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool(tmp)
            for seed in range(20):
                picked = stock_visual.load_stock_script(pool, seed, background="ocean.mp4")
                self.assertIn(
                    picked,
                    [
                        "Пять утра. Двенадцать подтягиваний.",
                        "Зарплата шестьдесят тысяч.",
                        "Телефон убран — и через пятнадцать минут выясняется.",
                    ],
                )


class ThemeFingerprintTests(unittest.TestCase):
    """Verify near-duplicate detection between differently worded quotes."""

    def test_tokens_drop_function_words_and_digits(self):
        self.assertEqual(theme_tokens("Я не знаю, что делать в 23:00"), frozenset({"знаю", "делать"}))

    def test_tokens_stem_inflections(self):
        self.assertEqual(
            theme_tokens("Погода сегодня дождливая."),
            theme_tokens("Погода сегодня дождливое."),
        )

    def test_theme_hash_is_stable_and_discriminating(self):
        self.assertEqual(theme_hash("Пять утра."), theme_hash("  пять УТРА "))
        self.assertNotEqual(theme_hash("Пять утра."), theme_hash("Пять вечера."))

    def test_similarity_calibration(self):
        base = "Пять утра. Двенадцать подтягиваний, пот на ладонях."
        rewritten = "Пять утра. Тринадцать подтягиваний, пот на ладонях."
        nested = base + " Завтра будет тринадцать."
        unrelated = "Зарплата шестьдесят тысяч. Пять с половиной уходит на комнату."
        self.assertGreaterEqual(theme_similarity(base, rewritten), 0.3)
        self.assertGreater(theme_similarity(base, rewritten), theme_similarity(base, unrelated))
        self.assertGreater(theme_similarity(base, nested), theme_similarity(base, unrelated))
        self.assertLess(theme_similarity(base, unrelated), 0.15)
        self.assertEqual(theme_similarity(base, "Зарплата шестьдесят тысяч."), 0.0)

    def test_similarity_of_empty_text_is_zero(self):
        self.assertEqual(theme_similarity("", "Пять утра."), 0.0)


class RecentScriptsTests(unittest.TestCase):
    """Verify the within-run repeat guard."""

    def test_is_same_catches_literal_repeats(self):
        recent = RecentScripts()
        recent.add("Пять утра. Двенадцать подтягиваний.")
        self.assertTrue(recent.is_same("  пять УТРА.  двенадцать подтягиваний. "))
        self.assertFalse(recent.is_same("Зарплата шестьдесят тысяч."))

    def test_is_same_theme_catches_rewordings(self):
        recent = RecentScripts()
        recent.add("Пять утра. Двенадцать подтягиваний, пот на ладонях.")
        self.assertTrue(
            recent.is_same_theme("Пять утра. Тринадцать подтягиваний, пот на ладонях.")
        )
        self.assertFalse(recent.is_same_theme("Зарплата шестьдесят тысяч."))

    def test_history_is_bounded(self):
        recent = RecentScripts(limit=2)
        recent.add("одно")
        recent.add("два")
        recent.add("три")
        self.assertEqual(len(recent), 2)
        self.assertFalse(recent.is_same("одно"))
        self.assertTrue(recent.is_same("три"))

    def test_guard_relaxes_rather_than_emptying_the_pool(self):
        recent = RecentScripts()
        recent.add("Пять утра. Двенадцать подтягиваний, пот на ладонях.")
        self.assertEqual(recent.guard([]), [])
        self.assertEqual(len(recent.guard(["Пять утра. Двенадцать подтягиваний, пот на ладонях."])), 1)
        self.assertEqual(
            recent.guard(
                [
                    "Пять утра. Тринадцать подтягиваний, пот на ладонях.",
                    "Зарплата шестьдесят тысяч.",
                ]
            ),
            ["Зарплата шестьдесят тысяч."],
        )


class ChooseFreshTests(unittest.TestCase):
    """Verify the seeded pick on top of the existing exhaustion semantics."""

    POOL = ["alpha quote", "beta quote", "gamma quote", "delta quote"]

    def test_matches_choose_unused_reset_semantics(self):
        for used in (set(), {script_hash("alpha quote")}):
            for seed in range(10):
                fresh, fresh_reset = choose_fresh(self.POOL, used, seed=seed)
                legacy, legacy_reset = choose_unused(self.POOL, used)
                self.assertEqual(legacy_reset, fresh_reset)
                if fresh_reset:
                    self.assertEqual(fresh, legacy)
                else:
                    self.assertNotIn(script_hash(fresh), used)

    def test_deterministic_for_a_given_seed(self):
        picks = [choose_fresh(self.POOL, set(), seed=s)[0] for s in range(25)]
        self.assertEqual(picks, [choose_fresh(self.POOL, set(), seed=s)[0] for s in range(25)])

    def test_seed_actually_varies_the_pick(self):
        picks = {choose_fresh(self.POOL, set(), seed=s)[0] for s in range(40)}
        self.assertGreater(len(picks), 1)

    def test_recent_guard_suppresses_a_theme_repeat(self):
        recent = RecentScripts()
        recent.add("alpha quote")
        picked, reset = choose_fresh(
            ["alpha quote", "beta quote"], set(), seed=0, recent=recent
        )
        self.assertEqual(picked, "beta quote")
        self.assertFalse(reset)

    def test_thin_pool_still_renders(self):
        recent = RecentScripts()
        recent.add("alpha quote")
        picked, _ = choose_fresh(["alpha quote"], set(), seed=0, recent=recent)
        self.assertEqual(picked, "alpha quote")


class SelectStockScriptMatchingTests(unittest.TestCase):
    """End-to-end checks of the runner's selection policy."""

    def _pool_file(self, tmp: Path, lines: list[str]) -> Path:
        path = tmp / "scripts.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_picks_a_script_that_matches_the_visual(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool_file(
                tmp,
                [
                    "Пять утра. Двенадцать подтягиваний.",
                    "Зарплата шестьдесят тысяч.",
                    "Телефон убран — и через пятнадцать минут выясняется.",
                ],
            )
            tags = stock_tags.background_tags("forest_morning.mp4")
            for seed in range(20):
                picked = stock_runner._select_stock_script(
                    pool,
                    seed,
                    niche="self-growth",
                    niche_dir=tmp,
                    used_path=tmp / "used.json",
                    background_tags=tags,
                )
                self.assertEqual(picked, "Пять утра. Двенадцать подтягиваний.")

    def test_deterministic_for_a_given_seed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            lines = [f"Строка номер {i} про утро и подтягивания." for i in range(12)]
            pool = self._pool_file(tmp, lines)
            tags = stock_tags.background_tags("forest_morning.mp4")

            def run() -> list[str]:
                used = tmp / "used.json"
                used.unlink(missing_ok=True)
                return [
                    stock_runner._select_stock_script(
                        pool,
                        seed,
                        niche="self-growth",
                        niche_dir=tmp,
                        used_path=used,
                        background_tags=tags,
                        recent=RecentScripts(),
                    )
                    for seed in range(1, 7)
                ]

            self.assertEqual(run(), run())

    def test_no_script_repeats_within_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool_file(tmp, ["alpha quote", "beta quote", "gamma quote"])
            recent = RecentScripts()
            picks = [
                stock_runner._select_stock_script(
                    pool,
                    seed,
                    niche="self-growth",
                    niche_dir=tmp,
                    used_path=tmp / "used.json",
                    background_tags=frozenset(),
                    recent=recent,
                )
                for seed in range(3)
            ]
            self.assertEqual(len(set(picks)), 3)

    def test_thin_pool_never_crashes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool_file(tmp, ["only quote"])
            recent = RecentScripts()
            picks = [
                stock_runner._select_stock_script(
                    pool,
                    seed,
                    niche="self-growth",
                    niche_dir=tmp,
                    used_path=tmp / "used.json",
                    background_tags=frozenset({"money"}),
                    recent=recent,
                )
                for seed in (1, 2)
            ]
            self.assertEqual(picks, ["only quote", "only quote"])

    def test_untagged_visual_still_renders(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool_file(tmp, ["alpha quote", "beta quote"])
            picked = stock_runner._select_stock_script(
                pool,
                1,
                niche="self-growth",
                niche_dir=tmp,
                used_path=tmp / "used.json",
                background_tags=stock_tags.background_tags("clip_01.mp4"),
                recent=RecentScripts(),
            )
            self.assertIn(picked, ["alpha quote", "beta quote"])
            self.assertEqual(len(load_used(tmp / "used.json")), 1)

    def test_calls_without_visual_and_history_still_work(self):
        """Backwards compatibility for callers that pass only the old kwargs."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            pool = self._pool_file(tmp, ["alpha quote", "beta quote"])
            picked = stock_runner._select_stock_script(
                pool,
                1,
                niche="self-growth",
                niche_dir=tmp,
                used_path=tmp / "used.json",
            )
            self.assertIn(picked, ["alpha quote", "beta quote"])

    def test_selection_is_stable_across_processes(self):
        """Same seed and same files must give the same picks in a fresh process.

        The pipeline leans on sets internally (theme fingerprints), so this
        guards against any ``PYTHONHASHSEED``-dependent ordering leaking into
        the selection.
        """
        code = (
            "from pathlib import Path; "
            "from shorts_clipper.pipeline import stock_runner; "
            "from shorts_clipper.pipeline.stock_dedup import RecentScripts, theme_hash; "
            "from shorts_clipper.visual import stock_tags; "
            "tags = stock_tags.background_tags('forest_morning.mp4'); "
            "recent = RecentScripts(); "
            "print('|'.join(theme_hash(stock_runner._select_stock_script("
            "POOL, 4242 + i, niche='self-growth', niche_dir=WORK, "
            "used_path=Path(WORK) / 'used.json', background_tags=tags, recent=recent))[:10] "
            "for i in range(1, 7)))"
        )
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            pool = work / "scripts.txt"
            pool.write_text(
                "\n".join(f"Утро номер {i} про подтягивания и будильник." for i in range(9)) + "\n",
                encoding="utf-8",
            )
            outputs: list[str] = []
            for hash_seed in ("0", "12345"):
                proc = subprocess.run(
                    [sys.executable, "-c", code.replace("POOL", repr(str(pool))).replace("WORK", repr(str(work)))],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={**os.environ, "PYTHONHASHSEED": hash_seed},
                )
                self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
                outputs.append(proc.stdout.strip())
        self.assertEqual(outputs[0], outputs[1])
        self.assertTrue(outputs[0])


class RunStockShortMatchingTests(unittest.TestCase):
    """Verify the pipeline picks the background before the script."""

    def test_pool_is_selected_before_the_script_and_tagged(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stock_dir = tmp / "stock"
            (stock_dir / "_pexels_cache" / "morning-sunrise-forest-calm").mkdir(parents=True)
            (stock_dir / "_pexels_cache" / "morning-sunrise-forest-calm" / "00.mp4").write_bytes(
                b"clip"
            )
            niche_dir = tmp / "niches"
            (niche_dir / "self-growth").mkdir(parents=True)
            (niche_dir / "self-growth" / "scripts.txt").write_text(
                "Пять утра. Двенадцать подтягиваний.\nЗарплата шестьдесят тысяч.\n",
                encoding="utf-8",
            )

            seen: list[str] = []
            pool_seen: list[int] = []

            def fake_pool(*args, **kwargs):
                pool_seen.append(1)
                return [stock_dir / "_pexels_cache" / "morning-sunrise-forest-calm" / "00.mp4"]

            def fake_tts(script: str, vo_path: Path, **kwargs):
                seen.append(script)
                Path(vo_path).write_bytes(b"voice")
                return Path(vo_path), [("alpha", 0.0, 0.5)]

            def fake_render(*args, **kwargs):
                out = Path(args[2])
                out.write_bytes(b"bg")
                return out

            def fake_burn(*args, **kwargs):
                out = Path(kwargs["output_path"])
                out.write_bytes(b"video")
                return out

            from types import SimpleNamespace
            from unittest import mock

            settings = SimpleNamespace(
                niche="self-growth",
                stock_script_path=None,
                niche_dir=str(niche_dir),
                stock_dir=str(stock_dir),
                pexels_api_key="",
                stock_edit=False,
                stock_edit_bpm=132.0,
                video_codec="libx264",
                video_preset="veryfast",
                subtitle_style="Default",
                affiliate_enabled=False,
                bgm_mode="off",
                music_dir=str(tmp),
                bgm_volume=0.2,
                vo_rate="+0%",
                vo_pitch="+3Hz",
                output_dir=tmp / "outputs",
                publish_platforms=[],
            )
            with mock.patch(
                "shorts_clipper.pipeline.stock_runner._refresh_retention_grades", return_value={}
            ), mock.patch(
                "shorts_clipper.audio.tts.synthesize_voiceover_boundaries", side_effect=fake_tts
            ), mock.patch(
                "shorts_clipper.captions.music.track_duration", return_value=8.0
            ), mock.patch(
                "shorts_clipper.audio.tts.speech_window", return_value=None
            ), mock.patch(
                "shorts_clipper.visual.stock.speech_window", return_value=None
            ), mock.patch(
                "shorts_clipper.visual.stock.list_stock_backgrounds", side_effect=fake_pool
            ), mock.patch(
                "shorts_clipper.visual.stock.render_stock_background", side_effect=fake_render
            ), mock.patch(
                "shorts_clipper.pipeline.stock_runner.generate_ass_file", return_value=tmp / "s.ass"
            ), mock.patch(
                "shorts_clipper.pipeline.stock_runner.burn_subtitles", side_effect=fake_burn
            ):
                stock_runner.run_stock_short(
                    settings=settings, count=1, used_path=tmp / "used.json"
                )

            self.assertEqual(len(pool_seen), 1)
            self.assertEqual(seen, ["Пять утра. Двенадцать подтягиваний."])

    def test_edit_flash_text_is_taken_from_the_rendered_script(self):
        """The on-screen flash must be said BY the short, not a stock slogan."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            stock_dir = tmp / "stock"
            (stock_dir / "_pexels_cache" / "morning-sunrise-forest-calm").mkdir(parents=True)
            (stock_dir / "_pexels_cache" / "morning-sunrise-forest-calm" / "00.mp4").write_bytes(
                b"clip"
            )
            niche_dir = tmp / "niches"
            (niche_dir / "self-growth").mkdir(parents=True)
            script = "Телефон убран — и через пятнадцать минут выясняется, что занимал их сам. Не ты без него. Он без тебя."
            (niche_dir / "self-growth" / "scripts.txt").write_text(
                script + "\n", encoding="utf-8"
            )

            flash_kwargs: list[dict] = []
            burned_flash_text: list[str] = []

            def fake_pool(*args, **kwargs):
                return [stock_dir / "_pexels_cache" / "morning-sunrise-forest-calm" / "00.mp4"]

            def fake_tts(script_text: str, vo_path: Path, **kwargs):
                Path(vo_path).write_bytes(b"voice")
                return Path(vo_path), [("alpha", 0.0, 0.5)]

            def fake_render(*args, **kwargs):
                out = Path(args[2])
                out.write_bytes(b"bg")
                return out

            def fake_burn(*args, **kwargs):
                out = Path(kwargs["output_path"])
                out.write_bytes(b"video")
                return out

            real_edit_flash_schedule = stock_visual.edit_flash_schedule

            def real_schedule(duration, **kwargs):
                flash_kwargs.append(kwargs)
                return real_edit_flash_schedule(duration, **kwargs)

            def fake_ass(*args, **kwargs):
                burned_flash_text.extend(f["text"] for f in kwargs.get("flash_events") or [])
                return tmp / "s.ass"

            from types import SimpleNamespace
            from unittest import mock

            settings = SimpleNamespace(
                niche="self-growth",
                stock_script_path=None,
                niche_dir=str(niche_dir),
                stock_dir=str(stock_dir),
                pexels_api_key="",
                stock_edit=True,
                stock_edit_bpm=132.0,
                stock_edit_flash_bars=2,
                video_codec="libx264",
                video_preset="veryfast",
                subtitle_style="Default",
                affiliate_enabled=False,
                bgm_mode="off",
                music_dir=str(tmp),
                bgm_volume=0.2,
                vo_rate="+0%",
                vo_pitch="+3Hz",
                output_dir=tmp / "outputs",
                publish_platforms=[],
            )
            with mock.patch(
                "shorts_clipper.pipeline.stock_runner._refresh_retention_grades", return_value={}
            ), mock.patch(
                "shorts_clipper.audio.tts.synthesize_voiceover_boundaries", side_effect=fake_tts
            ), mock.patch(
                "shorts_clipper.captions.music.track_duration", return_value=8.0
            ), mock.patch(
                "shorts_clipper.audio.tts.speech_window", return_value=None
            ), mock.patch(
                "shorts_clipper.visual.stock.speech_window", return_value=None
            ), mock.patch(
                "shorts_clipper.visual.stock.list_stock_backgrounds", side_effect=fake_pool
            ), mock.patch(
                "shorts_clipper.visual.stock.render_stock_background", side_effect=fake_render
            ), mock.patch(
                "shorts_clipper.visual.stock.edit_flash_schedule", side_effect=real_schedule
            ), mock.patch(
                "shorts_clipper.pipeline.stock_runner.generate_ass_file", side_effect=fake_ass
            ), mock.patch(
                "shorts_clipper.pipeline.stock_runner.burn_subtitles", side_effect=fake_burn
            ):
                stock_runner.run_stock_short(
                    settings=settings, count=1, used_path=tmp / "used.json"
                )

            self.assertEqual(len(flash_kwargs), 1)
            self.assertEqual(flash_kwargs[0]["script"], script)
            self.assertTrue(burned_flash_text)
            for text in burned_flash_text:
                self.assertIn(text, {"ОН БЕЗ ТЕБЯ", "НЕ ТЫ БЕЗ НЕГО"})
            for canned in stock_visual._EDIT_FLASH_PHRASES:
                self.assertNotIn(canned, burned_flash_text)


if __name__ == "__main__":
    unittest.main()