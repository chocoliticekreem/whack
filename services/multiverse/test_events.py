import gzip
import json
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events  # noqa: E402
import venues  # noqa: E402

T0 = "2030-03-02T18:00:00Z"
NOW = events.parse_ms(T0)
MIN = 60_000


def rec(payload, ts=T0):
    return {"ts": ts, "payload": payload}


def arrivals():
    """Fictional network: line l1 A-B-C-D-E, line l2 X-B-Y; B is an interchange."""
    rows = []
    for line, chain in (("l1", ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]), ("l2", ["Xray", "Bravo", "Yankee"])):
        for i, name in enumerate(chain):
            nap = f"N{name[:3].upper()}"
            loc = f"Between {name} and {chain[i + 1]}" if i + 1 < len(chain) else f"At {name}"
            rows.append({"naptanId": nap, "stationName": f"{name} Underground Station", "lineId": line,
                         "currentLocation": loc})
    return rows


def status(line, sev, desc, reason, name=None):
    return {"id": line, "name": name or line.upper(), "mode": "tube",
            "statuses": [{"statusSeverity": sev, "statusSeverityDescription": desc, "reason": reason}]}


def by_where(ivs):
    return {(i["kind"], json.dumps(i["where"], sort_keys=True)): i for i in ivs}


def nap(n):
    return json.dumps({"naptan": n})


class LineRule(unittest.TestCase):
    def snap(self, *statuses):
        return {"line_status": rec(list(statuses)), "tube": rec(arrivals())}

    def test_part_suspension_closes_section_and_displaces(self):
        ivs = events.derive(self.snap(status("l1", 3, "Part Suspended", "L1: No service between Alpha and Charlie.")), NOW)
        got = by_where(ivs)
        self.assertEqual(got[("closure", nap("NALP"))]["magnitude"], 0.0)
        self.assertEqual(got[("closure", nap("NCHA"))]["magnitude"], 0.0)
        self.assertEqual(got[("capacity", nap("NBRA"))]["magnitude"], 0.5)
        self.assertNotIn(("closure", nap("NDEL")), got)
        self.assertIn("No service between Alpha and Charlie".lower().split(" between")[0], got[("closure", nap("NALP"))]["reason"].lower())
        self.assertIn("Alpha–Charlie", got[("closure", nap("NALP"))]["reason"])
        surges = [i for i in ivs if i["kind"] == "demand_surge"]
        self.assertTrue(surges)
        self.assertTrue(all(i["rule"] == "line_displacement" and i["magnitude"] > 0 for i in surges))
        self.assertTrue({i["where"]["naptan"] for i in surges} <= {"NXRA", "NYAN", "NDEL", "NECH"})
        self.assertFalse(any(i["planned"] for i in ivs))

    def test_via_follows_named_branch(self):
        ivs = events.derive(self.snap(status("l1", 6, "Severe Delays", "L1: Severe delays between Alpha and Delta via Charlie.")), NOW)
        caps = {i["where"]["naptan"]: i["magnitude"] for i in ivs if i["rule"] == "line_disruption"}
        self.assertEqual(set(caps), {"NALP", "NBRA", "NCHA", "NDEL"})
        self.assertEqual(caps["NALP"], 0.5)
        self.assertEqual(caps["NBRA"], 0.75)
        self.assertFalse([i for i in ivs if i["kind"] == "demand_surge"])

    def test_rest_of_line_excludes_suspended_section(self):
        reason = "L1: No service between Alpha and Bravo. SEVERE DELAYS on the rest of the line."
        line = {"id": "l1", "name": "L1", "statuses": [
            {"statusSeverity": 6, "statusSeverityDescription": "Severe Delays", "reason": reason},
            {"statusSeverity": 3, "statusSeverityDescription": "Part Suspended", "reason": reason}]}
        got = by_where(events.derive(self.snap(line), NOW))
        self.assertEqual(got[("closure", nap("NALP"))]["magnitude"], 0.0)
        self.assertEqual(got[("capacity", nap("NDEL"))]["magnitude"], 0.5)
        self.assertIn("rest of the line", got[("capacity", nap("NDEL"))]["reason"])

    def test_part_status_without_section_and_unknown_line_skipped(self):
        with self.assertLogs("multiverse.events", "INFO") as cm:
            ivs = events.derive(self.snap(status("l1", 3, "Part Suspended", "L1: disruption."),
                                          status("l9", 2, "Suspended", "L9: No service.")), NOW)
        self.assertEqual(ivs, [])
        self.assertTrue(any("names no section" in m for m in cm.output))
        self.assertTrue(any("no station data for l9" in m for m in cm.output))

    def test_minor_delays_ignored(self):
        self.assertEqual(events.derive(self.snap(status("l1", 9, "Minor Delays", "L1: Minor delays.")), NOW), [])

    def test_planned_closure_flagged(self):
        ivs = events.derive(self.snap(status("l2", 4, "Planned Closure", "L2: no service at weekends.")), NOW)
        line_ivs = [i for i in ivs if i["rule"] == "line_disruption"]
        self.assertEqual({i["where"]["naptan"] for i in line_ivs}, {"NXRA", "NBRA", "NYAN"})
        self.assertTrue(all(i["planned"] for i in ivs))


