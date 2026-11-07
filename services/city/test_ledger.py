"""Wallet ledger: every invariant, idempotency, limits, breaks, restart, a randomized run, and the exchange hooks.

    python3 -m unittest discover -s services/city
"""
from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import ledger as lg
from ledger import GRANT, Ledger, WalletError

NOON = int(datetime(2026, 11, 7, 12, 0, tzinfo=lg.LONDON).timestamp() * 1000)
HOUR = 3600 * 1000


class Clock:
    def __init__(self, ms: int = NOON):
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


def flat(odds: float = 2.0):
    calls = []

    def price():
        calls.append(1)
        return 100.0, odds

    price.calls = calls
    return price


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.L = Ledger(":memory:", now=self.clock)
        self.addCleanup(self.L.close)
        self.n = 0

    def bet(self, player="alice", stake=100, odds=2.0, key=None, market="m1", option="yes", confirm=True,
            price=None):
        self.n += 1
        return self.L.place_bet(player, key or f"k{self.n}", stake, market=market, question="Q?", option=option,
                                label=option.title(), settles_at=self.clock.ms + 5 * 60_000,
                                price=price or flat(odds), confirm=confirm)

    def assertHealthy(self):
        self.assertEqual(self.L.audit(), [])


class GrantAndBalance(LedgerCase):
    def test_new_player_gets_one_grant_as_an_entry(self):
        for _ in range(3):
            self.L.ensure_player("alice")
        rows = self.L.ledger_rows("alice")["entries"]
        self.assertEqual([(r["kind"], r["account"], r["amount"]) for r in rows], [("grant", "player_cash", GRANT)])
        self.assertEqual(self.L.cash("alice"), GRANT)
        self.assertEqual(self.L.wallet("alice")["player"]["kycStatus"], "unverified")
        self.assertHealthy()

    def test_bad_player_id_rejected(self):
        for bad in ("", None, "a b", "x" * 65, "<script>"):
            with self.assertRaises(ValueError):
                self.L.ensure_player(bad)

    def test_balance_is_derived_not_stored(self):
        cols = {r[1] for r in self.L.db.execute("PRAGMA table_info(players)")}
        self.assertFalse(cols & {"balance", "cash", "points"})

    def test_entries_are_append_only(self):
        self.L.ensure_player("alice")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.L.db.execute("UPDATE entries SET amount = 5000")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.L.db.execute("DELETE FROM entries")

    def test_amounts_must_be_integers(self):
        self.L.ensure_player("alice")
        with self.assertRaises(sqlite3.IntegrityError):
            self.L.db.execute("INSERT INTO entries (ts, player, account, amount, kind, idempotency_key) "
                              "VALUES (1, 'alice', 'house', 1.5, 'adjustment', 'x')")
        with self.assertRaisesRegex(ValueError, "whole number"):
            self.bet(stake=10.5)
        self.assertEqual(self.bet(stake=10.0)["stake"], 10)


