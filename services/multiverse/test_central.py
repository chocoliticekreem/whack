"""Central-London model: bbox seed builder, interventions, derived events, filter invariants, perf smoke."""
from __future__ import annotations

import gzip
import json
import math
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import central  # noqa: E402
import central_seed  # noqa: E402
import engine  # noqa: E402
import events  # noqa: E402

T0 = "2030-03-02T18:00:00Z"
NOW = events.parse_ms(T0)
LAT0, LON0 = 51.5100, -0.1200
STEP_M = 100.0
GRID = 9
# Stations on the grid (column, row), named like the fictional line network in test_events.
STATIONS = {"NALP": ("Alpha", 1, 1), "NBRA": ("Bravo", 4, 1), "NCHA": ("Charlie", 7, 1), "NDEL": ("Delta", 7, 4),
            "NECH": ("Echo", 7, 7), "NXRA": ("Xray", 4, 7), "NYAN": ("Yankee", 1, 7), "NFAR": ("Faraway", 40, 40)}
CENTRAL_SEED = Path(__file__).resolve().parent / "seeds" / "central-london.json"


def ll(col: float, row: float) -> tuple[float, float]:
    k = 111_320.0 * math.cos(math.radians(LAT0))
    return round(LAT0 - row * STEP_M / 111_320.0, 6), round(LON0 + col * STEP_M / k, 6)


def grid_osm() -> dict:
    """A 9 x 9 street grid (100 m blocks, major every 4th line) with a cul-de-sac and an island to prune."""
    roads = []
    for i in range(GRID):
        kind = "major" if i % 4 == 0 else "minor"
        roads.append({"kind": kind, "line": [list(ll(c, i)) for c in range(GRID)]})
        roads.append({"kind": kind, "line": [list(ll(i, r)) for r in range(GRID)]})
    roads.append({"kind": "minor", "line": [list(ll(8, 8)), list(ll(9, 9))]})
    roads.append({"kind": "minor", "line": [list(ll(12, 12)), list(ll(13, 12)), list(ll(13, 13))]})
    stations = [{"name": n, "kind": "subway", "lat": ll(c, r)[0], "lon": ll(c, r)[1]} for n, c, r in STATIONS.values()]
    pois = [{"name": "Museum", "kind": "attraction", "lat": ll(4, 4)[0], "lon": ll(4, 4)[1]}]
    return {"bbox": None, "tiles": ["synthetic"], "roads": roads, "stations": stations, "pois": pois}


def bbox() -> tuple[float, float, float, float]:
    s, w = ll(-0.5, 8.5)
    n, e = ll(8.5, -0.5)
    return s, w, n, e


