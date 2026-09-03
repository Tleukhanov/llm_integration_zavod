"""Tests for select_non_overlapping (clip window de-duplication)."""

import unittest

from shorts_clipper.attention.gameplay import select_non_overlapping
from shorts_clipper.core.models import ClipWindow


class SelectNonOverlappingTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(select_non_overlapping([], min_gap=15.0), [])

    def test_single_window_kept(self):
        w = ClipWindow(start=10.0, end=40.0)
        result = select_non_overlapping([w], min_gap=15.0)
        self.assertEqual(result, [w])

    def test_two_overlapping_windows_keeps_first(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=20.0, end=50.0)  # overlaps w1
        result = select_non_overlapping([w1, w2], min_gap=15.0)
        self.assertEqual(result, [w1])
        self.assertEqual(len(result), 1)

    def test_two_distant_windows_both_kept(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=100.0, end=130.0)
        result = select_non_overlapping([w1, w2], min_gap=15.0)
        self.assertEqual(len(result), 2)
        self.assertIn(w1, result)
        self.assertIn(w2, result)

    def test_near_abut_with_gap_below_threshold(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=31.0, end=60.0)  # 1s gap < 15s
        result = select_non_overlapping([w1, w2], min_gap=15.0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], w1)

    def test_near_abut_with_gap_above_threshold(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=50.0, end=80.0)  # 20s gap > 15s
        result = select_non_overlapping([w1, w2], min_gap=15.0)
        self.assertEqual(len(result), 2)

    def test_three_windows_chain(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=25.0, end=55.0)   # overlaps w1
        w3 = ClipWindow(start=90.0, end=120.0)   # far from w1
        result = select_non_overlapping([w1, w2, w3], min_gap=15.0)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], w1)
        self.assertEqual(result[1], w3)

    def test_high_priority_kept_with_score(self):
        w_low = ClipWindow(start=0.0, end=30.0)
        w_high = ClipWindow(start=10.0, end=40.0)  # overlaps w_low
        result = select_non_overlapping(
            [w_low, w_high], min_gap=15.0, score=lambda w: 1.0 if w is w_high else 0.0
        )
        self.assertEqual(result, [w_high])

    def test_score_higher_first_but_too_close(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=15.0, end=45.0)  # overlaps w1, higher score
        w3 = ClipWindow(start=100.0, end=130.0)  # far away, lower score
        # w2 (score=2) is considered first, then w3 (score=1), then w1 (score=0)
        # w2 kept; w3 far from w2 → kept; w1 overlaps w2 → dropped
        result = select_non_overlapping(
            [w1, w2, w3], min_gap=15.0,
            score=lambda w: {id(w1): 0, id(w2): 2, id(w3): 1}[id(w)],
        )
        self.assertEqual(len(result), 2)
        self.assertIn(w2, result)
        self.assertIn(w3, result)

    def test_returns_in_original_order(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=100.0, end=130.0)
        w3 = ClipWindow(start=50.0, end=80.0)
        result = select_non_overlapping([w1, w2, w3], min_gap=15.0)
        # All far apart → all kept, in original order
        self.assertEqual(result, [w1, w2, w3])

    def test_original_list_not_mutated(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=20.0, end=50.0)
        original = [w1, w2]
        _ = select_non_overlapping(original, min_gap=15.0)
        self.assertEqual(original, [w1, w2])

    def test_all_identical_windows_keeps_one(self):
        w1 = ClipWindow(start=10.0, end=40.0)
        w2 = ClipWindow(start=10.0, end=40.0)
        w3 = ClipWindow(start=10.0, end=40.0)
        result = select_non_overlapping([w1, w2, w3], min_gap=15.0)
        self.assertEqual(len(result), 1)

    def test_min_gap_zero_keeps_all(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=30.0, end=60.0)
        # min_gap <= 0 → early return with all windows
        result = select_non_overlapping([w1, w2], min_gap=0.0)
        self.assertEqual(len(result), 2)

    def test_tie_breaking_keeps_earlier_in_original_order(self):
        w1 = ClipWindow(start=0.0, end=30.0)
        w2 = ClipWindow(start=5.0, end=35.0)  # same score as w1
        result = select_non_overlapping(
            [w1, w2], min_gap=15.0, score=lambda w: 1.0
        )
        # Both score 1.0 → sorted by list order (w1 before w2), w1 kept, w2 too close
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], w1)


class GamePlaySettingsClipMinSeparationTests(unittest.TestCase):
    def test_default_value(self):
        from shorts_clipper.core.settings import Settings
        s = Settings()
        self.assertEqual(s.clip_min_separation, 15.0)

    def test_env_parse(self):
        import os
        from shorts_clipper.core.settings import Settings
        os.environ["SHORTS_CLIP_MIN_SEPARATION"] = "25.0"
        try:
            s = Settings.from_env("_nonexistent.env")
            self.assertEqual(s.clip_min_separation, 25.0)
        finally:
            os.environ.pop("SHORTS_CLIP_MIN_SEPARATION", None)


if __name__ == "__main__":
    unittest.main()
