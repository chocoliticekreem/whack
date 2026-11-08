"""hub_surge's model (pricing.HubFit, pricing.RelativeRaceSim, markets.build_hub_surge): no look-ahead, prices that
sum to 1 inside the odds band, a Cholesky that holds on singular and shrunk correlations, and the missing-hub rule.
python3 -m unittest discover -s services/city"""
from __future__ import annotations

import math
import random
import statistics
import unittest

import markets as mk
import pricing
from test_city import CITY, MIN, SPOTS, T0, CityCase, Raw, crowd_history, crowding

IDS = [h["id"] for h in SPOTS]


def random_corr(rng: random.Random, n: int, t: int) -> list[list[float]]:
    """The sample correlation of t draws of n variables with a random common factor: singular when t <= n."""
    load = [rng.uniform(-1, 1) for _ in range(n)]
    rows = []
    for _ in range(t):
        f = rng.gauss(0, 1)
        rows.append([a * f + rng.gauss(0, rng.choice((0.01, 0.3, 1.0))) for a in load])
    return pricing.sample_corr(rows)


def toward_constant(corr: list[list[float]], delta: float) -> list[list[float]]:
    n = len(corr)
    rbar = sum(corr[i][j] for i in range(n) for j in range(n) if i != j) / (n * (n - 1))
    return [[1.0 if i == j else (1 - delta) * corr[i][j] + delta * rbar for j in range(n)] for i in range(n)]


class CholeskyPropertyTest(unittest.TestCase):
    def test_factor_is_finite_and_reproduces_every_psd_correlation(self):
        rng = random.Random(7)
        for trial in range(400):
            n = rng.randint(2, 6)
            t = rng.choice((1, 2, n - 1, n, n + 1, 8, 30, 90))
            corr = random_corr(rng, n, max(1, t))
            if trial % 2:
                corr = toward_constant(corr, rng.random())
            low = pricing.cholesky(corr)
            for i in range(n):
                self.assertGreaterEqual(low[i][i], 0.0)
                for j in range(n):
                    self.assertTrue(math.isfinite(low[i][j]))
                    if j > i:
                        self.assertEqual(low[i][j], 0.0)
                    got = sum(low[i][k] * low[j][k] for k in range(n))
                    self.assertAlmostEqual(got, corr[i][j], delta=1e-6, msg=f"trial {trial}, n {n}, t {t}")

    def test_sample_corr_gives_a_still_hub_zero_correlation(self):
        corr = pricing.sample_corr([[1.0, 0.0, 2.0], [2.0, 0.0, 1.0], [3.0, 0.0, 3.5]])
        self.assertEqual(corr[1], [0.0, 1.0, 0.0])
        self.assertEqual(pricing.cholesky(corr)[1], [0.0, 1.0, 0.0])


def sim_of(fit: pricing.HubFit, vs: list[float], seed: int = 1, steps: int = 3) -> pricing.RelativeRaceSim:
    typ = [0.4, 0.3, 0.2]
    stations = [(h, h, math.log(t * v), 0.0, 0.01) for h, t, v in zip("abc", typ, vs)]
    usual = [{s: math.log(t) for s in range(10)} for t in typ]
    return pricing.RelativeRaceSim(stations, 0, 5 * steps, random.Random(seed), typ, typ, {steps: fit}, [0] * 3,
                                   steps, usual, 1.0)


class RelativeRaceSimTest(unittest.TestCase):
    EYE = [[float(i == j) for j in range(3)] for i in range(3)]

    def test_stratified_futures_carry_the_many_draw_shares(self):
        fit = pricing.HubFit(0.2, [0.0] * 3, [[1, 0.3, 0.1], [0.3, 1, 0.2], [0.1, 0.2, 1]], [0.05, 0.08, 0.03], 40)
        sim = sim_of(fit, [1.1, 0.9, 1.0])
        shares = {h: sum(sim.outcome(i) == h for i in range(pricing.N_FUTURES)) / pricing.N_FUTURES for h in "abc"}
        many = {h: 0 for h in "abc"}
        for seed in range(400):
            s = sim_of(fit, [1.1, 0.9, 1.0], seed=1000 + seed)
            for i in range(pricing.N_FUTURES):
                many[s.outcome(i)] += 1
        for h in "abc":
            self.assertAlmostEqual(shares[h], many[h] / (400 * pricing.N_FUTURES), delta=0.04)
        self.assertEqual(len(sim.paths), pricing.N_FUTURES)
        self.assertTrue(all(len(p) == sim.m + 1 for f in sim.paths for p in f))

    def test_a_hub_at_its_own_level_does_not_catch_up(self):
        """Bank sat 0.15 below the other hubs all day and did not close the gap; with its level fitted, the model
        stops pricing a catch-up it never makes."""
        gap = math.log(0.8) - (math.log(1.1) + math.log(0.8) + math.log(1.1)) / 3
        pulled = sim_of(pricing.HubFit(0.4, [0.0] * 3, self.EYE, [0.001] * 3, 40), [1.1, 0.8, 1.1])
        level = sim_of(pricing.HubFit(0.4, [-gap / 2, gap, -gap / 2], self.EYE, [0.001] * 3, 40), [1.1, 0.8, 1.1])
        rise = [statistics.fmean(p[1][-1] - p[1][0] for p in s.paths) for s in (pulled, level)]
        self.assertGreater(rise[0], -0.4 * gap * 0.9)
        self.assertAlmostEqual(rise[1], 0.0, delta=0.002)

    def test_a_restart_draws_from_the_new_reading_with_the_steps_left(self):
        fit = pricing.HubFit(0.2, [0.0] * 3, self.EYE, [0.02] * 3, 40)
        sim = sim_of(fit, [1.0, 1.0, 1.0])
        typ = [0.4, 0.3, 0.2]
        self.assertTrue(sim.restart(10 * MIN, {"a": 0.4 * 1.3, "b": 0.3, "c": 0.2}))
        self.assertEqual(sim.power, 1.0)
        probs = pricing.Futures(sim, list("abc")).probs()
        self.assertGreater(probs["a"], 0.9, "a rose 30 points by the second step; one step left can't undo that")
        self.assertFalse(sim.restart(10 * MIN, {"a": 0.4, "b": 0.3, "c": 0.2}), "same stamp: no restart")
        self.assertTrue(all(abs(p[0][0] - math.log(typ[0])) < 1e-12 for p in sim.paths), "gains anchored at open")