def profile(nap: str, name: str, value: float = 0.4) -> dict:
    days = [{"dayOfWeek": d, "timeBands": [{"timeBand": f"{h:02d}:{m:02d}-x", "percentageOfBaseLine": value}
                                           for h in range(24) for m in (0, 15, 30, 45)]}
            for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")]
    return {"name": name, "naptan": nap, "daysOfWeek": days}


def write(raw: Path, source: str, ts: str, payload: object) -> None:
    d = raw / source / ts[:10]
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{ts[11:13]}.ndjson").open("a") as f:
        f.write(json.dumps({"ts": ts, "source": source, "payload": payload}) + "\n")


def synthetic_raw(raw: Path, names: dict[str, str] | None = None) -> None:
    names = names or {n: v[0] for n, v in STATIONS.items()}
    write(raw, "crowding", "2030-03-02T17:58:00Z",
          [{"naptan": n, "name": nm, "dataAvailable": True, "percentageOfBaseline": 0.4, "timeUtc": "2030-03-02T17:55:00.000Z"}
           for n, nm in names.items()])
    for n, nm in names.items():
        write(raw, "crowding_profile", "2030-03-02T12:00:00Z", profile(n, nm))
    cams = [{"id": "CAM_A", "name": "on Bravo st", "lat": ll(4, 1.03)[0], "lon": ll(4, 1.03)[1],
             "yolo": {"people": 6, "vehicles": 2, "at": NOW - 120_000}},
            {"id": "CAM_B", "name": "uncounted", "lat": ll(2, 3)[0], "lon": ll(2, 3)[1]},
            {"id": "CAM_FAR", "name": "in a park", "lat": ll(4.5, 4.5)[0], "lon": ll(4.5, 4.5)[1]},
            {"id": "CAM_OUT", "name": "outside", "lat": ll(30, 30)[0], "lon": ll(30, 30)[1]}]
    write(raw, "jamcams", "2030-03-02T17:59:00Z", cams)


def build_seed(raw: Path) -> dict:
    hubs = [{"id": "hub-b", "name": "Bravo", "naptan": "NBRA", "lat": ll(4, 1)[0], "lon": ll(4, 1)[1], "busStops": ["BUS1"]},
            {"id": "hub-x", "name": "Xray", "naptan": "NXRA", "lat": ll(4, 7)[0], "lon": ll(4, 7)[1], "busStops": []}]
    with mock.patch.object(central_seed, "CAM_SNAP_M", 30.0):
        return central_seed.build(grid_osm(), raw, NOW, bbox(), "test grid", hubs)


class Fixture(unittest.TestCase):
    raw: Path
    seed: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = Path(tempfile.mkdtemp())
        synthetic_raw(cls.raw)
        cls.seed = build_seed(cls.raw)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.raw, ignore_errors=True)

    def neutral(self, **over: object) -> dict:
        a = {"flowScale": 1.0, "demandTrend": 0.0, "touristFactor": 1.0, "capacityScale": 1.0, "alightPerBus": 8,
             "busDelayMin": 0.0, "walkPace": 1.0, "lingerScale": 1.0, "rainAtMin": None, "incident": None,
             "events": [], "interventions": []}
        return a | over


