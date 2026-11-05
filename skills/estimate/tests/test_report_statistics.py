"""The report's pure statistics: average ranks, Spearman and Pearson correlation, concordance, adjacent-bucket separation (AUC),
linear (numpy-default) quantiles and box summaries, least squares, the seeded bootstrap interval and the nearest scale step.
Every statistic is None when it cannot be computed."""
import math
import unittest

from estimation.statistics import (average_ranks, bootstrap_interval, box, concordance, least_squares, median, nearest_step,
                                   pearson, quantile, separation, spearman)

SCALE = [0, 0.5, 1, 2, 3, 5, 8, 13, 20, 40]


class RanksAndCorrelationTest(unittest.TestCase):
    def test_ties_share_the_average_one_based_rank(self):
        self.assertEqual([1.0, 2.5, 2.5, 4.0], average_ranks([10, 20, 20, 30]))
        self.assertEqual([3.0, 1.0, 2.0], average_ranks([9, 1, 5]))

    def test_spearman_of_a_monotonic_relation_is_one(self):
        self.assertAlmostEqual(1.0, spearman([1, 2, 3, 4], [1, 10, 100, 1000]))
        self.assertAlmostEqual(-1.0, spearman([1, 2, 3, 4], [9, 7, 5, 1]))

    def test_spearman_with_ties_is_pearson_of_average_ranks(self):
        # ranks x: 1, 2.5, 2.5, 4; ranks y: 1, 2, 3, 4
        self.assertAlmostEqual(0.9486832980505138, spearman([1, 2, 2, 3], [1, 2, 3, 4]))

    def test_pearson_of_a_linear_relation(self):
        self.assertAlmostEqual(1.0, pearson([1, 2, 3], [2, 4, 6]))
        self.assertAlmostEqual(0.8660254037844387, pearson([1, 2, 3], [1, 3, 3]))

    def test_none_below_three_pairs_or_with_a_constant_side(self):
        self.assertIsNone(spearman([1, 2], [1, 2]))
        self.assertIsNone(pearson([1, 2], [1, 2]))
        self.assertIsNone(spearman([1, 2, 3], [5, 5, 5]))
        self.assertIsNone(pearson([4, 4, 4], [1, 2, 3]))


class ConcordanceAndSeparationTest(unittest.TestCase):
    def test_concordance_counts_value_ties_as_half(self):
        # size pairs with different size: (1,2) concordant, (1,3) concordant, (2,3) tie in values
        self.assertAlmostEqual(2.5 / 3, concordance([1, 2, 3], [10, 20, 20]))

    def test_concordance_ignores_pairs_of_equal_size(self):
        # (1,1) ignored; (1a,2) discordant, (1b,2) concordant
        self.assertAlmostEqual(0.5, concordance([1, 1, 2], [30, 10, 20]))

    def test_concordance_none_when_every_size_is_equal(self):
        self.assertIsNone(concordance([5, 5], [1, 2]))
        self.assertIsNone(concordance([], []))

    def test_separation_is_the_share_of_higher_values_above_lower_ones(self):
        self.assertEqual(1.0, separation([1, 2], [3, 4]))
        self.assertEqual(0.0, separation([3, 4], [1, 2]))
        # pairs: (1,2) win, (1,1) half, (3,2) loss, (3,1) loss
        self.assertAlmostEqual(0.375, separation([1, 3], [2, 1]))

    def test_separation_none_with_an_empty_side(self):
        self.assertIsNone(separation([], [1]))
        self.assertIsNone(separation([1], []))


class QuantileTest(unittest.TestCase):
    def test_linear_quantiles_match_the_original_hours_box(self):
        self.assertEqual({"n": 4, "min": 6.5, "q1": 8.375, "med": 11.75, "q3": 16.75, "max": 23.5},
                         box([14.5, 6.5, 23.5, 9]))

    def test_quantile_of_one_value_is_that_value(self):
        self.assertEqual(7, quantile([7], 0.25))

    def test_quantile_ends(self):
        self.assertEqual(1, quantile([1, 2, 3], 0))
        self.assertEqual(3, quantile([1, 2, 3], 1))
        self.assertEqual(2.5, quantile([1, 2, 3, 4], 0.5))

    def test_median_of_unsorted_values(self):
        self.assertEqual(3, median([5, 1, 3]))
        self.assertEqual(2.5, median([4, 1, 3, 2]))


class LeastSquaresTest(unittest.TestCase):
    def test_exact_line_has_r_squared_one(self):
        intercept, slope, r2 = least_squares([1, 2, 3, 4], [3, 5, 7, 9])
        self.assertAlmostEqual(1.0, intercept)
        self.assertAlmostEqual(2.0, slope)
        self.assertAlmostEqual(1.0, r2)

    def test_noisy_line(self):
        intercept, slope, r2 = least_squares([1, 2, 3], [1, 3, 3])
        self.assertAlmostEqual(1 / 3, intercept)
        self.assertAlmostEqual(1.0, slope)
        self.assertAlmostEqual(0.75, r2)

    def test_none_below_three_points_or_with_constant_x(self):
        self.assertIsNone(least_squares([1, 2], [1, 2]))
        self.assertIsNone(least_squares([2, 2, 2], [1, 2, 3]))


class BootstrapTest(unittest.TestCase):
    def test_same_seed_gives_the_same_interval_inside_the_statistic_range(self):
        xs, ys = [1, 2, 3, 4, 5, 6, 7, 8], [2, 1, 4, 3, 6, 5, 8, 7]
        first = bootstrap_interval(xs, ys, spearman, 500, 7, 0.95)
        self.assertEqual(first, bootstrap_interval(xs, ys, spearman, 500, 7, 0.95))
        low, high = first
        self.assertLessEqual(-1.0, low)
        self.assertLessEqual(low, high)
        self.assertLessEqual(high, 1.0)
        self.assertLessEqual(low, spearman(xs, ys))

    def test_a_perfect_relation_gives_a_point_interval(self):
        self.assertEqual((1.0, 1.0), tuple(round(value, 9) for value in
                                           bootstrap_interval([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6], spearman, 200, 7, 0.95)))

    def test_none_when_most_draws_cannot_be_computed(self):
        self.assertIsNone(bootstrap_interval([1, 2], [1, 2], spearman, 100, 7, 0.95))


class NearestStepTest(unittest.TestCase):
    def test_nearest_positive_value_in_log_space(self):
        self.assertEqual(SCALE.index(5), nearest_step(SCALE, 5))
        self.assertEqual(SCALE.index(5), nearest_step(SCALE, 6.3))  # sqrt(5*8) = 6.32
        self.assertEqual(SCALE.index(8), nearest_step(SCALE, 6.4))
        self.assertEqual(SCALE.index(0.5), nearest_step(SCALE, 0.01))
        self.assertEqual(SCALE.index(40), nearest_step(SCALE, 1000))

    def test_a_tie_takes_the_smaller_value(self):
        self.assertEqual(SCALE.index(1), nearest_step(SCALE, math.sqrt(2)))


if __name__ == "__main__":
    unittest.main()
