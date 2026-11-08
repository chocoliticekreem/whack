"""City Pulse's trend model (pricing.fit_pulse, markets.build_city_pulse; analysis/judge/pulse_model.py).
python3 -m unittest discover -s services/city"""
from __future__ import annotations

import math
import random
import statistics
import unittest

import markets as mk
import pricing
from test_city import CITY, MIN, OTHERS, SPOTS, T0, CityCase, Raw, crowding, profile

MID = "city_pulse-1505"
SETTLES = T0 + 5 * MIN


def history(raw: Raw, level, until: int = T0, skip: tuple[int, ...] = ()) -> None:
    """Crowding every 5 min for 3 h up to `until`, every station at level(k) x its CITY value (others at 0.30),
    k = 0 for the newest reading, 1 for the one 5 min before... Readings with k in `skip` are left out."""
    for k in range(36, -1, -1):
        if k not in skip:
            f = level(k)
            raw.put("crowding", until - k * 5 * MIN, crowding({h: v * f for h, v in CITY.items()}, 0.30 * f))


def flat_profile(raw: Raw) -> None:
    """A usual day that never moves: each hub at its CITY value, the other stations at 0.30."""
    for h in SPOTS:
        raw.put("crowding_profile", T0 - 3 * 3_600_000, profile(h["naptan"], CITY[h["id"]]))
    for nap in OTHERS:
        raw.put("crowding_profile", T0 - 3 * 3_600_000, profile(nap, 0.30))


def old_walk(store, now: int, settles_at: int, mid: str) -> pricing.ScalarSim:
    """The futures as the builder made them before the trend: the plain walk plus the usual slope."""
    last = store.latest("crowding", now)
    p0 = mk.pulse_of(last[1])
    mu, sd = pricing.fit_log_walk(mk.pulse_vs_usual(store, now - mk.HISTORY_MS, now), 0.01, 0.002, 0.05)
    m0 = store.measured_at(last[0])
    t_end, minutes = mk.crowd_walk(m0, settles_at, now)
    mu += pricing.usual_drift(p0, mk.pulse_along_usual(store, (m0, last[1]), t_end), minutes)
    return pricing.ScalarSim(p0, mu, sd, m0, minutes, random.Random(mk.seed_of(mid)), mk.pulse_of, p0)


class FitPulseTest(unittest.TestCase):
    def test_drift_is_the_fitted_share_of_the_last_10_min_change_spread_over_the_walk(self):
        devs = [(T0 - 15 * MIN, 0.00), (T0 - 10 * MIN, 0.01), (T0 - 5 * MIN, 0.03), (T0, 0.05)]
        self.assertAlmostEqual(pricing.fit_pulse(devs, T0, 16), pricing.PULSE_MOMENTUM * 0.04 / 16)

    def test_reading_10_min_before_may_sit_off_the_grid_by_up_to_tol(self):
        near = [(T0 - 10 * MIN - pricing.STAMP_TOL_MS, 0.01), (T0, 0.05)]
        self.assertAlmostEqual(pricing.fit_pulse(near, T0, 10), pricing.PULSE_MOMENTUM * 0.04 / 10)
        far = [(T0 - 10 * MIN - pricing.STAMP_TOL_MS - 1, 0.01), (T0, 0.05)]
        self.assertIsNone(pricing.fit_pulse(far, T0, 10))

    def test_none_without_the_open_reading_or_the_one_before(self):
        self.assertIsNone(pricing.fit_pulse([(T0 - 10 * MIN, 0.01)], T0, 10), "no reading at the open stamp")
        self.assertIsNone(pricing.fit_pulse([(T0, 0.05)], T0, 10), "nothing 10 min before")
        self.assertIsNone(pricing.fit_pulse([], T0, 10))


