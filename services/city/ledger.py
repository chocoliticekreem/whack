"""Player wallets: an append-only, double-entry points ledger in SQLite, built so it could later hold money.

Balances are never stored. Every number is a SUM over `entries`, so the ledger is its own audit trail.

Accounts per player: player_cash, player_escrow, and house (the exchange's side of that player's bets).
    grant        +cash                                  (one-sided: points enter the system here only)
    bet_hold     -cash  +escrow                         (stake locked when the bet is placed)
    bet_win      -escrow  -house(winnings)  +cash(payout)
    bet_loss     -escrow  +house
    void_refund  -escrow  +cash
Every bet's entries sum to zero; the sum of all entries equals the sum of grants.

Points are integers end to end. Floats never touch the ledger.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

GRANT = 1000
DEFAULT_LOSS_LIMIT = 500
MAX_LOSS_LIMIT = 100_000
RAISE_DELAY_MS = 24 * 3600 * 1000
CONFIRM_SHARE = 0.25
BREAK_HOURS = (1, 24)
LONDON = ZoneInfo("Europe/London")
PLAYER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SETTLE_KINDS = ("bet_win", "bet_loss", "void_refund")
DEFAULT_DB = Path(__file__).resolve().parent / "data" / "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
  id          TEXT PRIMARY KEY,
  nickname    TEXT,
  created_at  INTEGER NOT NULL,
  -- KYC hooks for a real-money launch. Nothing personal is collected while this is a points game.
  kyc_status  TEXT NOT NULL DEFAULT 'unverified'
              CHECK (kyc_status IN ('unverified', 'pending', 'verified', 'rejected'))
);

CREATE TABLE IF NOT EXISTS bets (
  id               TEXT PRIMARY KEY,
  player           TEXT NOT NULL REFERENCES players(id),
  market           TEXT NOT NULL,
  question         TEXT NOT NULL,
  option           TEXT NOT NULL,
  label            TEXT NOT NULL,
  stake            INTEGER NOT NULL CHECK (typeof(stake) = 'integer' AND stake > 0),
  odds             REAL NOT NULL,
  shares           REAL NOT NULL,
  payout           INTEGER NOT NULL CHECK (typeof(payout) = 'integer' AND payout >= stake),
  placed_at        INTEGER NOT NULL,
  settles_at       INTEGER NOT NULL,
  idempotency_key  TEXT NOT NULL,
  UNIQUE (player, idempotency_key)
);

CREATE TABLE IF NOT EXISTS settlements (
  bet_id      TEXT PRIMARY KEY REFERENCES bets(id),
  outcome     TEXT NOT NULL CHECK (outcome IN ('won', 'lost', 'void')),
  reading     TEXT NOT NULL,
  settled_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ts               INTEGER NOT NULL,
  player           TEXT NOT NULL REFERENCES players(id),
  account          TEXT NOT NULL CHECK (account IN ('player_cash', 'player_escrow', 'house')),
  amount           INTEGER NOT NULL CHECK (typeof(amount) = 'integer' AND amount <> 0),
  kind             TEXT NOT NULL
                   CHECK (kind IN ('grant', 'bet_hold', 'bet_win', 'bet_loss', 'void_refund', 'adjustment')),
  market           TEXT,
  bet_id           TEXT REFERENCES bets(id),
  idempotency_key  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_player ON entries (player, account);
CREATE INDEX IF NOT EXISTS entries_bet ON entries (bet_id);
-- One leg per account per operation: a retried grant, hold or settle cannot post twice.
CREATE UNIQUE INDEX IF NOT EXISTS entries_once ON entries (idempotency_key, account);
-- A bet's escrow is released by exactly one of win, loss or void.
CREATE UNIQUE INDEX IF NOT EXISTS entries_settle_once ON entries (bet_id, account)
  WHERE kind IN ('bet_win', 'bet_loss', 'void_refund');

CREATE TABLE IF NOT EXISTS limit_changes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  player        TEXT NOT NULL REFERENCES players(id),
  requested_at  INTEGER NOT NULL,
  value         INTEGER NOT NULL CHECK (typeof(value) = 'integer' AND value > 0)
);

CREATE TABLE IF NOT EXISTS breaks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  player      TEXT NOT NULL REFERENCES players(id),
  started_at  INTEGER NOT NULL,
  until       INTEGER NOT NULL
);

CREATE TRIGGER IF NOT EXISTS cash_never_negative AFTER INSERT ON entries
WHEN NEW.account IN ('player_cash', 'player_escrow')
 AND (SELECT SUM(amount) FROM entries WHERE player = NEW.player AND account = NEW.account) < 0
BEGIN SELECT RAISE(ABORT, 'insufficient funds'); END;
"""

