"""Offline tests: python3 -m unittest discover -s services/city"""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import markets as mk
import pricing
import traders
from data import Store, load_hotspots
from exchange import Clock, Exchange

T0 = int(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60_000
SPOTS = load_hotspots()
NAPTANS = {h["id"]: h["naptan"] for h in SPOTS}


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def crowding(vals: dict[str, float]) -> list[dict]:
    return [{"naptan": NAPTANS[h], "name": h, "dataAvailable": True, "percentageOfBaseline": v, "timeUtc": "x"}
            for h, v in vals.items()]


def lines(sevs: dict[str, int]) -> list[dict]:
    desc = {10: "Good Service", 9: "Minor Delays", 6: "Severe Delays"}
    return [{"id": lid, "name": lid.title(), "mode": "tube",
             "statuses": [{"statusSeverity": s, "statusSeverityDescription": desc.get(s, "?")}]}
            for lid, s in sevs.items()]


def cams(people: dict[str, float]) -> list[dict]:
    return [{"id": c, "name": f"Cam {c[-5:]}", "yolo": {"people": p, "vehicles": 1, "at": 0}}
            for c, p in people.items()]


def rain(mm: float) -> dict:
    return {"current": {"time": "2026-11-07T15:00", "precipitation": mm}, "minutely_15": []}


class Raw:
    """A throwaway recorder tree: <tmp>/<source>/<date>/<HH>.ndjson."""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def put(self, source: str, ts: int, payload) -> None:
        d = datetime.fromtimestamp(ts / 1000, timezone.utc)
        path = self.root / source / d.strftime("%Y-%m-%d") / f"{d.strftime('%H')}.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"ts": iso(ts), "source": source, "payload": payload}) + "\n")

    def store(self, now: int) -> Store:
        s = Store(self.root, SPOTS)
        s.refresh(now - mk.HISTORY_MS, now)
        return s


def crowd_history(raw: Raw, until: int, vals: dict[str, float]) -> None:
    rng = random.Random(1)
    for k in range(24):
        ts = until - (23 - k) * 5 * MIN
        raw.put("crowding", ts, crowding({h: v * (1 + rng.uniform(-0.02, 0.02)) for h, v in vals.items()}))


def bus_history(raw: Raw, until: int) -> None:
    rng = random.Random(2)
    for k in range(60):
        rows = []
        for h in SPOTS:
            rows += [{"naptanId": h["busStops"][0], "timeToStation": 60}] * rng.randint(1, 6)
        raw.put("bus", until - (59 - k) * MIN, rows)


class OddsAndPricesTest(unittest.TestCase):
    def test_odds_formula(self):
        self.assertEqual(pricing.odds_of(0.5), 1.9)
        self.assertEqual(pricing.odds_of(0.3), 3.2)
        self.assertEqual(pricing.odds_of(0.99), 1.05)
        self.assertEqual(pricing.odds_of(0.01), 20.0)

    def test_every_kind_of_future_prices_sum_to_one(self):
        rng = random.Random(3)
        sims = [
            (pricing.CrowdSim([("a", "A", -0.9, 0, 0.01), ("b", "B", -0.95, 0, 0.01), ("c", "C", -1.2, 0, 0.01)],
                              T0, 6, rng), ["a", "b", "c"]),
            (pricing.CountSim("cam", 5, 0.2, 0.4, T0, 4, rng), ["over", "under"]),
            (pricing.LineAnySim({"x": 10, "y": 9}, 0.01, 0.02, T0, 5, rng), ["yes", "no"]),
            (pricing.LineOneSim("y", 0.05, 0.01, T0, 5, rng), ["yes", "no"]),
            (pricing.RainSim({T0: 0.03, T0 + 15 * MIN: 0.8}, T0, 5, rng), ["yes", "no"]),
        ]
        for sim, ids in sims:
            probs = pricing.Futures(sim, ids).probs()
            self.assertAlmostEqual(sum(probs.values()), 1.0)
            self.assertTrue(all(pricing.P_FLOOR * 0.9 <= p <= pricing.P_CEIL for p in probs.values()), probs)

    def test_lmsr_moves_a_coin_flip_three_to_five_points_and_still_sums_to_one(self):
        book = pricing.Book(["yes", "no"])
        model = {"yes": 0.5, "no": 0.5}
        shares = book.buy(model, "yes", 100)
        prices = book.prices(model)
        self.assertAlmostEqual(sum(prices.values()), 1.0)
        self.assertGreaterEqual(prices["yes"] - 0.5, 0.03)
        self.assertLessEqual(prices["yes"] - 0.5, 0.05)
        self.assertAlmostEqual(shares / 100, 0.95 / 0.5, delta=0.1)

    def test_futures_start_from_the_last_reading_and_steps_are_capped(self):
        sim = pricing.CrowdSim([("a", "A", -1.0, 0.0, 0.01)], T0, 10, random.Random(5))
        for path in sim.paths:
            self.assertEqual(path[0][0], -1.0)
            steps = [abs(b - a) for a, b in zip(path[0], path[0][1:])]
            self.assertLessEqual(max(steps), 0.01 * pricing.STEP_CAP + 1e-9)


class RuledOutCapTest(unittest.TestCase):
    def test_never_rules_out_more_than_sixty_percent(self):
        sim = pricing.CountSim("cam", 5, 0.0, 0.4, T0, 5, random.Random(1))  # every future stays at 5
        fut = pricing.Futures(sim, ["over", "under"])
        fut.observe(T0 + 2 * MIN, {"cam": 40})  # contradicts all 50
        self.assertEqual(fut.ruled_out, 30)
        fut.observe(T0 + 3 * MIN, {"cam": 41})
        self.assertEqual(fut.ruled_out, 30)
        self.assertEqual(sum(fut.alive), 20)

    def test_consistent_observation_rules_out_nothing(self):
        sim = pricing.CountSim("cam", 5, 0.0, 0.4, T0, 5, random.Random(1))
        fut = pricing.Futures(sim, ["over", "under"])
        fut.observe(T0 + MIN, {"cam": 5})
        self.assertEqual(fut.ruled_out, 0)


class SettlementTest(unittest.TestCase):
    def setUp(self):
        self.raw = Raw()

    def tearDown(self):
        self.raw.dir.cleanup()

    def busiest(self, now: int, settles: int) -> mk.Market:
        m = mk.build_busiest("busiest-test", self.raw.store(now), SPOTS, now, settles, set())
        self.assertIsNotNone(m)
        return m

    def test_versus_settles_on_the_last_reading_at_or_before_t(self):
        crowd_history(self.raw, T0, {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38, "piccadilly": 0.1,
                                     "bank": 0.05})
        settles = T0 + 6 * MIN
        m = self.busiest(T0, settles)
        self.raw.put("crowding", settles - 40_000, crowding({"oxford-circus": 0.30, "kings-cross": 0.45,
                                                            "waterloo": 0.38}))
        self.raw.put("crowding", settles + 20_000, crowding({"oxford-circus": 0.90, "kings-cross": 0.1,
                                                            "waterloo": 0.1}))  # after T: ignored
        got = mk.try_settle(m, self.raw.store(settles + MIN), settles + MIN)
        self.assertEqual(got[0], "done")
        self.assertEqual(got[1], "kings-cross")
        self.assertIn("King's Cross 45% of usual", got[2])
        self.assertEqual(got[3]["fetchedAt"], settles - 40_000)

    def test_versus_waits_then_voids_without_a_reading_near_t(self):
        crowd_history(self.raw, T0, {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38})
        settles = T0 + 6 * MIN
        m = self.busiest(T0, settles)
        self.assertEqual(mk.try_settle(m, self.raw.store(settles + MIN), settles + MIN)[0], "wait")
        got = mk.try_settle(m, self.raw.store(settles + 4 * MIN), settles + 4 * MIN)
        self.assertEqual(got[0], "void")
        self.assertEqual(got[1], "No data — void")

    def test_before_settles_at_it_waits(self):
        crowd_history(self.raw, T0, {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38})
        m = self.busiest(T0, T0 + 6 * MIN)
        got = mk.try_settle(m, self.raw.store(T0 + MIN), T0 + MIN)
        self.assertEqual(got, ("wait", 5 * MIN))

    def test_cam_settles_on_the_median_of_the_last_minute_with_named_boundary(self):
        hot = next(h for h in SPOTS if h["id"] == "oxford-circus")
        cam = hot["cams"][0]
        for k in range(30):
            self.raw.put("jamcams", T0 - (29 - k) * MIN, cams({cam: 12}))
        settles = T0 + 4 * MIN
        m = mk.build_cam("cam-test", self.raw.store(T0), hot, T0, settles)
        m.spec["line"] = m.futures.sim.line = 12  # pin the strike to test the boundary
        self.assertIn("exactly ", m.rule)
        self.assertEqual(m.closes_at, settles - 90_000)
        for ts, n in ((settles - 50_000, 12), (settles - 20_000, 14), (settles - 5_000, 13)):
            self.raw.put("jamcams", ts, cams({cam: n}))
        got = mk.try_settle(m, self.raw.store(settles), settles)
        self.assertEqual((got[0], got[1]), ("done", "over"))
        self.assertEqual(got[3]["value"], 13)

    def test_cam_falls_back_to_the_neighbouring_camera(self):
        hot = next(h for h in SPOTS if h["id"] == "oxford-circus")
        main, other = hot["cams"][0], hot["cams"][1]
        self.raw.put("jamcams", T0, cams({main: 5, other: 9}))
        settles = T0 + 3 * MIN
        m = mk.build_cam("cam-test", self.raw.store(T0), hot, T0, settles)
        m.spec["line"] = 5
        self.raw.put("jamcams", settles - 30_000, cams({other: 2}))
        got = mk.try_settle(m, self.raw.store(settles), settles)
        self.assertEqual((got[0], got[1]), ("done", "under"))
        self.assertIn("fallback", got[2])

    def test_line_market_with_no_reading_resolves_no(self):
        self.raw.put("line_status", T0, lines({"central": 10, "victoria": 9}))
        settles = T0 + 3 * MIN
        m = mk.build_lines_any("lines-test", self.raw.store(T0), SPOTS, T0, settles)
        got = mk.try_settle(m, self.raw.store(settles + 4 * MIN), settles + 4 * MIN)
        self.assertEqual((got[0], got[1]), ("done", "no"))

    def test_line_market_settles_yes_when_a_line_gets_worse(self):
        self.raw.put("line_status", T0, lines({"central": 10, "victoria": 9}))
        settles = T0 + 3 * MIN
        m = mk.build_lines_any("lines-test", self.raw.store(T0), SPOTS, T0, settles)
        self.raw.put("line_status", settles - 30_000, lines({"central": 9, "victoria": 10}))
        got = mk.try_settle(m, self.raw.store(settles), settles)
        self.assertEqual((got[0], got[1]), ("done", "yes"))
        self.assertIn("Central: Good Service → Minor Delays", got[2])

    def test_rain_is_yes_if_any_reading_in_the_window_is_wet(self):
        self.raw.put("rain_now", T0, rain(0.0))
        settles = T0 + 10 * MIN
        m = mk.build_rain("rain-test", self.raw.store(T0), SPOTS, T0, settles)
        self.raw.put("rain_now", T0 + 5 * MIN, rain(0.3))
        self.raw.put("rain_now", T0 + 9 * MIN, rain(0.0))
        got = mk.try_settle(m, self.raw.store(settles), settles)
        self.assertEqual((got[0], got[1]), ("done", "yes"))
        self.raw.put("rain_now", settles + MIN, rain(5.0))  # after T: must not matter for a dry market
        dry = mk.build_rain("rain-dry", self.raw.store(T0), SPOTS, T0 + 6 * MIN, settles)
        self.assertEqual(mk.try_settle(dry, self.raw.store(settles + 2 * MIN), settles + 2 * MIN)[1], "no")


class TraderTest(unittest.TestCase):
    def test_each_trader_bets_only_on_markets_its_feed_informs(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        crowd_history(raw, T0, {"oxford-circus": 0.40, "kings-cross": 0.30, "waterloo": 0.20})
        hot = next(h for h in SPOTS if h["id"] == "oxford-circus")
        raw.put("jamcams", T0, cams({hot["cams"][0]: 7}))
        raw.put("line_status", T0 - MIN, lines({"central": 10, "victoria": 9}))
        raw.put("line_status", T0, lines({"central": 9, "victoria": 9}))
        raw.put("rain_now", T0, rain(0.0))
        bus_history(raw, T0)
        store = raw.store(T0)
        built = {
            "busiest": mk.build_busiest("b", store, SPOTS, T0, T0 + 6 * MIN, set()),
            "cam": mk.build_cam("c", store, hot, T0, T0 + 4 * MIN),
            "level": mk.build_level("v", store, hot, T0, T0 + 6 * MIN),
            "bus": mk.build_bus("u", store, hot, T0, T0 + 4 * MIN),
            "lines_any": mk.build_lines_any("l", store, SPOTS, T0, T0 + 4 * MIN),
            "line": mk.build_line("o", store, SPOTS, T0, T0 + 4 * MIN, set()),
            "rain": mk.build_rain("r", store, SPOTS, T0, T0 + 6 * MIN),
        }
        informs = {"tube": {"busiest", "level"}, "cams": {"cam"}, "lines": {"lines_any", "line"},
                   "rain": {"rain"}, "bus": {"busiest", "cam", "bus"}}
        ctx = {"spots": {h["id"]: h for h in SPOTS}, "stakes": {}}
        for tid, allowed in informs.items():
            for sub, m in built.items():
                self.assertIsNotNone(m, sub)
                pick = traders.DECIDE[tid](m, store, T0, ctx)
                if sub not in allowed:
                    self.assertIsNone(pick, f"{tid} bet on {sub}")
                elif pick is not None:
                    self.assertIn(pick[0], m.option_ids())
                    self.assertRegex(pick[1], r"\d", "why must cite a number")
        tube = traders.DECIDE["tube"](built["busiest"], store, T0, ctx)
        self.assertEqual(tube[0], "oxford-circus")
        self.assertIn("Oxford Circus is at 4", tube[1])
        built["cam"].spec["line"] = 7
        self.assertEqual(traders.DECIDE["cams"](built["cam"], store, T0, ctx)[0], "under")  # 7 on a line of 7


class ExchangeTest(unittest.TestCase):
    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        crowd_history(self.raw, T0, {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38})
        for k in range(30):
            ts = T0 - (29 - k) * MIN
            self.raw.put("line_status", ts, lines({"central": 10, "victoria": 9}))
            self.raw.put("jamcams", ts, cams({"JamCams_00001.07451": 8}))
        self.raw.put("rain_now", T0, rain(0.0))
        bus_history(self.raw, T0)
        self.ex = Exchange(Store(self.raw.root, SPOTS), Clock(T0), SPOTS)

    def test_keeps_three_to_five_open_with_one_closing_soon(self):
        self.ex.tick(T0)
        open_now = self.ex.biddable(T0)
        self.assertGreaterEqual(len(open_now), 3)
        self.assertLessEqual(len(open_now), 5)
        self.assertTrue(any(m.closes_at - T0 <= 2 * MIN for m in open_now))
        for m in open_now:
            self.assertTrue(2 * MIN <= m.settles_at - T0 <= 16 * MIN, m.id)
            self.assertLessEqual(max(m.futures.probs().values()), 0.85, m.id)
            self.assertEqual(self.ex.status(m, T0), "open")
            js = self.ex.market_json(m, T0)
            self.assertAlmostEqual(sum(o["prob"] for o in js["options"]), 1.0, places=3)
            self.assertIn("settles", js["source"])

    def test_bet_moves_price_and_enforces_cap_and_status(self):
        self.ex.tick(T0)
        m = self.ex.biddable(T0)[0]
        yes, no = m.option_ids()[0], m.option_ids()[1]
        before = m.prices()[yes]
        out = self.ex.bet({"player": "p", "market": m.id, "option": yes, "stake": 100}, T0)
        self.assertGreater(out["price"], before)
        self.assertTrue(1.05 <= out["odds"] <= 20)
        other = self.ex.bet({"player": "r", "market": m.id, "option": no, "stake": 100}, T0)
        self.assertGreaterEqual(other["odds"], 1.05)
        self.ex.bet({"player": "p", "market": m.id, "option": yes, "stake": 150}, T0)
        with self.assertRaisesRegex(PermissionError, "position cap"):
            self.ex.bet({"player": "p", "market": m.id, "option": yes, "stake": 1}, T0)
        with self.assertRaisesRegex(PermissionError, "market closed"):
            self.ex.bet({"player": "q", "market": m.id, "option": yes, "stake": 10}, m.closes_at)

    def test_stale_feed_halts(self):
        self.ex.tick(T0)
        m = self.ex.biddable(T0)[0]
        later = T0 + 7 * MIN
        if later < m.closes_at:
            self.assertEqual(self.ex.status(m, later), "halted")
        self.assertEqual(self.ex.halt_reason(m, later), "stale feed")

    def test_jump_halts_and_two_clean_ticks_resume(self):
        store = self.raw.store(T0)
        m = mk.build_busiest("b", store, SPOTS, T0, T0 + 9 * MIN, set())
        hit = m.hotspots[-1]
        base = {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38}
        jumped = dict(base, **{hit: 0.9})
        self.raw.put("crowding", T0 + MIN, crowding(base))
        self.raw.put("crowding", T0 + 2 * MIN, crowding(jumped))
        mk.observe(m, self.raw.store(T0 + 2 * MIN), T0 + 2 * MIN)
        self.assertTrue(m.halt_reason.startswith(f"jump: {m.spec['names'][hit]}"))
        self.raw.put("crowding", T0 + 3 * MIN, crowding(jumped))
        mk.observe(m, self.raw.store(T0 + 3 * MIN), T0 + 3 * MIN)
        self.assertIsNotNone(m.halt_reason)
        self.raw.put("crowding", T0 + 4 * MIN, crowding(jumped))
        mk.observe(m, self.raw.store(T0 + 4 * MIN), T0 + 4 * MIN)
        self.assertIsNone(m.halt_reason)

    def test_void_refunds_traders(self):
        self.ex.tick(T0)
        m = next(m for m in self.ex.biddable(T0) if m.ai_bets and m.sub in ("bus", "level", "busiest", "cam")
                 and m.settles_at > T0 + 3 * MIN)  # no reading within 2 min of T
        res = self.ex.settle(m, m.settles_at + 4 * MIN)
        self.assertTrue(res["void"])
        self.assertIsNone(res["winner"])
        self.assertTrue(all(r["won"] is None for r in res["aiResults"]))
        for b in m.ai_bets:
            self.assertEqual(self.ex.record[b["trader"]], {"won": 0, "lost": 0, "points": 1000})


class StrikeTest(unittest.TestCase):
    def test_line_sits_at_the_median_with_the_closest_to_even_split(self):
        self.assertEqual(pricing.choose_line([1, 2, 3, 4, 5, 6]), 3)  # 4,5,6 over: 50/50
        self.assertEqual(pricing.choose_line([5] * 35 + [6] * 15), 5)  # 30% over beats 100% over at 4
        self.assertEqual(pricing.choose_line([41.2, 41.7, 42.3, 42.9], 0.5), 42)
        self.assertEqual(pricing.choose_line([1, 1, 2, 2]), 1)

    def test_bus_and_cam_markets_use_the_futures_median_strike(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        bus_history(raw, T0)
        hot = next(h for h in SPOTS if h["id"] == "waterloo")
        m = mk.build_bus("bus-x", raw.store(T0), hot, T0, T0 + 8 * MIN)
        finals = m.futures.sim.finals()
        self.assertEqual(m.spec["line"], pricing.choose_line(finals))
        self.assertIn(f"Over/under {m.spec['line']} buses", m.question)
        self.assertLessEqual(max(m.futures.probs().values()), 0.75)

    def test_busiest_asks_a_head_to_head_when_one_station_leads_by_a_mile(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        crowd_history(raw, T0, {"oxford-circus": 0.90, "kings-cross": 0.39, "waterloo": 0.385})
        m = mk.build_busiest("b", raw.store(T0), SPOTS, T0, T0 + 10 * MIN, set())
        self.assertEqual(set(m.hotspots), {"kings-cross", "waterloo"})
        self.assertIn("Busier at", m.question)


class EightyFiveRuleTest(unittest.TestCase):
    def test_never_lists_a_market_whose_favourite_is_above_85_percent(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        for k in range(30):  # a dead-flat city: every candidate is lopsided
            ts = T0 - (29 - k) * MIN
            raw.put("line_status", ts, lines({"central": 10, "victoria": 10}))
            raw.put("jamcams", ts, cams({"JamCams_00001.07451": 8}))
        raw.put("rain_now", T0, rain(0.0))
        ex = Exchange(Store(raw.root, SPOTS), Clock(T0), SPOTS)
        ex.tick(T0)
        self.assertGreater(ex.rejected, 0)
        for m in ex.markets.values():
            self.assertLessEqual(max(m.futures.probs().values()), 0.85, m.id)


if __name__ == "__main__":
    unittest.main()
