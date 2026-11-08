"""The TOPS loop (tops.py): track records, trust weights and the opening blend.

    python3 -m unittest discover -s services/city
"""
from __future__ import annotations

import json
import math
import random
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import markets as mk
import pricing
import server
import test_city as tc
import tops
import traders
from data import Store
from exchange import Clock, Exchange
from ledger import Ledger

OU = {"over": 0.5, "under": 0.5}


class FakeFutures:
    def __init__(self, probs: dict[str, float]):
        self.p = dict(probs)

    def probs(self) -> dict[str, float]:
        return dict(self.p)


class FakeMarket:
    """Just what Tops reads and writes on a market."""

    model = mk.Market.model

    def __init__(self, mid: str, sub: str = "city_pulse", probs: dict | None = None, settles_at: int = 0):
        self.id, self.sub, self.settles_at = mid, sub, settles_at
        self.futures = FakeFutures(probs or OU)
        self.tilt, self.tops = None, None
        self.open_model = self.futures.probs()

    def option_ids(self) -> list[str]:
        return list(self.futures.p)


def train(t: tops.Tops, n: int, series: str = "city_pulse", start: int = 0, hit: bool = True) -> None:
    """n settled markets where 'tube' picks Over and 'contrarian' picks Under; Over wins when hit."""
    for k in range(n):
        m = FakeMarket(f"{series}-{start + k}", series, settles_at=start + k)
        t.open(m, {"tube": ("pick", "over"), "contrarian": ("pick", "under")}, start + k)
        t.settle(m.id, m.settles_at, "over" if hit else "under", start + k)