class BetLifecycle(LedgerCase):
    def test_hold_moves_cash_to_escrow(self):
        out = self.bet(stake=200)
        self.assertEqual((out["balance"], out["escrow"]), (800, 200))
        self.assertEqual((self.L.cash("alice"), self.L.escrow("alice")), (800, 200))
        self.assertHealthy()

    def test_win_pays_escrow_back_plus_house_winnings(self):
        out = self.bet(stake=100, odds=2.37)
        self.assertEqual(out["payout"], 237)
        self.assertEqual(self.L.settle_bets([out["betId"]], "yes", "Yes, 0.4 mm"), 1)
        self.assertEqual((self.L.cash("alice"), self.L.escrow("alice")), (1137, 0))
        legs = {r["account"]: r["amount"] for r in self.L.ledger_rows("alice")["entries"] if r["kind"] == "bet_win"}
        self.assertEqual(legs, {"player_escrow": -100, "house": -137, "player_cash": 237})
        self.assertHealthy()

    def test_loss_moves_escrow_to_house(self):
        out = self.bet(stake=100)
        self.L.settle_bets([out["betId"]], "no", "No")
        self.assertEqual((self.L.cash("alice"), self.L.escrow("alice")), (900, 0))
        self.assertEqual(self.L._sum("account = 'house'", ()), 100)
        h = self.L.history("alice")["history"][0]
        self.assertEqual((h["result"], h["pnl"], h["payout"], h["reading"]), ("lost", -100, 0, "No"))
        self.assertHealthy()

    def test_void_refunds_escrow(self):
        out = self.bet(stake=150)
        self.L.settle_bets([out["betId"]], None, "No data — void")
        self.assertEqual((self.L.cash("alice"), self.L.escrow("alice")), (1000, 0))
        self.assertEqual(self.L.history("alice")["history"][0]["pnl"], 0)
        self.assertHealthy()

    def test_double_settle_changes_nothing(self):
        out = self.bet(stake=100)
        self.assertEqual(self.L.settle_bets([out["betId"]], "yes", "Yes"), 1)
        before = self.L.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        self.assertEqual(self.L.settle_bets([out["betId"]], "yes", "Yes"), 0)
        self.assertEqual(self.L.settle_bets([out["betId"]], "no", "No"), 0)
        self.assertEqual(self.L.settle_bets([out["betId"]], None, "void"), 0)
        self.assertEqual(self.L.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0], before)
        self.assertEqual(self.L.cash("alice"), 1100)
        self.assertHealthy()

    def test_database_refuses_a_second_settlement(self):
        out = self.bet(stake=100)
        self.L.settle_bets([out["betId"]], "yes", "Yes")
        with self.assertRaises(sqlite3.IntegrityError):  # same bet, different kind: the partial unique index
            self.L.db.execute("INSERT INTO entries (ts, player, account, amount, kind, bet_id, idempotency_key) "
                              "VALUES (1, 'alice', 'player_escrow', -100, 'bet_loss', ?, 'sneaky')", (out["betId"],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.L.db.execute("INSERT INTO settlements VALUES (?, 'lost', 'x', 1)", (out["betId"],))

    def test_positions_and_history(self):
        a = self.bet(stake=100, odds=3.0, market="m1")
        self.bet(stake=50, odds=1.5, market="m2")
        pos = self.L.positions("alice")["positions"]
        self.assertEqual([(p["market"], p["stake"], p["toWin"]) for p in pos], [("m1", 100, 300), ("m2", 50, 75)])
        self.L.settle_bets([a["betId"]], "yes", "Yes")
        self.assertEqual(len(self.L.positions("alice")["positions"]), 1)
        hist = self.L.history("alice")
        self.assertEqual(hist["series"][-1][1], 200)
        self.assertEqual(self.L.wallet("alice")["pnl"], {"today": 200, "allTime": 200})


class Idempotency(LedgerCase):
    def test_same_key_twice_creates_one_bet(self):
        price = flat()
        first = self.bet(key="tap-1", price=price)
        again = self.bet(key="tap-1", price=price)
        self.assertEqual(first["betId"], again["betId"])
        self.assertTrue(again["replayed"])
        self.assertEqual(len(price.calls), 1, "a replay must not touch the market again")
        self.assertEqual(self.L.db.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)
        self.assertEqual(self.L.cash("alice"), 900)
        self.assertHealthy()

    def test_key_reused_for_a_different_bet_is_refused(self):
        self.bet(key="tap-1", market="m1", stake=100)
        for change in ({"market": "m2"}, {"option": "no"}, {"stake": 50}):
            with self.assertRaisesRegex(ValueError, "different bet"):
                self.bet(key="tap-1", **({"market": "m1", "stake": 100} | change))
        self.assertEqual(self.L.cash("alice"), 900)

    def test_keys_are_per_player(self):
        self.bet(player="alice", key="same")
        self.bet(player="bob", key="same")
        self.assertEqual(self.L.db.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 2)

    def test_key_required(self):
        with self.assertRaisesRegex(ValueError, "idempotency_key"):
            self.L.place_bet("alice", "", 10, market="m", question="q", option="yes", label="Yes", settles_at=0,
                             price=flat())


class Funds(LedgerCase):
    def test_insufficient_funds_rejected_and_nothing_moves(self):
        price = flat()
        with self.assertRaises(WalletError) as cm:
            self.bet(stake=1001, price=price)
        self.assertEqual(cm.exception.code, "insufficient_funds")
        self.assertIn("available 1000", str(cm.exception))
        self.assertEqual(price.calls, [], "a refused bet must not move the price")
        self.assertEqual((self.L.cash("alice"), self.L.escrow("alice")), (1000, 0))

    def test_database_refuses_negative_cash(self):
        self.L.ensure_player("alice")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "insufficient funds"):
            self.L.db.execute("INSERT INTO entries (ts, player, account, amount, kind, idempotency_key) "
                              "VALUES (1, 'alice', 'player_cash', -1001, 'adjustment', 'x')")

    def test_failed_pricing_rolls_back(self):
        def boom():
            raise RuntimeError("book down")
        with self.assertRaises(RuntimeError):
            self.bet(stake=100, price=boom)
        self.assertEqual(self.L.cash("alice"), 1000)
        self.assertEqual(self.L.db.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)

    def test_big_stake_needs_confirmation(self):
        with self.assertRaises(WalletError) as cm:
            self.bet(stake=251, confirm=False)
        self.assertEqual(cm.exception.code, "confirm_required")
        self.bet(stake=250, confirm=False)
        self.bet(player="bob", stake=251, confirm=True)


class ResponsiblePlay(LedgerCase):
    def lose(self, stake):
        out = self.bet(stake=stake)
        self.L.settle_bets([out["betId"]], "no", "No")

    def test_daily_loss_limit_counts_losses_and_open_stakes(self):
        self.lose(200)
        self.lose(200)
        self.bet(stake=50)
        with self.assertRaises(WalletError) as cm:
            self.bet(stake=51)
        self.assertEqual(cm.exception.code, "loss_limit")
        self.assertIn("risk 50 more", str(cm.exception))
        self.assertEqual(self.L.wallet("alice")["limits"]["remainingToday"], 50)

    def test_loss_limit_resets_at_london_midnight(self):
        self.lose(250)
        self.lose(250)
        with self.assertRaises(WalletError):
            self.bet(stake=10)
        self.clock.ms = lg.london_midnight(NOON) + 24 * HOUR + 60_000
        self.bet(stake=10)

    def test_lowering_is_instant_raising_waits_24h(self):
        self.L.set_limit("alice", 100)
        self.assertEqual(self.L.loss_limit("alice"), (100, None))
        w = self.L.set_limit("alice", 800)
        self.assertEqual(w["limits"]["dailyLoss"], 100)
        self.assertEqual(w["limits"]["pending"], {"value": 800, "effectiveAt": NOON + 24 * HOUR})
        with self.assertRaises(WalletError):
            self.bet(stake=101)
        self.clock.ms = NOON + 24 * HOUR - 1
        self.assertEqual(self.L.loss_limit("alice")[0], 100)
        self.clock.ms = NOON + 24 * HOUR
        self.assertEqual(self.L.loss_limit("alice"), (800, None))

    def test_lowering_cancels_a_pending_raise(self):
        self.L.set_limit("alice", 900)
        self.L.set_limit("alice", 300)
        self.clock.ms = NOON + 48 * HOUR
        self.assertEqual(self.L.loss_limit("alice"), (300, None))

    def test_limit_validation(self):
        for bad in (0, -5, 10.5, "lots", lg.MAX_LOSS_LIMIT + 1):
            with self.assertRaises(ValueError):
                self.L.set_limit("alice", bad)

    def test_break_blocks_bets_until_it_ends(self):
        w = self.L.take_break("alice", 1)
        self.assertEqual(w["break"], {"active": True, "until": NOON + HOUR})
        with self.assertRaises(WalletError) as cm:
            self.bet(stake=10)
        self.assertEqual(cm.exception.code, "on_break")
        self.L.take_break("alice", 1)  # asking again cannot shorten it
        self.assertEqual(self.L.break_until("alice"), NOON + HOUR)
        self.clock.ms = NOON + HOUR
        self.bet(stake=10)

    def test_break_lengths(self):
        self.assertEqual(self.L.take_break("bob", 24)["break"]["until"], NOON + 24 * HOUR)
        for bad in (0, 2, 12, True, "1"):
            with self.assertRaises(ValueError):
                self.L.take_break("alice", bad)

    def test_session_pnl(self):
        self.lose(100)
        since = self.clock.ms + 1
        self.clock.ms += 1000
        self.lose(30)
        self.assertEqual(self.L.wallet("alice", since=str(since))["pnl"]["session"], -30)


class Persistence(unittest.TestCase):
    def test_balances_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "ledger.db"
            clock = Clock()
            a = Ledger(path, now=clock)
            won = a.place_bet("alice", "k1", 100, market="m", question="q", option="yes", label="Yes",
                              settles_at=0, price=flat(2.0))
            a.place_bet("alice", "k2", 50, market="m2", question="q", option="yes", label="Yes", settles_at=0,
                        price=flat(2.0))
            a.settle_bets([won["betId"]], "yes", "Yes")
            a.take_break("alice", 1)
            a.close()

            b = Ledger(path, now=clock)
            self.assertEqual((b.cash("alice"), b.escrow("alice")), (1050, 50))
            self.assertEqual(b.find_bet("alice", "k1", "m", "yes", 100)["betId"], won["betId"])
            self.assertIsNotNone(b.break_until("alice"))
            b.ensure_player("alice")
            self.assertEqual(b._sum("kind = 'grant'", ()), GRANT, "no second grant after a restart")
            self.assertEqual(b.void_open("restart"), 1)
            self.assertEqual((b.cash("alice"), b.escrow("alice")), (1100, 0))
            self.assertEqual(b.audit(), [])
            b.close()


class Randomized(unittest.TestCase):
    def test_1000_random_bets_and_settles_keep_every_invariant(self):
        rng = random.Random(26)
        clock = Clock()
        L = Ledger(":memory:", now=clock)
        players = [f"p{i}" for i in range(5)]
        for p in players:
            L.set_limit(p, lg.MAX_LOSS_LIMIT)
        clock.ms += 25 * HOUR  # the raises above take effect
        open_bets: list[str] = []
        placed = refused = settled = 0
        for i in range(1000):
            clock.ms += rng.randint(1, 60_000)
            if open_bets and rng.random() < 0.45:
                batch = rng.sample(open_bets, k=min(len(open_bets), rng.randint(1, 3)))
                winner = rng.choice(["yes", "no", None])
                settled += L.settle_bets(batch, winner, "r")
                if rng.random() < 0.2:
                    self.assertEqual(L.settle_bets(batch, rng.choice(["yes", "no", None]), "again"), 0)
                open_bets = [b for b in open_bets if b not in batch]
                continue
            p = rng.choice(players)
            stake = rng.choice([1, 5, 10, 50, 100, 250, 400, 1200])
            key = f"k{rng.randint(0, 1100)}"
            try:
                out = L.place_bet(p, key, stake, market=f"m{i % 7}", question="q", option=rng.choice(["yes", "no"]),
                                  label="x", settles_at=clock.ms, price=flat(rng.uniform(1.05, 20)),
                                  confirm=rng.random() < 0.8)
            except (WalletError, ValueError):  # ValueError: a key reused for a different bet
                refused += 1
                continue
            if not out["replayed"]:
                placed += 1
                open_bets.append(out["betId"])
            if i % 100 == 0:
                self.assertEqual(L.audit(), [], f"after op {i}")
        self.assertGreater(placed, 200)
        self.assertGreater(refused, 0)
        self.assertGreater(settled, 100)
        self.assertEqual(L.audit(), [])
        total = L._sum("1 = 1", ())
        self.assertEqual(total, 5 * GRANT)
        for p in players:
            self.assertGreaterEqual(L.cash(p), 0)
            open_stakes = sum(r["stake"] for r in L.positions(p)["positions"])
            self.assertEqual(L.escrow(p), open_stakes)
        L.close()


class ExchangeHooks(unittest.TestCase):
    """The exchange debits /bet through the ledger and settles player bets once, from its own settle path."""

    def setUp(self):
        import test_city as tc
        from data import Store
        from exchange import Clock as ExClock, Exchange

        self.tc = tc
        self.raw = tc.Raw()
        self.addCleanup(self.raw.dir.cleanup)
        tc.crowd_history(self.raw, tc.T0, {"oxford-circus": 0.40, "kings-cross": 0.39, "waterloo": 0.38})
        for k in range(30):
            ts = tc.T0 - (29 - k) * tc.MIN
            self.raw.put("line_status", ts, tc.lines({"central": 10, "victoria": 9}))
            self.raw.put("jamcams", ts, tc.cams({"JamCams_00001.07451": 8}))
        self.raw.put("rain_now", tc.T0, tc.rain(0.0))
        tc.bus_history(self.raw, tc.T0)
        self.ex = Exchange(Store(self.raw.root, tc.SPOTS), ExClock(tc.T0), tc.SPOTS)
        self.ex.tick(tc.T0)
        self.m = self.ex.biddable(tc.T0)[0]

    def place(self, player, stake, key, option=None, **extra):
        return self.ex.bet({"player": player, "market": self.m.id, "option": option or self.m.option_ids()[0],
                            "stake": stake, "idempotency_key": key, **extra}, self.tc.T0)

    def test_bet_debits_ledger_and_is_idempotent(self):
        out = self.place("alice", 100, "tap-1")
        self.assertEqual((out["balance"], out["escrow"]), (900, 100))
        price_after = out["price"]
        again = self.place("alice", 100, "tap-1")
        self.assertTrue(again["replayed"])
        self.assertEqual(again["betId"], out["betId"])
        self.assertEqual(again["price"], price_after, "a replay must not move the market")
        self.assertEqual(len(self.m.bets), 1)
        self.assertEqual(self.ex.ledger.cash("alice"), 900)

    def test_settle_pays_winner_once_and_loser_loses(self):
        yes, no = self.m.option_ids()[:2]
        win = self.place("alice", 100, "a", yes)
        self.place("bob", 100, "b", no)
        done = ("done", yes, "Reading 14", {"value": 14, "source": "test", "fetchedAt": 0})
        with mock.patch("exchange.mk.try_settle", return_value=done):
            self.ex.settle(self.m, self.m.settles_at)
            self.m.result = None  # force the exchange down its settle path a second time
            self.ex.settle(self.m, self.m.settles_at)
        L = self.ex.ledger
        self.assertEqual(L.cash("alice"), 900 + win["payout"])
        self.assertEqual(L.cash("bob"), 900)
        self.assertEqual(L.history("alice")["history"][0]["reading"], "Reading 14")
        self.assertEqual(L.audit(), [])

    def test_void_refunds_through_exchange(self):
        self.place("alice", 100, "a")
        with mock.patch("exchange.mk.try_settle", return_value=("void", "No data — void", {})):
            self.ex.settle(self.m, self.m.settles_at)
        self.assertEqual((self.ex.ledger.cash("alice"), self.ex.ledger.escrow("alice")), (1000, 0))

    def test_wallet_errors_surface_from_bet(self):
        self.ex.ledger.take_break("carol", 1)
        with self.assertRaises(WalletError) as cm:
            self.place("carol", 10, "c")
        self.assertEqual(cm.exception.code, "on_break")
        self.assertEqual(self.m.bets, [], "a refused bet leaves no trace on the market")


if __name__ == "__main__":
    unittest.main()