APPEND_ONLY = ("entries", "bets", "settlements", "limit_changes", "breaks")


class WalletError(PermissionError):
    """A bet or wallet request refused for a reason the player can act on. `code` is stable for the UI."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def wall_ms() -> int:
    return int(time.time() * 1000)


def london_midnight(ms: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, LONDON)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def as_points(value, field: str = "stake") -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a whole number of points")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} must be a whole number of points")
    return int(value)


class Ledger:
    def __init__(self, path: str | os.PathLike = ":memory:", now: Callable[[], int] = wall_ms):
        self.path, self.now = str(path), now
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = FULL")
        self.db.executescript(SCHEMA)
        for table in APPEND_ONLY:
            for op in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END")

    @classmethod
    def from_env(cls) -> "Ledger":
        return cls(os.environ.get("CITY_LEDGER_DB") or DEFAULT_DB)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ plumbing

    def _tx(self):
        return _Tx(self.db)

    def _post(self, legs: list[tuple[str, int]], *, player: str, kind: str, key: str, ts: int,
              market: str | None = None, bet_id: str | None = None) -> None:
        if kind != "grant" and sum(a for _, a in legs) != 0:
            raise AssertionError(f"{kind} legs must sum to zero: {legs}")
        for account, amount in legs:
            if amount:
                self.db.execute("INSERT INTO entries (ts, player, account, amount, kind, market, bet_id, "
                                "idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (ts, player, account, int(amount), kind, market, bet_id, key))

    def _sum(self, where: str, args: tuple) -> int:
        return int(self.db.execute(f"SELECT COALESCE(SUM(amount), 0) FROM entries WHERE {where}", args).fetchone()[0])

    # ------------------------------------------------------------ players

    @staticmethod
    def check_player(player) -> str:
        player = str(player or "")
        if not PLAYER_RE.match(player):
            raise ValueError("player must be 1-64 letters, digits, '-' or '_'")
        return player

    def ensure_player(self, player, nickname: str | None = None) -> str:
        """Creates the account on first sight and posts its one-time grant. Safe to call any number of times."""
        player = self.check_player(player)
        nick = (str(nickname).strip()[:32] or None) if nickname else None
        if self.db.execute("SELECT 1 FROM players WHERE id = ?", (player,)).fetchone():
            if nick:
                self.db.execute("UPDATE players SET nickname = ? WHERE id = ? AND nickname IS NOT ?",
                                (nick, player, nick))
            return player
        ts = self.now()
        with self._tx():
            self.db.execute("INSERT OR IGNORE INTO players (id, nickname, created_at) VALUES (?, ?, ?)",
                            (player, nick, ts))
            if not self.db.execute("SELECT 1 FROM entries WHERE idempotency_key = ?", (f"grant:{player}",)).fetchone():
                self._post([("player_cash", GRANT)], player=player, kind="grant", key=f"grant:{player}", ts=ts)
        return player

    def cash(self, player: str) -> int:
        return self._sum("player = ? AND account = 'player_cash'", (player,))

    def escrow(self, player: str) -> int:
        return self._sum("player = ? AND account = 'player_escrow'", (player,))

    def equity(self, player: str) -> int:
        return self._sum("player = ? AND account IN ('player_cash', 'player_escrow')", (player,))

    # ------------------------------------------------------------ responsible play

    def loss_limit(self, player: str, at: int | None = None) -> tuple[int, dict | None]:
        """(limit in force, pending raise or None). A lower limit applies at once and cancels any pending raise;
        a higher one applies 24 h after it was asked for."""
        at = self.now() if at is None else at
        current, pending = DEFAULT_LOSS_LIMIT, None
        for row in self.db.execute("SELECT requested_at, value FROM limit_changes WHERE player = ? ORDER BY id",
                                   (player,)):
            if pending and pending["effectiveAt"] <= row["requested_at"]:
                current, pending = pending["value"], None
            if row["value"] <= current:
                current, pending = row["value"], None
            else:
                pending = {"value": row["value"], "effectiveAt": row["requested_at"] + RAISE_DELAY_MS}
        if pending and pending["effectiveAt"] <= at:
            current, pending = pending["value"], None
        return current, pending

    def break_until(self, player: str, at: int | None = None) -> int | None:
        at = self.now() if at is None else at
        until = self.db.execute("SELECT MAX(until) FROM breaks WHERE player = ?", (player,)).fetchone()[0]
        return int(until) if until and until > at else None

    def set_limit(self, player, value) -> dict:
        player = self.ensure_player(player)
        value = as_points(value, "dailyLoss")
        if not 0 < value <= MAX_LOSS_LIMIT:
            raise ValueError(f"dailyLoss must be between 1 and {MAX_LOSS_LIMIT}")
        self.db.execute("INSERT INTO limit_changes (player, requested_at, value) VALUES (?, ?, ?)",
                        (player, self.now(), value))
        return self.wallet(player)

    def take_break(self, player, hours) -> dict:
        player = self.ensure_player(player)
        if hours not in BREAK_HOURS or isinstance(hours, bool):
            raise ValueError("hours must be 1 or 24")
        now = self.now()
        until = max(self.break_until(player, now) or now, now + int(hours) * 3600 * 1000)
        self.db.execute("INSERT INTO breaks (player, started_at, until) VALUES (?, ?, ?)", (player, now, until))
        return self.wallet(player)

    def pnl_since(self, player: str, since: int) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(CASE s.outcome WHEN 'won' THEN b.payout - b.stake WHEN 'lost' THEN -b.stake "
            "ELSE 0 END), 0) FROM settlements s JOIN bets b ON b.id = s.bet_id "
            "WHERE b.player = ? AND s.settled_at >= ?", (player, since)).fetchone()
        return int(row[0])

    def _guard(self, player: str, stake: int, confirm: bool, now: int) -> None:
        until = self.break_until(player, now)
        if until:
            raise WalletError("on_break", f"You are taking a break until {_clock(until)}. Bets are paused.")
        cash = self.cash(player)
        if stake > cash:
            raise WalletError("insufficient_funds", f"Insufficient funds: stake {stake}, available {cash}")
        if stake > CONFIRM_SHARE * cash and not confirm:
            raise WalletError("confirm_required",
                              f"This stake is {round(100 * stake / cash)}% of your balance. Confirm to place it.")
        limit, _ = self.loss_limit(player, now)
        lost_today = max(0, -self.pnl_since(player, london_midnight(now)))
        room = limit - lost_today - self.escrow(player)
        if stake > room:
            raise WalletError("loss_limit", f"Daily loss limit: you can risk {max(0, room)} more points today "
                                            f"(limit {limit}, lost {lost_today}, in play {self.escrow(player)})")

    # ------------------------------------------------------------ bets

    def find_bet(self, player: str, key: str, market: str, option: str, stake) -> dict | None:
        """The bet already booked under this key, or None. A key reused for a different bet is refused."""
        row = self.db.execute("SELECT * FROM bets WHERE player = ? AND idempotency_key = ?", (player, key)).fetchone()
        if row is None:
            return None
        if (row["market"], row["option"], row["stake"]) != (market, option, as_points(stake)):
            raise ValueError("idempotency_key was already used for a different bet")
        return self._booked(dict(row), replayed=True)

    def _booked(self, bet: dict, replayed: bool) -> dict:
        return {"betId": bet["id"], "market": bet["market"], "option": bet["option"], "stake": bet["stake"],
                "odds": bet["odds"], "shares": bet["shares"], "payout": bet["payout"], "replayed": replayed,
                "balance": self.cash(bet["player"]), "escrow": self.escrow(bet["player"])}

    def place_bet(self, player, key, stake, *, market: str, question: str, option: str, label: str,
                  settles_at: int, price: Callable[[], tuple[float, float]], confirm: bool = False) -> dict:
        """Moves the stake from cash to escrow in one transaction. `price()` books the bet on the market and
        returns (shares, locked odds); it runs inside the transaction, after every check has passed.
        Replaying an idempotency key returns the original bet and changes nothing."""
        player = self.ensure_player(player)
        key = str(key or "")
        if not 0 < len(key) <= 128:
            raise ValueError("idempotency_key is required (1-128 chars)")
        stake = as_points(stake)
        if stake <= 0:
            raise ValueError("stake must be a positive number")
        prior = self.find_bet(player, key, market, option, stake)
        if prior:
            return prior
        with self._tx():
            now = self.now()
            self._guard(player, stake, confirm, now)
            shares, odds = price()
            bet_id = uuid.uuid4().hex
            payout = max(stake, int(round(stake * odds)))
            self.db.execute("INSERT INTO bets (id, player, market, question, option, label, stake, odds, shares, "
                            "payout, placed_at, settles_at, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (bet_id, player, market, question, option, label, stake, float(odds), float(shares),
                             payout, now, int(settles_at), key))
            self._post([("player_cash", -stake), ("player_escrow", stake)], player=player, kind="bet_hold",
                       key=f"hold:{bet_id}", ts=now, market=market, bet_id=bet_id)
        return self._booked({"id": bet_id, "player": player, "market": market, "option": option, "stake": stake,
                             "odds": float(odds), "shares": float(shares), "payout": payout}, replayed=False)

    def settle_bets(self, bet_ids: list[str], winner: str | None, reading: str) -> int:
        """Settles each open bet in `bet_ids` against `winner` (None = void). Bets already settled are skipped,
        so calling this again changes nothing. Returns how many bets it settled."""
        done = 0
        with self._tx():
            now = self.now()
            for bet_id in bet_ids:
                bet = self.db.execute("SELECT b.* FROM bets b LEFT JOIN settlements s ON s.bet_id = b.id "
                                      "WHERE b.id = ? AND s.bet_id IS NULL", (bet_id,)).fetchone()
                if bet is None:
                    continue
                outcome = "void" if winner is None else "won" if bet["option"] == winner else "lost"
                self._settle_one(bet, outcome, reading, now)
                done += 1
        return done

    def _settle_one(self, bet: sqlite3.Row, outcome: str, reading: str, now: int) -> None:
        stake, payout = bet["stake"], bet["payout"]
        legs = {"won": [("player_escrow", -stake), ("house", -(payout - stake)), ("player_cash", payout)],
                "lost": [("player_escrow", -stake), ("house", stake)],
                "void": [("player_escrow", -stake), ("player_cash", stake)]}[outcome]
        kind = {"won": "bet_win", "lost": "bet_loss", "void": "void_refund"}[outcome]
        self.db.execute("INSERT INTO settlements (bet_id, outcome, reading, settled_at) VALUES (?, ?, ?, ?)",
                        (bet["id"], outcome, str(reading)[:300], now))
        self._post(legs, player=bet["player"], kind=kind, key=f"settle:{bet['id']}", ts=now,
                   market=bet["market"], bet_id=bet["id"])

    def open_bet_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT b.id FROM bets b LEFT JOIN settlements s ON s.bet_id = b.id WHERE s.bet_id IS NULL")]

    def void_open(self, reason: str) -> int:
        """Refunds every open bet. The exchange keeps its book in memory, so on a restart no market is left to
        settle these against."""
        return self.settle_bets(self.open_bet_ids(), None, reason)

    # ------------------------------------------------------------ views

    def wallet(self, player, since=None) -> dict:
        player = self.ensure_player(player)
        now = self.now()
        p = self.db.execute("SELECT * FROM players WHERE id = ?", (player,)).fetchone()
        cash, escrow = self.cash(player), self.escrow(player)
        limit, pending = self.loss_limit(player, now)
        today = self.pnl_since(player, london_midnight(now))
        lost_today = max(0, -today)
        t = self.db.execute(
            "SELECT COUNT(*) AS placed, COALESCE(SUM(b.stake), 0) AS staked, COUNT(s.bet_id) AS settled, "
            "COALESCE(SUM(CASE s.outcome WHEN 'won' THEN 1 ELSE 0 END), 0) AS won, "
            "COALESCE(SUM(CASE s.outcome WHEN 'lost' THEN 1 ELSE 0 END), 0) AS lost, "
            "COALESCE(SUM(CASE s.outcome WHEN 'void' THEN 1 ELSE 0 END), 0) AS voided, "
            "COALESCE(SUM(CASE s.outcome WHEN 'won' THEN b.payout WHEN 'void' THEN b.stake ELSE 0 END), 0) AS returned "
            "FROM bets b LEFT JOIN settlements s ON s.bet_id = b.id WHERE b.player = ?", (player,)).fetchone()
        granted = self._sum("player = ? AND kind = 'grant'", (player,))
        until = self.break_until(player, now)
        out = {
            "player": {"id": player, "nickname": p["nickname"], "kycStatus": p["kyc_status"],
                       "createdAt": p["created_at"]},
            "cash": cash, "escrow": escrow, "total": cash + escrow,
            "totals": {"granted": granted, "staked": int(t["staked"]), "returned": int(t["returned"]),
                       "betsPlaced": t["placed"], "betsSettled": t["settled"], "won": t["won"], "lost": t["lost"],
                       "void": t["voided"]},
            "pnl": {"today": today, "allTime": self.pnl_since(player, 0)},
            "limits": {"dailyLoss": limit, "pending": pending, "lostToday": lost_today, "inPlay": escrow,
                       "remainingToday": max(0, limit - lost_today - escrow),
                       "confirmAbove": int(CONFIRM_SHARE * cash)},
            "break": {"active": until is not None, "until": until},
            "asOf": now,
        }
        if since not in (None, ""):
            out["pnl"]["session"] = self.pnl_since(player, int(float(since)))
        return out

    def ledger_rows(self, player, limit=50) -> dict:
        player = self.ensure_player(player)
        limit = max(1, min(500, int(limit or 50)))
        rows = self.db.execute("SELECT id, ts, account, amount, kind, market, bet_id, idempotency_key FROM entries "
                               "WHERE player = ? ORDER BY id DESC LIMIT ?", (player, limit))
        return {"entries": [{"id": r["id"], "ts": r["ts"], "account": r["account"], "amount": r["amount"],
                             "kind": r["kind"], "market": r["market"], "betId": r["bet_id"],
                             "idempotencyKey": r["idempotency_key"]} for r in rows]}

    def positions(self, player) -> dict:
        player = self.ensure_player(player)
        rows = self.db.execute("SELECT b.* FROM bets b LEFT JOIN settlements s ON s.bet_id = b.id "
                               "WHERE b.player = ? AND s.bet_id IS NULL ORDER BY b.settles_at", (player,))
        return {"positions": [{"betId": r["id"], "market": r["market"], "question": r["question"],
                               "option": r["option"], "label": r["label"], "stake": r["stake"], "odds": r["odds"],
                               "toWin": r["payout"], "profit": r["payout"] - r["stake"], "placedAt": r["placed_at"],
                               "settlesAt": r["settles_at"]} for r in rows]}

    def history(self, player, limit=100) -> dict:
        player = self.ensure_player(player)
        limit = max(1, min(500, int(limit or 100)))
        rows = [dict(r) for r in self.db.execute(
            "SELECT b.*, s.outcome, s.reading, s.settled_at FROM bets b JOIN settlements s ON s.bet_id = b.id "
            "WHERE b.player = ? ORDER BY s.settled_at, b.placed_at", (player,))]
        series, cum = [], 0
        out = []
        for r in rows:
            paid = r["payout"] if r["outcome"] == "won" else r["stake"] if r["outcome"] == "void" else 0
            pnl = paid - r["stake"]
            cum += pnl
            series.append([r["settled_at"], cum])
            out.append({"betId": r["id"], "market": r["market"], "question": r["question"], "option": r["option"],
                        "label": r["label"], "stake": r["stake"], "odds": r["odds"], "payout": paid,
                        "result": r["outcome"], "pnl": pnl, "reading": r["reading"], "placedAt": r["placed_at"],
                        "settledAt": r["settled_at"]})
        return {"history": out[::-1][:limit], "series": series}

    # ------------------------------------------------------------ audit

    def audit(self) -> list[str]:
        """Every invariant the ledger promises, checked from the raw entries. Empty list = healthy."""
        bad = []
        for r in self.db.execute("SELECT bet_id, SUM(amount) AS s FROM entries WHERE bet_id IS NOT NULL "
                                 "GROUP BY bet_id HAVING s <> 0"):
            bad.append(f"bet {r['bet_id']} entries sum to {r['s']}")
        for r in self.db.execute("SELECT player, account, SUM(amount) AS s FROM entries "
                                 "WHERE account IN ('player_cash', 'player_escrow') GROUP BY player, account "
                                 "HAVING s < 0"):
            bad.append(f"{r['player']} {r['account']} is negative ({r['s']})")
        for r in self.db.execute("SELECT bet_id, COUNT(DISTINCT kind) AS n FROM entries "
                                 "WHERE kind IN ('bet_win', 'bet_loss', 'void_refund') GROUP BY bet_id HAVING n > 1"):
            bad.append(f"bet {r['bet_id']} settled more than once")
        for r in self.db.execute("SELECT b.id, COUNT(e.id) AS n FROM bets b LEFT JOIN entries e "
                                 "ON e.bet_id = b.id AND e.kind = 'bet_hold' GROUP BY b.id HAVING n <> 2"):
            bad.append(f"bet {r['id']} has {r['n']} hold legs")
        total = self._sum("1 = 1", ())
        minted = self._sum("kind IN ('grant', 'adjustment')", ())
        if total != minted:
            bad.append(f"accounts sum to {total} but grants total {minted}")
        for r in self.db.execute(
                "SELECT p.id, (SELECT COALESCE(SUM(amount), 0) FROM entries WHERE player = p.id "
                "AND account = 'player_escrow') AS escrow, (SELECT COALESCE(SUM(b.stake), 0) FROM bets b "
                "LEFT JOIN settlements s ON s.bet_id = b.id WHERE b.player = p.id AND s.bet_id IS NULL) AS open "
                "FROM players p"):
            if r["escrow"] != r["open"]:
                bad.append(f"{r['id']} escrow {r['escrow']} != open stakes {r['open']}")
        for r in self.db.execute("SELECT b.id FROM bets b JOIN settlements s ON s.bet_id = b.id "
                                 "LEFT JOIN entries e ON e.bet_id = b.id AND e.kind IN "
                                 "('bet_win', 'bet_loss', 'void_refund') WHERE e.id IS NULL"):
            bad.append(f"bet {r['id']} has a settlement but no settle entries")
        return bad


class _Tx:
    """BEGIN IMMEDIATE ... COMMIT, rolled back on any exception. Nested use joins the outer transaction."""

    def __init__(self, db: sqlite3.Connection):
        self.db, self.outer = db, False

    def __enter__(self):
        self.outer = not self.db.in_transaction
        if self.outer:
            self.db.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.outer:
            self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def _clock(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, LONDON)
    today = datetime.now(LONDON).date()
    return d.strftime("%H:%M") if d.date() == today else d.strftime("%a %H:%M")


def get_routes(ledger: Ledger, q: dict) -> dict:
    player = q.get("player")
    return {"/wallet": lambda: ledger.wallet(player, q.get("since")),
            "/ledger": lambda: ledger.ledger_rows(player, q.get("limit") or 50),
            "/positions": lambda: ledger.positions(player),
            "/history": lambda: ledger.history(player, q.get("limit") or 100)}


def post_routes(ledger: Ledger, body: dict) -> dict:
    return {"/limits": lambda: ledger.set_limit(body.get("player"), body.get("dailyLoss")),
            "/break": lambda: ledger.take_break(body.get("player"), body.get("hours"))}

