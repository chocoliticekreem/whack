"""Real-data path: recorder jamcams/yolo parser, bus lateness proxy, seed helpers. Fixtures: fixtures/real/."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import engine
import feeds
import real_seed

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