class SeedBuilderTest(Fixture):
    def test_bbox_gives_zones_stations_cams_and_hubs(self) -> None:
        s = self.seed
        self.assertEqual(s["model"], "central")
        self.assertEqual({x["naptan"] for x in s["stations"]}, set(STATIONS) - {"NFAR"})
        self.assertEqual(len(s["zones"]), 7)
        self.assertEqual({z["naptan"] for z in s["zones"]}, {x["naptan"] for x in s["stations"]})
        self.assertEqual(sum(len(z["segmentIds"]) for z in s["zones"]), len(s["segments"]))
        cams = {c["id"]: c for c in s["cams"]}
        self.assertEqual(set(cams), {"CAM_A", "CAM_B"})
        self.assertTrue(cams["CAM_A"]["counted"])
        self.assertEqual(cams["CAM_A"]["people"], 6)
        self.assertFalse(cams["CAM_B"]["counted"])
        self.assertEqual({h["id"] for h in s["hubs"]}, {"hub-b", "hub-x"})
        self.assertEqual(sorted(z for h in s["hubs"] for z in h["zoneIds"]), sorted(z["id"] for z in s["zones"]))
        self.assertGreater(s["boundarySegments"], 0)
        self.assertTrue(all(st["live"] == 0.4 and st["profile"]["SAT"][72] == 0.4 for st in s["stations"]))

    def test_graph_is_merged_pruned_and_connected(self) -> None:
        s = self.seed
        g = s["graph"]
        self.assertLess(g["edges"], g["microEdges"])
        self.assertEqual(g["nodes"], len(s["nodes"]))
        far = ll(12, 12)
        k = 111_320.0 * math.cos(math.radians(s["origin"]["lat"]))
        fx = (far[1] - s["origin"]["lon"]) * k
        self.assertTrue(all(max(p[0] for p in seg["line"]) < fx - 1 for seg in s["segments"]))
        adj: dict[int, set[int]] = {}
        for seg in s["segments"]:
            adj.setdefault(seg["u"], set()).add(seg["v"])
            adj.setdefault(seg["v"], set()).add(seg["u"])
        seen, stack = {0}, [0]
        while stack:
            for m in adj[stack.pop()] - seen:
                seen.add(m)
                stack.append(m)
        self.assertEqual(len(seen), len(s["nodes"]))
        self.assertTrue(all(len(adj[n]) > 1 for n in adj))

    def test_no_station_in_bbox_is_an_error(self) -> None:
        empty = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(ValueError):
                central_seed.build(grid_osm(), empty, NOW, bbox(), "x", [])
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    @unittest.skipUnless(central_seed.OSM_CACHE.is_file(), "no cached central streets")
    def test_oxford_circus_is_just_a_smaller_bbox(self) -> None:
        raw = Path(tempfile.mkdtemp())
        try:
            synthetic_raw(raw, {"940GZZLUOXC": "Oxford Circus", "940GZZLUBND": "Bond Street",
                                "940GZZLUPCC": "Piccadilly Circus", "940GZZLUKSX": "King's Cross St. Pancras"})
            osm = json.loads(gzip.open(central_seed.OSM_CACHE, "rt").read())
            s = central_seed.build(osm, raw, NOW, (51.509, -0.152, 51.521, -0.131), "Oxford Circus",
                                   central_seed.load_hotspots())
            self.assertEqual({x["naptan"] for x in s["stations"]}, {"940GZZLUOXC", "940GZZLUBND", "940GZZLUPCC"})
            self.assertEqual(sorted(h["id"] for h in s["hubs"]), ["oxford-circus", "piccadilly"])
            self.assertGreater(len(s["segments"]), 100)
            mv = engine.Multiverse(s, n=6, horizon=5, model=central)
            mv.spawn()
            self.assertEqual(len(mv.live()), 6)
            self.assertIn("940GZZLUOXC", mv.live()[0]["run"]["stations"])
        finally:
            shutil.rmtree(raw, ignore_errors=True)


