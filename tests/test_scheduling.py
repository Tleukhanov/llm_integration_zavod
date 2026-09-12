"""Offline tests for the factory publish-window scheduler."""

import unittest

from shorts_clipper.core.scheduling import in_publish_window


class InPublishWindowTests(unittest.TestCase):
    def test_both_none_always_open(self):
        for hour in range(24):
            self.assertTrue(in_publish_window(hour, None, None))

    def test_normal_window_bounds(self):
        for hour in range(9, 17):
            self.assertTrue(in_publish_window(hour, 9, 17))
        for hour in [0, 8, 17, 23]:
            self.assertFalse(in_publish_window(hour, 9, 17))

    def test_wrap_around_window(self):
        self.assertTrue(in_publish_window(22, 22, 6))
        self.assertTrue(in_publish_window(23, 22, 6))
        self.assertTrue(in_publish_window(0, 22, 6))
        self.assertTrue(in_publish_window(5, 22, 6))
        self.assertFalse(in_publish_window(21, 22, 6))
        self.assertFalse(in_publish_window(6, 22, 6))

    def test_reversed_still_wraps(self):
        self.assertTrue(in_publish_window(22, 20, 4))
        self.assertTrue(in_publish_window(3, 20, 4))
        self.assertTrue(in_publish_window(20, 20, 4))
        self.assertFalse(in_publish_window(5, 20, 4))
        self.assertFalse(in_publish_window(19, 20, 4))

    def test_single_hour_window(self):
        self.assertTrue(in_publish_window(10, 10, 11))
        self.assertFalse(in_publish_window(9, 10, 11))
        self.assertFalse(in_publish_window(11, 10, 11))

    def test_open_sided_windows(self):
        self.assertTrue(in_publish_window(5, None, 6))
        self.assertFalse(in_publish_window(7, None, 6))
        self.assertTrue(in_publish_window(22, 22, None))
        self.assertFalse(in_publish_window(21, 22, None))

    def test_deterministic(self):
        for _ in range(5):
            self.assertEqual(in_publish_window(3, 22, 6), in_publish_window(3, 22, 6))
            self.assertEqual(in_publish_window(12, 9, 17), in_publish_window(12, 9, 17))


if __name__ == "__main__":
    unittest.main()