class BuildHubSurgeTest(CityCase):
    def setUp(self):
        super().setUp()
        self.raw.profile()

    def test_no_look_ahead(self):
        """Readings polled after the open change nothing: the fits, the futures and the open price are the same
        whether or not the store already holds them."""
        crowd_history(self.raw, T0, CITY)
        before = mk.build_hub_surge("hub_surge-1505", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
        rng = random.Random(3)
        for k in range(1, 9):
            vals = {h: v * rng.uniform(0.5, 2.0) for h, v in CITY.items()}
            self.raw.put("crowding", T0 + k * 5 * MIN, crowding(vals))
        after_store = self.raw.store(T0 + 45 * MIN)
        self.assertEqual(len(after_store.between("crowding", T0 + 1, T0 + 45 * MIN)), 8, "the store holds the future")
        after = mk.build_hub_surge("hub_surge-1505", after_store, SPOTS, T0, T0 + 5 * MIN)
        self.assertEqual(before.open_model, after.open_model)
        self.assertEqual(before.futures.sim.fits, after.futures.sim.fits)
        self.assertEqual(before.futures.sim.paths, after.futures.sim.paths)

    def test_prices_sum_to_one_inside_the_odds_band_at_open_and_after_a_restart(self):
        rng = random.Random(11)
        floor = pricing.P_FLOOR / (1 + (len(IDS) - 1) * pricing.P_FLOOR)
        for trial in range(25):
            raw = Raw()
            self.addCleanup(raw.dir.cleanup)
            raw.profile()
            vals = {h: v * rng.choice((1.0, rng.uniform(0.2, 5.0))) for h, v in CITY.items()}
            crowd_history(raw, T0, vals, noise=rng.choice((0.0, 0.01, 0.2)))
            m = mk.build_hub_surge(f"hub_surge-{trial}", raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
            raw.put("crowding", T0 + 5 * MIN, crowding({h: v * rng.uniform(0.3, 3.0) for h, v in vals.items()}))
            states = [m.futures.probs()]
            m.futures.observe(T0 + 5 * MIN, raw.store(T0 + 5 * MIN).latest("crowding", T0 + 5 * MIN)[1])
            states.append(m.futures.probs())
            for probs in states:
                self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
                for p in probs.values():
                    self.assertGreaterEqual(p, floor - 1e-12)
                    self.assertLessEqual(p, pricing.P_CEIL)
                    self.assertTrue(pricing.MIN_ODDS <= pricing.odds_of(p) <= pricing.MAX_ODDS)

    def test_a_cold_start_falls_back_to_the_prior_fit(self):
        for k in range(3):
            self.raw.put("crowding", T0 - k * 5 * MIN, crowding(CITY))
        m = mk.build_hub_surge("hub_surge-1505", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
        fits = m.futures.sim.fits
        self.assertTrue(fits and all(f.pairs == 0 for f in fits.values()), "too little history: prior fit")
        self.assertAlmostEqual(fits[1].b, 1 - math.exp(-mk.HUB_KAPPA * 5))
        self.assertAlmostEqual(sum(m.open_model.values()), 1.0)

    def test_a_hub_missing_at_open_is_dropped_and_the_race_settles_without_it(self):
        crowd_history(self.raw, T0 - 5 * MIN, CITY)
        self.raw.put("crowding", T0, crowding({h: v for h, v in CITY.items() if h != "waterloo"}))
        m = mk.build_hub_surge("hub_surge-1505", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
        self.assertEqual(m.option_ids(), [h for h in IDS if h != "waterloo"])
        self.assertNotIn("waterloo", m.spec["stations"])
        self.assertNotIn("Waterloo", m.rule)
        self.assertIn("4 hubs", m.source)
        self.assertAlmostEqual(sum(m.open_model.values()), 1.0)
        self.raw.put("crowding", m.closes_at + MIN,
                     crowding({h: (0.30 if h == "bank" else v) for h, v in CITY.items() if h != "waterloo"}))
        now = m.settles_at + MIN
        got = mk.try_settle(m, self.raw.store(now), now)
        self.assertEqual(got[:2], ("done", "bank"))

    def test_fewer_than_three_hubs_lists_nothing(self):
        crowd_history(self.raw, T0 - 5 * MIN, CITY)
        self.raw.put("crowding", T0, crowding({"bank": 0.05, "waterloo": 0.38}))
        self.assertIsNone(mk.build_hub_surge("hub_surge-1505", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN))

    def test_a_hub_without_a_usual_day_is_dropped(self):
        crowd_history(self.raw, T0, CITY)
        store = self.raw.store(T0)
        store.profile.pop(SPOTS[0]["naptan"])
        m = mk.build_hub_surge("hub_surge-1505", store, SPOTS, T0, T0 + 5 * MIN)
        self.assertEqual(m.option_ids(), IDS[1:])


if __name__ == "__main__":
    unittest.main()
