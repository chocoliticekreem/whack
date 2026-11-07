"""python3 -m unittest discover -s services/health"""

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import health

NOW = datetime(2026, 11, 7, 17, 30, 0, tzinfo=timezone.utc).timestamp()


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(raw: Path, source: str, t: float, payload=None) -> Path:
    """Append one record the way the recorder does: one gzip member per write."""
    dt = datetime.fromtimestamp(t, timezone.utc)
    path = raw / source / dt.strftime("%Y-%m-%d") / f"{dt:%H}.ndjson.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": iso(t), "source": source, "payload": payload or []}, separators=(",", ":"))
    with gzip.open(path, "ab") as f:
        f.write((line + "\n").encode())
    return path


class ClassifyTest(unittest.TestCase):
    def test_thresholds_for_a_60s_feed(self):
        self.assertEqual(health.classify(30, 60), "ok")
        self.assertEqual(health.classify(120, 60), "ok")
        self.assertEqual(health.classify(121, 60), "stale")  # one missed write
        self.assertEqual(health.classify(180, 60), "stale")
        self.assertEqual(health.classify(181, 60), "dead")

    def test_slow_feed_scales_with_cadence(self):
        self.assertEqual(health.classify(500, 300), "ok")
        self.assertEqual(health.classify(700, 300), "stale")
        self.assertEqual(health.classify(901, 300), "dead")

    def test_fast_feed_is_not_dead_before_the_floor(self):
        self.assertEqual(health.classify(100, 30), "stale")
        self.assertEqual(health.classify(181, 30), "dead")

    def test_no_data_is_dead_and_streams_have_no_cadence(self):
        self.assertEqual(health.classify(None, 60), "dead")
        self.assertEqual(health.classify(None, None), "event")


class SourceHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_and_empty_dirs_are_dead(self):
        (self.raw / "tube" / "2026-11-07").mkdir(parents=True)
        for src in ("bus", "tube"):
            s = health.source_health(self.raw / src, 60, NOW)
            self.assertEqual((s["status"], s["age_s"], s["records_10min"], s["last_ts"]), ("dead", None, 0, None))

    def test_counts_only_the_last_10_min(self):
        for back in (900, 540, 300, 30):  # 17:15 (outside), 17:21, 17:25, 17:29:30
            write(self.raw, "bus", NOW - back)
        write(self.raw, "bus", NOW - 1900)  # 16:58:20, previous hour file
        s = health.source_health(self.raw / "bus", 60, NOW)
        self.assertEqual(s["records_10min"], 3)
        self.assertEqual(s["age_s"], 30)
        self.assertEqual(s["last_ts"], iso(NOW - 30))
        self.assertEqual(s["status"], "ok")

    def test_window_reaching_into_previous_hour(self):
        write(self.raw, "tube", NOW - 1900)  # 16:58:20
        write(self.raw, "tube", NOW - 1740)  # 17:01:00
        s = health.source_health(self.raw / "tube", 60, NOW - 1800 + 300)  # now = 17:05:00
        self.assertEqual(s["records_10min"], 2)

    def test_stalled_since_yesterday_is_dead(self):
        write(self.raw, "tube", NOW - 86400)
        s = health.source_health(self.raw / "tube", 60, NOW)
        self.assertEqual(s["status"], "dead")
        self.assertEqual(s["age_s"], 86400)

    def test_corrupt_gzip_tail_keeps_earlier_lines(self):
        path = write(self.raw, "jamcams", NOW - 120)
        write(self.raw, "jamcams", NOW - 60)
        with open(path, "ab") as f:  # a write cut off mid-member by a kill
            f.write(gzip.compress(b'{"ts":"2026-11-07T17:29:59Z","source":"jamcams","payload":[]}\n')[:20])
        s = health.source_health(self.raw / "jamcams", 60, NOW)
        self.assertEqual(s["records_10min"], 2)
        self.assertEqual(s["age_s"], 60)

    def test_garbage_file_and_bad_json_line_are_skipped(self):
        write(self.raw, "line_status", NOW - 40)
        junk = self.raw / "line_status" / "2026-11-07" / "16.ndjson.gz"
        junk.write_bytes(b"not gzip at all")
        bad = self.raw / "road_status" / "2026-11-07" / "17.ndjson.gz"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(gzip.compress(b"{broken json\n"))
        self.assertEqual(health.source_health(self.raw / "line_status", 60, NOW)["age_s"], 40)
        self.assertEqual(health.source_health(self.raw / "road_status", 60, NOW)["status"], "dead")

    def test_jamcams_yolo_freshness(self):
        ms = lambda t: int(t * 1000)  # noqa: E731
        rows = [{"id": "a", "yolo": {"people": 1, "at": ms(NOW - 30)}},
                {"id": "b", "yolo": {"people": 2, "at": ms(NOW - 599)}},
                {"id": "c", "yolo": {"people": 3, "at": ms(NOW - 3600)}},
                {"id": "d"}]
        write(self.raw, "jamcams", NOW - 20, rows)
        s = health.source_health(self.raw / "jamcams", 60, NOW)
        self.assertEqual((s["cams"], s["yolo_cams"], s["yolo_fresh_10min"]), (4, 3, 2))


class YoloStatusTest(unittest.TestCase):
    def test_status(self):
        fresh_jam = {"status": "ok", "yolo_cams": 8, "yolo_fresh_10min": 8}
        self.assertEqual(health.yolo_status(False, fresh_jam), "dead")
        self.assertEqual(health.yolo_status(True, fresh_jam), "ok")
        self.assertEqual(health.yolo_status(True, {**fresh_jam, "yolo_fresh_10min": 3}), "stale")
        self.assertEqual(health.yolo_status(True, {**fresh_jam, "yolo_fresh_10min": 0}), "dead")
        # counts can't be judged while the recorder itself is behind: reachability only
        self.assertEqual(health.yolo_status(True, {**fresh_jam, "status": "dead", "yolo_fresh_10min": 0}), "ok")


class SnapshotTest(unittest.TestCase):
    def test_core_drives_status_and_degraded_lists_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            rows = [{"id": "a", "yolo": {"at": int((NOW - 10) * 1000)}}]
            for src in health.CORE:
                write(raw, src, NOW - 20, rows if src == "jamcams" else None)
            write(raw, "news", NOW - 7200)
            snap = health.snapshot(raw, NOW, tfl={"status": "valid"}, yolo_reachable=True)
            self.assertEqual(snap["status"], "ok")
            self.assertIn("news", snap["degraded"])
            self.assertNotIn("wiki_edits", snap["degraded"])
            self.assertEqual(snap["sources"]["aircraft"]["status"], "dead")  # in the poll table, never written

            snap = health.snapshot(raw, NOW + 200, tfl={"status": "invalid"}, yolo_reachable=False)
            self.assertEqual(snap["status"], "dead")
            self.assertIn("yolo", snap["degraded"])
            self.assertIn("tfl_key", snap["degraded"])
            json.dumps(snap)

    def test_cadences_come_from_the_recorder_poll_table(self):
        c = health.cadences()
        self.assertEqual((c["bus"], c["jamcams"], c["bikepoint"]), (60, 60, 120))
        self.assertEqual(c["news"], 900)
        self.assertIsNone(c["wiki_edits"])


if __name__ == "__main__":
    unittest.main()
