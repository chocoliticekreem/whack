"""The HTTP boundary: player tokens, the new-player rate limit, body size, error codes and the lock.

    python3 -m unittest discover -s services/city
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import server
import test_city as tc
from data import Store
from exchange import Clock, Exchange
from ledger import GRANT, Ledger


class HttpCase(unittest.TestCase):
    def setUp(self):
        tc.no_delay(self)
        self.raw = tc.Raw()
        self.addCleanup(self.raw.dir.cleanup)
        tc.city(self.raw, tc.T0 + 30 * tc.MIN)
        ex = Exchange(Store(self.raw.root, tc.SPOTS), Clock(tc.T0), tc.SPOTS, Ledger(":memory:"))
        ex.tick(tc.T0)
        self.clock = [0.0]
        patches = [mock.patch.object(server, "EX", ex),
                   mock.patch.object(server, "NEW_PLAYERS", server.RateLimit(30, 60, lambda: self.clock[0])),
                   mock.patch.object(server, "NEW_PLAYERS_DAY", server.RateLimit(500, 86_400, lambda: self.clock[0]))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.ex = ex
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def call(self, path: str, body=None, token: str | None = None, headers: dict | None = None, raw: bytes | None = None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method="GET" if data is None else "POST")
        req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("X-Player-Token", token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read() or b"{}")

    def new_player(self, **headers):
        status, out = self.call("/player/new", {}, headers=headers)
        self.assertEqual(status, 200, out)
        return out["player"], out["token"]

    def market(self):
        return self.ex.biddable(self.ex.clock.now())[0]


class Identity(HttpCase):
    def test_wallet_calls_need_the_players_own_token(self):
        pid, token = self.new_player()
        other, other_token = self.new_player()
        self.assertEqual(self.call(f"/wallet?player={pid}", token=token)[1]["cash"], GRANT)
        self.assertEqual(self.call(f"/versus?player={pid}", token=token)[1], {"versus": []})
        for bad in (None, "", "wrong", other_token):
            for path in ("/wallet", "/positions", "/history", "/ledger", "/versus"):
                self.assertEqual(self.call(f"{path}?player={pid}", token=bad)[0], 401, (path, bad))
            for path, body in (("/limits", {"dailyLoss": 100}), ("/break", {"hours": 1}), ("/player/name", {"name": "x"})):
                self.assertEqual(self.call(path, {"player": pid} | body, token=bad)[0], 401, (path, bad))
        m = self.market()
        bet = {"player": pid, "market": m.id, "option": m.option_ids()[0], "stake": 50, "idempotency_key": "k"}
        self.assertEqual(self.call("/bet", bet, token=other_token)[0], 401)
        self.assertEqual(self.call("/bet", bet, token=token)[0], 200)
        self.assertEqual(self.call("/limits", {"player": pid, "dailyLoss": 100}, token=token)[0], 200)

    def test_a_read_with_a_new_id_never_creates_a_player_or_grants(self):
        for pid in ("p_brandnew", "anything"):
            self.assertEqual(self.call(f"/wallet?player={pid}", token="x")[0], 401)
        self.assertEqual(self.ex.ledger.db.execute("SELECT COUNT(*) FROM players").fetchone()[0], 0)
        self.assertEqual(self.ex.ledger._sum("1 = 1", ()), 0)

    def test_a_room_on_one_ip_can_join_in_a_burst_of_30_a_minute(self):
        venue = {"X-Forwarded-For": "203.0.113.7"}
        for _ in range(30):
            self.new_player(**venue)
        status, out = self.call("/player/new", {}, headers=venue)
        self.assertEqual((status, out["error"]), (429, "Too many new players from this network, try again in a minute"))
        self.new_player(**{"X-Forwarded-For": "203.0.113.8"})
        self.clock[0] += 60
        self.new_player(**venue)
        self.assertEqual(self.ex.ledger.db.execute("SELECT COUNT(*) FROM players").fetchone()[0], 32)

    def test_new_players_per_ip_stop_at_the_daily_cap(self):
        venue = {"X-Forwarded-For": "203.0.113.9"}
        with mock.patch.object(server, "NEW_PLAYERS_DAY", server.RateLimit(3, 86_400, lambda: self.clock[0])):
            for _ in range(3):
                self.new_player(**venue)
                self.clock[0] += 61
            status, out = self.call("/player/new", {}, headers=venue)
            self.assertEqual(status, 429)
            self.assertIn("today, try again tomorrow", out["error"])
            self.clock[0] += 86_400
            self.new_player(**venue)

    def test_the_limits_read_their_env_vars(self):
        import os
        import subprocess
        import sys
        env = dict(os.environ, CITY_NEWPLAYER_PER_MIN="7", CITY_NEWPLAYER_PER_DAY="70")
        code = "import server; print(server.NEW_PLAYERS.n, server.NEW_PLAYERS_DAY.n)"
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=os.path.dirname(server.__file__), capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split(), ["7", "70"])

    def test_nickname_only_through_player_name(self):
        pid, token = self.new_player()
        m = self.market()
        self.call("/bet", {"player": pid, "name": "Mallory", "market": m.id, "option": m.option_ids()[0], "stake": 50,
                           "idempotency_key": "k"}, token=token)
        self.assertIsNone(self.call(f"/wallet?player={pid}", token=token)[1]["player"]["nickname"])
        self.assertEqual(self.call("/player/name", {"player": pid, "name": "x" * 25}, token=token)[0], 400)
        self.assertEqual(self.call("/player/name", {"player": pid, "name": "Ann"}, token=token)[0], 200)
        self.assertEqual(self.call(f"/wallet?player={pid}", token=token)[1]["player"]["nickname"], "Ann")


class PendingBets(HttpCase):
    def test_bet_is_pending_then_get_bet_shows_the_fill(self):
        import integrity
        real = staticmethod(lambda series: integrity.rule_for(series).delay_s * 1000)
        with mock.patch.object(integrity.Integrity, "delay_ms", real):
            pid, token = self.new_player()
            m = self.market()
            bet = {"player": pid, "market": m.id, "option": m.option_ids()[0], "stake": 50, "idempotency_key": "k"}
            status, out = self.call("/bet", bet, token=token)
            self.assertEqual((status, out["status"], out["escrow"]), (200, "pending", 0))
            self.assertEqual(out["fillsAt"] - out["placedAt"], 5000)
            got = self.call(f"/bet?player={pid}&id={out['betId']}", token=token)[1]
            self.assertEqual(got["status"], "pending")
            self.assertEqual(self.call(f"/positions?player={pid}", token=token)[1]["pending"][0]["betId"],
                             out["betId"])
            self.assertEqual(self.call(f"/bet?player={pid}&id={out['betId']}", token="wrong")[0], 401)
            self.assertEqual(self.call(f"/bet?player={pid}&id=nope", token=token)[0], 404)
            with server.LOCK:
                self.ex.tick(out["fillsAt"])
            got = self.call(f"/bet?player={pid}&id={out['betId']}", token=token)[1]
            self.assertEqual(got["status"], "filled")
            self.assertEqual(self.call(f"/positions?player={pid}", token=token)[1]["positions"][0]["betId"],
                             out["betId"])


class Boundary(HttpCase):
    def test_confirm_needs_the_server_token(self):
        pid, token = self.new_player()
        m = self.market()
        L = self.ex.ledger
        L.place_bet(pid, "elsewhere", 250, market="other", question="q", option="yes", label="Yes", settles_at=0,
                    price=lambda: (1000.0, 2.0))
        bet = {"player": pid, "market": m.id, "option": m.option_ids()[1], "stake": 200, "idempotency_key": "big"}
        status, out = self.call("/bet", bet, token=token)
        self.assertEqual((status, out["code"]), (409, "confirm_required"))
        self.assertEqual(self.call("/bet", bet | {"confirm": True}, token=token)[1]["code"], "confirm_required")
        status, ok = self.call("/bet", bet | {"confirm_token": out["confirmToken"]}, token=token)
        self.assertEqual((status, ok["escrow"]), (200, 450))

    def test_body_over_16kb_is_413(self):
        status, _ = self.call("/player/new", raw=b'{"name": "' + b"x" * (16 * 1024) + b'"}')
        self.assertEqual(status, 413)

    def test_since_inf_is_400(self):
        pid, token = self.new_player()
        for since in ("inf", "nan", "1e400"):
            self.assertEqual(self.call(f"/wallet?player={pid}&since={since}", token=token)[0], 400, since)

    def test_score_is_gone_and_the_leaderboard_reads_the_ledger(self):
        self.assertEqual(self.call("/score", {"player": "x", "name": "x", "points": 10**9})[0], 404)
        pid, token = self.new_player()
        m = self.market()
        self.call("/player/name", {"player": pid, "name": "Ann"}, token=token)
        self.call("/bet", {"player": pid, "market": m.id, "option": m.option_ids()[0], "stake": 50,
                           "idempotency_key": "k"}, token=token)
        rows = self.call("/leaderboard")[1]["rows"]
        humans = [r for r in rows if r["kind"] == "human"]
        self.assertEqual(humans, [{"name": "Ann", "points": GRANT, "kind": "human"}])
        self.assertEqual(len([r for r in rows if r["kind"] == "ai"]), len(self.ex.record))

    def test_lock_is_released_before_the_response_is_written(self):
        held = []
        real = server.Handler._send

        def spy(handler, status, data):
            held.append(server.LOCK.locked())
            real(handler, status, data)
        with mock.patch.object(server.Handler, "_send", spy):
            self.call("/markets")
            self.call("/player/new", {})
        self.assertEqual(held, [False, False])

    def test_listen_backlog_and_default_host(self):
        self.assertEqual(server.Server.request_queue_size, 128)
        import os
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if k != "CITY_HOST"}
        out = subprocess.run([sys.executable, "-c", "import server; print(server.HOST)"], env=env,
                             cwd=os.path.dirname(server.__file__), capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "127.0.0.1")


class PinSignIn(HttpCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "RECOVER_IP", server.RateLimit(20, 600, lambda: self.clock[0]))
        p.start()
        self.addCleanup(p.stop)

    def named(self, name="Ann", pin="4821"):
        status, out = self.call("/player/new", {"name": name})
        self.assertEqual(status, 200, out)
        if pin:
            self.assertEqual(self.call("/player/pin", {"player": out["player"], "pin": pin}, token=out["token"]),
                             (200, {"ok": True, "hasPin": True}))
        return out["player"], out["token"]

    def test_set_pin_needs_the_token_and_change_pin_works(self):
        pid, token = self.named(pin=None)
        self.assertEqual(self.call("/player/pin", {"player": pid, "pin": "1234"}, token="wrong")[0], 401)
        self.assertEqual(self.call("/player/pin", {"player": pid, "pin": "12a4"}, token=token)[0], 400)
        self.assertEqual(self.call("/player/pin", {"player": pid, "pin": "1234"}, token=token)[0], 200)
        self.assertTrue(self.call(f"/wallet?player={pid}", token=token)[1]["player"]["hasPin"])
        self.assertEqual(self.call("/player/pin", {"player": pid, "pin": "9999"}, token=token)[0], 200)
        self.assertEqual(self.call("/player/recover", {"name": "Ann", "pin": "1234"})[0], 401)
        self.assertEqual(self.call("/player/recover", {"name": "Ann", "pin": "9999"})[0], 200)

    def test_recover_gives_a_second_token_and_both_devices_work(self):
        pid, token = self.named()
        status, out = self.call("/player/recover", {"name": "ann", "pin": "4821"})
        self.assertEqual((status, out["player"], out["nickname"]), (200, pid, "Ann"))
        for t in (token, out["token"]):
            self.assertEqual(self.call(f"/wallet?player={pid}", token=t)[1]["cash"], GRANT)
        m = self.market()
        bet = {"player": pid, "market": m.id, "option": m.option_ids()[0], "stake": 50, "idempotency_key": "k1"}
        self.assertEqual(self.call("/bet", bet, token=out["token"])[0], 200)

    def test_no_account_enumeration(self):
        self.named("Ann", "4821")
        self.named("Bea", None)
        replies = {json.dumps(self.call("/player/recover", body), sort_keys=True)
                   for body in ({"name": "Ann", "pin": "0000"}, {"name": "Nobody", "pin": "4821"},
                                {"name": "Bea", "pin": "4821"}, {"name": "Ann"}, {})}
        self.assertEqual(replies, {json.dumps([401, {"ok": False, "error": "Name or PIN didn't match"}],
                                              sort_keys=True)})

    def test_five_wrong_pins_lock_the_name(self):
        self.named()
        for _ in range(5):
            self.assertEqual(self.call("/player/recover", {"name": "Ann", "pin": "0000"})[0], 401)
        status, out = self.call("/player/recover", {"name": "Ann", "pin": "4821"})
        self.assertEqual((status, out["code"]), (409, "locked"))

    def test_per_ip_limit_is_20_tries_per_10_minutes(self):
        self.named()
        venue = {"X-Forwarded-For": "203.0.113.20"}
        for i in range(20):
            self.call("/player/recover", {"name": f"guess{i}", "pin": "0000"}, headers=venue)
        status, out = self.call("/player/recover", {"name": "Ann", "pin": "4821"}, headers=venue)
        self.assertEqual(status, 429, out)
        self.assertEqual(self.call("/player/recover", {"name": "Ann", "pin": "4821"},
                                   headers={"X-Forwarded-For": "203.0.113.21"})[0], 200)
        self.clock[0] += 600
        self.assertEqual(self.call("/player/recover", {"name": "Ann", "pin": "4821"}, headers=venue)[0], 200)

    def test_duplicate_name_is_409_taken(self):
        self.named("Ann", None)
        status, out = self.call("/player/new", {"name": "ANN"})
        self.assertEqual((status, out["code"]), (409, "name_taken"))
        pid, token = self.named("Bea", None)
        status, out = self.call("/player/name", {"player": pid, "name": "ann"}, token=token)
        self.assertEqual((status, out["code"], out["error"]), (409, "name_taken", "That name is taken"))


if __name__ == "__main__":
    unittest.main()