class PulseTrendTest(CityCase):
    def build(self, store, now: int = T0, settles_at: int = SETTLES, mid: str = MID) -> mk.Market:
        return mk.build_city_pulse(mid, store, SPOTS, now, settles_at)

    def test_futures_carry_the_fitted_share_of_the_last_10_min_rise_vs_usual(self):
        flat_profile(self.raw)
        history(self.raw, lambda k: {0: 1.10, 1: 1.05}.get(k, 1.0))
        m = self.build(self.raw.store(T0))
        sim = m.futures.sim
        want = m.spec["open"] * math.exp(pricing.PULSE_MOMENTUM * math.log(1.10))
        self.assertAlmostEqual(statistics.median(math.exp(p[-1]) for p in sim.paths), want, delta=want * 0.01)
        self.assertGreater(want, m.spec["open"] * 1.02, "a usual day that never moves: only the trend lifts it")

    def test_a_falling_trend_lowers_the_line(self):
        flat_profile(self.raw)
        history(self.raw, lambda k: {0: 0.85, 1: 0.92}.get(k, 1.0))
        m = self.build(self.raw.store(T0))
        self.assertLess(m.spec["line"], m.spec["open"] - 0.5)

    def test_no_look_ahead_readings_after_the_open_change_nothing(self):
        flat_profile(self.raw)
        history(self.raw, lambda k: 1.0 + 0.01 * (k % 3))
        clean = self.build(self.raw.store(T0))
        for k in range(1, 7):
            self.raw.put("crowding", T0 + k * MIN, crowding({h: 0.95 for h in CITY}, 0.95))
        store = mk.Store(self.raw.root, SPOTS)
        store.refresh(T0 - mk.HISTORY_MS, T0 + 60 * MIN)
        self.assertTrue(any(ts > T0 for ts, _ in store.between("crowding", T0, T0 + 60 * MIN)))
        later = self.build(store)
        self.assertEqual(later.futures.sim.paths, clean.futures.sim.paths)
        self.assertEqual((later.spec["line"], later.open_model), (clean.spec["line"], clean.open_model))
        self.assertTrue(all(ts <= T0 for ts, _ in mk.pulse_devs(store, T0 - mk.PULSE_TREND_MS, T0)))

    def test_prices_stay_in_the_odds_band_sum_to_one_and_the_line_is_on_a_half_point(self):
        flat_profile(self.raw)
        for name, level in (("surge", lambda k: 1.6 if k == 0 else 1.0), ("crash", lambda k: 0.5 if k == 0 else 1.0),
                            ("flat", lambda k: 1.0)):
            with self.subTest(name):
                raw = Raw()
                self.addCleanup(raw.dir.cleanup)
                flat_profile(raw)
                history(raw, level)
                m = self.build(raw.store(T0))
                self.assertEqual((m.spec["line"] * 2) % 1, 0)
                self.assertAlmostEqual(sum(m.open_model.values()), 1.0, places=9)
                for o, p in m.open_model.items():
                    self.assertTrue(pricing.P_FLOOR <= p <= pricing.P_CEIL, (o, p))
                    self.assertTrue(pricing.MIN_ODDS <= pricing.odds_of(p) <= pricing.MAX_ODDS)
                    odds = pricing.locked_odds(m.book.quote(m.open_model, o, 10), 10)
                    self.assertTrue(odds is None or pricing.MIN_ODDS <= odds <= pricing.MAX_ODDS, odds)

    def test_without_the_usual_profile_it_is_the_old_walk(self):
        history(self.raw, lambda k: {0: 1.10, 1: 1.05}.get(k, 1.0))
        store = self.raw.store(T0)
        self.assertEqual(mk.pulse_devs(store, T0 - mk.PULSE_TREND_MS, T0), [])
        m = self.build(store)
        self.assertEqual(m.futures.sim.paths, old_walk(store, T0, SETTLES, MID).paths)

    def test_without_a_reading_10_min_before_the_open_it_is_the_old_walk(self):
        flat_profile(self.raw)
        history(self.raw, lambda k: {0: 1.10}.get(k, 1.0), skip=(1, 2, 3))
        store = self.raw.store(T0)
        self.assertIsNone(pricing.fit_pulse(mk.pulse_devs(store, T0 - mk.PULSE_TREND_MS, T0), T0, 10))
        m = self.build(store)
        self.assertEqual(m.futures.sim.paths, old_walk(store, T0, SETTLES, MID).paths)
        self.assertEqual(m.spec["line"], round(statistics.median(math.exp(p[-1]) for p in m.futures.sim.paths) * 2) / 2)


if __name__ == "__main__":
    unittest.main()