class ModelTest(Fixture):
    def run_with(self, ivs: list[dict], **over: object) -> dict:
        return central.run_world(self.seed, self.neutral(interventions=ivs, **over), 65, 47)

    def test_common_random_numbers(self) -> None:
        a, b = self.run_with([]), self.run_with([])
        self.assertEqual(a["zones"], b["zones"])
        self.assertEqual(a["segments"]["data"], b["segments"]["data"])
        more = self.run_with([], flowScale=2.0)
        self.assertGreater(sum(more["zones"][z][10] for z in more["zones"]), sum(a["zones"][z][10] for z in a["zones"]))

    def test_closure_reroutes_flow(self) -> None:
        base = self.run_with([])
        E = len(self.seed["segments"])

        def loads(run: dict, minute: int) -> list[float]:
            import array
            vals = array.array("f")
            vals.frombytes(run["segments"]["data"])
            k = run["segments"]["minutes"].index(minute) * E
            return list(vals[k:k + E])

        b = loads(base, 15)
        busiest = max(range(E), key=lambda i: b[i])
        sid = self.seed["segments"][busiest]["id"]
        iv = central.normalise_intervention({"kind": "closure", "where": {"segments": [sid]}, "start": -30}, self.seed, NOW)
        closed = loads(self.run_with([iv]), 15)
        self.assertGreater(b[busiest], 1.0)
        self.assertEqual(closed[busiest], 0.0)
        gained = [i for i in range(E) if closed[i] > b[i] + 0.5]
        self.assertTrue(gained, "the flow went somewhere else")
        self.assertGreater(sum(closed), 0.8 * sum(b))

    def test_demand_surge_raises_nearby_stations_only(self) -> None:
        lat, lon = ll(1, 1)
        iv = central.normalise_intervention({"kind": "demand_surge", "where": {"lat": lat, "lon": lon, "radius_m": 50},
                                             "magnitude": 1.0, "start": 0, "end": 60}, self.seed, NOW)
        base, surge = self.run_with([]), self.run_with([iv])
        at = 10
        self.assertAlmostEqual(surge["stations"]["NALP"][at] / base["stations"]["NALP"][at], 2.0, places=2)
        self.assertGreater(surge["stations"]["NBRA"][at], base["stations"]["NBRA"][at])
        self.assertLess(surge["stations"]["NBRA"][at] / base["stations"]["NBRA"][at],
                        surge["stations"]["NALP"][at] / base["stations"]["NALP"][at])
        gain = {n: surge["stations"][n][at] / base["stations"][n][at] for n in ("NALP", "NBRA", "NECH")}
        self.assertGreater(gain["NBRA"], gain["NECH"])
        self.assertLess(gain["NECH"], 1.2)
        self.assertGreater(sum(surge["zones"]["z0"]), sum(base["zones"]["z0"]))
        self.assertEqual(surge["stations"]["NALP"][0], base["stations"]["NALP"][0] * 2)

    def test_bad_interventions_are_rejected(self) -> None:
        for raw in ({"kind": "nope", "where": {"naptan": "NALP"}}, {"kind": "closure"},
                    {"kind": "closure", "where": {"segments": ["s99999"]}}, {"kind": "demand_surge", "where": {"naptan": "NOPE"}},
                    {"kind": "capacity", "where": {"naptan": "NALP"}, "start": 10, "end": 5},
                    {"kind": "demand_drop", "where": {"naptan": "NALP"}, "shape": "zigzag"}):
            with self.assertRaises(ValueError, msg=str(raw)):
                central.normalise_intervention(raw, self.seed, NOW)
        iv = central.normalise_intervention({"kind": "demand_surge", "where": {"naptan": "NOPE"}}, self.seed, NOW, strict=False)
        self.assertFalse(iv["resolved"])

    def test_ramp_scales_effect_over_its_window(self) -> None:
        iv = central.normalise_intervention({"kind": "demand_surge", "where": {"naptan": "NDEL"}, "magnitude": 1.0,
                                             "start": 0, "end": 40, "shape": "ramp_up"}, self.seed, NOW)
        base, ramp = self.run_with([]), self.run_with([iv])
        r = [ramp["stations"]["NDEL"][m] / base["stations"]["NDEL"][m] for m in (5, 20, 35)]
        self.assertTrue(1.0 < r[0] < r[1] < r[2] < 2.0, r)


class EventLayerTest(Fixture):
    def test_derived_line_suspension_lowers_the_station_and_raises_the_displaced_one(self) -> None:
        rows = []
        for line, chain in (("l1", ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]), ("l2", ["Xray", "Bravo", "Yankee"])):
            for i, name in enumerate(chain):
                loc = f"Between {name} and {chain[i + 1]}" if i + 1 < len(chain) else f"At {name}"
                rows.append({"naptanId": f"N{name[:3].upper()}", "stationName": f"{name} Underground Station",
                             "lineId": line, "currentLocation": loc})
        status = {"id": "l1", "name": "L1", "statuses": [{"statusSeverity": 3, "statusSeverityDescription": "Part Suspended",
                                                           "reason": "L1: No service between Alpha and Charlie."}]}
        snap = {"line_status": {"ts": T0, "payload": [status]}, "tube": {"ts": T0, "payload": rows}}
        derived = events.derive(snap, NOW)
        resolved, shown = central.resolve_events(derived, self.seed, NOW)
        self.assertTrue(any(e["kind"] == "closure" and e["inSim"] for e in shown))
        surged = {iv["where"]["naptan"] for iv in resolved if iv["kind"] == "demand_surge"}
        self.assertTrue(surged)
        mv = engine.Multiverse(self.seed, n=6, horizon=5, model=central)
        mv.spawn()
        before = {n: central.station_forecast(mv, n, 15)["busyness"] for n in mv.station_names}
        self.assertTrue(mv.set_applied(resolved))
        self.assertFalse(mv.set_applied(resolved), "the same set again changes nothing")
        after = {n: central.station_forecast(mv, n, 15)["busyness"] for n in mv.station_names}
        carriers = [w for w in mv.live() if any(i["where"].get("naptan") == "NALP" for i in w["assumptions"]["interventions"])]
        self.assertTrue(0 < len(carriers) < len(mv.live()), "a derived event enters some worlds, not all")
        self.assertTrue(all(w["run"]["stations"]["NALP"][15] == 0.0 for w in carriers))
        self.assertLess(after["NALP"], 0.9 * before["NALP"])
        for n in surged:
            self.assertGreater(after[n], before[n] * 1.005, n)
        mv.advance({"ts": self.seed["at"] + 60_000, "readings": {}, "busDelayMin": None, "stations": {"NALP": 0.001}})
        confirmed = central.station_forecast(mv, "NALP", 15)["busyness"]
        self.assertLess(confirmed, 0.2 * before["NALP"], "TfL showing Alpha empty prices the closure in")
        mv.set_applied([])
        self.assertTrue(all(not w["assumptions"]["interventions"] for w in mv.live()), "an ended event leaves every world")