class MathsTest(unittest.TestCase):
    def test_decay_halves_a_view_after_half_life_settles(self):
        rec = tops.new_record()
        tops.update(rec, 1.0, None)
        for _ in range(tops.HALF_LIFE):
            tops.update(rec, 0.0, None)
        self.assertAlmostEqual(rec["s"], 0.5, places=9)
        self.assertAlmostEqual(rec["n"], sum(tops.DECAY ** k for k in range(tops.HALF_LIFE + 1)), places=9)
        self.assertEqual(rec["count"], tops.HALF_LIFE + 1)

    def test_posterior_shrinks_small_samples_toward_zero_and_narrows_with_data(self):
        one, many = tops.new_record(), tops.new_record()
        tops.update(one, 0.2, None)
        for _ in range(200):
            tops.update(many, 0.2, None)
        m1, sd1 = tops.posterior(one, 0.04, 10.0)
        m2, sd2 = tops.posterior(many, 0.04, 10.0)
        self.assertAlmostEqual(m1, 0.2 / (1 + 10.0))
        self.assertAlmostEqual(m2, 0.2 * many["n"] / (many["n"] + 10.0))
        self.assertAlmostEqual(sd1, (0.04 / 11.0) ** 0.5)
        self.assertLess(m1, m2)
        self.assertGreater(sd1, sd2)
        self.assertEqual(tops.posterior(None, 0.04, 10.0), (0.0, 0.0))
        self.assertEqual(tops.p_skill(0.0, 0.0), 0.5)
        self.assertGreater(tops.p_skill(m2, sd2), tops.p_skill(m1, sd1))
        self.assertAlmostEqual(tops.p_skill(0.1, 0.1), 0.841344746, places=6)

    def test_empirical_bayes_prior_follows_the_spread_of_the_records(self):
        def rec(mean: float, n: int = 60) -> dict:
            r = tops.new_record()
            for k in range(n):
                tops.update(r, mean + (0.05 if k % 2 else -0.05), None)
            return r
        alike = [[rec(0.0), rec(0.0), rec(0.0)]]
        self.assertEqual(tops.prior_k(alike), tops.K_MAX, "no spread beyond noise: the strongest prior")
        spread = [[rec(-0.2), rec(0.0), rec(0.2)]]
        self.assertLess(tops.prior_k(spread), 2.0, "real differences: the data speaks")
        self.assertEqual(tops.prior_k([]), tops.K_MAX)

    def test_score_is_log_skill_against_the_prior(self):
        self.assertAlmostEqual(tops.score({"over": 0.6, "under": 0.4}, OU, "over"), math.log(0.6 / 0.5))
        self.assertAlmostEqual(tops.score(OU, OU, "under"), 0.0)

    def test_a_pick_with_no_history_or_a_losing_one_is_exactly_the_prior(self):
        prior = {"a": 0.5, "b": 0.3, "c": 0.2}
        loser = tops.new_record()
        tops.update(loser, 0.0, -0.3)
        for view in (tops.pick_view(prior, "b", None), tops.pick_view(prior, "b", loser)):
            for o in prior:
                self.assertAlmostEqual(view[o], prior[o], places=12)
        winner = tops.new_record()
        tops.update(winner, 0.0, 0.7)
        view = tops.pick_view(prior, "b", winner)
        self.assertGreater(view["b"], prior["b"])
        self.assertAlmostEqual(sum(view.values()), 1.0)
        self.assertAlmostEqual(view["a"] / view["c"], prior["a"] / prior["c"])

    def test_weights_sum_to_one_respect_the_floor_and_the_cap(self):
        rng = random.Random(7)
        for _ in range(2000):
            m = rng.randint(1, 7)
            p = {f"t{i}": rng.choice([0.0, 1.0, rng.random()]) for i in range(m)}
            w = tops.weights(p)
            self.assertAlmostEqual(sum(w.values()), 1.0, places=9)
            traders_w = [v for k, v in w.items() if k != tops.FUTURES]
            self.assertTrue(all(v <= tops.CAP + 1e-12 for v in traders_w))
            self.assertTrue(all(v >= min(tops.FLOOR, 1 / m) - 1e-12 for v in traders_w))
            self.assertGreaterEqual(w[tops.FUTURES], 0.0)

    def test_more_likely_skill_gets_more_weight_and_the_cap_goes_to_the_futures(self):
        w = tops.weights({"good": 0.99, "bad": 0.01})
        self.assertEqual(w["good"], tops.CAP)
        self.assertAlmostEqual(w["bad"], tops.FLOOR + (1 - 2 * tops.FLOOR) * 0.01)
        self.assertAlmostEqual(w[tops.FUTURES], 1 - tops.CAP - w["bad"])
        self.assertEqual(tops.weights({}), {tops.FUTURES: 1.0})
        even = tops.weights({"a": 0.5, "b": 0.5, "c": 0.5})
        self.assertAlmostEqual(even["a"], even["c"])

    def test_lambda_is_zero_without_evidence_or_when_the_pool_lost(self):
        self.assertEqual(tops.lam_of(None), 0.0)
        self.assertEqual(tops.lam_of(tops.new_pool_record()), 0.0)
        lost = tops.new_pool_record()
        for _ in range(40):
            tops.update_pool(lost, -0.05)
        self.assertEqual(tops.lam_of(lost), 0.0)
        prior = {"a": 0.6, "b": 0.4}
        self.assertEqual(tops.blend(prior, {"x": {"a": 0.1, "b": 0.9}}, {"x": 1.0}, 0.0), prior)

    def test_lambda_is_the_shrunk_second_order_fit_and_is_capped(self):
        rec = tops.new_pool_record()
        for r in (0.1, -0.05, 0.08, 0.02):
            tops.update_pool(rec, r)
        want = rec["r1"] / rec["r2"] * rec["n"] / (rec["n"] + tops.K_SHRINK)
        self.assertGreater(want, 0)
        self.assertAlmostEqual(tops.lam_of(rec), min(tops.LAMBDA_MAX, want))
        rng = random.Random(11)
        for _ in range(2000):
            rec = tops.new_pool_record()
            for _ in range(rng.randint(1, 80)):
                tops.update_pool(rec, rng.uniform(-0.5, 2.0))
            self.assertTrue(0.0 <= tops.lam_of(rec) <= tops.LAMBDA_MAX)
        big = tops.new_pool_record()
        for _ in range(200):
            tops.update_pool(big, 0.5)
        self.assertEqual(tops.lam_of(big), tops.LAMBDA_MAX)

    def test_lambda_maximises_the_log_likelihood_to_second_order(self):
        rs = [0.5, -0.4, 0.6, -0.5, 0.3, 0.4, -0.3]
        rec = tops.new_pool_record()
        for r in rs:
            tops.update_pool(rec, r)
        w = [tops.DECAY ** (len(rs) - 1 - k) for k in range(len(rs))]
        unshrunk = rec["r1"] / rec["r2"]
        grid = [k / 10000 for k in range(0, 10001)]
        quad = max(grid, key=lambda lam: sum(wk * (lam * r - lam * lam * r * r / 2) for wk, r in zip(w, rs)))
        self.assertAlmostEqual(unshrunk, quad, places=3)
        exact = max(grid, key=lambda lam: sum(wk * math.log(1 + lam * r) for wk, r in zip(w, rs)))
        self.assertGreater(unshrunk, 0.2)
        self.assertLess(abs(unshrunk - exact), 0.1)

    def test_blend_and_tilt_stay_on_the_simplex_inside_the_futures_clamp(self):
        rng = random.Random(3)
        for _ in range(300):
            k = rng.randint(2, 5)
            opts = [f"o{i}" for i in range(k)]
            prior = tops.clamp({o: rng.random() for o in opts})
            views = {f"t{j}": tops.clamp({o: rng.random() for o in opts}) for j in range(rng.randint(1, 4))}
            w = tops.weights({t: rng.random() for t in views})
            out = tops.blend(prior, views, w, rng.uniform(0, tops.LAMBDA_MAX))
            self.assertAlmostEqual(sum(out.values()), 1.0, places=9)
            self.assertTrue(all(0 < v < 1 for v in out.values()))
            tilt = {o: out[o] / prior[o] for o in opts}
            later = tops.tilted(tops.clamp({o: rng.random() for o in opts}), tilt)
            self.assertAlmostEqual(sum(later.values()), 1.0, places=9)
            self.assertTrue(all(tops.P_LO - 1e-9 <= v <= tops.P_HI + 1e-9 for v in later.values()))
        self.assertEqual(tops.tilted(OU, None), OU)


