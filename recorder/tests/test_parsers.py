"""Offline parser tests: python3 -m unittest discover -s recorder"""

import json
import unittest
from datetime import date
from pathlib import Path

import alt
import record
from scrape import fixtures

FIX = Path(__file__).parent / "fixtures"


class AircraftTest(unittest.TestCase):
    def test_summary_counts_stack_low_and_ground(self):
        out = alt.parse_aircraft(json.loads((FIX / "adsb.json").read_text()))
        s = out["summary"]
        self.assertEqual(s["count"], 7)
        self.assertEqual(s["ground"], 2)  # one real helicopter on the ground + the crafted one
        self.assertEqual(s["holding"]["BNN"], 1)  # 9000 ft over Bovingdon; the 5000 ft one is below the stack
        self.assertEqual(s["holding_total"], 1)
        self.assertEqual(out["aircraft"][4]["flight"], "BAW1")  # callsign padding stripped
        self.assertEqual(set(out["aircraft"][0]), set(alt.AIRCRAFT_KEYS))


class GridTest(unittest.TestCase):
    def test_latest_fuelinst_takes_newest_period(self):
        rows = json.loads((FIX / "fuelinst.json").read_text())["data"]
        latest = alt.latest_fuelinst(rows)
        self.assertEqual(latest["startTime"], max(r["startTime"] for r in rows))
        self.assertIn("CCGT", latest["fuel"])
        self.assertGreater(latest["total_mw"], 10000)
        self.assertIsNone(alt.latest_fuelinst([]))


class GdeltTest(unittest.TestCase):
    def test_keeps_only_events_near_oxford_circus(self):
        scanned, events = alt.parse_gdelt_export((FIX / "gdelt_export.zip").read_bytes())
        self.assertEqual(scanned, 7)  # 3 London + 3 abroad + 1 malformed row
        self.assertEqual([e["GLOBALEVENTID"] for e in events], ["1331968574", "1331968575", "1331968617"])
        self.assertIsInstance(events[0]["AvgTone"], float)
        self.assertTrue(events[0]["SOURCEURL"].startswith("http"))

    def test_lastupdate_and_previous_slot(self):
        txt = ("1 a http://data.gdeltproject.org/gdeltv2/20261107131500.export.CSV.zip\n"
               "2 b http://data.gdeltproject.org/gdeltv2/20261107131500.mentions.CSV.zip\n")
        url = alt.gdelt_export_url(txt)
        self.assertEqual(url, "https://data.gdeltproject.org/gdeltv2/20261107131500.export.CSV.zip")
        self.assertTrue(alt.gdelt_previous_url(url).endswith("/20261107130000.export.CSV.zip"))
        midnight = url.replace("131500", "000000")
        self.assertTrue(alt.gdelt_previous_url(midnight).endswith("/20261106234500.export.CSV.zip"))


class WikiTest(unittest.TestCase):
    def test_sse_stream_matches_only_enwiki_watchlist(self):
        lines = [ln.encode() + b"\n" for ln in (FIX / "recentchange.sse").read_text().splitlines()]
        rows = [r for _, d in alt.sse_events(lines) if (r := alt.match_wiki_edit(d))]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Oxford Street")
        self.assertEqual((rows[0]["len_old"], rows[0]["len_new"]), (100, 120))
        self.assertNotIn("user", rows[0])

    def test_pageviews(self):
        doc = {"items": [{"timestamp": "2026110600", "views": 299}]}
        self.assertEqual(alt.parse_pageviews(doc), {"2026-11-06": 299})


class FixturesTest(unittest.TestCase):
    def test_london_home_games_in_window_deduped(self):
        ev = {"idEvent": "1", "idHomeTeam": "133632", "strHomeTeam": "Crystal Palace", "strAwayTeam": "Liverpool",
              "strTimestamp": "2026-11-08T14:00:00", "strLeague": "English Premier League", "strStatus": "NS"}
        away = dict(ev, idEvent="2", idHomeTeam="133602")  # not a London home game
        later = dict(ev, idEvent="3", strTimestamp="2026-11-12T19:45:00")
        rows = fixtures.parse_events([ev, ev, away, later], date(2026, 11, 7))
        self.assertEqual([r["idEvent"] for r in rows], ["1"])
        self.assertEqual(rows[0]["egress_est_utc"], "2026-11-08T15:55:00Z")
        self.assertIn("Selhurst", rows[0]["stations"])


class SourcesTest(unittest.TestCase):
    def test_cli_names_match_registered_sources(self):
        names = [n for n, _, _ in record.Recorder(yolo=False).sources()]
        self.assertEqual(names, list(record.SOURCE_NAMES))


if __name__ == "__main__":
    unittest.main()
