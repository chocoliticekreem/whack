"""Fixes for the live trading pressure test (analysis/live_trading/README.md, bugs 4-7 and 9).

    python3 -m unittest discover -s services/city
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import markets as mk
import server
import traders
from data import Store
from exchange import Clock, Exchange
from ledger import Ledger, WalletError
from test_city import CITY, MIN, SPOTS, T0, Raw, TestSeries, city, crowd_history, no_delay, vix_payload
from test_judge_fixes import stamped
from test_local import RECORDED, utc


def fill_all(ex: Exchange, now: int) -> None:
    ex.tick(now)
    ex.tick(now + 5_000)


class RestartMidRoundTest(unittest.TestCase):
    """Bug 4: a restart mid-round keeps the position cap, the hourly headline, the AI records and the VIX round."""

    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        city(self.raw, T0 + 70 * MIN)
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "ledger.db"

    def exchange(self, now: int) -> Exchange:
        return Exchange(Store(self.raw.root, SPOTS), Clock(now), SPOTS, Ledger(self.path))

    def bet(self, ex: Exchange, market: str, option: str, stake: int, now: int) -> dict:
        return ex.bet({"player": "p", "market": market, "option": option, "stake": stake}, now)

    def before_restart(self, series: str, option: str, stake: int) -> str:
        ex = self.exchange(T0)
        ex.ledger.open_account("p")
        ex.tick(T0)
        mid = next(m.id for m in ex.active() if m.sub == series)
        out = self.bet(ex, mid, option, stake, T0)
        ex.tick(T0 + 5_000)
        self.assertEqual(ex.integrity.orders[out["betId"]]["status"], "filled")
        ex.ledger.close()  # the process dies here
        return mid

    def after_restart(self, now: int) -> Exchange:
        ex = self.exchange(now)
        self.addCleanup(ex.ledger.close)
        ex.recover()
        ex.tick(now)
        return ex

    def test_bets_booked_before_a_restart_count_toward_the_position_cap(self):
        mid = self.before_restart("hub_surge", "bank", 20)
        ex = self.after_restart(T0 + MIN)
        self.assertEqual(ex.markets[mid].bets, [], "the round's market is rebuilt fresh by open_round")
        with self.assertRaisesRegex(PermissionError, r"position cap: max 250 points in this market \(20 already"):
            self.bet(ex, mid, "bank", 235, T0 + MIN)
        self.assertEqual(self.bet(ex, mid, "bank", 230, T0 + MIN)["status"], "pending")

    def test_an_open_hourly_headline_is_relisted_after_a_restart(self):
        mid = self.before_restart("hub_hour", "bank", 50)
        self.assertEqual(mid, "hub_hour-1600")
        before = Ledger(self.path)
        rec = before.open_markets()[0]["record"]
        before.close()
        now = T0 + 10 * MIN
        ex = self.after_restart(now)
        m = ex.markets[mid]
        self.assertEqual((m.opened_at, m.spec, m.options), (rec["openedAt"], rec["spec"], rec["options"]))
        self.assertEqual([b["stake"] for b in m.bets], [50.0])
        self.assertEqual(ex.restored, [])
        self.assertEqual(ex.market_json(m, now)["status"], "open")
        self.assertIn(mid, ex.round_json(now)["current"]["headlines"])
        with self.assertRaisesRegex(PermissionError, r"\(50 already staked\)"):
            self.bet(ex, mid, "bank", 210, now)
        late = self.bet(ex, mid, "waterloo", 100, now)
        ex.tick(now + 5_000)
        self.assertEqual(ex.integrity.orders[late["betId"]]["status"], "filled")
        ex.tick(m.settles_at + MIN)
        self.assertTrue(m.result and m.result["settled"])
        results = [h["result"] for h in ex.ledger.history("p")["history"]]
        self.assertEqual(len(results), 2)
        self.assertNotIn("void", results)
        self.assertEqual((ex.ledger.escrow("p"), ex.ledger.audit()), (0, []))

    def test_a_restart_inside_the_headlines_own_round_keeps_one_market(self):
        mid = self.before_restart("hub_hour", "bank", 50)
        ex = self.after_restart(T0 + MIN)
        self.assertEqual(ex.order.count(mid), 1)
        self.assertEqual([b["stake"] for b in ex.markets[mid].bets], [50.0])
        self.assertIn(mid, ex.rounds[T0 + 5 * MIN]["markets"])

    def test_ai_points_won_and_lost_load_after_a_restart(self):
        ex = self.exchange(T0)
        traders.save_records(ex.ledger, T0, {"sim": {"won": 7, "lost": 11, "points": 2872, "why": "edge 9 pts"}})
        ex.ledger.close()
        again = self.exchange(T0 + MIN)
        self.addCleanup(again.ledger.close)
        self.assertEqual(again.record["sim"], {"won": 7, "lost": 11, "points": 2872, "why": "edge 9 pts"})
        row = next(r for r in again.traders_json(T0 + MIN)["traders"] if r["id"] == "sim")
        self.assertEqual((row["record"], row["points"]), ({"won": 7, "lost": 11}, 2872))
        board = {r["name"]: r["points"] for r in again.leaderboard()["rows"] if r["kind"] == "ai"}
        self.assertEqual(board["Simulation"], 2872)

    def test_london_vix_lists_in_the_first_round_after_start(self):
        store = Store(self.raw.root, SPOTS)
        timeouts = []

        def fetch(url, timeout):
            timeouts.append(timeout)
            return vix_payload(T0 - 2 * 3_600_000, T0)
        self.assertTrue(server.prime_vix(store, fetch))
        self.assertEqual(timeouts, [server.VIX_BOOT_TIMEOUT_S])
        ex = Exchange(store, Clock(T0), SPOTS, Ledger(":memory:"))
        self.addCleanup(ex.ledger.close)
        ex.recover()
        ex.tick(T0)
        self.assertIn("london_vix", [ex.markets[i].sub for i in ex.rounds[T0 + 5 * MIN]["markets"]])

    def test_a_vix_engine_that_is_down_at_start_does_not_hold_the_round(self):
        store = Store(self.raw.root, SPOTS)
        self.assertFalse(server.prime_vix(store, lambda url, timeout: None))
        ex = Exchange(store, Clock(T0), SPOTS, Ledger(":memory:"))
        self.addCleanup(ex.ledger.close)
        ex.tick(T0)
        self.assertIn("heathrow_stack", [ex.markets[i].sub for i in ex.rounds[T0 + 5 * MIN]["markets"]])


class PendingDeskCase(unittest.TestCase):
    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        city(self.raw, T0 + 30 * MIN)
        self.ex = Exchange(Store(self.raw.root, SPOTS), Clock(T0), SPOTS, Ledger(":memory:"))
        self.addCleanup(self.ex.ledger.close)
        self.L = self.ex.ledger
        self.L.open_account("p")
        self.ex.tick(T0)
        self.m = {m.sub: m for m in self.ex.active()}

    def bet(self, series: str, option: str, stake: int, now: int = T0, **extra) -> dict:
        return self.ex.bet({"player": "p", "market": self.m[series].id, "option": option, "stake": stake} | extra,
                           now)

    def order(self, out: dict) -> dict:
        return self.ex.integrity.orders[out["betId"]]


class DoomedPendingTest(PendingDeskCase):
    """Bug 5: a bet that can never fill is refused at placement; fills keep placement order."""

    def test_a_bet_already_priced_under_the_floor_is_refused_not_left_pending(self):
        m = self.m["hub_surge"]
        ids = m.option_ids()
        model = {o: 0.01 for o in ids} | {ids[0]: 1 - 0.01 * (len(ids) - 1)}
        with mock.patch.object(m.futures, "probs", return_value=model):
            with self.assertRaises(WalletError) as cm:
                self.bet("hub_surge", ids[0], 100)
        self.assertEqual(cm.exception.code, "price_too_short")
        self.assertIn("Price too short", str(cm.exception))
        self.assertFalse([o for o in self.ex.integrity.orders.values() if o["player"] == "p"])

    def test_an_earlier_bet_keeps_its_place_ahead_of_a_later_faster_one(self):
        pulse = self.bet("city_pulse", "over", 200, T0)            # 5 s delay: fills at T0 + 5 s
        stack = self.bet("heathrow_stack", "over", 40, T0 + 1_000)  # 2 s delay: fills first, at T0 + 3 s
        self.L.set_limit("p", 220)
        self.ex.tick(T0 + 3_000)
        self.assertEqual(self.order(stack)["status"], "cancelled")
        self.assertIn("Daily loss limit", self.order(stack)["reason"])
        self.ex.tick(T0 + 5_000)
        self.assertEqual(self.order(pulse)["status"], "filled")
        self.assertEqual((self.L.escrow("p"), self.L.audit()), (200, []))

    def test_a_bet_that_would_spend_an_earlier_pending_bets_room_is_refused_at_placement(self):
        self.L.set_limit("p", 220)
        self.bet("city_pulse", "over", 200, T0)
        with self.assertRaises(WalletError) as cm:
            self.bet("heathrow_stack", "over", 40, T0 + 1_000)
        self.assertEqual(cm.exception.code, "loss_limit")
        self.assertIn("in play 200", str(cm.exception))


class BurstConfirmTest(PendingDeskCase):
    """Bug 6: the over-25% confirmation counts the player's other pending stakes and is re-checked at fill."""

    def test_a_second_simultaneous_large_bet_needs_its_own_confirm_token(self):
        first = self.bet("hub_surge", "bank", 250)
        with self.assertRaises(WalletError) as cm:
            self.bet("city_pulse", "over", 250, idempotency_key="k2")
        self.assertEqual(cm.exception.code, "confirm_required")
        self.assertIn("after 250 in pending bets", str(cm.exception))
        token = cm.exception.data["confirmToken"]
        second = self.bet("city_pulse", "over", 250, idempotency_key="k2", confirm_token=token)
        self.ex.tick(T0 + 5_000)
        self.assertEqual((self.order(first)["status"], self.order(second)["status"]), ("filled", "filled"))
        self.assertEqual((self.L.cash("p"), self.L.escrow("p"), self.L.audit()), (500, 500, []))

    def test_a_fill_past_the_threshold_without_a_confirm_token_is_cancelled(self):
        out = self.bet("hub_surge", "bank", 230)  # 23% of 1000 at placement: no confirm needed
        self.L.place_bet("p", "elsewhere", 200, market="other-1505", question="q", option="yes", label="Yes",
                         settles_at=T0 + 5 * MIN, price=lambda: (400.0, 2.0))
        self.ex.tick(T0 + 5_000)  # cash is now 800, so 230 is 29%
        self.assertEqual(self.order(out)["status"], "cancelled")
        self.assertIn("Confirm to place it", self.order(out)["reason"])
        self.assertEqual((self.L.cash("p"), self.L.escrow("p"), self.L.audit()), (800, 200, []))