def profile(naptan, value):
    bands = [{"timeBand": f"{h:02d}:{m:02d}-xx", "percentageOfBaseLine": value} for h in range(24) for m in (0, 15, 30, 45)]
    days = [{"dayOfWeek": d, "timeBands": bands} for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")]
    return {"naptan": naptan, "name": naptan, "daysOfWeek": days}


class CrowdingRule(unittest.TestCase):
    def snap(self, live):
        rows = [{"naptan": "S1", "name": "Station One", "dataAvailable": True, "percentageOfBaseline": live,
                 "timeUtc": "2030-03-02T17:55:00.000Z"}]
        return {"crowding": rec(rows), "crowding_profile": rec([profile("S1", 0.2)])}

    def test_surge_magnitude_is_live_over_typical_minus_one(self):
        (iv,) = events.derive(self.snap(0.4), NOW)
        self.assertEqual((iv["kind"], iv["where"], iv["magnitude"]), ("demand_surge", {"naptan": "S1"}, 1.0))
        self.assertIn("x2.00", iv["reason"])

    def test_surge_capped_and_threshold(self):
        self.assertEqual(events.derive(self.snap(5.0), NOW)[0]["magnitude"], events.SURGE_CAP)
        self.assertEqual(events.derive(self.snap(0.25), NOW), [])


def disruption(did, street, category, pts, start="2030-01-01T00:00:00Z", end="2030-12-31T00:00:00Z"):
    return {"disruptionId": did, "streetName": street, "closure": "Closed", "category": category,
            "severity": "Moderate", "comments": "Road closed.", "startDateTime": start, "endDateTime": end,
            "lineString": json.dumps(pts), "startLat": pts[0][1], "startLon": pts[0][0],
            "endLat": pts[-1][1], "endLon": pts[-1][0]}


class RoadRule(unittest.TestCase):
    def test_closure_circle_and_planned_flag(self):
        rows = [disruption("D1", "SOME ROAD", "Works", [[-0.1, 51.5], [-0.1, 51.501]]),
                disruption("D2", "OTHER ROAD", "Collisions", [[-0.2, 51.4], [-0.2001, 51.4]])]
        ivs = events.derive({"disruptions": rec(rows)}, NOW)
        got = {i["reason"].split(" closed")[0]: i for i in ivs}
        works, crash = got["Some Road"], got["Other Road"]
        self.assertEqual(works["kind"], "closure")
        self.assertAlmostEqual(works["where"]["lat"], 51.5005, places=4)
        self.assertTrue(80 <= works["where"]["radius_m"] <= 90)
        self.assertTrue(works["planned"])
        self.assertFalse(crash["planned"])
        self.assertEqual(crash["where"]["radius_m"], events.ROAD_MIN_RADIUS_M)

    def test_inactive_and_open_roads_ignored(self):
        old = disruption("D3", "PAST ROAD", "Works", [[-0.1, 51.5]], end="2029-01-01T00:00:00Z")
        open_ = dict(disruption("D4", "OPEN ROAD", "Works", [[-0.1, 51.5]]), closure="Open")
        self.assertEqual(events.derive({"disruptions": rec([old, open_])}, NOW), [])

    def test_duplicate_sections_dedupe(self):
        row = disruption("D1", "SOME ROAD", "Works", [[-0.1, 51.5], [-0.1, 51.501]])
        self.assertEqual(len(events.derive({"disruptions": rec([row, dict(row), dict(row)])}, NOW)), 1)

    def test_road_status_closure(self):
        rows = [{"id": "r1", "displayName": "R1", "statusSeverity": "Closure", "statusSeverityDescription": "Closure",
                 "bounds": "[[-0.1,51.5],[-0.09,51.51]]"},
                {"id": "r2", "displayName": "R2", "statusSeverity": "Serious", "bounds": "[[-0.1,51.5],[-0.09,51.51]]"}]
        (iv,) = events.derive({"road_status": rec(rows)}, NOW)
        self.assertEqual(iv["kind"], "closure")
        self.assertIn("R1", iv["reason"])


def fixture(venue, kickoff, status="NS", lat=None, lon=None, stations=None, league="Some League"):
    return {"idEvent": "e1", "league": league, "home": "Home FC", "away": "Away FC", "kickoff_utc": kickoff,
            "venue": venue, "lat": lat, "lon": lon, "stations": stations or [], "status": status}


class FixtureRule(unittest.TestCase):
    def test_known_venue_end_surge_from_capacity(self):
        name, v = sorted(venues.VENUES.items())[0]
        ivs = events.derive({"scrape_fixtures": rec([fixture(name, "2030-03-02T17:00:00Z")])}, NOW)
        ends = [i for i in ivs if i["rule"] == "fixture_end"]
        self.assertTrue(ends)
        expect = min(events.FIXTURE_SURGE_CAP, v["capacity"] * events.FIXTURE_TRANSIT_SHARE / len(v["stations"])
                     / events.STATION_HOURLY_RIDERS)
        for iv in ends:
            self.assertEqual(iv["start"], events.parse_ms("2030-03-02T17:00:00Z") + venues.DURATION_MIN["football"] * MIN)
            self.assertEqual(iv["end"] - iv["start"], events.FIXTURE_DECAY_MIN * MIN)
            self.assertAlmostEqual(iv["magnitude"], round(expect, 3))
            self.assertEqual(iv["shape"], "linear_decay")
            self.assertFalse(iv["planned"])
        self.assertFalse([i for i in ivs if i["rule"] == "fixture_start"])

    def test_upcoming_fixture_has_arrival_surge(self):
        name = sorted(venues.VENUES)[0]
        ivs = events.derive({"scrape_fixtures": rec([fixture(name, "2030-03-02T19:00:00Z")])}, NOW)
        starts = [i for i in ivs if i["rule"] == "fixture_start"]
        self.assertTrue(starts)
        self.assertEqual(starts[0]["end"], events.parse_ms("2030-03-02T19:00:00Z"))

    def test_finished_status_pulls_end_to_observation(self):
        name = sorted(venues.VENUES)[0]
        ivs = events.derive({"scrape_fixtures": rec([fixture(name, "2030-03-02T16:30:00Z", status="FT")])}, NOW)
        self.assertEqual({i["start"] for i in ivs}, {NOW})

    def test_unknown_venue_with_location_uses_default_capacity(self):
        ivs = events.derive({"scrape_fixtures": rec([fixture("Nowhere Park", "2030-03-02T17:00:00Z", lat=51.3,
                                                             lon=0.3, stations=["Nowhere"])])}, NOW)
        (iv,) = [i for i in ivs if i["rule"] == "fixture_end"]
        self.assertEqual(iv["where"], {"lat": 51.3, "lon": 0.3, "radius_m": events.FIXTURE_STATION_RADIUS_M})
        self.assertIn(f"default capacity {venues.DEFAULT_CAPACITY}", iv["reason"])

    def test_unknown_venue_without_location_skipped_and_logged(self):
        with self.assertLogs("multiverse.events", "INFO") as cm:
            ivs = events.derive({"scrape_fixtures": rec([fixture("Nowhere Park", "2030-03-02T17:00:00Z")])}, NOW)
        self.assertEqual(ivs, [])
        self.assertTrue(any("no location" in m for m in cm.output))

    def test_called_off_and_far_future_skipped(self):
        name = sorted(venues.VENUES)[0]
        rows = [fixture(name, "2030-03-02T17:00:00Z", status="Postponed"), fixture(name, "2030-03-04T15:00:00Z")]
        self.assertEqual(events.derive({"scrape_fixtures": rec(rows)}, NOW), [])

    def test_tube_stations_resolved_to_naptans(self):
        ivs = events.derive({"scrape_fixtures": rec([fixture("Nowhere Park", "2030-03-02T17:00:00Z", lat=51.3, lon=0.3,
                                                             stations=["Charlie", "Delta"])]),
                             "tube": rec(arrivals())}, NOW)
        self.assertEqual({json.dumps(i["where"]) for i in ivs}, {nap("NCHA"), nap("NDEL")})

    def test_venue_table_is_complete(self):
        for name, v in venues.VENUES.items():
            self.assertTrue(v["capacity"] > 0 and v["stations"] and v["sport"] in venues.DURATION_MIN, name)


class RainRule(unittest.TestCase):
    def snap(self, mm):
        steps = [{"time": f"2030-03-02T{h:02d}:{m:02d}", "precipitation": v}
                 for (h, m), v in zip([(17, 45), (18, 0), (18, 15), (18, 30), (18, 45), (19, 0)], mm)]
        return {"rain_now": rec({"current": {"time": "2030-03-02T18:00", "precipitation": mm[1]}, "minutely_15": steps})}

    def test_onset_ahead(self):
        (iv,) = events.derive(self.snap([0, 0, 0, 0.4, 0.2, 0]), NOW)
        self.assertEqual((iv["kind"], iv["start"], iv["end"]), ("weather", NOW + 30 * MIN, NOW + 60 * MIN))
        self.assertAlmostEqual(iv["magnitude"], 1.6)
        self.assertEqual(iv["where"]["radius_m"], events.CITY["radius_m"])

    def test_dry_is_nothing(self):
        self.assertEqual(events.derive(self.snap([0] * 6), NOW), [])


class MergeAndDeterminism(unittest.TestCase):
    def full_snapshot(self):
        return {
            "line_status": rec([status("l1", 3, "Part Suspended", "L1: No service between Alpha and Charlie.")]),
            "tube": rec(arrivals()),
            "crowding": rec([{"naptan": "NDEL", "name": "Delta", "dataAvailable": True, "percentageOfBaseline": 0.6,
                              "timeUtc": "2030-03-02T17:55:00.000Z"}]),
            "crowding_profile": rec([profile("NDEL", 0.2), profile("NALP", 0.2)]),
            "disruptions": rec([disruption("D1", "SOME ROAD", "Works", [[-0.1, 51.5], [-0.1, 51.501]])]),
        }

    def test_deterministic_and_order_independent(self):
        a = events.derive(self.full_snapshot(), NOW)
        b = events.derive(self.full_snapshot(), NOW)
        self.assertEqual(a, b)
        shuffled = self.full_snapshot()
        random.Random(7).shuffle(shuffled["tube"]["payload"])
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(events.derive(shuffled, NOW), sort_keys=True))

    def test_overlapping_surges_at_one_station_merge(self):
        ivs = events.derive(self.full_snapshot(), NOW)
        delta = [i for i in ivs if i["kind"] == "demand_surge" and i["where"] == {"naptan": "NDEL"}]
        self.assertEqual(len(delta), 1)
        self.assertEqual(delta[0]["rule"], "crowding_surge+line_displacement")
        self.assertIn(" | ", delta[0]["reason"])
        self.assertGreater(delta[0]["magnitude"], 2.0 - 1e-9)

    def test_merge_keeps_disjoint_windows_apart(self):
        a = events.make("demand_surge", {"naptan": "S"}, NOW, NOW + 10 * MIN, 0.5, "x", "r1", "first", False)
        b = events.make("demand_surge", {"naptan": "S"}, NOW + 5 * MIN, NOW + 20 * MIN, 0.4, "y", "r2", "second", False)
        c = events.make("demand_surge", {"naptan": "S"}, NOW + 60 * MIN, NOW + 70 * MIN, 0.4, "y", "r2", "third", False)
        out = events.merge([c, b, a])
        self.assertEqual(len(out), 2)
        self.assertEqual((out[0]["start"], out[0]["end"], out[0]["magnitude"]), (NOW, NOW + 20 * MIN, 0.9))
        planned = events.make("demand_surge", {"naptan": "S"}, NOW, NOW + 10 * MIN, 0.5, "x", "r1", "p", True)
        self.assertEqual(len(events.merge([a, planned])), 2)

    def test_every_intervention_matches_the_shape(self):
        for iv in events.derive(self.full_snapshot(), NOW):
            self.assertIn(iv["kind"], {"closure", "capacity", "demand_surge", "demand_drop", "weather"})
            self.assertTrue(set(iv["where"]) in ({"segments"}, {"lat", "lon", "radius_m"}, {"naptan"}))
            self.assertLess(iv["start"], iv["end"])
            self.assertIsInstance(iv["magnitude"], float)
            self.assertTrue(iv["reason"] and iv["source"] and isinstance(iv["planned"], bool))


