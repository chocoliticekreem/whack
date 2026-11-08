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
import server
import test_city as tc
import tops
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

    def test_shrinkage_pulls_small_samples_toward_zero(self):
        one, many = tops.new_record(), tops.new_record()
        tops.update(one, 0.2, None)
        for _ in range(200):
            tops.update(many, 0.2, None)
        self.assertAlmostEqual(tops.shrunk(one), 0.2 / (1 + tops.K_SHRINK))
        self.assertAlmostEqual(tops.shrunk(many), 0.2 * many["n"] / (many["n"] + tops.K_SHRINK))
        self.assertLess(tops.shrunk(one), tops.shrunk(many))
        self.assertEqual(tops.shrunk(None), 0.0)

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
        for _ in range(500):
            m = rng.randint(1, 7)
            skill = {f"t{i}": rng.uniform(-0.2, 0.2) for i in range(m)}
            w = tops.weights(skill)
            self.assertAlmostEqual(sum(w.values()), 1.0, places=9)
            traders_w = [v for k, v in w.items() if k != tops.FUTURES]
            self.assertTrue(all(v <= tops.CAP + 1e-12 for v in traders_w))
            self.assertTrue(all(v >= min(tops.FLOOR, 1 / m) - 1e-12 for v in traders_w))
            self.assertGreaterEqual(w[tops.FUTURES], 0.0)

    def test_better_skill_gets_more_weight_and_the_cap_goes_to_the_futures(self):
        w = tops.weights({"good": 0.05, "bad": -0.05})
        self.assertEqual(w["good"], tops.CAP)
        self.assertAlmostEqual(w["bad"], tops.FLOOR, places=3)
        self.assertAlmostEqual(w[tops.FUTURES], 1 - tops.CAP - w["bad"])
        self.assertEqual(tops.weights({}), {tops.FUTURES: 1.0})

    def test_lambda_is_zero_without_skill(self):
        self.assertEqual(tops.lam_of({}), 0.0)
        self.assertEqual(tops.lam_of({"a": 0.0, "b": -0.3}), 0.0)
        prior = {"a": 0.6, "b": 0.4}
        self.assertEqual(tops.blend(prior, {"x": {"a": 0.1, "b": 0.9}}, {"x": 1.0}, 0.0), prior)

    def test_lambda_is_capped(self):
        self.assertLessEqual(tops.lam_of({"a": 50.0, "b": 50.0}), tops.LAMBDA_MAX)
        self.assertGreater(tops.lam_of({"a": 50.0}), 0.99 * tops.LAMBDA_MAX)
        self.assertGreater(tops.lam_of({"a": 0.02}), 0.0)

    def test_blend_and_tilt_stay_on_the_simplex_inside_the_futures_clamp(self):
        rng = random.Random(3)
        for _ in range(300):
            k = rng.randint(2, 5)
            opts = [f"o{i}" for i in range(k)]
            prior = tops.clamp({o: rng.random() for o in opts})
            views = {f"t{j}": tops.clamp({o: rng.random() for o in opts}) for j in range(rng.randint(1, 4))}
            w = tops.weights({t: rng.uniform(-0.1, 0.1) for t in views})
            out = tops.blend(prior, views, w, rng.uniform(0, tops.LAMBDA_MAX))
            self.assertAlmostEqual(sum(out.values()), 1.0, places=9)
            self.assertTrue(all(0 < v < 1 for v in out.values()))
            tilt = {o: out[o] / prior[o] for o in opts}
            later = tops.tilted(tops.clamp({o: rng.random() for o in opts}), tilt)
            self.assertAlmostEqual(sum(later.values()), 1.0, places=9)
            self.assertTrue(all(tops.P_LO - 1e-9 <= v <= tops.P_HI + 1e-9 for v in later.values()))
        self.assertEqual(tops.tilted(OU, None), OU)


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
            shape = t.json()
            ledger.close()

            ledger = Ledger(path)
            self.addCleanup(ledger.close)
            again = tops.Tops(ledger)
            self.assertEqual(set(again.records), set(before))
            for k, v in before.items():
                for f in ("s", "n", "e", "c", "count"):
                    self.assertAlmostEqual(again.records[k][f], v[f], places=9)
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
        for key in ("halfLife", "shrinkK", "temperature", "floor", "cap", "lambdaMax", "series"):
            self.assertIn(key, out)
        s = out["series"]["city_pulse"]
        self.assertEqual(set(s), {"lambdaNow", "traders", "futuresWeight", "history"})
        self.assertEqual(set(s["traders"]), {"tube", "contrarian"})
        for v in s["traders"].values():
            self.assertEqual(set(v), {"score", "n", "nEff", "weight"})
            self.assertEqual(v["n"], 12)
        self.assertAlmostEqual(sum(v["weight"] for v in s["traders"].values()) + s["futuresWeight"], 1.0, places=3)
        self.assertEqual(len(s["history"]), 12)
        self.assertEqual(set(s["history"][-1]), {"ts", "traders"})


if __name__ == "__main__":
    unittest.main()
