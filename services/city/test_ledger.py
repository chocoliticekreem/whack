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


SETTLES = NOON + 5 * 60_000


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.L = Ledger(":memory:", now=self.clock)
        self.addCleanup(self.L.close)
        for p in ("alice", "bob", "carol"):
            self.L.open_account(p)
        self.n = 0

    def bet(self, player="alice", stake=100, odds=2.0, key=None, market="m1", option="yes", confirm=True,
            price=None, settles_at=SETTLES, commit=None):
        self.n += 1
        token = self.L.confirm_token(player, market, settles_at, option, int(stake)) if confirm else None
        return self.L.place_bet(player, key or f"k{self.n}", stake, market=market, question="Q?", option=option,
                                label=option.title(), settles_at=settles_at, price=price or flat(odds),
                                confirm_token=token, commit=commit)

    def assertHealthy(self):
        self.assertEqual(self.L.audit(), [])


class GrantAndBalance(LedgerCase):
    def test_new_player_gets_one_grant_as_an_entry(self):
        for _ in range(3):
            self.L.open_account("alice")
        rows = self.L.ledger_rows("alice")["entries"]
        self.assertEqual([(r["kind"], r["account"], r["amount"]) for r in rows], [("grant", "player_cash", GRANT)])
        self.assertEqual(self.L.cash("alice"), GRANT)
        self.assertEqual(self.L.wallet("alice")["player"]["kycStatus"], "unverified")
        self.assertHealthy()

    def test_bad_player_id_rejected(self):
        for bad in ("", None, "a b", "x" * 65, "<script>"):
            with self.assertRaises(ValueError):
                self.L.open_account(bad)

    def test_balance_is_derived_not_stored(self):
        cols = {r[1] for r in self.L.db.execute("PRAGMA table_info(players)")}
        self.assertFalse(cols & {"balance", "cash", "points"})

    def test_entries_are_append_only(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.L.db.execute("UPDATE entries SET amount = 5000")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.L.db.execute("DELETE FROM entries")

    def test_amounts_must_be_integers(self):
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
        for change in ({"option": "no"}, {"stake": 50}):
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
        self.assertIn("confirmToken", cm.exception.data)
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
        self.assertIn("risk W50 more", str(cm.exception))
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
            a.open_account("alice")
            won = a.place_bet("alice", "k1", 100, market="m", question="q", option="yes", label="Yes",
                              settles_at=0, price=flat(2.0))
            a.place_bet("alice", "k2", 50, market="m2", question="q", option="yes", label="Yes", settles_at=0,
                        price=flat(2.0))
            a.settle_bets([won["betId"]], "yes", "Yes")
            a.take_break("alice", 1)
            a.close()

            b = Ledger(path, now=clock)
            self.assertEqual((b.cash("alice"), b.escrow("alice")), (1050, 50))
            self.assertEqual(b.find_bet("alice", "k1", "m", 0, "yes", 100)["betId"], won["betId"])
            self.assertIsNotNone(b.break_until("alice"))
            b.open_account("alice")
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
            L.open_account(p)
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
            stake = rng.choice([10, 20, 50, 100, 250, 400, 1200])
            key = f"k{rng.randint(0, 1100)}"
            market, option = f"m{i % 7}", rng.choice(["yes", "no"])
            token = L.confirm_token(p, market, 0, option, stake) if rng.random() < 0.8 else None
            try:
                out = L.place_bet(p, key, stake, market=market, question="q", option=option, label="x",
                                  settles_at=0, price=flat(rng.uniform(1.05, 20)), confirm_token=token)
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
        tc.no_delay(self)
        self.raw = tc.Raw()
        self.addCleanup(self.raw.dir.cleanup)
        tc.city(self.raw, tc.T0 + 30 * tc.MIN)
        self.ex = Exchange(Store(self.raw.root, tc.SPOTS), ExClock(tc.T0), tc.SPOTS)
        for p in ("alice", "bob", "carol"):
            self.ex.ledger.open_account(p)
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

    def test_a_refused_insert_leaves_the_book_untouched(self):
        before_q, before_p = dict(self.m.book.q), self.m.prices()
        with mock.patch.object(self.ex.ledger, "_post", side_effect=sqlite3.IntegrityError("disk")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.place("alice", 100, "a")
        self.assertEqual(self.m.book.q, before_q)
        self.assertEqual(self.m.prices(), before_p)
        self.assertEqual(self.m.bets, [])

    def test_too_short_a_price_is_refused_and_nothing_moves(self):
        ids = self.m.option_ids()
        model = {o: 0.01 for o in ids} | {ids[0]: 1 - 0.01 * (len(ids) - 1)}
        with mock.patch.object(self.m.futures, "probs", return_value=model):
            with self.assertRaises(WalletError) as cm:
                self.place("alice", 100, "a", ids[0])
        self.assertEqual(cm.exception.code, "price_too_short")
        self.assertEqual(self.ex.ledger.cash("alice"), GRANT)

    def test_a_failed_ledger_settle_leaves_the_market_retryable(self):
        yes = self.m.option_ids()[0]
        self.place("alice", 100, "a", yes)
        records = {k: dict(v) for k, v in self.ex.record.items()}
        done = ("done", yes, "Reading 14", {"value": 14, "source": "test", "fetchedAt": 0})
        with mock.patch("exchange.mk.try_settle", return_value=done), \
                mock.patch.object(self.ex.ledger, "settle_bets", side_effect=RuntimeError("db locked")):
            with self.assertRaises(RuntimeError):
                self.ex.settle(self.m, self.m.settles_at)
        self.assertIsNone(self.m.result)
        self.assertEqual(self.ex.record, records, "trader records only move once the ledger has settled")
        with mock.patch("exchange.mk.try_settle", return_value=done):
            self.assertTrue(self.ex.settle(self.m, self.m.settles_at)["settled"])
        self.assertEqual(self.ex.ledger.escrow("alice"), 0)
        self.assertEqual(self.ex.ledger.audit(), [])

    def test_bet_name_never_renames_and_confirm_true_is_not_enough(self):
        self.place("alice", 50, "n", name="Mallory")
        self.assertIsNone(self.ex.ledger.wallet("alice")["player"]["nickname"])
        L = self.ex.ledger
        L.place_bet("bob", "elsewhere", 250, market="other", question="q", option="yes", label="Yes", settles_at=0,
                    price=flat())
        with self.assertRaises(WalletError) as cm:  # 200 of 750 cash: over 25%
            self.place("bob", 200, "big", confirm=True)
        self.assertEqual(cm.exception.code, "confirm_required")
        out = self.place("bob", 200, "big", confirm_token=cm.exception.data["confirmToken"])
        self.assertEqual(out["escrow"], 450)


def build_sure(mid, store, spots, now, settles_at):
    import markets as mk
    import pricing
    import test_city as tc
    sim = pricing.LevelSim(tc.oxford_station(0.02), now, 5, random.Random(mk.seed_of(mid)))
    sim.edges = pricing.place_pct_brackets(sim.finals(), mk.BUSY_WIDTHS)
    return mk.Market(mid, "test-sure", "bracket", "Spike?", ["oxford-circus"], now, settles_at,
                     mk.bracket_options(mk.pct_labels(sim.edges), tc.OXFORD), sim, "src", "rule", {})


def settle_sure(m, store, now):
    """Final as soon as any reading after open shows Oxford Circus over 80% of usual; otherwise b0 at settlesAt."""
    import test_city as tc
    for ts, snap in store.between("crowding", m.opened_at + 1, now):
        if snap.get(tc.OXFORD["naptan"], 0) > 0.8:
            return ("done", "b3", "spike", {"value": 90, "source": "test", "fetchedAt": ts})
    return ("wait", m.settles_at - now) if now < m.settles_at else ("done", "b0", "calm", {})


class DecidedOutcomes(unittest.TestCase):
    def setUp(self):
        import markets as mk
        import test_city as tc
        from data import Store
        from exchange import Clock as ExClock, Exchange

        self.tc, self.mk = tc, mk
        self.raw = tc.Raw()
        self.addCleanup(self.raw.dir.cleanup)
        tc.crowd_history(self.raw, tc.T0, tc.CITY)
        spike = dict(tc.CITY, **{"oxford-circus": 0.9})
        self.raw.put("crowding", tc.T0 + 2 * tc.MIN, tc.crowding(spike))
        self.Exchange = lambda: Exchange(Store(self.raw.root, tc.SPOTS), ExClock(tc.T0), tc.SPOTS)

    def run_series(self, settles_early: bool):
        self.tc.TestSeries(self, self.mk.Series("test-sure", "crowding", build_sure, settle_sure,
                                           settles_early=settles_early))
        ex = self.Exchange()
        ex.ledger.open_account("alice")
        ex.tick(self.tc.T0)
        m = next(x for x in ex.active() if x.sub == "test-sure")
        self.assertEqual(ex.status(m, self.tc.T0), "open")
        ex.tick(self.tc.T0 + 2 * self.tc.MIN + 1000)
        return ex, m

    def test_a_decided_market_closes_and_settles_at_its_time(self):
        ex, m = self.run_series(settles_early=False)
        now = self.tc.T0 + 2 * self.tc.MIN + 1000
        self.assertEqual(ex.status(m, now), "closed")
        with self.assertRaisesRegex(PermissionError, "market closed"):
            ex.bet({"player": "alice", "market": m.id, "option": "b3", "stake": 100}, now)
        self.assertIsNone(m.result)
        ex.tick(m.settles_at)
        self.assertEqual(m.result["winner"], "b3")

    def test_a_settles_early_series_settles_as_soon_as_it_is_decided(self):
        _, m = self.run_series(settles_early=True)
        self.assertEqual((m.result["winner"], m.result["reading"]), ("b3", "spike"))


class Restart(unittest.TestCase):
    def setUp(self):
        import test_city as tc
        self.tc = tc
        self.raw = tc.Raw()
        self.addCleanup(self.raw.dir.cleanup)
        tc.city(self.raw, tc.T0 + 30 * tc.MIN)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "ledger.db"

    def exchange(self):
        from data import Store
        from exchange import Clock as ExClock, Exchange
        return Exchange(Store(self.raw.root, self.tc.SPOTS), ExClock(self.tc.T0), self.tc.SPOTS, Ledger(self.path))

    def test_bets_on_a_recorded_market_settle_from_data_after_a_restart(self):
        tc = self.tc
        tc.no_delay(self)
        ex = self.exchange()
        ex.ledger.open_account("alice")
        ex.tick(tc.T0)
        m = ex.biddable(tc.T0)[0]
        ex.bet({"player": "alice", "market": m.id, "option": m.option_ids()[1], "stake": 100,
                "idempotency_key": "k"}, tc.T0)
        orphan = ex.ledger.place_bet("alice", "o", 50, market="gone-1500", question="q", option="yes", label="Yes",
                                     settles_at=tc.T0, price=flat())
        ex.ledger.close()  # the process dies here, before the market settles

        ex2 = self.exchange()
        self.assertEqual(ex2.recover(), {"refunded": 1, "pending": 1})
        self.assertEqual(ex2.ledger.history("alice")["history"][0]["betId"], orphan["betId"])
        ex2.tick(tc.T0 + 2 * tc.MIN)
        self.assertEqual(ex2.ledger.escrow("alice"), 100, "not refunded while the result is still to come")
        ex2.tick(m.settles_at + 2 * tc.MIN)
        settled = {h["betId"]: h["result"] for h in ex2.ledger.history("alice")["history"]}
        self.assertEqual(sorted(settled.values()).count("void"), 1, settled)
        self.assertIn(settled[next(b for b in settled if b != orphan["betId"])], ("won", "lost"))
        self.assertEqual(ex2.ledger.escrow("alice"), 0)
        self.assertEqual(ex2.ledger.audit(), [])
        ex2.ledger.close()


class Identity(LedgerCase):
    def test_create_player_is_the_only_grant_and_keeps_only_a_hash(self):
        out = self.L.create_player("Ann")
        self.assertRegex(out["player"], r"^p_[0-9a-f]{16}$")
        self.assertGreaterEqual(len(out["token"]), 40)
        self.assertEqual(self.L.cash(out["player"]), GRANT)
        stored = self.L.db.execute("SELECT token_hash FROM players WHERE id = ?", (out["player"],)).fetchone()[0]
        self.assertNotEqual(stored, out["token"])
        self.assertEqual(stored, lg.token_hash(out["token"]))
        self.assertEqual(self.L.auth(out["player"], out["token"]), out["player"])

    def test_auth_refuses_wrong_missing_or_other_players_tokens(self):
        a, b = self.L.create_player(), self.L.create_player()
        for player, token in ((a["player"], b["token"]), (a["player"], ""), (a["player"], None),
                              ("nobody", a["token"]), ("alice", "x"), ("a b", a["token"])):
            with self.assertRaises(lg.AuthError):
                self.L.auth(player, token)

    def test_reads_never_create_players_or_grant(self):
        before = self.L._sum("kind = 'grant'", ())
        for read in (self.L.wallet, self.L.positions, self.L.history, self.L.ledger_rows):
            with self.assertRaises(LookupError):
                read("p_newcomer")
        with self.assertRaises(LookupError):
            self.L.set_limit("p_newcomer", 100)
        with self.assertRaises(LookupError):
            self.bet(player="p_newcomer")
        self.assertEqual(self.L._sum("kind = 'grant'", ()), before)
        self.assertIsNone(self.L.db.execute("SELECT 1 FROM players WHERE id = 'p_newcomer'").fetchone())

    def test_nickname_is_capped_and_printable(self):
        self.assertEqual(self.L.set_nickname("alice", "  Ann  ")["nickname"], "Ann")
        for bad in ("", "x" * 25, "a\nb", "tab\there", "\x00", "\u202e"):
            with self.assertRaises(ValueError):
                self.L.set_nickname("alice", bad)
        self.assertEqual(self.L.set_nickname("alice", "<b>x</b>")["nickname"], "<b>x</b>")  # React escapes it

    def test_leaderboard_points_are_ledger_equity(self):
        won = self.bet(player="alice", stake=100, odds=3.0)
        self.bet(player="bob", stake=200)
        self.L.settle_bets([won["betId"]], "yes", "Yes")
        self.L.set_nickname("alice", "Ann")
        rows = self.L.leaderboard()
        self.assertEqual(rows, [{"name": "Ann", "points": 1200, "kind": "human"},
                                {"name": lg.default_name("bob"), "points": 1000, "kind": "human"}])


class LeaderboardHidesTests(LedgerCase):
    def test_test_accounts_never_reach_the_leaderboard(self):
        self.bet(player="alice", stake=100)
        self.bet(player="bob", stake=100)
        self.L.set_nickname("bob", "test-lt-b-4580")
        self.assertEqual([r["name"] for r in self.L.leaderboard()], [lg.default_name("alice")])


class OddsFloor(unittest.TestCase):
    """A bet never pays more than the market maker's shares, so its value at the market price is never above 1."""

    def test_expected_value_at_most_one_at_every_price(self):
        import pricing
        priced = 0
        for i in range(1, 100):
            p = i / 100
            for stake in (10, 37, 100, 250, 1000):
                book = pricing.Book(["yes", "no"])
                shares = book.quote({"yes": p, "no": 1 - p}, "yes", stake)
                odds = pricing.locked_odds(shares, stake)
                if odds is None:
                    self.assertGreater(p, 0.85, "only heavy favourites are too short to bet")
                    continue
                priced += 1
                payout = lg.payout_of(stake, odds)
                self.assertLessEqual(payout, shares + 1e-9)
                self.assertLessEqual(p * payout / stake, 1.0, f"p={p} stake={stake} odds={odds}")
        self.assertGreater(priced, 400)

    def test_too_short_is_refused_and_the_ledger_also_refuses(self):
        import pricing
        self.assertIsNone(pricing.locked_odds(104.9, 100))
        self.assertEqual(pricing.locked_odds(105.0, 100), 1.05)
        self.assertEqual(pricing.locked_odds(5000, 100), 20.0)
        L = Ledger(":memory:", now=Clock())
        L.open_account("alice")
        with self.assertRaises(WalletError) as cm:
            L.place_bet("alice", "k", 100, market="m", question="q", option="yes", label="Yes", settles_at=0,
                        price=flat(1.04))
        self.assertEqual(cm.exception.code, "price_too_short")
        self.assertEqual(L.cash("alice"), GRANT)

    def test_payout_rounds_down(self):
        self.assertEqual(lg.payout_of(10, 1.99), 19)
        self.assertEqual(lg.payout_of(15, 1.5), 22)  # 22.5: down, not half-to-even 22 / half-up 23
        self.assertEqual(lg.payout_of(25, 1.5), 37)  # 37.5: down, where half-to-even gives 38
        self.assertEqual(lg.payout_of(100, 2.37), 237)


class Hardening(LedgerCase):
    def test_minimum_stake_is_10(self):
        with self.assertRaisesRegex(ValueError, "at least W10"):
            self.bet(stake=9)
        self.bet(stake=10)

    def test_gross_loss_limit_ignores_wins(self):
        won = self.bet(stake=500, odds=3.0)
        self.L.settle_bets([won["betId"]], "yes", "Yes")  # +1000
        self.assertEqual(self.L.cash("alice"), 2000)
        for stake in (300, 200):
            lost = self.bet(stake=stake)
            self.L.settle_bets([lost["betId"]], "no", "No")
        with self.assertRaises(WalletError) as cm:
            self.bet(stake=10)
        self.assertEqual(cm.exception.code, "loss_limit")
        self.assertEqual(self.L.wallet("alice")["limits"]["lostToday"], 500)

    def test_confirm_token_is_bound_and_expires(self):
        def place(token, stake=300, option="yes"):
            return self.L.place_bet("alice", f"k{stake}{option}{token}", stake, market="m1", question="q",
                                    option=option, label="x", settles_at=SETTLES, price=flat(), confirm_token=token)
        with self.assertRaises(WalletError) as cm:
            place(None)
        token = cm.exception.data["confirmToken"]
        for bad in ("true", "1", f"{NOON + 10**9}.forged", self.L.confirm_token("bob", "m1", SETTLES, "yes", 300),
                    self.L.confirm_token("alice", "m1", SETTLES, "no", 300),
                    self.L.confirm_token("alice", "m1", SETTLES, "yes", 310)):
            with self.assertRaises(WalletError):
                place(bad)
        self.clock.ms += lg.CONFIRM_TTL_MS + 1
        with self.assertRaises(WalletError):
            place(token)
        self.clock.ms = NOON
        self.assertEqual(place(token)["stake"], 300)

    def test_idempotency_is_scoped_to_the_market_round(self):
        first = self.bet(key="tap", market="oxford-busy-1505", settles_at=SETTLES)
        later = self.bet(key="tap", market="oxford-busy-1505", settles_at=SETTLES + 24 * HOUR)
        self.assertNotEqual(first["betId"], later["betId"])
        self.assertFalse(later["replayed"])
        self.assertTrue(self.bet(key="tap", market="oxford-busy-1505", settles_at=SETTLES)["replayed"])

    def test_book_commits_only_after_the_insert(self):
        committed = []
        with mock.patch.object(self.L, "_post", side_effect=sqlite3.IntegrityError("disk")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.bet(commit=lambda: committed.append(1))
        self.assertEqual(committed, [])
        self.assertEqual(self.L.db.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)
        self.bet(commit=lambda: committed.append(1))
        self.assertEqual(committed, [1])

    def test_since_must_be_finite(self):
        for bad in ("inf", "-inf", "nan", "soon"):
            with self.assertRaises(ValueError):
                self.L.wallet("alice", since=bad)
        self.assertIn("session", self.L.wallet("alice", since=str(NOON))["pnl"])


class HouseExposure(unittest.TestCase):
    def test_bets_past_the_worst_case_cap_are_refused(self):
        L = Ledger(":memory:", now=Clock(), bankroll=100_000, exposure_cap=1_000)
        for p in ("a", "b", "c"):
            L.open_account(p)

        def bet(p, option, stake, odds, market="m"):
            return L.place_bet(p, f"{p}{option}{stake}{market}", stake, market=market, question="q", option=option,
                               label="x", settles_at=0, price=flat(odds),
                               confirm_token=L.confirm_token(p, market, 0, option, stake))
        bet("a", "yes", 200, 5.0)  # house worst case: yes wins, pays 800
        self.assertEqual(L.house()["exposure"], 800)
        bet("b", "no", 200, 2.0)  # yes still worst: 800 - 200 = 600; no: 200 - 200 = 0
        self.assertEqual(L.house()["exposure"], 600)
        bet("c", "yes", 200, 2.0)  # yes: 800 + 200 won, less 200 lost on no = 800
        self.assertEqual(L.house()["exposure"], 800)
        with self.assertRaises(WalletError) as cm:
            bet("c", "yes", 100, 6.0, market="m2")  # m2 adds 500: 1300 > 1000
        self.assertEqual(cm.exception.code, "house_limit")
        self.assertEqual(L.audit(), [])

    def test_bankroll_shrinks_the_room_after_house_losses(self):
        L = Ledger(":memory:", now=Clock(), bankroll=1_000, exposure_cap=100_000)
        L.open_account("a")
        L.set_limit("a", lg.MAX_LOSS_LIMIT)
        won = L.place_bet("a", "k1", 200, market="m", question="q", option="yes", label="x", settles_at=0,
                          price=flat(4.0), confirm_token=L.confirm_token("a", "m", 0, "yes", 200))
        L.settle_bets([won["betId"]], "yes", "Yes")  # house -600
        self.assertEqual(L.house()["cap"], 400)
        with self.assertRaises(WalletError) as cm:
            L.place_bet("a", "k2", 250, market="m2", question="q", option="yes", label="x", settles_at=0,
                        price=flat(3.0), confirm_token=L.confirm_token("a", "m2", 0, "yes", 250))
        self.assertEqual(cm.exception.code, "house_limit")


class ThreadSafety(unittest.TestCase):
    def test_8_threads_x_40_bets_without_a_server_lock_keep_the_audit_clean(self):
        import threading
        L = Ledger(":memory:")
        players = ["a", "b", "c", "d"]
        for p in players:
            L.open_account(p)
        errors, placed = [], []

        def worker(t):
            for i in range(40):
                p = players[(t + i) % len(players)]
                try:
                    out = L.place_bet(p, f"t{t}-{i}", 10, market=f"m{i % 3}", question="q", option="yes", label="x",
                                      settles_at=0, price=flat(2.0))
                    placed.append(out["betId"])
                    if i % 4 == 0:
                        L.settle_bets(placed[-3:], "yes" if i % 8 else "no", "r")
                    L.wallet(p)
                except WalletError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    errors.append(repr(exc))
        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertGreater(len(placed), 100)
        self.assertEqual(L.audit(), [])


class SharedDb(unittest.TestCase):
    def test_a_second_ledger_on_the_same_file_fails_fast(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.db"
            a = Ledger(path)
            with self.assertRaisesRegex(lg.LedgerInUse, "in use by another city server"):
                Ledger(path)
            a.close()
            Ledger(path).close()

    def test_replay_never_opens_the_real_ledger(self):
        import server
        with mock.patch.dict("os.environ", {"CITY_LEDGER_DB": "/nonexistent/should-not-open.db"}):
            L = server.open_ledger(replay=True)
        self.assertEqual(L.path, ":memory:")
        L.close()


class Versus(LedgerCase):
    """GET /versus: head-to-head P&L against each AI trader on settled markets both bet on."""

    @staticmethod
    def market(mid, winner, ai_bets, settles_at=SETTLES):
        from types import SimpleNamespace
        return SimpleNamespace(id=mid, settles_at=settles_at, result={"winner": winner}, ai_bets=ai_bets)

    def test_scores_shared_markets_only(self):
        a = self.bet(market="m1", option="yes", stake=100, odds=2.0)["betId"]
        b = self.bet(market="m2", option="no", stake=100, odds=2.0)["betId"]
        c = self.bet(market="m3", option="yes", stake=50, odds=2.0)["betId"]
        self.L.settle_bets([a], "yes", "r")
        self.L.settle_bets([b], "yes", "r")
        self.L.settle_bets([c], None, "void")
        markets = {
            "m1": self.market("m1", "yes", [{"trader": "tube", "option": "no", "stake": 100, "odds": 2.0}]),
            "m2": self.market("m2", "yes", [{"trader": "tube", "option": "yes", "stake": 100, "odds": 3.0},
                                            {"trader": "rain", "option": "no", "stake": 100, "odds": 2.0}]),
            "m3": self.market("m3", None, [{"trader": "tube", "option": "yes", "stake": 100, "odds": 2.0}]),
        }
        got = {t["trader"]: t for t in lg.versus(self.L, "alice", markets)["versus"]}
        self.assertEqual(got["tube"], {"trader": "tube", "markets": 2, "won": 1, "lost": 1, "tied": 0,
                                       "pnl": 0, "theirPnl": 100})
        self.assertEqual((got["rain"]["markets"], got["rain"]["tied"]), (1, 1))

    def test_ignores_a_reused_market_id_from_another_round(self):
        a = self.bet(market="m1", option="yes")["betId"]
        self.L.settle_bets([a], "yes", "r")
        markets = {"m1": self.market("m1", "yes", [{"trader": "tube", "option": "no", "stake": 100, "odds": 2.0}],
                                     settles_at=SETTLES + 86_400_000)}
        self.assertEqual(lg.versus(self.L, "alice", markets), {"versus": []})


class PinRecovery(LedgerCase):
    def player(self, name="Ann", pin="4821"):
        out = self.L.create_player(name)
        if pin:
            self.L.set_pin(out["player"], pin)
        return out

    def test_start_balance_is_one_grant_of_1000(self):
        self.assertEqual(GRANT, 1000)
        self.assertEqual(self.L.cash(self.L.create_player()["player"]), 1000)

    def test_pin_is_salted_hashed_and_verifies(self):
        a, b = self.player("Ann"), self.player("Bea")
        rows = {r["player"]: r for r in self.L.db.execute("SELECT * FROM player_pins")}
        self.assertNotEqual(rows[a["player"]]["salt"], rows[b["player"]]["salt"])
        self.assertNotIn(b"4821", rows[a["player"]]["pin_hash"])
        self.assertNotEqual(rows[a["player"]]["pin_hash"], rows[b["player"]]["pin_hash"], "same PIN, other salt")
        self.assertTrue(self.L.wallet(a["player"])["player"]["hasPin"])
        self.assertFalse(self.L.wallet(self.L.create_player("Cy")["player"])["player"]["hasPin"])
        self.assertEqual(self.L.recover("ann", "4821")["player"], a["player"], "names match case-insensitively")

    def test_pin_must_be_four_digits_and_needs_a_name(self):
        a = self.player(pin=None)
        for bad in ("", "123", "12345", "abcd", "12 4", None, 1234.5):
            with self.assertRaises(ValueError):
                self.L.set_pin(a["player"], bad)
        nameless = self.L.create_player()
        with self.assertRaises(WalletError) as err:
            self.L.set_pin(nameless["player"], "1234")
        self.assertEqual(err.exception.code, "name_required")

    def test_change_pin_replaces_the_old_one(self):
        a = self.player(pin="1111")
        self.L.set_pin(a["player"], "2222")
        with self.assertRaises(lg.AuthError):
            self.L.recover("Ann", "1111")
        self.assertEqual(self.L.recover("Ann", "2222")["player"], a["player"])

    def test_recover_issues_a_new_token_and_the_old_one_still_works(self):
        a = self.player()
        out = self.L.recover("Ann", "4821")
        self.assertEqual((out["player"], out["nickname"]), (a["player"], "Ann"))
        self.assertNotEqual(out["token"], a["token"])
        second = self.L.recover("Ann", "4821")["token"]
        for t in (a["token"], out["token"], second):
            self.assertEqual(self.L.auth(a["player"], t), a["player"])
        other = self.player("Bea", "1111")
        with self.assertRaises(lg.AuthError):
            self.L.auth(other["player"], out["token"])
        stored = [r[0] for r in self.L.db.execute("SELECT token_hash FROM player_tokens")]
        self.assertIn(lg.token_hash(out["token"]), stored)
        self.assertNotIn(out["token"], stored)
        self.assertEqual(self.L._sum("kind = 'grant'", ()), 5 * GRANT, "recover grants nothing")

    def test_wrong_pin_unknown_name_and_no_pin_fail_identically(self):
        self.player("Ann", "4821")
        self.player("Bea", pin=None)
        seen = set()
        for name, pin in (("Ann", "0000"), ("Nobody", "4821"), ("Bea", "4821"), ("", "4821"), ("Ann", ""),
                          ("Ann", "48211"), (None, None)):
            with self.assertRaises(lg.AuthError) as err:
                self.L.recover(name, pin)
            seen.add(str(err.exception))
        self.assertEqual(seen, {lg.RECOVER_FAILED})

    def test_five_misses_lock_the_name_for_15_minutes(self):
        a = self.player()
        for _ in range(lg.RECOVER_FAILS):
            with self.assertRaises(lg.AuthError):
                self.L.recover("Ann", "0000")
        with self.assertRaises(WalletError) as err:
            self.L.recover("ANN", "4821")
        self.assertEqual(err.exception.code, "locked", "even the right PIN is refused while locked")
        self.clock.ms += lg.RECOVER_WINDOW_MS - 1
        with self.assertRaises(WalletError):
            self.L.recover("Ann", "4821")
        self.clock.ms += 1
        self.assertEqual(self.L.recover("Ann", "4821")["player"], a["player"])

    def test_misses_spread_over_more_than_15_minutes_do_not_lock(self):
        self.player()
        for _ in range(lg.RECOVER_FAILS - 1):
            with self.assertRaises(lg.AuthError):
                self.L.recover("Ann", "0000")
        self.clock.ms += lg.RECOVER_WINDOW_MS
        with self.assertRaises(lg.AuthError):
            self.L.recover("Ann", "0000")
        self.L.recover("Ann", "4821")

    def test_an_unknown_name_locks_the_same_way(self):
        for _ in range(lg.RECOVER_FAILS):
            with self.assertRaises(lg.AuthError):
                self.L.recover("Ghost", "0000")
        with self.assertRaises(WalletError) as err:
            self.L.recover("Ghost", "0000")
        self.assertEqual(err.exception.code, "locked")

    def test_names_are_unique_case_insensitively(self):
        a = self.player("Ann", pin=None)
        for name in ("Ann", "ANN", "  ann "):
            with self.assertRaises(WalletError) as err:
                self.L.create_player(name)
            self.assertEqual(err.exception.code, "name_taken")
        b = self.L.create_player("Bea")
        with self.assertRaises(WalletError):
            self.L.set_nickname(b["player"], "aNN")
        self.assertEqual(self.L.set_nickname(a["player"], "ANN")["nickname"], "ANN", "a player may recase its own")
        self.L.create_player()
        self.L.create_player()

    def test_tables_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.db"
            first = Ledger(path, now=self.clock)
            a = first.create_player("Ann")
            first.set_pin(a["player"], "4821")
            extra = first.recover("Ann", "4821")["token"]
            first.close()
            again = Ledger(path, now=self.clock)
            self.addCleanup(again.close)
            self.assertEqual(again.auth(a["player"], extra), a["player"])
            self.assertEqual(again.recover("Ann", "4821")["player"], a["player"])


if __name__ == "__main__":
    unittest.main()
