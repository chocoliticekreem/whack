"""Metric functions of eval_real on tiny synthetic inputs (no recorder data, no worlds)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_real  # noqa: E402


class Mae(unittest.TestCase):
    def test_mean_absolute_error(self) -> None:
        self.assertAlmostEqual(eval_real.mae([1.0, 4.0, 2.0], [2.0, 2.0, 2.0]), 1.0)

    def test_perfect_forecast_is_zero(self) -> None:
        self.assertEqual(eval_real.mae([3.0, 0.0], [3.0, 0.0]), 0.0)

    def test_no_observations_is_none(self) -> None:
        self.assertIsNone(eval_real.mae([], []))


class Coverage(unittest.TestCase):
    def test_share_inside_with_inclusive_ends(self) -> None:
        intervals = [(0.0, 2.0), (1.0, 3.0), (5.0, 6.0), (2.0, 2.0)]
        self.assertAlmostEqual(eval_real.coverage(intervals, [2.0, 1.0, 4.0, 2.0]), 0.75)

    def test_no_observations_is_none(self) -> None:
        self.assertIsNone(eval_real.coverage([], []))


class Persistence(unittest.TestCase):
    def test_forecast_is_previous_value(self) -> None:
        self.assertEqual(eval_real.persistence_forecasts([4.0, 6.0, 1.0], 9.0), [9.0, 4.0, 6.0])

    def test_gaps_carry_the_last_seen_value(self) -> None:
        self.assertEqual(eval_real.persistence_forecasts([None, 2.0, None, 5.0], 0.5), [0.5, 0.5, 2.0, 2.0])

    def test_empty(self) -> None:
        self.assertEqual(eval_real.persistence_forecasts([], 1.0), [])


class Interval(unittest.TestCase):
    def test_bus_interval_matches_gaussian_quantiles(self) -> None:
        lo, hi = eval_real.interval80([5.0], [1.0], "bus")
        z90 = 1.2815515655446004
        self.assertAlmostEqual(lo, 5.0 - z90 * eval_real.BUS_SD, places=4)
        self.assertAlmostEqual(hi, 5.0 + z90 * eval_real.BUS_SD, places=4)

    def test_floor_at_zero_when_mass_sits_below(self) -> None:
        lo, hi = eval_real.interval80([0.0], [1.0], "count")
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)

    def test_count_interval_is_symmetric_on_the_sqrt_scale(self) -> None:
        lo, hi = eval_real.interval80([16.0], [1.0], "count")
        z90 = 1.2815515655446004
        self.assertAlmostEqual(lo ** 0.5, 4.0 - z90 * eval_real.COUNT_SD, places=4)
        self.assertAlmostEqual(hi ** 0.5, 4.0 + z90 * eval_real.COUNT_SD, places=4)

    def test_zero_weight_worlds_do_not_move_the_mixture(self) -> None:
        for a, b in zip(eval_real.interval80([5.0, 50.0], [1.0, 0.0], "bus"),
                        eval_real.interval80([5.0], [1.0], "bus")):
            self.assertAlmostEqual(a, b, places=6)
        self.assertAlmostEqual(eval_real.weighted_mean([5.0, 50.0], [1.0, 0.0]), 5.0)


if __name__ == "__main__":
    unittest.main()
