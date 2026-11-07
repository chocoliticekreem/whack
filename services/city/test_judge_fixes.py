"""Tests for the judge-prep and economy-simulation fixes (docs/judge-prep.md §4, docs/market-structure.md §10).
python3 -m unittest discover -s services/city"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import markets as mk
import pricing
import traders
from data import Store
from exchange import Clock, Exchange
from ledger import Ledger
from test_city import (CITY, MIN, OTHERS, OXFORD, SPOTS, T0, CityCase, Raw, city, crowd_history, crowding,
                       iso, lines, oxford_station, profile, vix_payload)

HERE = Path(__file__).resolve().parent


def rising_profile(raw: Raw, at: int, before: float, after: float) -> None:
    """Every station's typical day steps from `before` to `after` at `at` (a 15-min band edge)."""
    edge = iso(at)[11:16]
    for nap in [h["naptan"] for h in SPOTS] + OTHERS:
        raw.put("crowding_profile", T0 - 3 * 3_600_000,
                profile(nap, lambda band: after if band >= edge else before))


def stamped(vals: dict[str, float], stamp: int, others: float = 0.30) -> list[dict]:
    rows = crowding(vals, others)
    for r in rows:
        r["timeUtc"] = iso(stamp)
    return rows


class UsualSlopeTest(CityCase):
    """1. The walks drift along TfL's usual-day curve, so copying the usual slope no longer beats the house."""

    def setUp(self):
        super().setUp()
        rising_profile(self.raw, T0 + 15 * MIN, 0.30, 0.45)
        for k in range(24):
            ts = T0 + 3 * MIN - (23 - k) * 5 * MIN
            self.raw.put("crowding", ts, crowding(dict.fromkeys(CITY, 0.30), 0.30))

    def test_usual_drift_is_the_log_ratio_per_minute(self):
        self.assertAlmostEqual(pricing.usual_drift(0.30, 0.45, 10), math.log(1.5) / 10)
        self.assertEqual(pricing.usual_drift(None, 0.45, 10), 0.0)

    def test_station_walk_carries_the_usual_curve_to_the_settle_reading(self):
        store = self.raw.store(T0 + 10 * MIN)
        now, ts0 = T0 + 10 * MIN, T0 + 3 * MIN
        t_end = T0 + 20 * MIN
        _, _, _, mu, _ = mk.crowd_station(store, OXFORD, store.latest("crowding", now)[1], now, ts0, t_end, 17)
        want = math.log(store.usual(OXFORD["naptan"], t_end) / store.usual(OXFORD["naptan"], ts0)) / 17
        self.assertGreater(want, 0.005)
        self.assertAlmostEqual(mu, want, delta=0.002)

    def test_city_pulse_futures_rise_with_a_rising_usual_day(self):
        now = T0 + 10 * MIN
        m = mk.build_city_pulse("p", self.raw.store(now), SPOTS, now, T0 + 15 * MIN)
        finals = sorted(math.exp(p[-1]) for p in m.futures.sim.paths)
        self.assertGreater(finals[len(finals) // 2], m.spec["open"] * 1.03)
        self.assertGreater(m.spec["line"], m.spec["open"] + 0.5, "the line rises with the futures")

    def test_hub_furthest_below_its_usual_drifts_up_relative_to_the_others(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        raw.profile()
        crowd_history(raw, T0, CITY, noise=0.0)
        store = raw.store(T0)
        m = mk.build_hub_surge("hub_surge-1505", store, SPOTS, T0, T0 + 5 * MIN)
        sim = m.futures.sim
        ids = [s[0] for s in sim.stations]
        rise = {o: sum(p[j][-1] - p[j][0] - sim.stations[j][3] * sim.m for p in sim.paths) / len(sim.paths)
                for j, o in enumerate(ids)}
        vs = m.spec["open"]
        low, high = min(vs, key=vs.get), max(vs, key=vs.get)
        self.assertGreater(rise[low] - rise[high], 0.5 * mk.HUB_KAPPA * sim.m * math.log(vs[high] / vs[low]))


class WeightedFilterTest(unittest.TestCase):
    """2. After the 60% ruled-out cap, readings still move the model price through likelihood weights."""

    def test_a_reading_after_the_cap_still_moves_the_price(self):
        sim = pricing.LevelSim(oxford_station(0.01), T0, 5, random.Random(1))
        sim.edges = [39, 40, 41]
        fut = pricing.Futures(sim, ["b0", "b1", "b2", "b3"])
        fut.observe(T0 + 2 * MIN, {OXFORD["naptan"]: 0.9})
        self.assertEqual(fut.ruled_out, 30)
        before = fut.probs()
        fut.observe(T0 + 4 * MIN, {OXFORD["naptan"]: 0.415})
        after = fut.probs()
        self.assertEqual(fut.ruled_out, 30, "the cap still holds")
        self.assertGreater(after["b3"], before["b3"] + 0.05)

    def test_weights_never_collapse_below_half_the_survivors(self):
        weights = {i: math.exp(-i) for i in range(20)}
        self.assertLess(pricing.ess(weights.values()), 10)
        self.assertGreaterEqual(pricing.ess(pricing.temper(weights, 10).values()), 10 - 1e-6)

    def test_huge_errors_neither_underflow_nor_move_equally_wrong_futures(self):
        sim = pricing.LevelSim(oxford_station(0.01), T0, 5, random.Random(1))
        sim.edges = [39, 40, 41]
        fut = pricing.Futures(sim, ["b0", "b1", "b2", "b3"])
        fut.reweight([(1e6, i) for i in range(pricing.N_FUTURES)])
        self.assertEqual(fut.weights, [1.0] * pricing.N_FUTURES)
        fut.reweight([(40.0 + i, i) for i in range(pricing.N_FUTURES)])
        self.assertAlmostEqual(sum(fut.weights), pricing.N_FUTURES)
        self.assertGreaterEqual(pricing.ess(fut.weights), pricing.MIN_ESS * pricing.N_FUTURES - 1e-6)


class TraderRecordTest(CityCase):
    """3 and 4. AI records survive a restart; stakes scale with points and a busted trader sits out."""

    def test_stake_is_ten_percent_up_to_100_and_zero_under_10(self):
        self.assertEqual([traders.stake_for(p) for p in (5000, 1000, 500, 100, 99, 0, -50)],
                         [100, 100, 50, 10, 0, 0, 0])

    def test_records_persist_across_a_restart(self):
        city(self.raw, T0 + 20 * MIN)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.db"
            ex = Exchange(Store(self.raw.root, SPOTS), Clock(T0), SPOTS, Ledger(path))
            for t in range(T0, T0 + 20 * MIN, 12_000):
                ex.tick(t)
            settled = [b for m in ex.markets.values() if m.result and not m.result["void"] for b in m.ai_bets]
            self.assertTrue(settled)
            want = {tid: dict(r) for tid, r in ex.record.items()}
            self.assertTrue(any(r["won"] + r["lost"] for r in want.values()))
            ex.ledger.close()
            again = Exchange(Store(self.raw.root, SPOTS), Clock(T0 + 20 * MIN), SPOTS, Ledger(path))
            self.addCleanup(again.ledger.close)
            self.assertEqual(again.record, want)
            row = next(r for r in again.traders_json(T0 + 20 * MIN)["traders"] if r["id"] == settled[-1]["trader"])
            self.assertTrue(row["lastWhy"])

    def test_a_busted_trader_sits_out_and_says_why(self):
        city(self.raw, T0 + 10 * MIN)
        ex = self.exchange()
        ex.record["tube"]["points"] = 90
        for t in range(T0, T0 + 20_000, 5_000):
            ex.tick(t)
        self.assertFalse([b for m in ex.markets.values() for b in m.ai_bets if b["trader"] == "tube"])
        self.assertIn("90 points left", ex.sat_out["tube"][1])
        ex2 = self.exchange()
        ex2.record["tube"]["points"] = 400
        for t in range(T0, T0 + 20_000, 5_000):
            ex2.tick(t)
        self.assertEqual({b["stake"] for m in ex2.markets.values() for b in m.ai_bets if b["trader"] == "tube"}, {40})


class StampSettlementTest(CityCase):
    """5. Crowding settles on each station's first reading TfL stamped after the close, not on our poll time."""

    def setUp(self):
        super().setUp()
        self.raw.profile()
        crowd_history(self.raw, T0, CITY)
        self.m = mk.build_hub_surge("hub_surge-1505", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN)

    def test_a_poll_after_close_stamped_before_it_does_not_settle(self):
        close = self.m.closes_at
        self.raw.put("crowding", close + MIN, stamped(dict(CITY, bank=0.30), close - 3 * MIN))
        now = self.m.settles_at + 2 * MIN
        self.assertEqual(mk.try_settle(self.m, self.raw.store(now), now)[0], "wait")
        self.raw.put("crowding", close + 8 * MIN, stamped(dict(CITY, waterloo=0.60), close + 2 * MIN))
        now = close + 9 * MIN
        got = mk.try_settle(self.m, self.raw.store(now), now)
        self.assertEqual(got[:2], ("done", "waterloo"), "settles on the reading stamped after the close")
        self.assertIn("stamps (timeUtc) after bets close", self.m.rule)

    def test_no_reading_stamped_after_close_within_the_window_voids(self):
        close = self.m.closes_at
        self.raw.put("crowding", close + MIN, stamped(CITY, close - 3 * MIN))
        now = close + mk.CROWD_WAIT_MS + MIN
        self.assertEqual(mk.try_settle(self.m, self.raw.store(now), now)[:2], ("void", "No data — void"))

    def test_city_pulse_takes_each_stations_first_reading_after_the_close(self):
        store = self.raw.store(T0)
        m = mk.build_city_pulse("city_pulse-1505", store, SPOTS, T0, T0 + 5 * MIN)
        close = m.closes_at
        late = stamped(CITY, close - 2 * MIN, others=0.90)
        self.raw.put("crowding", close + MIN, late)
        rows = stamped(CITY, close + MIN, others=0.30)
        self.raw.put("crowding", close + 6 * MIN, rows)
        now = close + 7 * MIN
        got = mk.try_settle(m, self.raw.store(now), now)
        want = 100 * (sum(CITY.values()) + 15 * 0.30) / 20
        self.assertEqual(got[0], "done")
        self.assertAlmostEqual(got[3]["value"], round(want, 2), places=2)


class CleanSheetCloseTest(CityCase):
    """6. Clean sheet closes betting with its opening round, so a line stop can't be courtsided for 59 minutes."""

    def test_betting_closes_55_minutes_before_the_settle(self):
        for ts in range(T0 - 3 * 3_600_000, T0 + 35 * MIN, MIN):
            self.raw.put("line_status", ts, lines())
        at, now = T0 + 35 * MIN, T0 + 29 * MIN
        m = mk.build_clean_sheet("x", self.raw.store(now), SPOTS, now, at)
        self.assertEqual(m.closes_at, m.settles_at - 55 * MIN)
        self.assertEqual(m.closes_at, at)
        self.assertIn("Betting closes at 15:35", m.rule)

    def test_a_courtsider_ten_minutes_in_is_refused(self):
        city(self.raw, T0 + 41 * MIN)
        ex = self.exchange()
        self.addCleanup(ex.ledger.close)
        ex.ledger.open_account("courtsider")
        for t in range(T0 + 29 * MIN + 10_000, T0 + 40 * MIN, 15_000):
            ex.tick(t)
        m = next(m for m in ex.active() if m.sub == "clean_sheet")
        with self.assertRaisesRegex(PermissionError, "market closed"):
            ex.bet({"player": "courtsider", "market": m.id, "option": "no", "stake": 100}, T0 + 40 * MIN)


class VixListingTest(CityCase):
    """7. The VIX lists on the engine's normal 60-120 s frame cadence, and /round says why when it doesn't."""

    def setUp(self):
        super().setUp()
        city(self.raw, T0 + 10 * MIN)

    def test_a_frame_150_s_old_still_lists(self):
        store = self.raw.store(T0)
        store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, T0 - 150_000))
        self.assertTrue(mk.vix_state(store, T0)["ok"], mk.vix_state(store, T0)["reason"])
        self.assertIsNotNone(mk.build_london_vix("london_vix-1505", store, SPOTS, T0, T0 + 5 * MIN))

    def test_round_reports_the_vix_reason_not_no_fresh_reading(self):
        ex = self.exchange()
        ex.store.refresh(T0 - mk.HISTORY_MS, T0)
        ex.store.ingest_vix(vix_payload(T0 - 30 * MIN, T0))
        ex.tick(T0)
        self.assertIn("warming up", ex.round_json(T0)["current"]["skipped"]["london_vix"])


class SimWeightTest(unittest.TestCase):
    """8. The central sim stays out of the price by default; SIM_WEIGHT still turns it on."""

    def weight(self, env: dict) -> str:
        env = {k: v for k, v in os.environ.items() if k != "SIM_WEIGHT"} | env
        out = subprocess.run([sys.executable, "-c", "import pricing; print(pricing.SIM_WEIGHT, pricing.sim_count())"],
                             cwd=HERE, env=env, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_default_is_zero_and_the_env_still_works(self):
        self.assertEqual(self.weight({}), "0.0 0")
        self.assertEqual(self.weight({"SIM_WEIGHT": "0.5"}), "0.5 25")


if __name__ == "__main__":
    unittest.main()


class PricingRound2Test(CityCase):
    """Out-of-sample pricing fixes: hub_surge's relative dynamics and City Pulse's line at the futures' median."""

    def test_city_pulse_line_is_the_futures_median_at_settle_on_a_half_point(self):
        rising_profile(self.raw, T0 + 15 * MIN, 0.30, 0.45)
        crowd_history(self.raw, T0 + 3 * MIN, CITY)
        for now, settles in ((T0 + 3 * MIN, T0 + 5 * MIN), (T0 + 10 * MIN, T0 + 15 * MIN)):
            m = mk.build_city_pulse("city_pulse-x", self.raw.store(now), SPOTS, now, settles)
            finals = sorted(math.exp(p[-1]) for p in m.futures.sim.paths)
            median = (finals[24] + finals[25]) / 2
            self.assertEqual((m.spec["line"] * 2) % 1, 0)
            self.assertLessEqual(abs(m.spec["line"] - median), 0.5)
            self.assertEqual(m.futures.sim.strike, m.spec["line"], "futures settle against the listed line")
            over = sum(f > m.spec["line"] for f in finals)
            self.assertLessEqual(abs(over - 25), 25 - 1, "never a lock either way")

    def test_relative_ou_fit_recovers_known_parameters(self):
        ids = ["a", "b", "c"]
        corr = [[1.0, 0.5, -0.2], [0.5, 1.0, 0.1], [-0.2, 0.1, 1.0]]
        typ = [0.4, 0.3, 0.2]
        stations = [(h, h, math.log(t * v), 0.0, 0.01) for h, t, v in zip(ids, typ, (1.3, 0.8, 1.0))]
        sim = pricing.RelativeRaceSim(stations, 0, 1, random.Random(5), typ, typ, 0.03, pricing.cholesky(corr), 1.0)
        sim.m = 5 * 3000
        path = sim.walk_all(0.03, pricing.cholesky(corr), random.Random(6))
        grid = {k: {h: (path[j][5 * k], path[j][5 * k] - math.log(typ[j])) for j, h in enumerate(ids)}
                for k in range(3000)}
        kappa, sds, got, n = pricing.fit_relative_ou(grid, ids)
        self.assertEqual(n, 2999)
        self.assertAlmostEqual(kappa, 0.03, delta=0.006)
        for h in ids:
            self.assertAlmostEqual(sds[h], 0.01, delta=0.0015)  # 2.5-sigma step cap trims a little variance
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(got[i][j], corr[i][j], delta=0.08)

    def test_tempering_does_not_worsen_calibration_on_the_recorded_day(self):
        """102 hub_surge races replayed on 2026-11-07 (untempered open prices, power 1). HUB_POWER must not make
        Brier or log loss worse, on the 12:40-17:00 window it was fitted on or on 17:00-21:05 after it."""
        rows = json.loads((HERE / "fixtures" / "hub_surge_open_2026-11-07.json").read_text())
        split = 1794070800000  # 2026-11-07T17:00Z

        def scores(part, power):
            b = ll = 0.0
            for r in part:
                w = {o: p ** power for o, p in r["open"].items()}
                s = sum(w.values())
                probs = {o: v / s for o, v in w.items()}
                b += sum((p - (o == r["winner"])) ** 2 for o, p in probs.items())
                ll -= math.log(probs[r["winner"]])
            return b / len(part), ll / len(part)

        for part in ([r for r in rows if r["opened"] < split], [r for r in rows if r["opened"] >= split]):
            self.assertEqual(len(part) > 40, True)
            raw_b, raw_ll = scores(part, 1.0)
            b, ll = scores(part, mk.HUB_POWER)
            self.assertLessEqual(b, raw_b)
            self.assertLessEqual(ll, raw_ll)

    def test_tempered_shares_feed_the_open_price(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        raw.profile()
        crowd_history(raw, T0, CITY)
        m = mk.build_hub_surge("hub_surge-1505", raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
        self.assertEqual(m.futures.sim.power, mk.HUB_POWER)
        m.futures.sim.power = 1.0
        raw_probs = m.futures.probs()
        self.assertLess(max(m.open_model.values()), max(raw_probs.values()))
        self.assertAlmostEqual(sum(m.open_model.values()), 1.0)