class Replay(unittest.TestCase):
    def test_replay_yields_per_update(self):
        with tempfile.TemporaryDirectory() as d:
            for source, ts, payload in (
                    ("tube", "2030-03-02T17:59:00Z", arrivals()),
                    ("line_status", "2030-03-02T18:00:00Z", [status("l1", 3, "Part Suspended", "L1: No service between Alpha and Bravo.")]),
                    ("line_status", "2030-03-02T18:01:00Z", [status("l1", 10, "Good Service", None)])):
                path = Path(d) / source / ts[:10] / f"{ts[11:13]}.ndjson.gz"
                path.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(path, "at") as f:
                    f.write(json.dumps({"ts": ts, "source": source, "payload": payload}) + "\n")
            out = list(events.replay(d, NOW - 5 * MIN, NOW + 5 * MIN))
        self.assertEqual([ts for ts, _ in out], [NOW, NOW + MIN])
        self.assertTrue(any(i["kind"] == "closure" for i in out[0][1]))
        self.assertEqual(out[1][1], [])


class NoHardcodedNames(unittest.TestCase):
    """events.py must stay generic: names live in the feeds and in venues.py, never in rule code."""

    LINES = ["bakerloo", "central", "circle", "district", "hammersmith", "jubilee", "metropolitan", "northern",
             "piccadilly", "victoria", "waterloo", "elizabeth", "liberty", "lioness", "mildmay", "suffragette",
             "weaver", "windrush", "dlr", "overground", "tram"]
    PLACES = ["oxford circus", "king's cross", "kings cross", "elephant", "lambeth", "bank", "paddington",
              "euston", "stratford", "wembley", "twickenham", "lord's", "o2", "arsenal", "chelsea", "tottenham",
              "fulham", "west ham", "brentford", "charlton", "crystal palace"]

    def test_events_py_names_nothing(self):
        src = (Path(__file__).resolve().parent / "events.py").read_text().lower()
        names = set(self.LINES + self.PLACES)
        for vname, v in venues.VENUES.items():
            names.add(vname.lower())
            names.update(s.lower() for s in v["stations"])
        found = sorted(n for n in names if re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", src))
        self.assertEqual(found, [])
        self.assertIsNone(re.search(r"940g|490\d{3}|tims-", src))
        self.assertIsNone(re.search(r"20\d\d-\d\d-\d\d", src))


if __name__ == "__main__":
    unittest.main()
