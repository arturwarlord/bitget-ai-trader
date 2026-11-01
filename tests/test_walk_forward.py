import unittest

from walk_forward import fold_ranges


class WalkForwardTests(unittest.TestCase):
    def test_folds_are_contiguous_non_overlapping_and_cover_final_60_percent(self):
        ranges = fold_ranges(10000, initial_fraction=0.4, fold_count=6)
        self.assertEqual(len(ranges), 6)
        self.assertEqual(ranges[0][0], 4000)
        self.assertEqual(ranges[-1][1], 10000)
        for index, (start, end) in enumerate(ranges):
            self.assertLess(start, end)
            if index:
                self.assertEqual(ranges[index - 1][1], start)

    def test_rejects_too_few_candles_for_requested_folds(self):
        with self.assertRaises(ValueError):
            fold_ranges(1000, initial_fraction=0.4, fold_count=6)

    def test_rejects_invalid_fold_count(self):
        with self.assertRaises(ValueError):
            fold_ranges(10000, fold_count=0)


if __name__ == "__main__":
    unittest.main()