class SizingTest(unittest.TestCase):
    """traders.stake_for: quarter Kelly on the LMSR, from the trader's belief and the option's price."""

    def book(self, p: float) -> tuple[pricing.Book, dict[str, float]]:
        """A 2-outcome LMSR whose 'yes' is priced p, half of it from the model and half from shares already sold."""
        model = {"yes": 0.5, "no": 0.5}
        b = pricing.Book(["yes", "no"])
        b.q["yes"] = pricing.LMSR_B * math.log(p / (1 - p))
        self.assertAlmostEqual(b.prices(model)["yes"], p, places=12)
        return b, model

    def test_shares_match_the_book(self):
        for p in (0.05, 0.3, 0.5, 0.8):
            b, model = self.book(p)
            for x in (1, 10, 100, 900):
                self.assertAlmostEqual(traders.shares_for(p, x), b.quote(model, "yes", x), places=6)

    def test_edge_stake_matches_the_closed_form_on_a_two_outcome_lmsr(self):
        rng = random.Random(5)
        for _ in range(300):
            p, q = rng.uniform(0.05, 0.9), rng.uniform(0.05, 0.99)
            x = traders.edge_stake(q, p)
            if q * (1 - pricing.FEE) <= p:
                self.assertEqual(x, 0.0)
                continue
            want = pricing.LMSR_B / (1 - pricing.FEE) * math.log((1 - p) / (1 - q * (1 - pricing.FEE)))
            self.assertAlmostEqual(x, want, places=9)
            b, model = self.book(p)
            b.buy(model, "yes", x)
            self.assertAlmostEqual(b.prices(model)["yes"] / (1 - pricing.FEE), q, places=9,
                                   msg="the next share costs exactly the belief")

    def test_kelly_without_slippage_is_the_textbook_fraction(self):
        for q, p in ((0.6, 0.4), (0.35, 0.2), (0.9, 0.7)):
            o = (1 - pricing.FEE) / p
            want = 1000 * (q * o - 1) / (o - 1)
            self.assertAlmostEqual(traders.kelly_stake(q, p, 1000, b=1e12), want, delta=0.01)

    def test_kelly_on_the_lmsr_is_the_log_optimum_below_the_edge_and_the_bankroll(self):
        rng = random.Random(9)
        for _ in range(300):
            p, q, w = rng.uniform(0.05, 0.9), rng.uniform(0.05, 0.99), rng.uniform(10, 5000)
            x = traders.kelly_stake(q, p, w)
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, min(w, traders.edge_stake(q, p)) + 1e-9)
            if x <= 0:
                self.assertLessEqual(q * (1 - pricing.FEE), p + 1e-12)
                continue

            def g(y: float) -> float:
                pay = min(traders.shares_for(p, y), pricing.MAX_ODDS * y)
                return q * math.log(w - y + pay) + (1 - q) * math.log(w - y)
            for y in (x * 0.9, x * 1.1):
                if 0 < y < min(w, traders.edge_stake(q, p)):
                    self.assertLessEqual(g(y), g(x) + 1e-9)

    def test_no_edge_no_bet(self):
        for p in (0.1, 0.4, 0.8):
            for q in (p * 0.5, p, p / (1 - pricing.FEE)):
                self.assertEqual(traders.edge_stake(q, p), 0.0)
                self.assertEqual(traders.kelly_stake(q, p, 1000), 0.0)
                self.assertEqual(traders.stake_for(1000, 0, q, p), 0)
        self.assertEqual(traders.stake_for(1000, 0, None, 0.4), 0)
        self.assertGreater(traders.stake_for(1000, 0, 0.6, 0.4), 0)

    def test_a_record_is_what_buys_a_trader_an_edge(self):
        t = tops.Tops(Ledger(":memory:"))
        self.assertAlmostEqual(t.belief("tube", "city_pulse", OU, "over"), 0.5)
        train(t, 40)
        self.assertGreater(t.belief("tube", "city_pulse", OU, "over"), 0.5 / (1 - pricing.FEE))
        self.assertAlmostEqual(t.belief("contrarian", "city_pulse", OU, "under"), 0.5)

    def test_no_random_sequence_of_bets_and_settles_overdraws(self):
        """Property: whatever the beliefs, prices, fills and outcomes, every open stake together never exceeds the
        points, so the balance never goes negative (the Contrarian's -181 in #86)."""
        for seed in range(300):
            rng = random.Random(seed)
            points = rng.choice([0, 9, 10, 11, 50, 120, 1000, rng.randint(0, 3000)])
            open_bets: list[tuple[int, float]] = []
            for _ in range(rng.randint(1, 200)):
                if open_bets and rng.random() < 0.4:
                    stake, odds = open_bets.pop(rng.randrange(len(open_bets)))
                    won = rng.random() < 0.5
                    points += round((stake * odds if won else 0) - stake)
                else:
                    p = rng.uniform(0.03, 0.97)
                    q = rng.choice([rng.random(), min(1.0, p + rng.uniform(0, 0.6)), 1.0])
                    staked = sum(s for s, _ in open_bets)
                    stake = traders.stake_for(points, staked, q, p)
                    self.assertGreaterEqual(stake, 0)
                    if stake:
                        self.assertGreaterEqual(stake, traders.MIN_STAKE)
                        self.assertLessEqual(stake, traders.STAKE)
                        self.assertLessEqual(staked + stake, points, f"seed {seed}")
                        odds = pricing.locked_odds(traders.shares_for(p, stake), stake)
                        if odds is not None:
                            open_bets.append((stake, odds))
                self.assertGreaterEqual(points - sum(s for s, _ in open_bets), 0, f"seed {seed}")
            for stake, _ in open_bets:
                points -= stake
            self.assertGreaterEqual(points, 0, f"seed {seed}: losing every open bet")


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(":memory:")
        self.addCleanup(self.ledger.close)
        self.t = tops.Tops(self.ledger)

    def test_no_skill_leaves_the_price_on_the_futures(self):
        m = FakeMarket("m1", probs={"over": 0.62, "under": 0.38})
        self.t.open(m, {"tube": ("pick", "over"), "contrarian": ("pick", "under")}, 0)
        self.assertEqual(m.tops["lambda"], 0.0)
        self.assertIsNone(m.tilt)
        self.assertEqual(m.model(), m.futures.probs())
        self.assertEqual(m.tops["contribution"], {"over": 0.0, "under": 0.0})

    def test_a_trader_that_keeps_being_right_moves_the_open_toward_its_pick(self):
        train(self.t, 40)
        self.assertGreater(self.t.skill("tube", "city_pulse"), 0)
        self.assertLessEqual(self.t.skill("contrarian", "city_pulse"), 0)
        m = FakeMarket("next", settles_at=100)
        self.t.open(m, {"tube": ("pick", "over"), "contrarian": ("pick", "under")}, 100)
        self.assertGreater(m.tops["lambda"], 0)
        self.assertLessEqual(m.tops["lambda"], tops.LAMBDA_MAX)
        self.assertGreater(m.tops["contribution"]["over"], 0)
        self.assertAlmostEqual(sum(m.tops["contribution"].values()), 0.0, places=3)
        self.assertGreater(m.open_model["over"], 0.5)
        self.assertGreater(m.tops["weights"]["tube"], m.tops["weights"]["contrarian"])

    def test_no_look_ahead_unsettled_markets_never_count(self):
        early = FakeMarket("early", settles_at=10)
        self.t.open(early, {"tube": ("pick", "over")}, 0)
        train(self.t, 30, start=1000)
        before = self.t.skill("tube", "city_pulse")
        late = FakeMarket("late", settles_at=2000)
        self.t.open(late, {"tube": ("pick", "over")}, 1500)
        self.assertEqual(self.t.records[("tube", "city_pulse")]["count"], 30)
        self.t.settle("early", 10, "under", 1600)
        self.assertEqual(self.t.records[("tube", "city_pulse")]["count"], 31)
        self.assertNotEqual(self.t.skill("tube", "city_pulse"), before)
        self.t.settle("early", 10, "over", 1700)  # a second settle of the same market scores nothing
        self.assertEqual(self.t.records[("tube", "city_pulse")]["count"], 31)

    def test_a_void_scores_nothing(self):
        m = FakeMarket("v")
        self.t.open(m, {"tube": ("pick", "over")}, 0)
        self.t.settle("v", 0, None, 5)
        self.assertNotIn(("tube", "city_pulse"), self.t.records)

    def test_records_are_per_series(self):
        train(self.t, 30, "city_pulse")
        train(self.t, 30, "heathrow_stack", start=100, hit=False)
        self.assertGreater(self.t.skill("tube", "city_pulse"), 0)
        self.assertLessEqual(self.t.skill("tube", "heathrow_stack"), 0)


