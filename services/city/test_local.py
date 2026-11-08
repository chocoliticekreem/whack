"""On-demand local markets (local.py): sensor lookup, dedupe, caps, forecast-only, pricing and settlement on
recorded data, and that every settlement names a real sensor.

    python3 -m unittest discover -s services/city
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import local
import markets as mk
import pricing
import server
import test_city as tc
from data import Store
from exchange import Clock, Exchange, round_for
from ledger import Ledger

HERE = Path(__file__).resolve().parent
RECORDED = HERE / "fixtures" / "local"  # trimmed recorder files, 2026-11-07 18:00-20:45Z
MIN = tc.MIN
OXC = (51.515224, -0.141903)
FAR = (51.5000, -0.1000)  # south of the river by Waterloo Road: nothing settleable within 400 m in these tests
CAMS = {f"JamCams_00001.0{k}": (51.5100 + k * 0.0060, -0.1300) for k in range(1, 7)}  # 6 cams, ~670 m apart


def utc(h: int, m: int, s: int = 0) -> int:
    return int(datetime(2026, 11, 7, h, m, s, tzinfo=timezone.utc).timestamp() * 1000)


def cam_rows(ts: int, counts: dict[str, int], image_ms: int) -> list[dict]:
    stamp = datetime.fromtimestamp(image_ms / 1000, timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return [{"id": cid, "name": f"Cam {cid[-1]}", "lat": CAMS[cid][0], "lon": CAMS[cid][1], "lastModified": stamp,
             "yolo": {"people": n, "at": image_ms + 30_000}} for cid, n in counts.items()]


def synthetic(raw: tc.Raw, until: int, counts=lambda ts, cid: 5 + (ts // (5 * MIN)) % 4) -> None:
    """The test city (5 hubs + 15 stations, typical-day profile) plus 6 counted cams, a new image every 5 min."""
    tc.city(raw, until)
    for ts in range(tc.T0 - 60 * MIN, until + 1, MIN):
        img = ts - ts % (5 * MIN)
        raw.put("jamcams", ts, cam_rows(ts, {cid: counts(img, cid) for cid in CAMS}, img))


class SyntheticCase(unittest.TestCase):
    def setUp(self):
        self.raw = tc.Raw()
        self.addCleanup(self.raw.dir.cleanup)
        synthetic(self.raw, tc.T0 + 40 * MIN)
        self.now = tc.T0 + 31 * MIN
        self.ex = Exchange(Store(self.raw.root, tc.SPOTS), Clock(self.now), tc.SPOTS, Ledger(":memory:"))
        self.t = [0.0]
        self.ex.local = local.LocalMarkets(self.ex, clock=lambda: self.t[0])
        self.ex.tick(self.now)
        self.player = self.ex.ledger.create_player("tester")["player"]

    def click(self, lat: float, lon: float, player: str | None = None) -> dict:
        return self.ex.local.create({"lat": lat, "lon": lon}, player or self.player, self.now)


class SensorLookupTest(SyntheticCase):
    def test_a_station_within_400_m_is_found_nearest_first(self):
        near = local.nearest(self.ex.store, OXC[0] + 0.001, OXC[1], self.now)
        self.assertEqual(near[0]["id"], "940GZZLUOXC")
        self.assertEqual(near[0]["kind"], "station")
        self.assertLessEqual(near[0]["distanceM"], local.RADIUS_M)
        self.assertEqual([s["distanceM"] for s in near], sorted(s["distanceM"] for s in near))

    def test_nothing_settleable_within_400_m(self):
        self.assertEqual(local.nearest(self.ex.store, *FAR, self.now), [])

    def test_a_cam_counts_only_while_its_counts_are_fresh(self):
        cid = "JamCams_00001.01"
        self.assertTrue(any(s["id"] == cid for s in local.sensors(self.ex.store, self.now)))
        later = tc.T0 + 40 * MIN + local.CAM_FRESH_MS + MIN
        self.assertFalse(any(s["id"] == cid for s in local.sensors(self.ex.store, later)))

    def test_a_station_without_a_typical_day_is_not_settleable(self):
        ids = {s["id"] for s in local.sensors(self.ex.store, self.now)}
        self.assertIn("940GZZLUOXC", ids)
        self.assertNotIn("940GZZLUVIC", ids)  # the test feed has no Victoria


class CreateTest(SyntheticCase):
    def test_click_near_a_station_lists_a_tradeable_local_market_in_this_round(self):
        out = self.click(OXC[0] + 0.0005, OXC[1])
        m = out["market"]
        self.assertEqual(m["series"], "local_station")
        self.assertEqual(m["settlesAt"], round_for(self.now))
        self.assertEqual(m["status"], "open")
        self.assertEqual(m["local"]["sensor"]["id"], "940GZZLUOXC")
        self.assertIn(m["id"], self.ex.rounds[round_for(self.now)]["markets"])
        self.assertTrue(0.3 <= max(o["prob"] for o in m["options"]) <= 0.7, m["options"])
        self.assertEqual(m["futures"]["lines"], [self.ex.markets[m["id"]].spec["line"]])

    def test_a_second_click_near_the_same_sensor_returns_the_same_market(self):
        a = self.click(*CAMS["JamCams_00001.03"])["market"]
        b = self.click(CAMS["JamCams_00001.03"][0] + 0.0004, CAMS["JamCams_00001.03"][1], "someone-else")["market"]
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.ex.local.in_round(round_for(self.now))), 1)

    def test_creation_is_rate_limited_per_player(self):
        cams = list(CAMS)
        for cid in cams[:local.CREATE_N]:
            self.assertIn("market", self.click(*CAMS[cid]))
        with self.assertRaises(local.LocalLimit) as err:
            self.click(*CAMS[cams[local.CREATE_N]])
        self.assertEqual(err.exception.code, "rate_limited")
        self.assertIn(f"open another in {int(local.CREATE_WINDOW_S)} s", str(err.exception))
        other = self.ex.ledger.create_player("other")["player"]
        self.assertIn("market", self.click(*CAMS[cams[local.CREATE_N]], other))
        self.assertIn("market", self.click(*CAMS[cams[0]]))  # an existing market is not a new one
        self.t[0] += local.CREATE_WINDOW_S + 1
        self.assertIn("market", self.click(*CAMS[cams[local.CREATE_N + 1]]))

    def test_open_local_markets_are_capped_per_round(self):
        with mock.patch.object(local, "MAX_PER_ROUND", 2):
            self.click(*CAMS["JamCams_00001.01"])
            self.click(*CAMS["JamCams_00001.02"])
            with self.assertRaises(local.LocalLimit) as err:
                self.click(*CAMS["JamCams_00001.03"])
        self.assertEqual(err.exception.code, "local_cap")

    def test_a_click_outside_london_is_refused(self):
        with self.assertRaises(ValueError):
            self.click(52.2, 0.12)
        with self.assertRaises(ValueError):
            self.ex.local.create({"lat": "x"}, self.player, self.now)


class ForecastOnlyTest(SyntheticCase):
    def test_no_sensor_means_forecast_only_and_nothing_to_trade(self):
        before = len(self.ex.markets)
        out = self.click(*FAR)
        self.assertNotIn("market", out)
        fo = out["forecastOnly"]
        self.assertFalse(fo["tradeable"])
        self.assertEqual(fo["label"], local.FORECAST_LABEL)
        self.assertIn("400 m", fo["reason"])
        self.assertIsNone(fo["forecast"])
        self.assertEqual(len(self.ex.markets), before)

    def test_forecast_only_names_the_nearest_sensor_and_clicking_it_opens_its_market(self):
        near = self.click(*FAR)["forecastOnly"]["nearest"]
        self.assertGreater(near["distanceM"], local.RADIUS_M)
        out = self.click(near["lat"], near["lon"])
        self.assertIn("market", out)
        self.assertEqual(out["market"]["local"]["sensor"]["name"], near["name"])

    def test_the_forecast_reads_the_nearest_street_segments_from_the_simulation(self):
        origin = {"lat": FAR[0], "lon": FAR[1]}
        geo = {"origin": origin, "segments": [{"id": "s0", "line": [[-30, 0], [30, 0]]},
                                              {"id": "s1", "line": [[0, 60], [0, 200]]},
                                              {"id": "s2", "line": [[2000, 0], [2100, 0]]}]}
        replies = {"/geometry": geo, "/segments?horizon=0": {"at": self.now, "people": [1.0, 2.0, 3.0]},
                   "/segments?horizon=4": {"at": self.now + 4 * MIN, "people": [12.5, 4.0, 99.0]}}
        streets = local.StreetForecast("http://sim", lambda url: replies.get(url.removeprefix("http://sim")))
        self.assertTrue(streets.poll(self.now + 4 * MIN, self.now))
        self.ex.local.streets = streets
        fc = self.click(*FAR)["forecastOnly"]["forecast"]
        self.assertEqual([s["id"] for s in fc["segments"]], ["s0", "s1"])
        self.assertEqual(fc["people"], 16.5)
        self.assertEqual(fc["horizonMin"], 4)
        self.assertIsNone(streets.near(*FAR, self.now + local.SEG_STALE_MS + 1))


class RecordedCase(unittest.TestCase):
    """Real recorder files: Regent St/Conduit St and Shaftesbury Ave cams with YOLO counts, TfL Crowding, and the
    Bond Street / Oxford Circus typical Saturday."""

    def run_market(self, lat: float, lon: float, now: int) -> tuple[Exchange, mk.Market, str]:
        ex = Exchange(Store(RECORDED, tc.SPOTS), Clock(now), tc.SPOTS, Ledger(":memory:"))
        ex.tick(now)
        player = ex.ledger.create_player("rec")["player"]
        m = ex.markets[ex.local.create({"lat": lat, "lon": lon}, player, now)["market"]["id"]]
        return ex, m, player

    def settle_all(self, ex: Exchange, m: mk.Market, until: int) -> dict:
        t = m.opened_at
        while t <= until and m.result is None:
            ex.tick(t)
            t += 30_000
        self.assertIsNotNone(m.result, "never settled")
        return m.result

    def test_camera_market_prices_and_settles_on_the_first_image_after_the_close(self):
        now = utc(20, 20, 30)  # round 20:25, bets close 20:24:00
        ex, m, player = self.run_market(51.5132, -0.14091, now)
        self.assertEqual(m.sub, "local_cam")
        self.assertEqual(m.spec["sensor"]["id"], "JamCams_00001.07451")
        self.assertEqual(m.settles_at, utc(20, 25))
        probs = m.futures.probs()
        self.assertTrue(0.3 <= max(probs.values()) <= 0.7, probs)
        bet = ex.bet({"player": player, "market": m.id, "option": "over", "stake": 100}, now)
        self.assertEqual(bet["status"], "pending")  # the locally observable delay applies to local markets too
        self.assertEqual(bet["fillsAt"] - now, 45_000)
        res = self.settle_all(ex, m, utc(20, 40))
        order = ex.integrity.orders[bet["betId"]]
        self.assertEqual(order["status"], "filled")
        # Recorded: TfL published the next image at 20:26:44 (after the 20:24 close); YOLO counted 8 people in it.
        snap = res["settlement"]
        self.assertEqual(snap["imageAt"], utc(20, 26, 44))
        self.assertEqual(snap["value"], 8)
        self.assertEqual(res["winner"], "over" if 8 > m.spec["line"] else "under")
        self.assertEqual(snap["sensorId"], "JamCams_00001.07451")
        self.assertGreater(snap["imageAt"], m.closes_at)
        self.assertEqual(ex.ledger.equity(player), 900 + (order["fill"]["payout"] if res["winner"] == "over" else 0))

    def test_station_market_prices_and_settles_on_the_first_reading_after_the_close(self):
        now = utc(20, 20, 30)
        ex, m, _ = self.run_market(51.5143, -0.1497, now)
        self.assertEqual(m.sub, "local_station")
        self.assertEqual(m.spec["sensor"]["id"], "940GZZLUBND")
        probs = m.futures.probs()
        self.assertTrue(0.3 <= max(probs.values()) <= 0.7, probs)
        res = self.settle_all(ex, m, utc(20, 40))
        after = next((ts, s) for ts, s in ex.store.between("crowding", m.closes_at + 1, utc(20, 40))
                     if "940GZZLUBND" in s and ex.store.tfl_stamp(ts, "940GZZLUBND") > m.closes_at)
        pct = after[1]["940GZZLUBND"] / ex.store.typical("940GZZLUBND", after[0]) * 100
        self.assertEqual(res["settlement"]["fetchedAt"], after[0])
        self.assertAlmostEqual(res["settlement"]["value"], pct, places=2)
        self.assertEqual(res["winner"], "over" if pct > m.spec["line"] else "under")
        self.assertEqual(res["settlement"]["sensorId"], "940GZZLUBND")

    def test_no_new_image_after_the_close_voids_and_refunds(self):
        now = utc(20, 35, 30)  # round 20:40 closes 20:39; the recording stops at 20:45, the last image is 20:41:42
        ex, m, player = self.run_market(51.5132, -0.14091, now)
        bet = ex.bet({"player": player, "market": m.id, "option": "under", "stake": 50}, now)
        with mock.patch.object(local, "CAM_WAIT_MS", 2 * MIN):  # count lands at 20:42:27, after a 20:41 deadline
            res = self.settle_all(ex, m, utc(20, 50))
        self.assertEqual(ex.integrity.orders[bet["betId"]]["status"], "filled")
        self.assertIsNone(res["winner"])
        self.assertTrue(res["void"])
        self.assertEqual(ex.ledger.equity(player), 1000)


class SettleSourceTest(SyntheticCase):
    def test_every_settled_local_market_names_a_real_sensor(self):
        for cid in list(CAMS)[:3]:
            self.click(*CAMS[cid])
        self.click(*OXC)
        ids = [m.id for m in self.ex.local.in_round(round_for(self.now))]
        t = self.now
        while t < tc.T0 + 40 * MIN:
            self.ex.tick(t)
            t += 30_000
        real = set(local.STATIONS) | set(self.ex.store.ctx["cam_pos"])
        for mid in ids:
            m = self.ex.markets[mid]
            self.assertIsNotNone(m.result, mid)
            self.assertIn(m.spec["sensor"]["id"], real)
            self.assertEqual(m.result["settlement"]["sensorId"], m.spec["sensor"]["id"])
            self.assertIn(m.result["settlement"]["source"], (local.CAM_FEED, local.CROWD_FEED))


class TradersTest(SyntheticCase):
    def test_camera_eye_and_tube_crowd_opt_in_by_feed(self):
        cam = self.ex.markets[self.click(*CAMS["JamCams_00001.01"])["market"]["id"]]
        st = self.ex.markets[self.click(*OXC)["market"]["id"]]
        pick = local.cams_local(cam, self.ex.store, self.now, {})
        self.assertIn(pick[0], ("over", "under"))
        self.assertIn("frames", pick[1])
        self.assertIn("local_cam", local.traders.PLAYS["cams"])
        self.assertIn("local_station", local.traders.PLAYS["tube"])
        self.assertIsNone(local.traders.PLAYS["tube"].get("local_cam"))
        self.assertEqual(local.traders.decide("lines", st, self.ex.store, self.now, {}), None)


class IntegrityTest(SyntheticCase):
    def test_local_markets_get_the_locally_observable_delay_and_pause_on_their_own_sensor(self):
        cam = self.ex.markets[self.click(*CAMS["JamCams_00001.01"])["market"]["id"]]
        for series in ("local_cam", "local_station"):
            self.assertEqual(local.integrity.rule_for(series).delay_s, 45)
            self.assertEqual(local.integrity.rule_for(series).cap_pct, 0.10)
        cid = cam.spec["sensor"]["id"]
        self.assertIsNone(local.cam_moved(cam, {cid: 9}, {cid: 11}))
        self.assertIn("9 → 25", local.cam_moved(cam, {cid: 9}, {cid: 25}))


class CamSimTest(unittest.TestCase):
    def test_a_repeated_count_is_scored_once(self):
        sim = local.CamSim("c", 3.0, 0.1, 0.5, 9, tc.T0, 5, __import__("random").Random(1))
        sim.line = 8.5
        fut = pricing.Futures(sim, ["over", "under"])
        self.assertEqual(fut.observe(tc.T0 + MIN, {"c": 9}), 0)
        cut = fut.observe(tc.T0 + 2 * MIN, {"c": 30})
        self.assertGreater(cut, 0)
        self.assertEqual(fut.observe(tc.T0 + 3 * MIN, {"c": 30}), 0)


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.raw = tc.Raw()
        self.addCleanup(self.raw.dir.cleanup)
        synthetic(self.raw, tc.T0 + 40 * MIN)
        ex = Exchange(Store(self.raw.root, tc.SPOTS), Clock(tc.T0 + 31 * MIN), tc.SPOTS, Ledger(":memory:"))
        ex.tick(tc.T0 + 31 * MIN)
        for p in (mock.patch.object(server, "EX", ex), mock.patch.object(server, "NEW_PLAYERS", server.RateLimit(5, 3600))):
            p.start()
            self.addCleanup(p.stop)
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def call(self, path: str, body=None, token: str | None = None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Player-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read() or b"{}")

    def test_post_needs_a_token_and_get_lists_the_round(self):
        _, p = self.call("/player/new", {})
        status, _ = self.call("/local", {"player": p["player"], "lat": OXC[0], "lon": OXC[1]})
        self.assertEqual(status, 401)
        status, out = self.call("/local", {"player": p["player"], "lat": OXC[0], "lon": OXC[1]}, p["token"])
        self.assertEqual(status, 200, out)
        status, far = self.call("/local", {"player": p["player"], "lat": FAR[0], "lon": FAR[1]}, p["token"])
        self.assertEqual(status, 200)
        self.assertIn("forecastOnly", far)
        status, listed = self.call("/local")
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in listed["markets"]], [out["market"]["id"]])
        self.assertGreaterEqual(listed["sensors"]["stations"], 5)
        status, bet = self.call("/bet", {"player": p["player"], "market": out["market"]["id"], "option": "over",
                                         "stake": 50}, p["token"])
        self.assertEqual(status, 200, bet)


if __name__ == "__main__":
    unittest.main()
