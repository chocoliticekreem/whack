"""Real-data path: recorder jamcams/yolo parser, bus lateness proxy, seed helpers. Fixtures: fixtures/real/."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Run from the repo root too (`python3 -m unittest services/multiverse/test_real.py`): the modules are siblings.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine  # noqa: E402
import feeds  # noqa: E402
import fixture  # noqa: E402
import real_seed  # noqa: E402

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def lines(name: str) -> list[dict]:
    return [json.loads(line) for line in (REAL / f"{name}.ndjson").read_text().splitlines() if line.strip()]


def ms(ts: str) -> int:
    return feeds.parse_ts(ts)


class JamcamParser(unittest.TestCase):
    def test_reads_nested_yolo_and_skips_rows_without_a_count(self) -> None:
        first, second, _ = lines("jamcams")
        self.assertEqual(feeds.jamcam_readings(first["payload"]), {})
        self.assertEqual(feeds.jamcam_readings(second["payload"]),
                         {"JamCams_00001.06592": {"people": 9.0, "vehicles": 22.0, "at": 1794067021082}})

    def test_null_people_and_junk_rows_are_skipped(self) -> None:
        rows = [{"id": "A", "yolo": {"people": None, "vehicles": 3, "at": 1}}, {"id": "B"}, "junk", {"yolo": {"people": 2}}]
        self.assertEqual(feeds.jamcam_readings(rows), {})
        self.assertEqual(feeds.jamcam_readings({"not": "a list"}), {})

    def test_fresh_people_drops_stale_counts_and_cams_outside_the_seed(self) -> None:
        payload = lines("jamcams")[2]["payload"]
        at = 1794067021082
        self.assertEqual(feeds.fresh_people(payload, at + 60_000, None), {"JamCams_00001.06592": 9.0})
        stale = at + (feeds.READING_MAX_AGE_MIN + 1) * 60_000
        self.assertEqual(feeds.fresh_people(payload, stale, None), {})
        self.assertEqual(feeds.fresh_people(payload, at + 60_000, {"JamCams_00001.07451"}), {})


class BusDelay(unittest.TestCase):
    def snapshots(self) -> list[tuple[int, list[dict]]]:
        return [(ms(r["ts"]), r["payload"]) for r in lines("bus")]

    def test_median_eta_slip_over_buses_seen_twice(self) -> None:
        # 16:07: LTZ1896 slipped 16:04:42 -> 16:06:45 (2.05), LTZ1457 16:06:47 -> 16:07:52 (1.08), LK15CSZ 0.
        self.assertEqual(feeds.bus_delay_min(self.snapshots(), ms("2026-11-07T16:07:02Z")), (1.08, 3))
        self.assertEqual(feeds.bus_delay_min(self.snapshots(), ms("2026-11-07T16:06:02Z")), (0.0, 3))

    def test_no_bus_seen_twice_means_no_reading(self) -> None:
        self.assertEqual(feeds.bus_delay_min(self.snapshots(), ms("2026-11-07T16:04:02Z")), (None, 0))
        self.assertEqual(feeds.bus_delay_min([], ms("2026-11-07T16:07:02Z")), (None, 0))

    def test_earlier_eta_is_floored_at_zero(self) -> None:
        row = {"vehicleId": "V", "naptanId": "S"}
        snaps = [(0, [dict(row, expectedArrival="2026-11-07T16:10:00Z")]),
                 (60_000, [dict(row, expectedArrival="2026-11-07T16:08:00Z")])]
        self.assertEqual(feeds.bus_delay_min(snaps, 60_000), (0.0, 1))


class RealFrames(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        for source, hour in (("jamcams", "15"), ("bus", "16")):
            day = self.root / source / "2026-11-07"
            day.mkdir(parents=True)
            shutil.copy(REAL / f"{source}.ndjson", day / f"{hour}.ndjson")

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_one_frame_per_snapshot_with_seed_cams_only(self) -> None:
        frames = feeds.load_real_frames(self.root, {"JamCams_00001.06592"}, ms("2026-11-07T15:55:00Z"),
                                        ms("2026-11-07T16:00:00Z"), {"490000173RF"})
        self.assertEqual([f["ts"] for f in frames], [ms(r["ts"]) for r in lines("jamcams")])
        self.assertEqual([f["readings"] for f in frames], [{}, {"JamCams_00001.06592": 9.0}, {"JamCams_00001.06592": 9.0}])
        self.assertTrue(all(f["busDelayMin"] is None for f in frames))

    def test_default_window_lands_on_the_counts(self) -> None:
        start, end = feeds.default_window(self.root, {"JamCams_00001.06592"}, ms("2026-11-07T16:00:00Z"), span_min=2)
        self.assertEqual(start, ms("2026-11-07T15:57:00Z"))
        self.assertEqual(end - start, 2 * 60_000 - 1)


class RobustLikelihood(unittest.TestCase):
    def test_one_wild_camera_costs_at_most_the_outlier_floor(self) -> None:
        self.assertEqual(engine.log_likelihood_counts(68.0, 2.0), engine.OUTLIER_LL)
        self.assertAlmostEqual(engine.log_likelihood_counts(4.0, 4.0), 0.0)
        self.assertGreater(engine.log_likelihood_counts(5.0, 4.0), engine.OUTLIER_LL)


def station(sid: str, lat: float, lon: float, bikes: int, empty: int = 5) -> dict:
    return {"id": sid, "lat": lat, "lon": lon, "NbBikes": str(bikes), "NbEmptyDocks": str(empty)}


NEAR, FAR = (51.5160, -0.1420), (51.5292, -0.1100)
OXC_ROW = {"naptan": feeds.OXFORD_CIRCUS_NAPTAN, "name": "Oxford Circus", "dataAvailable": True,
           "percentageOfBaseline": 0.42}
PROFILE = {"naptan": feeds.OXFORD_CIRCUS_NAPTAN, "daysOfWeek": [
    {"dayOfWeek": "SAT", "timeBands": [{"timeBand": "16:45-17:00", "percentageOfBaseLine": 0.38}]}]}


class ExtraParsers(unittest.TestCase):
    def test_rain_rate_from_the_15_min_interval(self) -> None:
        self.assertEqual(feeds.rain_mm_h({"current": {"interval": 900, "precipitation": 0.2}}), 0.8)
        self.assertEqual(feeds.rain_mm_h({"current": {"precipitation": 0.0}}), 0.0)

    def test_rain_missing_is_null(self) -> None:
        for payload in (None, {}, {"current": {}}, {"current": {"precipitation": None}}, ["junk"]):
            self.assertIsNone(feeds.rain_mm_h(payload))

    def test_crowding_reads_oxford_circus_only_when_available(self) -> None:
        other = dict(OXC_ROW, naptan="940GZZLUBND", percentageOfBaseline=0.9)
        self.assertEqual(feeds.crowding_now([other, OXC_ROW]), 0.42)
        self.assertIsNone(feeds.crowding_now([other]))
        self.assertIsNone(feeds.crowding_now([dict(OXC_ROW, dataAvailable=False)]))
        self.assertIsNone(feeds.crowding_now({"not": "a list"}))

    def test_bikes_out_is_the_share_gone_near_oxford_circus(self) -> None:
        base = [station("A", *NEAR, 10), station("B", *NEAR, 10), station("F", *FAR, 50)]
        now = [station("A", *NEAR, 6), station("B", *NEAR, 9), station("F", *FAR, 0)]
        self.assertEqual(feeds.bikes_out(base, now), 0.25)
        self.assertEqual(feeds.bikes_out(now, base), round(-5 / 15, 4))

    def test_bikes_out_missing_is_null(self) -> None:
        self.assertIsNone(feeds.bikes_out(None, [station("A", *NEAR, 6)]))
        self.assertIsNone(feeds.bikes_out([station("A", *NEAR, 0)], [station("A", *NEAR, 6)]))
        self.assertIsNone(feeds.bikes_out([station("F", *FAR, 9)], [station("F", *FAR, 6)]))


class RealExtras(unittest.TestCase):
    T0 = "2026-11-07T16:50:02Z"
    T1 = "2026-11-07T16:58:02Z"

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def write(self, source: str, hour: str, *recs: tuple[str, object]) -> None:
        day = self.root / source / "2026-11-07"
        day.mkdir(parents=True, exist_ok=True)
        with open(day / f"{hour}.ndjson", "a") as f:
            for ts, payload in recs:
                f.write(json.dumps({"ts": ts, "source": source, "payload": payload}) + "\n")

    def test_all_four_fields_from_the_recorder(self) -> None:
        self.write("rain_now", "16", (self.T1, {"current": {"interval": 900, "precipitation": 0.1}}))
        self.write("crowding", "16", (self.T1, [OXC_ROW]))
        self.write("crowding_profile", "12", ("2026-11-07T12:00:00Z", PROFILE))
        self.write("bikepoint", "16", (self.T0, [station("A", *NEAR, 20)]), (self.T1, [station("A", *NEAR, 15)]))
        base = feeds.bikes_base(self.root, ms(self.T0))
        self.assertEqual(feeds.real_extras(self.root, ms(self.T1), base),
                         {"rainMmH": 0.4, "crowding": 0.42, "crowdingTypical": 0.38, "bikesOut": 0.25})

    def test_nothing_recorded_means_all_null(self) -> None:
        self.assertIsNone(feeds.bikes_base(self.root, ms(self.T0)))
        self.assertEqual(feeds.real_extras(self.root, ms(self.T1), None),
                         {"rainMmH": None, "crowding": None, "crowdingTypical": None, "bikesOut": None})

    def test_stale_records_do_not_count(self) -> None:
        self.write("rain_now", "15", ("2026-11-07T15:00:00Z", {"current": {"precipitation": 1.0}}))
        self.assertIsNone(feeds.real_extras(self.root, ms(self.T1), None)["rainMmH"])

    def test_real_root_needs_recorded_jamcams(self) -> None:
        with mock.patch.dict("os.environ", {"MULTIVERSE_RAW": str(self.root)}):
            self.assertIsNone(feeds.real_root())
            self.write("jamcams", "16", (self.T1, []))
            self.assertEqual(feeds.real_root(), self.root)


class FeedLikelihood(unittest.TestCase):
    def test_rain_agreeing_is_free_disagreeing_costs(self) -> None:
        self.assertEqual(engine.log_likelihood_rain(None, 3, 0.0), 0.0)
        self.assertEqual(engine.log_likelihood_rain(2.0, 3, 1.2), 0.0)
        self.assertEqual(engine.log_likelihood_rain(4.0, 3, 0.0), 0.0)
        self.assertEqual(engine.log_likelihood_rain(2.0, 3, 0.0), engine.RAIN_MISS_LL)
        self.assertEqual(engine.log_likelihood_rain(None, 3, 2.0), engine.RAIN_MISS_LL)
        self.assertGreaterEqual(engine.RAIN_MISS_LL, engine.OUTLIER_LL)

    def test_crowding_peaks_at_the_live_over_typical_ratio_and_is_capped(self) -> None:
        self.assertAlmostEqual(engine.log_likelihood_crowding(1.5, 0.6, 0.4), 0.0)
        self.assertGreater(engine.log_likelihood_crowding(1.4, 0.6, 0.4), engine.log_likelihood_crowding(0.8, 0.6, 0.4))
        self.assertEqual(engine.log_likelihood_crowding(0.5, 0.9, 0.1), engine.OUTLIER_LL)

    def test_bikes_leaving_point_to_a_higher_moving_share_and_are_capped(self) -> None:
        self.assertAlmostEqual(engine.log_likelihood_bikes(engine.MOVE_MID, 0.0), 0.0)
        self.assertGreater(engine.log_likelihood_bikes(0.8, 0.05), engine.log_likelihood_bikes(0.4, 0.05))
        self.assertEqual(engine.log_likelihood_bikes(0.3, 1.0), engine.OUTLIER_LL)

    def test_missing_reading_adds_no_term(self) -> None:
        a = {"rainAtMin": None, "touristFactor": 1.0, "movingShare": 0.6}
        self.assertEqual(engine.feed_parts(a, 2, None), [])
        self.assertEqual(engine.feed_parts(a, 2, {"rainMmH": None, "crowding": 0.4, "crowdingTypical": None,
                                                  "bikesOut": None}), [])
        parts = engine.feed_parts(a, 2, {"rainMmH": 0.0, "crowding": 0.4, "crowdingTypical": 0.4, "bikesOut": 0.0})
        self.assertEqual([p["cam"] for p in parts], ["rain", "crowding", "bikes"])
        self.assertTrue(all(p["ll"] == 0.0 for p in parts))


class FeedObservations(unittest.TestCase):
    def market(self) -> engine.Multiverse:
        mv = engine.Multiverse(fixture.build_seed(), n=8, horizon=5)
        mv.spawn()
        return mv

    def test_fields_are_null_without_data_and_no_term_is_scored(self) -> None:
        mv = self.market()
        obs = mv.observe(mv.seed["at"] + 60_000, {})
        self.assertEqual({k: obs[k] for k in engine.EXTRA_KEYS}, dict.fromkeys(engine.EXTRA_KEYS))
        self.assertEqual(obs["compared"], 0)
        self.assertEqual(obs["delisted"], [])

    def test_feeds_alone_reprice_the_market_toward_the_reading(self) -> None:
        mv = self.market()
        for w in mv.live():
            w["assumptions"]["rainAtMin"] = None
        wet = mv.live()[0]
        wet["assumptions"]["rainAtMin"] = 0.0
        obs = mv.observe(mv.seed["at"] + 60_000, {}, None,
                         {"rainMmH": 0.0, "crowding": 0.4, "crowdingTypical": 0.4, "bikesOut": 0.02})
        self.assertEqual((obs["rainMmH"], obs["crowding"], obs["bikesOut"]), (0.0, 0.4, 0.02))
        self.assertTrue(wet["trail"][-1]["worst"]["cam"] in {"rain", "crowding", "bikes"})
        self.assertLess(wet["trail"][-1]["ll"], max(w["trail"][-1]["ll"] for w in mv.worlds if w["trail"]))
        self.assertLessEqual(len(obs["delisted"]), engine.MAX_DELIST_SHARE * 8)


class Anchoring(unittest.TestCase):
    def test_forecast_starts_at_the_last_count_and_moves_at_most_the_cap(self) -> None:
        raw = [9.8, 17.0, 58.0, 58.0]
        out = engine.anchored(raw, (0, 9.0))
        self.assertEqual(out[0], 9.0)
        for m, v in enumerate(out):
            self.assertLessEqual(abs(v ** 0.5 - 3.0), engine.ANCHOR_STEP_SQRT * m + 1e-6)
        self.assertGreater(out[3], out[1])

    def test_only_the_deviation_from_the_average_world_moves_it(self) -> None:
        raw = [4.0, 9.0, 16.0]
        self.assertEqual(engine.anchored(raw, (0, 4.0), [2.0, 3.0, 4.0]), [4.0, 4.0, 4.0])

    def test_no_anchor_keeps_the_raw_series(self) -> None:
        self.assertEqual(engine.anchored([1.0, 2.0], None), [1.0, 2.0])

    def test_real_seed_anchors_counted_cams_and_fills_the_rest_with_their_mean(self) -> None:
        seed = {"at": 5, "source": "real(test)", "cams": [{"id": "A", "people": 4.0, "counted": True, "at": 3},
                                                          {"id": "B", "people": 8.0, "counted": True, "at": 4},
                                                          {"id": "C", "people": 0, "counted": False, "at": None}]}
        anchors, taken = engine.seed_anchors(seed)
        self.assertEqual(anchors, {"A": (0, 4.0), "B": (0, 8.0), "C": (0, 6.0)})
        self.assertEqual(taken, {"A": 3, "B": 4, "C": -1})
        self.assertEqual(engine.seed_anchors(dict(seed, source="fixture(synthetic grid)")), ({}, {}))

    def test_stale_counts_are_the_ones_too_old_to_score(self) -> None:
        payload = [{"id": "A", "yolo": {"people": 3, "at": 0}}, {"id": "B", "yolo": {"people": 5, "at": 590_000}}]
        ts = 11 * 60_000
        self.assertEqual(feeds.stale_counts(payload, ts, None), {"A": {"people": 3.0, "at": 0}})
        self.assertEqual(feeds.fresh_people(payload, ts, None), {"B": 5.0})


class RealMarket(unittest.TestCase):
    def market(self) -> engine.Multiverse:
        seed = json.loads(real_seed.SEED_PATH.read_text())
        mv = engine.Multiverse(seed, n=6, horizon=2)
        mv.spawn()
        return mv

    def test_spawned_forecasts_start_at_the_seed_counts(self) -> None:
        mv = self.market()
        cam = next(c for c in mv.seed["cams"] if c.get("counted"))
        self.assertTrue(all(w["run"]["cams"][cam["id"]][0] == cam["people"] for w in mv.live()))

    def test_bus_prior_is_small_then_shrinks_toward_the_reading(self) -> None:
        mv = self.market()
        self.assertTrue(all(w["assumptions"]["busDelayMin"] <= 0.8 for w in mv.live()))
        mv.last_bus = 0.0
        self.assertAlmostEqual(mv.bus_prior({"busDelayMin": 5.0})["busDelayMin"], 5.0 * engine.BUS_SHRINK)

    def test_an_observed_count_re_anchors_every_live_world(self) -> None:
        mv = self.market()
        cid = mv.seed["cams"][0]["id"]
        mv.observe(mv.seed["at"] + 60_000, {cid: 30.0})
        self.assertTrue(all(w["run"]["cams"][cid][1] == 30.0 for w in mv.live()))

    def test_an_older_count_does_not_replace_a_newer_anchor(self) -> None:
        mv = self.market()
        cam = next(c for c in mv.seed["cams"] if c.get("counted"))
        mv.adopt_counts({cam["id"]: {"people": 99.0, "at": cam["at"] - 1}})
        self.assertEqual(mv.anchors[cam["id"]], (0, cam["people"]))
        mv.adopt_counts({cam["id"]: {"people": 99.0, "at": mv.seed["at"]}})
        self.assertEqual(mv.anchors[cam["id"]], (0, 99.0))


class SeedHelpers(unittest.TestCase):
    def test_snap_origin_matches_the_twin_grid(self) -> None:
        self.assertEqual(real_seed.snap_origin(51.5152, -0.1419), (51.514292, -0.141551))
        self.assertEqual(real_seed.snap_origin(*real_seed.TOWER_HILL), real_seed.TOWER_HILL)

    def test_simplify_keeps_ends_of_straight_runs_and_real_bends(self) -> None:
        self.assertEqual(real_seed.simplify([[0, 0], [10, 0.5], [20, 0]]), [[0, 0], [20, 0]])
        self.assertEqual(real_seed.simplify([[0, 0], [10, 10], [20, 0]]), [[0, 0], [10, 10], [20, 0]])

    def test_merge_chains_joins_pieces_through_two_way_nodes(self) -> None:
        def piece(a, b, peds):
            return {"line": [a, b], "kind": "minor", "lengthM": 10.0, "peds": peds, "vehicles": 0.0, "confidence": 0.2}
        merged = real_seed.merge_chains([piece([0, 0], [10, 0], 2.0), piece([10, 0], [20, 0], 4.0),
                                         piece([20, 0], [20, 10], 6.0), piece([20, 0], [30, 0], 0.0)])
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["line"], [[0, 0], [10, 0], [20, 0]])
        self.assertEqual(merged[0]["peds"], 3.0)

    def test_prune_dead_ends_keeps_the_loop(self) -> None:
        loop = [{"line": [a, b]} for a, b in (([0, 0], [10, 0]), ([10, 0], [10, 10]), ([10, 10], [0, 0]))]
        spur = [{"line": [[10, 10], [10, 20]]}, {"line": [[10, 20], [10, 30]]}]
        self.assertEqual(real_seed.prune_dead_ends(loop + spur), loop)

    def test_saved_seed_uses_recorder_cam_ids_pinned_to_streets(self) -> None:
        seed = json.loads(real_seed.SEED_PATH.read_text())
        pinned = {s["camId"] for s in seed["segments"] if s.get("camId")}
        self.assertTrue(seed["cams"])
        self.assertTrue(all(c["id"].startswith("JamCams_") and c["id"] in pinned for c in seed["cams"]))
        self.assertIn("Oxford Circus station", {z["name"] for z in seed["zones"]})
        self.assertTrue(all(b.get("naptanId") for b in seed["buses"]))


if __name__ == "__main__":
    unittest.main()