class PersistenceTest(unittest.TestCase):
    def test_records_and_open_markets_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.db"
            ledger = Ledger(path)
            t = tops.Tops(ledger)
            train(t, 25)
            pending = FakeMarket("pending", settles_at=500)
            t.open(pending, {"tube": ("pick", "over")}, 400)
            before = {k: dict(v) for k, v in t.records.items()}
            pools = {k: dict(v) for k, v in t.pools.items()}
            self.assertTrue(pools)
            shape = t.json()
            ledger.close()

            ledger = Ledger(path)
            self.addCleanup(ledger.close)
            again = tops.Tops(ledger)
            self.assertEqual(set(again.records), set(before))
            for k, v in before.items():
                for f in ("s", "q", "n", "e", "c", "count"):
                    self.assertAlmostEqual(again.records[k][f], v[f], places=9)
            self.assertEqual(again.pools, pools)
            self.assertEqual(again.json()["series"]["city_pulse"]["traders"], shape["series"]["city_pulse"]["traders"])
            again.settle("pending", 500, "over", 600)
            self.assertEqual(again.records[("tube", "city_pulse")]["count"], 26)
            relisted = FakeMarket("pending", settles_at=500)
            again.reattach(relisted)
            self.assertEqual(relisted.tops["lambda"], pending.tops["lambda"])