class FilterTest(Fixture):
    def frame(self, minute: int, people: float, stations: dict[str, float] | None = None) -> dict:
        return {"ts": self.seed["at"] + minute * 60_000, "readings": {"CAM_A": people}, "busDelayMin": 0.5,
                "stations": stations or {}}

    def test_prices_live_count_delist_cap_and_station_term(self) -> None:
        mv = engine.Multiverse(self.seed, n=12, horizon=5, model=central, rng_seed=3)
        mv.spawn()
        for m, st in ((1, {"NALP": 0.8}), (2, {}), (3, {"NBRA": 0.2, "NDEL": 0.5})):
            obs = mv.advance(self.frame(m, 6 + m, st))
            live = mv.live()
            self.assertEqual(len(live), 12)
            self.assertAlmostEqual(sum(w["price"] for w in live), 1.0, places=9)
            self.assertLessEqual(len(obs["delisted"]), int(engine.MAX_DELIST_SHARE * 12))
            self.assertEqual(obs.get("stations", {}), st)
        parts = [p for w in mv.live() for t in w["trail"] for p in ([t["worst"]] if t["worst"] else [])]
        self.assertTrue(parts)
        self.assertGreaterEqual(engine.log_likelihood_station(0.01, 5.0), engine.OUTLIER_LL)
        self.assertEqual(engine.log_likelihood_station(0.4, 0.4), 0.0)
        self.assertGreater(engine.log_likelihood_station(0.4, 0.44), engine.log_likelihood_station(0.4, 0.6))

    def test_station_reading_anchors_forecasts_and_names_the_driver(self) -> None:
        mv = engine.Multiverse(self.seed, n=8, horizon=5, model=central, rng_seed=5)
        mv.spawn()
        mv.advance({"ts": self.seed["at"] + 60_000, "readings": {}, "busDelayMin": None, "stations": {"NECH": 0.8}})
        for w in mv.live():
            self.assertAlmostEqual(w["run"]["stations"]["NECH"][1], 0.8, places=3)
        self.assertNotEqual(mv.vix_history[-1]["driver"], "")
        self.assertIn("Echo crowding", mv.source_label(engine.STATION_PREFIX + "NECH"))

    def test_fork_conserves_price_and_all_reruns_every_world(self) -> None:
        mv = engine.Multiverse(self.seed, n=8, horizon=5, model=central)
        mv.spawn()
        iv = central.normalise_intervention({"kind": "closure", "where": {"naptan": "NBRA"}}, self.seed, NOW, "t1")
        forked = mv.apply(iv, "fork", 0.3)
        self.assertEqual(len(forked), 8)
        self.assertAlmostEqual(sum(w["price"] for w in mv.live()), 1.0, places=9)
        mv.apply(central.normalise_intervention({"kind": "demand_drop", "where": {"naptan": "NDEL"}}, self.seed, NOW, "t2"))
        self.assertTrue(all("t2" in [i["id"] for i in w["assumptions"]["interventions"]] for w in mv.live()))
        self.assertEqual(sum("t1" in [i["id"] for i in w["assumptions"]["interventions"]] for w in mv.live()), 8)

    def test_roll_reseeds_and_keeps_n(self) -> None:
        mv = engine.Multiverse(self.seed, n=6, horizon=5, model=central)
        mv.spawn()
        for m in range(1, 9):
            mv.advance(self.frame(m, 5.0, {"NALP": 0.5} if m == 4 else None))
        self.assertEqual(mv.epochs, 1)
        self.assertEqual(len(mv.live()), 6)
        self.assertEqual(next(s for s in mv.seed["stations"] if s["naptan"] == "NALP")["live"], 0.5)


