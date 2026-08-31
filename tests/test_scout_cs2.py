import unittest

from shorts_clipper.scout.keywords import NICHE_KEYWORDS, build_queries, get_keywords


class ScoutCs2Tests(unittest.TestCase):
    def test_get_keywords_includes_cs2_terms(self):
        kws = get_keywords("cs2")
        self.assertIn("cs2 clutch", kws)
        self.assertIn("cs2 1v5", kws)
        self.assertIn("cs2 awp ace", kws)
        self.assertIn("cs2 best moments", kws)
        self.assertIn("cs2 highlight", kws)
        # Russian variants included.
        self.assertIn("клатч кс", kws)
        self.assertIn("кс2 клатч", kws)

    def test_build_queries_yields_at_least_four_for_cs2(self):
        queries = build_queries("cs2", keyword=None, count=4)
        self.assertGreaterEqual(len(queries), 4)

    def test_cs2_added_to_niche_keywords(self):
        self.assertIn("cs2", NICHE_KEYWORDS)

    def test_cs2_queries_are_valid_ytsearch(self):
        for q in build_queries("cs2", keyword=None, count=4):
            self.assertTrue(q.startswith("ytsearch15:"))


if __name__ == "__main__":
    unittest.main()