class ExchangeTest(tc.CityCase):
    def test_markets_carry_tops_and_settles_feed_the_records(self):
        tc.city(self.raw, tc.T0 + 40 * tc.MIN)
        ex = self.exchange()
        for k in range(0, 40 * 60, 30):
            ex.tick(tc.T0 + k * 1000)
        now = tc.T0 + 40 * tc.MIN
        listed = [m for m in ex.markets.values() if m.tops is not None]
        self.assertTrue(listed)
        settled = [m for m in listed if m.result and m.result["winner"]]
        self.assertTrue(settled)
        self.assertTrue(ex.tops.records)
        shown = ex.markets_json(now)["markets"]
        for row in shown:
            if "tops" in row:
                self.assertEqual(set(row["tops"]), {"lambda", "weights", "contribution"})
                self.assertEqual(set(row["tops"]["contribution"]), {o["id"] for o in row["options"]})
        for row in ex.traders_json(now)["traders"]:
            self.assertTrue(0.0 <= row["trust"] <= tops.CAP)
        for m in listed:
            self.assertAlmostEqual(sum(m.prices().values()), 1.0, places=9)


class TopsRouteTest(unittest.TestCase):
    def test_get_tops_shape(self):
        raw = tc.Raw()
        self.addCleanup(raw.dir.cleanup)
        tc.city(raw, tc.T0 + 30 * tc.MIN)
        ex = Exchange(Store(raw.root, tc.SPOTS), Clock(tc.T0), tc.SPOTS, Ledger(":memory:"))
        train(ex.tops, 12)
        p = mock.patch.object(server, "EX", ex)
        p.start()
        self.addCleanup(p.stop)
        httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/tops", timeout=5) as res:
            out = json.loads(res.read())
        self.assertEqual(res.status, 200)
        for key in ("halfLife", "priorK", "lambdaShrinkK", "floor", "cap", "lambdaMax", "series"):
            self.assertIn(key, out)
        s = out["series"]["city_pulse"]
        self.assertEqual(set(s), {"lambdaNow", "traders", "futuresWeight", "history"})
        self.assertEqual(set(s["traders"]), {"tube", "contrarian"})
        for v in s["traders"].values():
            self.assertEqual(set(v), {"score", "sd", "pSkill", "n", "nEff", "weight"})
            self.assertEqual(v["n"], 12)
        self.assertAlmostEqual(sum(v["weight"] for v in s["traders"].values()) + s["futuresWeight"], 1.0, places=3)
        self.assertEqual(len(s["history"]), 12)
        self.assertEqual(set(s["history"][-1]), {"ts", "traders"})


if __name__ == "__main__":
    unittest.main()