class SettlementSnapshotTest(unittest.TestCase):
    """Bug 7: the snapshot records TfL's own measurement time and our fetch time, and a crowding or camera market
    never settles on a value measured before its close."""

    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        self.raw.profile()
        crowd_history(self.raw, T0, CITY)

    def test_crowding_settlements_carry_tfls_time_and_our_fetch_time(self):
        store = self.raw.store(T0)
        built = [mk.build_hub_surge("hub_surge-1505", store, SPOTS, T0, T0 + 5 * MIN),
                 mk.build_city_pulse("city_pulse-1505", store, SPOTS, T0, T0 + 5 * MIN)]
        close = built[0].closes_at
        self.raw.put("crowding", close + 8 * MIN, stamped(dict(CITY, waterloo=0.60), close + 2 * MIN))
        now = close + 9 * MIN
        for m in built:
            got = mk.try_settle(m, self.raw.store(now), now)
            self.assertEqual(got[0], "done", m.id)
            snap = got[3]
            self.assertEqual((snap["measuredAt"], snap["fetchedAt"]), (close + 2 * MIN, close + 8 * MIN), m.id)
            self.assertLessEqual({"value", "source", "measuredAt", "fetchedAt"}, set(snap))

    def test_every_settled_crowding_market_was_measured_after_its_close(self):
        city(self.raw, T0 + 30 * MIN)
        ex = Exchange(Store(self.raw.root, SPOTS), Clock(T0), SPOTS, Ledger(":memory:"))
        self.addCleanup(ex.ledger.close)
        for t in range(T0, T0 + 30 * MIN, 30_000):
            ex.tick(t)
        settled = [m for m in ex.markets.values() if m.result and m.feed == "crowding" and not m.result["void"]]
        self.assertGreaterEqual(len(settled), 6)
        for m in settled:
            snap = m.result["settlement"]
            self.assertGreater(snap["measuredAt"], m.closes_at, m.id)
            self.assertGreaterEqual(snap["fetchedAt"], snap["measuredAt"], m.id)

    def test_a_value_measured_at_or_before_the_close_voids(self):
        m = mk.build_hub_surge("hub_surge-1505", self.raw.store(T0), SPOTS, T0, T0 + 5 * MIN)
        answer = {}
        TestSeries(self, mk.Series("test-early", "crowding", mk.build_hub_surge,
                                   lambda m, store, now: ("done", "bank", "Bank", dict(answer)), enabled=False))
        m.sub = "test-early"
        now = m.settles_at + MIN
        for measured, want in ((m.closes_at, "void"), (None, "void"), (m.closes_at - 1, "void"),
                               (m.closes_at + 1, "done")):
            answer.update(value=1, source="TfL Crowding API", measuredAt=measured, fetchedAt=m.closes_at + 5)
            got = mk.try_settle(m, self.raw.store(now), now)
            self.assertEqual(got[0], want, measured)
            if want == "void":
                self.assertIn("measured before bets closed", got[1])

    def test_a_camera_settlement_is_measured_at_the_image_time(self):
        now = utc(20, 20, 30)
        ex = Exchange(Store(RECORDED, SPOTS), Clock(now), SPOTS, Ledger(":memory:"))
        self.addCleanup(ex.ledger.close)
        ex.tick(now)
        player = ex.ledger.create_player("rec")["player"]
        m = ex.markets[ex.local.create({"lat": 51.5132, "lon": -0.14091}, player, now)["market"]["id"]]
        t = now
        while t <= utc(20, 40) and m.result is None:
            ex.tick(t)
            t += 30_000
        snap = m.result["settlement"]
        self.assertEqual((snap["measuredAt"], snap["imageAt"]), (utc(20, 26, 44), utc(20, 26, 44)))
        self.assertGreater(snap["measuredAt"], m.closes_at)
        self.assertGreaterEqual(snap["fetchedAt"], snap["measuredAt"])