class ScenarioTest(Fixture):
    def test_scenario_answers_what_if_and_leaves_state_byte_identical(self) -> None:
        import server
        app = server.App()
        mv = engine.Multiverse(self.seed, n=8, horizon=5, model=central)
        mv.spawn()
        mv.advance({"ts": self.seed["at"] + 60_000, "readings": {"CAM_A": 7.0}, "busDelayMin": 0.5,
                    "stations": {"NBRA": 0.45}})
        app.mv = mv
        before = json.dumps(app.state(), sort_keys=True)
        out = app.scenario({"interventions": [{"kind": "closure", "where": {"naptan": "NALP"}, "start": 0, "end": 90},
                                              {"kind": "demand_surge", "where": {"naptan": "NDEL"}, "magnitude": 1.0}],
                            "horizons": [15, 60]})
        self.assertEqual(json.dumps(app.state(), sort_keys=True), before)
        b = {s["naptan"]: s for s in out["before"]["stations"]}
        a = {s["naptan"]: s for s in out["after"]["stations"]}
        self.assertEqual(set(b["NALP"]), {"naptan", "name", "hub", "+15", "+60"})
        self.assertEqual(a["NALP"]["+15"]["mean"], 0.0)
        self.assertGreater(a["NDEL"]["+60"]["mean"], b["NDEL"]["+60"]["mean"] * 1.5)
        self.assertEqual(a["NECH"]["+15"], b["NECH"]["+15"])
        self.assertTrue(out["after"]["hubs"] and "ms" in out)
        with self.assertRaises(ValueError):
            app.scenario({"interventions": []})


@unittest.skipUnless(CENTRAL_SEED.is_file(), "no central seed")
class PerfSmokeTest(unittest.TestCase):
    def test_fifty_worlds_spawn_and_step_inside_budget(self) -> None:
        seed = json.loads(CENTRAL_SEED.read_text())
        counted = [c["id"] for c in seed["cams"]][:10]
        t = time.perf_counter()
        mv = engine.Multiverse(seed, n=50, horizon=5, model=central)
        mv.spawn()
        spawn_s = time.perf_counter() - t
        rng = random.Random(1)
        steps = []
        for m in range(1, 4):
            frame = {"ts": seed["at"] + m * 60_000, "readings": {c: float(rng.randint(0, 9)) for c in counted},
                     "busDelayMin": 0.5, "stations": {seed["stations"][m]["naptan"]: 0.3}}
            t = time.perf_counter()
            mv.advance(frame)
            steps.append(time.perf_counter() - t)
        self.assertEqual(len(mv.live()), 50)
        self.assertLess(spawn_s, 60.0)
        self.assertLess(max(steps), 20.0)
        central.forecasts(mv)
        central.segment_forecast(mv, 60)


def tearDownModule() -> None:
    central.shutdown()


if __name__ == "__main__":
    unittest.main()
