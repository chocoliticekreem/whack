"""Offline tests: python3 -m unittest discover -s services/city"""
from __future__ import annotations

import json
import math
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import data
import markets as mk
import pricing
import signals
import traders
from data import Store, load_hotspots
from exchange import Clock, Exchange, round_for

T0 = int(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)  # a Saturday, GMT
MIN = 60_000
SPOTS = load_hotspots()
BY_ID = {h["id"]: h for h in SPOTS}
NAPTANS = {h["id"]: h["naptan"] for h in SPOTS}
OXFORD = BY_ID["oxford-circus"]
HUBS = [h["id"] for h in SPOTS]
CITY = {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38, "piccadilly": 0.10, "bank": 0.05}
OTHERS = [f"940GZZLUX{k:02d}" for k in range(15)]  # 15 more Crowding stations: 20 in all, as in the real feed
TYPICAL = {"oxford-circus": 0.40, "kings-cross": 0.30, "waterloo": 0.40, "piccadilly": 0.10, "bank": 0.05}
TUBE = ["bakerloo", "central", "circle", "district", "hammersmith-city", "jubilee", "metropolitan", "northern",
        "piccadilly", "victoria", "waterloo-city"]


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def crowding(vals: dict[str, float], others: float | None = 0.30, n_others: int = 15) -> list[dict]:
    rows = [{"naptan": NAPTANS[h], "name": h, "dataAvailable": True, "percentageOfBaseline": v, "timeUtc": "x"}
            for h, v in vals.items()]
    if others is not None:
        rows += [{"naptan": nap, "name": nap, "dataAvailable": True, "percentageOfBaseline": others, "timeUtc": "x"}
                 for nap in OTHERS[:n_others]]
    return rows


def profile(naptan: str, typical) -> dict:
    """A crowding_profile payload; `typical` is a number or a function of the band start 'HH:MM'."""
    bands = []
    for k in range(96):
        start = f"{k // 4:02d}:{k % 4 * 15:02d}"
        v = typical(start) if callable(typical) else typical
        bands.append({"timeBand": f"{start}-xx", "percentageOfBaseLine": v})
    return {"naptan": naptan, "daysOfWeek": [{"dayOfWeek": "SAT", "timeBands": bands}]}


def lines(bad: dict[str, tuple[int, str, str]] | None = None) -> list[dict]:
    bad = bad or {}
    out = []
    for lid in TUBE:
        sev, desc, reason = bad.get(lid, (10, "Good Service", ""))
        st = {"statusSeverity": sev, "statusSeverityDescription": desc}
        if reason:
            st["reason"] = reason
        out.append({"id": lid, "name": lid.title(), "mode": "tube", "statuses": [st]})
    return out + [{"id": "weaver", "name": "Weaver", "mode": "overground", "statuses": [{"statusSeverity": 9}]}]


def aircraft(total: int) -> dict:
    a = total // 2
    return {"summary": {"holding": {"BNN": a, "LAM": total - a, "BIG": 0, "OCK": 0}, "holding_total": total}}


def rain(mm: float) -> dict:
    return {"current": {"time": "2026-11-07T15:00", "precipitation": mm}, "minutely_15": []}


def vix_payload(spawn: int, until: int, base: float = 2.3, wiggle: float = 0.3) -> dict:
    hist = [{"ts": spawn, "vix": 15.8, "delta": 0, "driver": "spawn", "driverDelta": 0}]
    ts, k = spawn + MIN, 0
    while ts <= until:
        hist.append({"ts": ts, "vix": round(base + wiggle * math.sin(k / 3), 3), "driver": "bike docks",
                     "driverDelta": 0.1})
        ts, k = ts + MIN, k + 1
    return {"vix": hist[-1]["vix"], "vix_history": hist, "vix_driver": "bike docks"}


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

    def profile(self, typical: dict | None = None) -> None:
        typical = typical or {}
        for h in SPOTS:
            self.put("crowding_profile", T0 - 3 * 3_600_000, profile(h["naptan"], typical.get(h["id"],
                                                                                          TYPICAL[h["id"]])))
        for nap in OTHERS:
            self.put("crowding_profile", T0 - 3 * 3_600_000, profile(nap, 0.30))

    def store(self, now: int) -> Store:
        s = Store(self.root, SPOTS)
        s.refresh(now - mk.HISTORY_MS, now)
        return s


def crowd_history(raw: Raw, until: int, vals: dict[str, float], noise: float = 0.02) -> None:
    rng = random.Random(1)
    for k in range(24):
        ts = until - (23 - k) * 5 * MIN
        raw.put("crowding", ts, crowding({h: v * (1 + rng.uniform(-noise, noise)) for h, v in vals.items()},
                                         0.30 * (1 + rng.uniform(-noise, noise))))


def minutely(raw: Raw, start: int, until: int, source: str, payload_of) -> None:
    for ts in range(start, until + 1, MIN):
        raw.put(source, ts, payload_of(ts))


