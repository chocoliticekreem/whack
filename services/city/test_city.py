"""Offline tests: python3 -m unittest discover -s services/city"""
from __future__ import annotations

import json
import math
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import markets as mk
import pricing
import traders
from data import Store, load_hotspots
from exchange import Clock, Exchange, round_for

T0 = int(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60_000
SPOTS = load_hotspots()
BY_ID = {h["id"]: h for h in SPOTS}
NAPTANS = {h["id"]: h["naptan"] for h in SPOTS}
OXFORD = BY_ID["oxford-circus"]
CITY = {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38, "piccadilly": 0.10, "bank": 0.05}


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def crowding(vals: dict[str, float]) -> list[dict]:
    return [{"naptan": NAPTANS[h], "name": h, "dataAvailable": True, "percentageOfBaseline": v, "timeUtc": "x"}
            for h, v in vals.items()]


def rain(mm: float) -> dict:
    return {"current": {"time": "2026-11-07T15:00", "precipitation": mm}, "minutely_15": []}


def buses(counts: dict[str, int]) -> list[dict]:
    rows = []
    for spot, n in counts.items():
        rows += [{"naptanId": BY_ID[spot]["busStops"][0], "timeToStation": 60}] * n
    return rows


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


def crowd_history(raw: Raw, until: int, vals: dict[str, float], noise: float = 0.02) -> None:
    rng = random.Random(1)
    for k in range(24):
        ts = until - (23 - k) * 5 * MIN
        raw.put("crowding", ts, crowding({h: v * (1 + rng.uniform(-noise, noise)) for h, v in vals.items()}))


def city(raw: Raw, until: int) -> None:
    """Crowding every 5 min, bus and rain up to `until`, so later rounds open too (a Store never reads past now)."""
    crowd_history(raw, T0, CITY)
    rng = random.Random(2)
    for ts in range(T0 + 5 * MIN, until + 1, 5 * MIN):
        raw.put("crowding", ts, crowding({h: v * (1 + rng.uniform(-0.02, 0.02)) for h, v in CITY.items()}))
    for ts in range(T0 - 30 * MIN, until + 1, MIN):
        raw.put("bus", ts, buses({"kings-cross": 3, "waterloo": 3}))
    raw.put("rain_now", T0, rain(0.0))


def oxford_station(sigma: float) -> tuple[str, str, float, float, float]:
    return "oxford-circus", OXFORD["naptan"], math.log(0.40), 0.0, sigma


def build_test_busy(mid, store, spots, now, settles_at):
    """A whole new series in a dozen lines: a wider Oxford Circus ladder that reuses the oxford-busy settler."""
    sim = pricing.LevelSim(oxford_station(0.02), now, 5, random.Random(mk.seed_of(mid)))
    sim.edges = pricing.place_pct_brackets(sim.finals(), mk.BUSY_WIDTHS)
    return mk.Market(mid, "test-busy", "bracket", "Test ladder", ["oxford-circus"], now, settles_at,
                     mk.bracket_options(mk.pct_labels(sim.edges), OXFORD), sim, "TfL Crowding API · test", "rule",
                     {"naptan": OXFORD["naptan"], "edges": sim.edges, "name": "Oxford Circus",
                      "sigmas": {OXFORD["naptan"]: 0.02}, "stations": {"oxford-circus": OXFORD["naptan"]},
                      "names": {"oxford-circus": "Oxford Circus"}})


def build_hopeless(mid, store, spots, now, settles_at):
    """Every future lands in the same bracket, so no placement can bring the favourite under 85%."""
    sim = pricing.LevelSim(oxford_station(1e-9), now, 5, random.Random(1))
    sim.edges = [10.0, 20.0, 30.0]
    return mk.Market(mid, "test-hopeless", "bracket", "Hopeless", ["oxford-circus"], now, settles_at,
                     mk.bracket_options(mk.pct_labels(sim.edges), OXFORD), sim, "src", "rule", {})


class TestSeries:
    """Registers series for one test and removes them afterwards."""

    def __init__(self, case: unittest.TestCase, *series: mk.Series):
        for s in series:
            mk.register(s)
            case.addCleanup(mk.SERIES.pop, s.id)


class OddsAndPricesTest(unittest.TestCase):
    def test_odds_formula(self):
        self.assertEqual(pricing.odds_of(0.5), 1.9)
        self.assertEqual(pricing.odds_of(0.3), 3.2)
        self.assertEqual(pricing.odds_of(0.99), 1.05)
        self.assertEqual(pricing.odds_of(0.01), 20.0)

    def test_bracket_futures_prices_sum_to_one(self):
        sim = pricing.LevelSim(oxford_station(0.01), T0, 6, random.Random(3))
        sim.edges = [39, 40, 41]
        probs = pricing.Futures(sim, ["b0", "b1", "b2", "b3"]).probs()
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
    def futures(self) -> pricing.Futures:
        sim = pricing.LevelSim(oxford_station(0.01), T0, 5, random.Random(1))
        sim.edges = [39, 40, 41]
        return pricing.Futures(sim, ["b0", "b1", "b2", "b3"])

    def test_never_rules_out_more_than_sixty_percent(self):
        fut = self.futures()
        fut.observe(T0 + 2 * MIN, {OXFORD["naptan"]: 0.9})  # contradicts all 50
        self.assertEqual(fut.ruled_out, 30)
        fut.observe(T0 + 3 * MIN, {OXFORD["naptan"]: 0.95})
        self.assertEqual(fut.ruled_out, 30)
        self.assertEqual(sum(fut.alive), 20)

    def test_consistent_observation_rules_out_nothing(self):
        fut = self.futures()
        fut.observe(T0, {OXFORD["naptan"]: 0.40})
        self.assertEqual(fut.ruled_out, 0)


class BracketPlacementTest(unittest.TestCase):
    def test_ladder_has_round_equal_edges_spread_mass_and_a_middle_favourite(self):
        rng = random.Random(7)
        for centre, sd in ((41.3, 1.5), (37.8, 0.6), (55.0, 4.0), (12.2, 1.0)):
            finals = [rng.gauss(centre, sd) for _ in range(pricing.N_FUTURES)]
            edges = pricing.place_pct_brackets(finals, mk.BUSY_WIDTHS)
            self.assertEqual(len(edges), 3)
            self.assertAlmostEqual(edges[1] - edges[0], edges[2] - edges[1])
            for e in edges:
                self.assertAlmostEqual(e * 2, round(e * 2), msg=f"{edges} not on whole or half points")
            probs = pricing.bracket_probs(finals, edges)
            self.assertLessEqual(max(probs), 0.55, (centre, sd, edges, probs))
            self.assertGreaterEqual(min(probs), pricing.FLOOR_BRACKET, (centre, sd, edges, probs))
            self.assertNotIn(probs.index(max(probs)), (0, 3), f"favourite should be a middle bracket: {probs}")

    def test_edges_are_lower_inclusive_and_the_rule_says_so(self):
        self.assertEqual(pricing.bracket_of(39.99, [40, 44, 48]), "b0")
        self.assertEqual(pricing.bracket_of(40.0, [40, 44, 48]), "b1")
        self.assertEqual(pricing.bracket_of(48.0, [40, 44, 48]), "b3")
        self.assertEqual(mk.pct_labels([40, 44, 48]), ["Under 40%", "40–44%", "44–48%", "Over 48%"])
        rule = mk.pct_rule_edges([40, 44, 48])
        self.assertIn("Under 40% = below 40%", rule)
        self.assertIn("40–44% = 40% or more and below 44%", rule)
        self.assertIn("Over 48% = 48% or more", rule)


class SettlementTest(unittest.TestCase):
    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        crowd_history(self.raw, T0, CITY)
        self.settles = T0 + 5 * MIN
        self.m = mk.build_oxford_busy("oxford-busy-test", self.raw.store(T0), SPOTS, T0, self.settles)

    def test_oxford_busy_is_a_four_bracket_ladder_with_its_edges_in_the_rule(self):
        m = self.m
        self.assertEqual((m.kind, m.sub, m.closes_at), ("bracket", "oxford-busy", self.settles - 60_000))
        self.assertEqual(m.option_ids(), ["b0", "b1", "b2", "b3"])
        self.assertIn("or more and below", m.rule)
        self.assertIn("first TfL Crowding reading published after bets close at 15:04:00", m.rule)

    def test_settles_on_the_first_reading_after_close_with_lower_inclusive_edges(self):
        m = self.m
        m.spec["edges"] = [25, 29, 33]
        m.options = mk.bracket_options(mk.pct_labels(m.spec["edges"]), OXFORD)
        self.raw.put("crowding", m.closes_at - 30_000, crowding({"oxford-circus": 0.50}))  # still betting: ignored
        self.raw.put("crowding", m.closes_at + MIN, crowding({"oxford-circus": 0.29}))  # 0.29*100 = 28.999…
        got = mk.try_settle(m, self.raw.store(self.settles), self.settles)
        self.assertEqual((got[0], got[1]), ("done", "b2"))
        self.assertIn("29.0% of usual → 29–33%", got[2])
        self.assertEqual(got[3]["fetchedAt"], m.closes_at + MIN)

    def test_waits_then_voids_without_a_reading_after_close(self):
        m = self.m
        self.assertEqual(mk.try_settle(m, self.raw.store(T0 + MIN), T0 + MIN), ("wait", 4 * MIN))
        self.assertEqual(mk.try_settle(m, self.raw.store(self.settles + MIN), self.settles + MIN)[0], "wait")
        late = m.closes_at + mk.CROWD_WAIT_MS
        self.assertEqual(mk.try_settle(m, self.raw.store(late), late)[:2], ("void", "No data — void"))

    def test_jump_halts_and_two_clean_ticks_resume(self):
        m = mk.build_oxford_busy("j", self.raw.store(T0), SPOTS, T0, T0 + 10 * MIN)
        jumped = dict(CITY, **{"oxford-circus": 0.9})
        self.raw.put("crowding", T0 + MIN, crowding(CITY))
        self.raw.put("crowding", T0 + 2 * MIN, crowding(jumped))
        mk.observe(m, self.raw.store(T0 + 2 * MIN), T0 + 2 * MIN)
        self.assertTrue(m.halt_reason.startswith("jump: Oxford Circus"))
        for k in (3, 4):
            self.assertIsNotNone(m.halt_reason)
            self.raw.put("crowding", T0 + k * MIN, crowding(jumped))
            mk.observe(m, self.raw.store(T0 + k * MIN), T0 + k * MIN)
        self.assertIsNone(m.halt_reason)


class RoundTest(unittest.TestCase):
    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        city(self.raw, T0 + 30 * MIN)

    def exchange(self) -> Exchange:
        return Exchange(Store(self.raw.root, SPOTS), Clock(T0), SPOTS)

    def test_round_for_follows_the_five_minute_wall_clock(self):
        self.assertEqual(round_for(T0), T0 + 5 * MIN)
        self.assertEqual(round_for(T0 + 4 * MIN - 1), T0 + 5 * MIN)
        self.assertEqual(round_for(T0 + 4 * MIN), T0 + 10 * MIN)  # 15:05 closes at 15:04 → 15:10 opens
        self.assertEqual(round_for(T0 - 30_000), T0 + 5 * MIN)

    def test_a_round_opens_closes_and_settles_together(self):
        TestSeries(self, mk.Series("test-busy", "crowding", build_test_busy, mk.settle_oxford_busy,
                                   close_before_ms=90_000))
        ex = self.exchange()
        ex.tick(T0)
        rnd = ex.rounds[T0 + 5 * MIN]
        self.assertEqual([ex.markets[i].sub for i in rnd["markets"]], ["oxford-busy", "test-busy"])
        for mid in rnd["markets"]:
            m = ex.markets[mid]
            self.assertEqual((m.opened_at, m.settles_at, m.settles_at % (5 * MIN)), (T0, T0 + 5 * MIN, 0))
            self.assertEqual(m.closes_at, m.settles_at - (90_000 if m.sub == "test-busy" else 60_000))
            js = ex.market_json(m, T0)
            self.assertEqual((js["series"], js["round"], js["roundSettlesAt"]), (m.sub, "15:05", T0 + 5 * MIN))
            self.assertEqual(js["status"], "open")
            self.assertAlmostEqual(sum(o["prob"] for o in js["options"]), 1.0, places=3)
        ex.tick(T0 + 4 * MIN - 1)
        self.assertNotIn(T0 + 10 * MIN, ex.rounds)
        ex.tick(T0 + 4 * MIN)
        nxt = [ex.markets[i] for i in ex.rounds[T0 + 10 * MIN]["markets"]]
        self.assertEqual(len(nxt), 2)
        self.assertTrue(all(m.opened_at == T0 + 4 * MIN and m.settles_at == T0 + 10 * MIN for m in nxt))
        cur = ex.round_json(T0 + 4 * MIN)["current"]
        self.assertEqual((cur["round"], cur["closesAt"], cur["settlesAt"]), ("15:10", T0 + 9 * MIN, T0 + 10 * MIN))
        self.assertEqual(cur["markets"], [m.id for m in nxt])

    def test_round_json_lists_the_previous_round_results_and_stats_count_rounds(self):
        ex = self.exchange()
        for t in range(T0, T0 + 12 * MIN, 12_000):
            ex.tick(t)
        out = ex.round_json(T0 + 12 * MIN)
        self.assertEqual((out["current"]["round"], out["previous"]["round"]), ("15:15", "15:10"))
        first = ex.round_json(T0 + 6 * MIN)["previous"]
        self.assertEqual(first["round"], "15:05")
        res = first["results"][0]
        self.assertEqual((res["market"], res["series"], res["status"]), ("oxford-busy-1505", "oxford-busy", "settled"))
        self.assertIn(res["winner"], ["b0", "b1", "b2", "b3"])
        self.assertIn("% of usual →", res["reading"])
        stats = ex.stats_json()
        self.assertEqual(stats["series"]["oxford-busy"]["rounds"], 3)
        self.assertEqual(stats["series"]["oxford-busy"]["settled"]["real"], 2)

    def test_only_registered_series_are_listed_and_bus_markets_are_gone(self):
        self.assertEqual(list(mk.SERIES), ["oxford-busy"])
        self.assertFalse(hasattr(mk, "build_bus"))
        ex = self.exchange()
        for t in range(T0, T0 + 20 * MIN, 12_000):
            ex.tick(t)
        self.assertEqual({m.sub for m in ex.markets.values()}, {"oxford-busy"})
        self.assertEqual(len(ex.rounds), 5)

    def test_brackets_are_re_placed_and_only_a_hopeless_series_is_dropped(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        crowd_history(raw, T0, CITY, noise=0.0)  # a dead-flat city: the quietest possible station
        TestSeries(self, mk.Series("test-hopeless", "crowding", build_hopeless, mk.settle_oxford_busy))
        ex = Exchange(Store(raw.root, SPOTS), Clock(T0), SPOTS)
        ex.tick(T0)
        rnd = ex.rounds[T0 + 5 * MIN]
        self.assertEqual(rnd["markets"], ["oxford-busy-1505"])
        self.assertLessEqual(max(ex.markets["oxford-busy-1505"].open_model.values()), 0.85)
        self.assertIn("even after re-placing", rnd["skipped"]["test-hopeless"])
        self.assertEqual(ex.rejected, 1)

    def test_a_series_skip_rule_keeps_it_out_of_the_round(self):
        TestSeries(self, mk.Series("test-busy", "crowding", build_test_busy, mk.settle_oxford_busy,
                                   skip=lambda m: "not today"))
        ex = self.exchange()
        ex.tick(T0)
        self.assertEqual(ex.rounds[T0 + 5 * MIN]["skipped"], {"test-busy": "not today"})

    def test_bet_moves_price_and_enforces_cap_and_status(self):
        ex = self.exchange()
        ex.tick(T0)
        m = ex.biddable(T0)[0]
        a, b = m.option_ids()[1], m.option_ids()[2]
        before = m.prices()[a]
        out = ex.bet({"player": "p", "market": m.id, "option": a, "stake": 100}, T0)
        self.assertGreater(out["price"], before)
        self.assertTrue(1.05 <= out["odds"] <= 20)
        ex.bet({"player": "r", "market": m.id, "option": b, "stake": 100}, T0)
        ex.bet({"player": "p", "market": m.id, "option": a, "stake": 150}, T0)
        with self.assertRaisesRegex(PermissionError, "position cap"):
            ex.bet({"player": "p", "market": m.id, "option": a, "stake": 1}, T0)
        with self.assertRaisesRegex(PermissionError, "market closed"):
            ex.bet({"player": "q", "market": m.id, "option": a, "stake": 10}, m.closes_at)

    def test_stale_feed_halts(self):
        ex = self.exchange()
        ex.tick(T0)
        self.assertEqual(ex.halt_reason(ex.biddable(T0)[0], T0 + 40 * MIN), "stale feed")

    def test_void_refunds_traders(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        crowd_history(raw, T0, CITY)
        ex = Exchange(Store(raw.root, SPOTS), Clock(T0), SPOTS)
        ex.tick(T0)
        m = ex.biddable(T0)[0]
        self.assertTrue(m.ai_bets)
        res = ex.settle(m, m.closes_at + mk.CROWD_WAIT_MS)
        self.assertTrue(res["void"])
        self.assertIsNone(res["winner"])
        self.assertTrue(all(r["won"] is None for r in res["aiResults"]))
        for bet in m.ai_bets:
            self.assertEqual(ex.record[bet["trader"]], {"won": 0, "lost": 0, "points": 1000})


class TraderTest(unittest.TestCase):
    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        crowd_history(self.raw, T0, {"oxford-circus": 0.40, "kings-cross": 0.30})
        self.raw.put("crowding", T0 + 1, crowding({"oxford-circus": 0.42}))

    def test_traders_bet_only_on_the_series_their_feed_informs(self):
        self.raw.put("rain_now", T0, rain(0.6))
        TestSeries(self, mk.Series("test-busy", "crowding", build_test_busy, mk.settle_oxford_busy))
        store = self.raw.store(T0 + 1)
        busy = mk.build_oxford_busy("o", store, SPOTS, T0 + 1, T0 + 5 * MIN)
        other = build_test_busy("t", store, SPOTS, T0 + 1, T0 + 5 * MIN)
        ctx = {"spots": BY_ID, "stakes": {o: 0.0 for o in busy.option_ids()}}
        for tid in ("tube", "rain"):
            pick = traders.decide(tid, busy, store, T0 + 1, ctx)
            self.assertIn(pick[0], busy.option_ids(), tid)
            self.assertRegex(pick[1], r"\d", "why must cite a number")
            self.assertIsNone(traders.decide(tid, other, store, T0 + 1, ctx), f"{tid} bet on an unmapped series")
        for tid in ("cams", "lines", "bus"):
            self.assertIsNone(traders.decide(tid, busy, store, T0 + 1, ctx))
        for m in (busy, other):
            pick = traders.decide("contrarian", m, store, T0 + 1, ctx)
            self.assertNotEqual(pick[0], max(m.prices(), key=lambda o: m.prices()[o]))

    def test_traders_with_nothing_sit_out_and_keep_the_reason_they_decided_on(self):
        self.raw.put("rain_now", T0, rain(0.0))
        ex = Exchange(Store(self.raw.root, SPOTS), Clock(T0 + 1), SPOTS)
        ex.tick(T0 + 1)
        why = {t["id"]: t["why"] for t in ex.traders_json(T0 + 1)["traders"]}
        self.assertIn("Sitting out: 0 mm of rain", why["rain"])
        for tid in ("cams", "lines", "bus"):
            self.assertIn("no series this round is informed by its feed", why[tid])
        self.assertRegex(why["tube"], r"\d")
        self.raw.put("rain_now", T0 + MIN, rain(2.0))
        ex.store.refresh(T0, T0 + 2 * MIN)
        later = {t["id"]: t["why"] for t in ex.traders_json(T0 + 2 * MIN)["traders"]}
        self.assertIn("0 mm of rain", later["rain"], "the reason is the one it decided on, not the latest reading")


if __name__ == "__main__":
    unittest.main()