class AiExposureTest(unittest.TestCase):
    """Bug 9: the AI traders' open bets count toward the house's worst-case exposure cap."""

    def setUp(self):
        no_delay(self)
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        city(self.raw, T0 + 30 * MIN)

    def exchange(self, **caps) -> Exchange:
        ex = Exchange(Store(self.raw.root, SPOTS), Clock(T0), SPOTS, Ledger(":memory:", **caps))
        self.addCleanup(ex.ledger.close)
        return ex

    def test_ai_open_stakes_are_in_the_house_exposure_and_block_a_player_bet_past_the_cap(self):
        ex = self.exchange()
        ex.ledger.open_account("p")
        ex.tick(T0)
        ai = [(m, b) for m in ex.active() for b in m.ai_bets]
        self.assertTrue(ai)
        exposure = ex.ledger.house()["exposure"]
        self.assertGreater(exposure, 0, "no player has bet: all of it is the AI traders'")
        m, worst = max(((m, b["option"]) for m, b in ai), key=lambda mo: self.liability(*mo))
        ex.ledger.exposure_cap = exposure
        with self.assertRaises(WalletError) as cm:
            ex.bet({"player": "p", "market": m.id, "option": worst, "stake": 50}, T0)
        self.assertEqual(cm.exception.code, "house_limit")
        self.assertEqual((ex.ledger.cash("p"), ex.ledger.audit()), (1000, []))

    def test_ai_bets_past_the_cap_do_not_fill(self):
        ex = self.exchange(exposure_cap=0)
        ex.tick(T0)
        self.assertEqual([b for m in ex.active() for b in m.ai_bets], [])
        self.assertEqual(ex.ledger.house()["exposure"], 0)

    @staticmethod
    def liability(m, option: str) -> float:
        staked = sum(b["stake"] for b in m.ai_bets)
        return sum(round(b["stake"] * b["odds"]) for b in m.ai_bets if b["option"] == option) - staked


if __name__ == "__main__":
    unittest.main()