def city(raw: Raw, until: int) -> None:
    """Everything the lineup reads up to `until` (a Store never reads past now): profile, crowding every 5 min,
    line status, aircraft and fixtures every minute, rain once."""
    raw.profile()
    crowd_history(raw, T0, CITY)
    rng = random.Random(2)
    for ts in range(T0 + 5 * MIN, until + 1, 5 * MIN):
        raw.put("crowding", ts, crowding({h: v * (1 + rng.uniform(-0.02, 0.02)) for h, v in CITY.items()}))
    minutely(raw, T0 - 3 * 3_600_000, until, "line_status", lambda ts: lines())
    minutely(raw, T0 - 3 * 3_600_000, until, "aircraft", lambda ts: aircraft(6 + (ts // MIN) % 5))
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
            case.addCleanup(mk.SERIES.pop, s.id, None)
            case.addCleanup(mk.REGISTRY.pop, s.id, None)


class CityCase(unittest.TestCase):
    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)

    def exchange(self, start: int = T0) -> Exchange:
        return Exchange(Store(self.raw.root, SPOTS), Clock(start), SPOTS)


# ---------------------------------------------------------------- pricing


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

    def test_deltas_at_pairs_each_reading_with_the_one_h_minutes_later(self):
        series = [(T0 + k * MIN, float(k)) for k in range(12)]
        self.assertEqual(pricing.deltas_at(series, 5), [5.0] * 7)
        gappy = [(T0, 1.0), (T0 + 5 * MIN + 20_000, 4.0), (T0 + 20 * MIN, 9.0)]
        self.assertEqual(pricing.deltas_at(gappy, 5), [3.0])

    def test_break_hazard_counts_good_to_bad_transitions_per_good_line_minute(self):
        good, bad = {"sev": 10}, {"sev": 9}
        snaps = [(T0 + k * MIN, {"a": good if k != 3 else bad, "b": good}) for k in range(6)]
        breaks, minutes = pricing.fit_break_hazard(snaps, ["a", "b"])
        self.assertEqual(breaks, 1)
        self.assertEqual(minutes, 9.0)  # a: Good before 4 of the 5 gaps, b: all 5

    def test_break_sim_drifts_to_yes_as_a_clean_hour_runs_down(self):
        sim = pricing.BreakSim(0.5, ["a"], T0, 60, random.Random(1))
        fut = pricing.Futures(sim, ["yes", "no"])
        before = fut.probs()["yes"]
        fut.observe(T0 + 40 * MIN, {"a": {"sev": 10}})
        self.assertGreater(fut.probs()["yes"], before)


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
            probs = pricing.bracket_probs(finals, edges)
            self.assertLessEqual(max(probs), 0.55, (centre, sd, edges, probs))
            self.assertGreaterEqual(min(probs), pricing.FLOOR_BRACKET, (centre, sd, edges, probs))
            self.assertNotIn(probs.index(max(probs)), (0, 3), f"favourite should be a middle bracket: {probs}")

    def test_edges_are_lower_inclusive_and_the_rule_says_so(self):
        self.assertEqual(pricing.bracket_of(39.99, [40, 44, 48]), "b0")
        self.assertEqual(pricing.bracket_of(40.0, [40, 44, 48]), "b1")
        self.assertEqual(mk.pct_labels([40, 44, 48]), ["Under 40%", "40–44%", "44–48%", "Over 48%"])
        self.assertIn("Over 48% = 48% or more", mk.pct_rule_edges([40, 44, 48]))


# ---------------------------------------------------------------- data


class DataTest(CityCase):
    def test_profile_gives_the_typical_value_for_the_weekday_and_band(self):
        self.raw.profile({"waterloo": lambda band: 0.5 if band == "15:15" else 0.4})
        store = self.raw.store(T0)
        self.assertEqual(store.typical(NAPTANS["waterloo"], T0 + 16 * MIN), 0.5)
        self.assertEqual(store.typical(NAPTANS["waterloo"], T0 + 14 * MIN), 0.4)
        sunday = T0 + 24 * 3_600_000
        self.assertIsNone(store.typical(NAPTANS["waterloo"], sunday), "only SAT is in the profile")

    def test_line_status_keeps_the_reason_and_aircraft_the_stacks(self):
        self.raw.put("line_status", T0, lines({"central": (9, "Minor Delays", "Central Line:  train  cancellations")}))
        self.raw.put("aircraft", T0, aircraft(5))
        store = self.raw.store(T0)
        self.assertEqual(store.latest("line_status", T0)[1]["central"]["reason"], "Central Line: train cancellations")
        self.assertEqual(store.latest("aircraft", T0)[1], {"total": 5, "stacks": {"BNN": 2, "LAM": 3, "BIG": 0,
                                                                                  "OCK": 0}})

    def test_vix_history_is_ingested_with_its_spawn_time(self):
        store = self.raw.store(T0)
        store.ingest_vix(vix_payload(T0 - 70 * MIN, T0))
        ts, snap = store.latest("vix", T0)
        self.assertEqual((ts, snap["spawnTs"], snap["driver"]), (T0, T0 - 70 * MIN, "bike docks"))

    def test_fetch_vix_is_none_when_the_engine_is_unreachable(self):
        self.assertIsNone(data.fetch_vix("http://127.0.0.1:1", timeout=0.5))


# ---------------------------------------------------------------- the series


class HubSurgeTest(CityCase):
    def setUp(self):
        super().setUp()
        self.raw.profile()
        crowd_history(self.raw, T0, CITY)
        self.m = mk.build_hub_surge("hub_surge-1505", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN)

    def settle_on(self, vals: dict[str, float]):
        self.raw.put("crowding", self.m.closes_at + MIN, crowding(vals))
        now = self.m.settles_at + MIN
        return mk.try_settle(self.m, self.raw.store(now), now)

    def test_is_a_five_way_race_in_the_fixed_hub_order_with_vs_usual_frozen_at_open(self):
        m = self.m
        self.assertEqual((m.kind, m.question), ("versus", "Which hub surges most by 15:05?"))
        self.assertEqual(m.option_ids(), HUBS)
        last = self.raw.store(T0).latest("crowding", T0)[1]
        self.assertAlmostEqual(m.spec["open"]["kings-cross"], last[NAPTANS["kings-cross"]] / 0.30, places=3)
        self.assertIn("first TfL Crowding reading published after bets close", m.rule)
        self.assertAlmostEqual(sum(m.open_model.values()), 1.0)
        self.assertLess(max(m.open_model.values()), 0.7)

    def test_settles_on_the_biggest_rise_in_vs_usual(self):
        vals = {h: m0 * TYPICAL[h] for h, m0 in self.m.spec["open"].items()}
        vals["waterloo"] *= 1.10
        vals["bank"] *= 1.05
        got = self.settle_on(vals)
        self.assertEqual((got[0], got[1]), ("done", "waterloo"))
        self.assertIn("Waterloo rose most", got[2])

    def test_a_tie_goes_to_the_first_in_the_fixed_order_and_no_move_is_void(self):
        vals = {h: m0 * TYPICAL[h] for h, m0 in self.m.spec["open"].items()}
        self.assertEqual(self.settle_on(vals)[:2], ("void", "No hub moved since open — void, stakes refunded"))
        vals = {h: (m0 + 0.05) * TYPICAL[h] for h, m0 in self.m.spec["open"].items()}
        self.assertEqual(self.settle_on(vals)[1], "kings-cross")  # all +0.05: King's Cross's reading is first


class CityPulseTest(CityCase):
    def setUp(self):
        super().setUp()
        self.raw.profile()
        crowd_history(self.raw, T0, CITY)
        self.store = self.raw.store(T0)
        self.m = mk.build_city_pulse("city_pulse-1505", self.store, SPOTS, T0, T0 + 5 * MIN)

    def test_line_is_the_pulse_at_open_plus_the_median_change_on_a_half_point(self):
        hist = mk.pulse_series(self.store, T0 - mk.HISTORY_MS, T0)
        med = sorted(pricing.deltas_at(hist, 5, mk.CROWD_TOL_MS))[len(hist) // 2 - 1:len(hist) // 2 + 1]
        self.assertAlmostEqual(self.m.spec["open"], hist[-1][1], places=2)
        self.assertEqual(self.m.spec["line"] % 1, 0.5)
        self.assertLessEqual(abs(self.m.spec["line"] - hist[-1][1]), max(abs(x) for x in med) + 0.51)
        self.assertEqual(self.m.option_ids(), ["over", "under"])
        self.assertIn(f"over or under {self.m.spec['line']:.1f}?", self.m.question)
        self.assertLessEqual(max(self.m.open_model.values()), 0.8)

    def test_exactly_on_the_line_is_under_and_too_few_stations_is_void(self):
        m = self.m
        target = m.spec["line"] / 100
        self.raw.put("crowding", m.closes_at + MIN, crowding({h: target for h in CITY}, target))
        now = m.settles_at + MIN
        got = mk.try_settle(m, self.raw.store(now), now)
        self.assertEqual((got[0], got[1]), ("done", "under"))
        m2 = mk.build_city_pulse("city_pulse-1510", self.store, SPOTS, T0, T0 + 10 * MIN)
        self.raw.put("crowding", m2.closes_at + MIN, crowding(CITY, 0.3, n_others=10))
        now = m2.settles_at + MIN
        self.assertEqual(mk.try_settle(m2, self.raw.store(now), now)[0], "void")


class VixAndFallbackTest(CityCase):
    def setUp(self):
        super().setUp()
        city(self.raw, T0 + 20 * MIN)

    def test_london_vix_prices_from_post_warm_up_moves_and_settles_on_the_last_frame(self):
        store = self.raw.store(T0)
        store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, T0))
        m = mk.build_london_vix("london_vix-1505", store, SPOTS, T0, T0 + 5 * MIN)
        self.assertEqual((m.kind, m.feed, m.spec["line"]), ("overunder", "vix", round(store.latest("vix", T0)[1]["vix"],
                                                                                         2)))
        self.assertIn("higher or lower than", m.question)
        store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, T0 + 5 * MIN))
        store.add("vix", m.settles_at - 20_000, dict(store.latest("vix", m.settles_at)[1], vix=m.spec["line"]))
        self.assertEqual(mk.try_settle(m, store, m.settles_at)[0], "wait", "waits for a late frame")
        got = mk.try_settle(m, store, m.settles_at + 40_000)
        self.assertEqual(got[:2], ("done", "under"), "exactly on the line counts as Lower")

    def test_vix_voids_on_a_respawn_and_when_the_engine_goes_quiet(self):
        store = self.raw.store(T0)
        store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, T0))
        m = mk.build_london_vix("london_vix-1505", store, SPOTS, T0, T0 + 5 * MIN)
        late = m.settles_at + mk.GRACE_MS
        self.assertEqual(mk.try_settle(m, store, late)[:2], ("void", "No data — void"))
        store.ingest_vix(vix_payload(T0 + 2 * MIN, T0 + 5 * MIN))
        self.assertIn("re-spawned", mk.try_settle(m, store, m.settles_at + 40_000)[1])

    def test_vix_warming_up_or_down_lists_heathrow_stack_in_its_place(self):
        ex = self.exchange()
        ex.store.refresh(T0 - mk.HISTORY_MS, T0)
        ex.store.ingest_vix(vix_payload(T0 - 30 * MIN, T0))
        self.assertIn("warming up", mk.vix_state(ex.store, T0)["reason"])
        ex.tick(T0)
        rnd = ex.rounds[T0 + 5 * MIN]
        subs = [ex.markets[i].sub for i in rnd["markets"]]
        self.assertEqual(subs, ["hub_surge", "city_pulse", "heathrow_stack", "hub_hour"])
        self.assertIn("london_vix", rnd["skipped"])
        stack = ex.markets[rnd["markets"][2]]
        self.assertIn("warming up", stack.rule)

    def test_a_live_vix_keeps_heathrow_stack_on_the_bench(self):
        ex = self.exchange()
        ex.store.refresh(T0 - mk.HISTORY_MS, T0)
        ex.store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, T0))
        ex.tick(T0)
        rnd = ex.rounds[T0 + 5 * MIN]
        self.assertEqual([ex.markets[i].sub for i in rnd["markets"]],
                         ["hub_surge", "city_pulse", "london_vix", "hub_hour"])
        self.assertIn("London VIX is live", rnd["skipped"]["heathrow_stack"])

    def test_heathrow_stack_line_is_the_count_plus_a_half_and_settles_on_the_last_reading(self):
        store = self.raw.store(T0)
        m = mk.build_heathrow_stack("heathrow_stack-1505", store, SPOTS, T0, T0 + 5 * MIN)
        n0 = store.latest("aircraft", T0)[1]["total"]
        self.assertEqual(m.spec["line"], n0 + 0.5)
        self.assertEqual(m.question, f"Planes holding over Heathrow at 15:05: over or under {n0 + 0.5:g}?")
        now = m.settles_at + 40_000
        n_t = self.raw.store(now).latest("aircraft", m.settles_at)[1]["total"]
        got = mk.try_settle(m, self.raw.store(now), now)
        self.assertEqual(got[1], "over" if n_t > n0 else "under")
        self.assertIn("holding", got[2])

    def test_heathrow_stack_voids_without_a_reading_in_the_last_two_minutes(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        minutely(raw, T0 - 3 * 3_600_000, T0, "aircraft", lambda ts: aircraft(4 + (ts // MIN) % 3))
        m = mk.build_heathrow_stack("h", raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
        late = m.settles_at + mk.GRACE_MS
        self.assertEqual(mk.try_settle(m, raw.store(late), late)[0], "void")


class HeadlineTest(CityCase):
    def test_hub_hour_opens_with_the_round_spanning_00_and_clean_sheet_with_the_one_spanning_30(self):
        start = T0 + 50 * MIN  # 15:50
        city(self.raw, T0 + 130 * MIN)
        ex = self.exchange(start)
        for t in range(start, T0 + 125 * MIN, 12_000):
            ex.tick(t)
        heads = sorted((m.sub, m.id, m.opened_at, m.settles_at) for m in ex.markets.values()
                       if m.sub in ("hub_hour", "clean_sheet"))
        self.assertEqual([(s, i) for s, i, _, _ in heads],
                         [("clean_sheet", "clean_sheet-1730"), ("hub_hour", "hub_hour-1700"),
                          ("hub_hour", "hub_hour-1800")])
        by_id = {i: (o, e) for _, i, o, e in heads}
        self.assertEqual(by_id["hub_hour-1700"], (T0 + 59 * MIN, T0 + 120 * MIN))
        self.assertEqual(by_id["clean_sheet-1730"], (T0 + 89 * MIN, T0 + 150 * MIN))
        hub = ex.markets["hub_hour-1700"]
        self.assertIsNotNone(hub.result)
        self.assertIn("of a usual Saturday", hub.result["reading"])
        self.assertLessEqual(max(hub.open_model.values()), mk.HEADLINE_CAP + 0.01)
        for at, rnd in ex.rounds.items():
            subs = [ex.markets[i].sub for i in rnd["markets"]]
            self.assertEqual(subs.count("hub_hour"), int(at % mk.HOUR_MS == 5 * MIN), local_time_of(at))
        cur = ex.round_json(T0 + 100 * MIN)["current"]
        self.assertIn("clean_sheet-1730", cur["headlines"])
        self.assertNotIn("clean_sheet-1730", cur["markets"])

    def clean_sheet(self, bad_at: int | None = None, gap: tuple[int, int] | None = None) -> mk.Market:
        def board(ts):
            if bad_at is not None and ts >= bad_at:
                return lines({"central": (9, "Minor Delays", "Central Line: Minor delays due to train cancellations.")})
            return lines({"waterloo-city": (4, "Planned Closure", "Waterloo & City line: weekdays only.")})
        for ts in range(T0 - 3 * 3_600_000, T0 + 95 * MIN, MIN):
            if gap and gap[0] <= ts < gap[1]:
                continue
            self.raw.put("line_status", ts, board(ts))
        at = T0 + 35 * MIN
        now = at - 6 * MIN
        return mk.build_clean_sheet("x", self.raw.store(now), SPOTS, now, at)

    def test_clean_sheet_question_good_lines_and_a_30_to_70_price(self):
        m = self.clean_sheet()
        self.assertEqual(m.id, "clean_sheet-1630")
        self.assertEqual(m.question,
                         "Clean sheet: does every Tube line on Good Service at 15:30 stay Good until 16:30?")
        self.assertEqual(len(m.spec["good"]), 10)
        self.assertNotIn("waterloo-city", m.spec["good"])
        self.assertTrue(0.3 - 0.03 <= max(m.open_model.values()) <= 0.7 + 0.03, m.open_model)

    def test_clean_sheet_resolves_no_the_moment_a_good_line_breaks(self):
        m = self.clean_sheet(bad_at=T0 + 42 * MIN)
        now = T0 + 43 * MIN
        got = mk.try_settle(m, self.raw.store(now), now)
        self.assertEqual(got[:2], ("done", "no"))
        self.assertIn("Central broke the clean sheet at 15:42", got[2])
        self.assertEqual(mk.try_settle(m, self.raw.store(T0 + 40 * MIN), T0 + 40 * MIN)[0], "wait")

    def test_clean_sheet_yes_at_the_bell_and_void_on_missing_readings(self):
        m = self.clean_sheet()
        now = m.settles_at + 40_000
        self.assertEqual(mk.try_settle(m, self.raw.store(now), now)[:2][1], "yes")
        raw2 = Raw()
        self.addCleanup(raw2.dir.cleanup)
        self.raw = raw2
        m2 = self.clean_sheet(gap=(T0 + 40 * MIN, T0 + 47 * MIN))
        self.assertIn("missing for", mk.try_settle(m2, self.raw.store(now), now)[1])


def local_time_of(ms: int) -> str:
    return data.local_time(ms)


# ---------------------------------------------------------------- rounds, betting, the lineup


class RoundTest(CityCase):
    def setUp(self):
        super().setUp()
        city(self.raw, T0 + 30 * MIN)

    def test_round_for_follows_the_five_minute_wall_clock(self):
        self.assertEqual(round_for(T0), T0 + 5 * MIN)
        self.assertEqual(round_for(T0 + 4 * MIN - 1), T0 + 5 * MIN)
        self.assertEqual(round_for(T0 + 4 * MIN), T0 + 10 * MIN)
        self.assertEqual(round_for(T0 - 30_000), T0 + 5 * MIN)

    def test_the_lineup_and_oxford_busy_retired_but_registered(self):
        self.assertEqual(list(mk.SERIES), ["hub_surge", "city_pulse", "london_vix", "heathrow_stack", "hub_hour",
                                           "clean_sheet"])
        self.assertIn("oxford-busy", mk.REGISTRY)
        self.assertFalse(mk.REGISTRY["oxford-busy"].enabled)
        ex = self.exchange()
        for t in range(T0, T0 + 20 * MIN, 12_000):
            ex.tick(t)
        self.assertNotIn("oxford-busy", {m.sub for m in ex.markets.values()})
        for at, rnd in ex.rounds.items():
            subs = [ex.markets[i].sub for i in rnd["markets"] if ex.markets[i].settles_at == at]
            self.assertEqual(subs, ["hub_surge", "city_pulse", "heathrow_stack"], local_time_of(at))

    def test_a_retired_series_still_builds_and_settles(self):
        m = mk.build_oxford_busy("oxford-busy-test", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
        self.assertEqual((m.kind, m.closes_at), ("bracket", T0 + 4 * MIN))
        now = m.closes_at + mk.CROWD_WAIT_MS
        self.assertEqual(mk.try_settle(m, self.raw.store(now), now)[0], "done")

    def test_a_round_opens_closes_and_settles_together(self):
        TestSeries(self, mk.Series("test-busy", "crowding", build_test_busy, mk.settle_oxford_busy,
                                   close_before_ms=90_000))
        ex = self.exchange()
        ex.tick(T0)
        rnd = ex.rounds[T0 + 5 * MIN]
        self.assertEqual([ex.markets[i].sub for i in rnd["markets"]],
                         ["hub_surge", "city_pulse", "heathrow_stack", "hub_hour", "test-busy"])
        for mid in [i for i in rnd["markets"] if ex.markets[i].sub != "hub_hour"]:
            m = ex.markets[mid]
            self.assertEqual((m.opened_at, m.settles_at), (T0, T0 + 5 * MIN))
            self.assertEqual(m.closes_at, m.settles_at - (90_000 if m.sub == "test-busy" else 60_000))
            js = ex.market_json(m, T0)
            self.assertEqual((js["series"], js["round"], js["status"]), (m.sub, "15:05", "open"))
            self.assertAlmostEqual(sum(o["prob"] for o in js["options"]), 1.0, places=3)
        ex.tick(T0 + 4 * MIN)
        cur = ex.round_json(T0 + 4 * MIN)["current"]
        self.assertEqual((cur["round"], cur["closesAt"], cur["settlesAt"]), ("15:10", T0 + 9 * MIN, T0 + 10 * MIN))

    def test_round_json_lists_the_previous_round_results_and_stats_count_rounds(self):
        ex = self.exchange()
        for t in range(T0, T0 + 12 * MIN, 12_000):
            ex.tick(t)
        out = ex.round_json(T0 + 12 * MIN)
        self.assertEqual((out["current"]["round"], out["previous"]["round"]), ("15:15", "15:10"))
        first = ex.round_json(T0 + 6 * MIN)["previous"]
        res = {r["series"]: r for r in first["results"]}
        self.assertEqual(res["hub_surge"]["status"], "settled")
        self.assertIn(res["hub_surge"]["winner"], HUBS)
        self.assertIn(res["heathrow_stack"]["winner"], ["over", "under"])
        stats = ex.stats_json()
        self.assertEqual(stats["series"]["hub_surge"]["rounds"], 3)
        self.assertEqual(stats["series"]["hub_surge"]["settled"]["real"], 2)

    def test_brackets_are_re_placed_and_only_a_hopeless_series_is_dropped(self):
        TestSeries(self, mk.Series("test-hopeless", "crowding", build_hopeless, mk.settle_oxford_busy))
        ex = self.exchange()
        ex.tick(T0)
        rnd = ex.rounds[T0 + 5 * MIN]
        self.assertIn("even after re-placing", rnd["skipped"]["test-hopeless"])
        self.assertEqual(ex.rejected, 1)

    def test_bet_moves_price_and_enforces_cap_and_status(self):
        ex = self.exchange()
        for p in ("p", "q", "r"):
            ex.ledger.open_account(p)
        ex.tick(T0)
        m = ex.biddable(T0)[0]
        a, b = m.option_ids()[1], m.option_ids()[2]
        before = m.prices()[a]
        out = ex.bet({"player": "p", "market": m.id, "option": a, "stake": 100}, T0)
        self.assertGreater(out["price"], before)
        ex.bet({"player": "r", "market": m.id, "option": b, "stake": 100}, T0)
        ex.bet({"player": "p", "market": m.id, "option": a, "stake": 150}, T0)
        with self.assertRaisesRegex(PermissionError, "position cap"):
            ex.bet({"player": "p", "market": m.id, "option": a, "stake": 10}, T0)
        with self.assertRaisesRegex(PermissionError, "market closed"):
            ex.bet({"player": "q", "market": m.id, "option": a, "stake": 10}, m.closes_at)

    def test_stale_feed_halts(self):
        ex = self.exchange()
        ex.tick(T0)
        self.assertEqual(ex.halt_reason(ex.biddable(T0)[0], T0 + 40 * MIN), "stale feed")

    def test_void_refunds_traders(self):
        raw = Raw()
        self.addCleanup(raw.dir.cleanup)
        raw.profile()
        crowd_history(raw, T0, CITY)
        ex = Exchange(Store(raw.root, SPOTS), Clock(T0), SPOTS)
        ex.tick(T0)
        m = ex.biddable(T0)[0]
        self.assertEqual(m.sub, "hub_surge")
        self.assertTrue(m.ai_bets)
        res = ex.settle(m, m.closes_at + mk.CROWD_WAIT_MS)
        self.assertTrue(res["void"])
        for bet in m.ai_bets:
            self.assertEqual(ex.record[bet["trader"]], {"won": 0, "lost": 0, "points": 1000})


# ---------------------------------------------------------------- signals


class SignalsTest(CityCase):
    def setUp(self):
        super().setUp()
        city(self.raw, T0 + 10 * MIN)
        self.raw.put("line_status", T0 + 10 * MIN,
                     lines({"central": (9, "Minor Delays", "Central Line: Minor delays due to train cancellations.")}))
        self.raw.put("scrape_fixtures", T0 + 5 * MIN, [
            {"home": "Arsenal", "away": "Hull City", "status": "FT", "home_score": "1", "away_score": "0",
             "kickoff_utc": iso(T0 - 115 * MIN), "egress_est_utc": iso(T0 - 5 * MIN), "venue": "Emirates Stadium",
             "stations": ["Arsenal", "Holloway Road", "Finsbury Park", "Highbury & Islington"]},
            {"home": "Crystal Palace", "away": "Liverpool", "status": "NS", "home_score": None, "away_score": None,
             "kickoff_utc": iso(T0 + 86_400_000), "egress_est_utc": None, "stations": ["Selhurst"]}])
        self.now = T0 + 10 * MIN
        self.ex = self.exchange(self.now)
        self.ex.store.refresh(self.now - mk.HISTORY_MS, self.now)
        self.ex.store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, self.now))

    def assert_shape(self, sigs: list[dict], ids: list[str]):
        self.assertLessEqual(len(sigs), 2)
        self.assertEqual([s["id"] for s in sigs], ids)
        for s in sigs:
            self.assertEqual(set(s), {"id", "text", "data"})
            self.assertIsInstance(s["text"], str)
            self.assertRegex(s["text"], r"\d")

    def test_every_market_carries_one_or_two_documented_clues(self):
        st = self.ex.store
        at = T0 + 15 * MIN
        surge = mk.build_hub_surge("s", st, SPOTS, self.now, at)
        sigs = signals.market(surge, st, self.now)
        self.assert_shape(sigs, ["vs_usual", "usual_curve"])
        hubs = sigs[0]["data"]["hubs"]
        self.assertEqual([h["id"] for h in hubs], HUBS)
        self.assertEqual(sum(h["furthestBelow"] for h in hubs), 1)
        self.assertEqual(len(hubs[0]["typicalNext15"]), 4)
        self.assertIn("of a usual Saturday", sigs[0]["text"])
        self.assert_shape(signals.market(mk.build_city_pulse("p", st, SPOTS, self.now, at), st, self.now), ["pulse"])
        vix = signals.market(mk.build_london_vix("v", st, SPOTS, self.now, at), st, self.now)
        self.assert_shape(vix, ["vix_driver"])
        self.assertIn("moved most by: bike docks", vix[0]["text"])
        stack = signals.market(mk.build_heathrow_stack("h", st, SPOTS, self.now, at), st, self.now)
        self.assert_shape(stack, ["stacks"])
        self.assertEqual(set(stack[0]["data"]["stacks"]), {"BNN", "LAM", "BIG", "OCK"})
        hour = mk.build_hub_hour("x", st, SPOTS, self.now, T0 + 65 * MIN)
        self.assert_shape(signals.market(hour, st, self.now), ["vs_usual", "fixture"])
        clean = mk.build_clean_sheet("c", st, SPOTS, self.now, T0 + 35 * MIN)
        sig = signals.market(clean, st, self.now)
        self.assert_shape(sig, ["lines"])
        self.assertEqual(sig[0]["data"]["disrupted"][0]["name"], "Central")
        self.assertIn("train cancellations", sig[0]["text"])

    def test_market_json_has_signals_and_the_strip_has_hubs_lines_football_vix(self):
        self.ex.tick(self.now)
        js = self.ex.markets_json(self.now)
        self.assertTrue(all("signals" in m for m in js["markets"]))
        strip = signals.strip(self.ex.store, SPOTS, self.now)
        self.assertEqual(set(strip), {"now", "hubs", "lines", "football", "vix"})
        self.assertEqual(len(strip["hubs"]["hubs"]), 5)
        laggard = min(strip["hubs"]["hubs"], key=lambda h: h["vsUsual"])
        self.assertEqual(strip["hubs"]["laggard"], laggard["id"])
        self.assertIn("furthest below", strip["hubs"]["text"])
        self.assertEqual((strip["lines"]["good"], strip["lines"]["of"]), (10, 11))
        self.assertEqual(strip["football"]["score"], "1-0")
        self.assertEqual(strip["football"]["hub"], "kings-cross")
        self.assertIn("Arsenal 1-0 Hull City · FT", strip["football"]["text"])
        self.assertIn("vix", strip["vix"])
        json.dumps(strip)


# ---------------------------------------------------------------- futures fan


class FuturesFanTest(CityCase):
    def setUp(self):
        super().setUp()
        city(self.raw, T0 + 70 * MIN)

    def assert_fan(self, f: dict, m: dict):
        self.assertEqual(f["n"], 50)
        self.assertEqual(f["alive"] + f["ruledOut"], 50)
        self.assertEqual(set(f["byOption"]), {o["id"] for o in m["options"]})
        self.assertEqual(sum(f["byOption"].values()), f["alive"], m["id"])
        self.assertEqual(len(f["paths"]), 50)
        self.assertEqual(sum(f["alivePaths"]), f["alive"])
        for path in f["paths"] + [f["real"]]:
            self.assertLessEqual(len(path), 12)
            for ts, v in path:
                self.assertIsInstance(ts, int)
                self.assertIsInstance(v, (int, float))

    def test_every_market_carries_its_futures_and_by_option_sums_to_alive(self):
        ex = self.exchange()
        ex.store.refresh(T0 - mk.HISTORY_MS, T0)
        ex.store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, T0 + 70 * MIN))
        for t in range(T0, T0 + 8 * MIN, 12_000):
            ex.tick(t)
        js = ex.markets_json(T0 + 8 * MIN)
        subs = {m["series"] for m in js["markets"]}
        self.assertTrue({"hub_surge", "city_pulse", "london_vix", "hub_hour"} <= subs, subs)
        for m in js["markets"]:
            self.assert_fan(m["futures"], m)
            self.assertGreaterEqual(len(m["futures"]["real"]), 1, m["id"])
        vix = next(m for m in js["markets"] if m["series"] == "london_vix")
        self.assertIn("50 simulated futures of Oxford Circus", vix["futures"]["about"])
        self.assertLess(len(json.dumps(js)), 150_000)

    def test_a_settled_market_returns_the_realised_value_and_the_closest_future(self):
        ex = self.exchange()
        for t in range(T0, T0 + 7 * MIN, 12_000):
            ex.tick(t)
        m = ex.markets["city_pulse-1505"]
        self.assertIsNotNone(m.result)
        f = ex.market_json(m, T0 + 7 * MIN)["futures"]
        self.assert_fan(f, ex.market_json(m, T0 + 7 * MIN))
        self.assertAlmostEqual(f["realised"]["value"], m.result["settlement"]["value"], places=1)
        self.assertEqual(f["real"][-1][0], m.result["settlement"]["fetchedAt"])
        self.assertIn(f["realised"]["closest"], range(50))
        self.assertTrue(f["alivePaths"][f["realised"]["closest"]])


# ---------------------------------------------------------------- traders


class TraderTest(CityCase):
    def setUp(self):
        super().setUp()
        city(self.raw, T0 + 10 * MIN)
        self.now = T0 + 10 * MIN
        self.store = self.raw.store(self.now)
        self.at = T0 + 15 * MIN

    def ctx(self, m):
        return {"spots": BY_ID, "stakes": {o: 0.0 for o in m.option_ids()}}

    def test_tube_crowd_backs_the_hub_furthest_below_its_usual(self):
        m = mk.build_hub_surge("s", self.store, SPOTS, self.now, self.at)
        hid, why = traders.decide("tube", m, self.store, self.now, self.ctx(m))
        self.assertEqual(hid, min(m.spec["open"], key=m.spec["open"].get))
        self.assertRegex(why, rf"{BY_ID[hid]['short']} is at \d+% of a usual Saturday, the furthest below")

    def test_tube_crowd_on_the_hour_follows_a_final_whistle_to_its_hub(self):
        self.raw.put("scrape_fixtures", self.now, [
            {"home": "Arsenal", "away": "Hull City", "status": "FT", "home_score": "1", "away_score": "0",
             "kickoff_utc": iso(T0 - 115 * MIN), "egress_est_utc": iso(T0 - 5 * MIN),
             "stations": ["Arsenal", "Finsbury Park"]}])
        store = self.raw.store(self.now)
        m = mk.build_hub_hour("x", store, SPOTS, self.now, T0 + 65 * MIN)
        hid, why = traders.decide("tube", m, store, self.now, self.ctx(m))
        self.assertEqual(hid, "kings-cross")
        self.assertIn("Arsenal 1-0 Hull City ended", why)
        far = T0 + 120 * MIN
        self.assertNotEqual(traders.tube_hour(m, store, far, {})[1][:7], "Arsenal")

    def test_camera_eye_fades_the_vix_and_plane_spotter_fades_the_stacks(self):
        store = self.store
        store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, self.now))
        vix = mk.build_london_vix("v", store, SPOTS, self.now, self.at)
        side, why = traders.decide("cams", vix, store, self.now, self.ctx(vix))
        hist = [s["vix"] for _, s in store.between("vix", self.now - 5 * MIN, self.now)]
        self.assertEqual(side, "under" if hist[-1] > hist[0] else "over")
        self.assertRegex(why, r"VIX (jumped|dropped) \d+\.\d\d → \d+\.\d\d in 5 min on bike docks")
        stack = mk.build_heathrow_stack("h", store, SPOTS, self.now, self.at)
        pick = traders.decide("bus", stack, store, self.now, self.ctx(stack))
        tot = [s["total"] for _, s in store.between("aircraft", self.now - 10 * MIN, self.now)]
        if tot[-1] == tot[0]:
            self.assertIsNone(pick)
        else:
            self.assertEqual(pick[0], "under" if tot[-1] > tot[0] else "over")
            self.assertRegex(pick[1], r"\d+ → \d+ in 10 min \(BNN")

    def test_line_watcher_says_no_on_fresh_unplanned_trouble_else_yes(self):
        m = mk.build_clean_sheet("c", self.store, SPOTS, self.now, T0 + 35 * MIN)
        side, why = traders.decide("lines", m, self.store, self.now, self.ctx(m))
        self.assertEqual(side, "yes")
        self.assertIn("11 of 11 Tube lines Good", why)
        self.raw.put("line_status", self.now + MIN,
                     lines({"central": (9, "Minor Delays", "Central Line: Minor delays due to train cancellations.")}))
        store = self.raw.store(self.now + MIN)
        side, why = traders.decide("lines", m, store, self.now + MIN, self.ctx(m))
        self.assertEqual(side, "no")
        self.assertIn("Central went to Minor Delays 1 min ago", why)

    def test_rain_radar_sits_out_dry_and_backs_pulse_over_in_the_rain(self):
        pulse = mk.build_city_pulse("p", self.store, SPOTS, self.now, self.at)
        self.assertIsNone(traders.decide("rain", pulse, self.store, self.now, self.ctx(pulse)))
        self.raw.put("rain_now", self.now, rain(0.6))
        store = self.raw.store(self.now)
        side, why = traders.decide("rain", pulse, store, self.now, self.ctx(pulse))
        self.assertEqual(side, "over")
        self.assertIn("0.6 mm of rain", why)

    def test_traders_bet_only_on_their_series_and_the_contrarian_fades_every_favourite(self):
        built = [mk.build_hub_surge("s", self.store, SPOTS, self.now, self.at),
                 mk.build_city_pulse("p", self.store, SPOTS, self.now, self.at),
                 mk.build_heathrow_stack("h", self.store, SPOTS, self.now, self.at),
                 mk.build_clean_sheet("c", self.store, SPOTS, self.now, T0 + 35 * MIN)]
        for m in built:
            for tid in ("tube", "cams", "lines", "rain", "bus"):
                if m.sub not in traders.PLAYS[tid]:
                    self.assertIsNone(traders.decide(tid, m, self.store, self.now, self.ctx(m)), (tid, m.sub))
            alt, _ = traders.decide("contrarian", m, self.store, self.now, self.ctx(m))
            self.assertNotEqual(alt, max(m.prices(), key=lambda o: m.prices()[o]))

    def test_traders_with_nothing_sit_out_with_a_number(self):
        ex = self.exchange(self.now)
        ex.tick(self.now)
        why = {t["id"]: t["why"] for t in ex.traders_json(self.now)["traders"]}
        self.assertIn("Sitting out: 0 mm of rain", why["rain"])
        self.assertIn("no series this round is informed by its feed", why["cams"])
        self.assertRegex(why["tube"], r"\d")
        names = {t["id"]: t["name"] for t in traders.TRADERS}
        self.assertEqual(names["bus"], "Plane Spotter")


if __name__ == "__main__":
    unittest.main()
