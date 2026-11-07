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

Identity: POST /player/new is the only way in. It issues a server-made player id and a random token (stored as a
SHA-256 hash) and posts the one grant. Every other wallet call proves the token; reads never create a player.

Every public method takes the ledger's own lock, so the ledger is safe without the HTTP server's lock.
"""
from __future__ import annotations

import fcntl
import functools
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from decimal import Decimal
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
NICK_MAX = 24
MIN_STAKE = 10
MIN_ODDS = 1.05
CONFIRM_TTL_MS = 60_000
HOUSE_BANKROLL = int(os.environ.get("CITY_HOUSE_BANKROLL") or 100_000)
EXPOSURE_CAP = int(os.environ.get("CITY_EXPOSURE_CAP") or 100_000)
SETTLE_KINDS = ("bet_win", "bet_loss", "void_refund")
DEFAULT_DB = Path(__file__).resolve().parent / "data" / "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
  id          TEXT PRIMARY KEY,
  nickname    TEXT,
  created_at  INTEGER NOT NULL,
  token_hash  TEXT,
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
CREATE INDEX IF NOT EXISTS bets_player ON bets (player);

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
CREATE INDEX IF NOT EXISTS entries_account ON entries (account);
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

-- Enough of each market a player bet on to settle it from recorded data after a restart.
CREATE TABLE IF NOT EXISTS markets (
  id          TEXT NOT NULL,
  settles_at  INTEGER NOT NULL,
  record      TEXT NOT NULL,
  PRIMARY KEY (id, settles_at)
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

APPEND_ONLY = ("entries", "bets", "settlements", "limit_changes", "breaks", "markets")


class WalletError(PermissionError):
    """A bet or wallet request refused for a reason the player can act on. `code` is stable for the UI; `data` is
    merged into the 409 reply (the confirm token)."""

    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.data = code, data or {}


class AuthError(Exception):
    """Missing or wrong X-Player-Token. The server answers 401."""


class LedgerInUse(RuntimeError):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_nickname(value) -> str:
    nick = str(value or "").strip()
    if not 0 < len(nick) <= NICK_MAX or not nick.isprintable():
        raise ValueError(f"nickname must be 1-{NICK_MAX} printable characters")
    return nick


def payout_of(stake: int, odds: float) -> int:
    """stake x odds, rounded down to a whole point (never half-to-even)."""
    return int(Decimal(str(odds)) * stake)


def default_name(player: str) -> str:
    """Matches the UI's defaultName, so an unnamed player reads the same on the board and in their own bar."""
    try:
        return f"Player {int(player[2:6], 36) % 900 + 100}"
    except ValueError:
        return "Player"


def _locked(fn):
    @functools.wraps(fn)
    def run(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return run


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
    def __init__(self, path: str | os.PathLike = ":memory:", now: Callable[[], int] = wall_ms,
                 bankroll: int = HOUSE_BANKROLL, exposure_cap: int = EXPOSURE_CAP):
        self.path, self.now = str(path), now
        self.bankroll, self.exposure_cap = bankroll, exposure_cap
        self.lock = threading.RLock()
        self.secret = secrets.token_bytes(32)
        self.lockfile = None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self.lockfile = _exclusive(self.path)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = FULL")
        self.db.executescript(SCHEMA)
        if "token_hash" not in {r[1] for r in self.db.execute("PRAGMA table_info(players)")}:
            self.db.execute("ALTER TABLE players ADD COLUMN token_hash TEXT")  # a ledger.db from before tokens
        for table in APPEND_ONLY:
            for op in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END")

    @classmethod
    def from_env(cls) -> "Ledger":
        return cls(os.environ.get("CITY_LEDGER_DB") or DEFAULT_DB)

    @_locked
    def close(self) -> None:
        self.db.close()
        if self.lockfile:
            self.lockfile.close()
            self.lockfile = None

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

    @_locked
    def create_player(self, nickname=None) -> dict:
        """The only call that makes a player: a server-made id, a random token (only its hash is kept), the grant."""
        nick = clean_nickname(nickname) if nickname not in (None, "") else None
        player, token = f"p_{secrets.token_hex(8)}", secrets.token_urlsafe(32)
        self.open_account(player, nick, token_hash(token))
        return {"player": player, "token": token, "cash": self.cash(player)}

    @_locked
    def open_account(self, player, nickname: str | None = None, hashed: str | None = None) -> str:
        """Inserts the player and posts its one-time grant. Idempotent. Not reachable over HTTP: create_player is."""
        player = self.check_player(player)
        if self.db.execute("SELECT 1 FROM players WHERE id = ?", (player,)).fetchone():
            return player
        ts = self.now()
        with self._tx():
            self.db.execute("INSERT INTO players (id, nickname, created_at, token_hash) VALUES (?, ?, ?, ?)",
                            (player, nickname, ts, hashed))
            self._post([("player_cash", GRANT)], player=player, kind="grant", key=f"grant:{player}", ts=ts)
        return player

    @_locked
    def auth(self, player, token) -> str:
        """The player id if `token` is theirs. The hash compare is constant time and runs even for unknown ids."""
        row = None
        if isinstance(player, str) and PLAYER_RE.match(player):
            row = self.db.execute("SELECT token_hash FROM players WHERE id = ?", (player,)).fetchone()
        stored = (row["token_hash"] if row else None) or "0" * 64
        ok = hmac.compare_digest(token_hash(str(token or "")), stored)
        if not (ok and row and row["token_hash"] and token):
            raise AuthError("missing or wrong X-Player-Token for this player")
        return player

    @_locked
    def require(self, player) -> str:
        player = self.check_player(player)
        if not self.db.execute("SELECT 1 FROM players WHERE id = ?", (player,)).fetchone():
            raise LookupError("unknown player: create one with POST /player/new")
        return player

    @_locked
    def set_nickname(self, player, nickname) -> dict:
        player = self.require(player)
        self.db.execute("UPDATE players SET nickname = ? WHERE id = ?", (clean_nickname(nickname), player))
        return {"ok": True, "nickname": self.db.execute("SELECT nickname FROM players WHERE id = ?",
                                                        (player,)).fetchone()[0]}

    @_locked
    def cash(self, player: str) -> int:
        return self._sum("player = ? AND account = 'player_cash'", (player,))

    @_locked
    def escrow(self, player: str) -> int:
        return self._sum("player = ? AND account = 'player_escrow'", (player,))

    @_locked
    def equity(self, player: str) -> int:
        return self._sum("player = ? AND account IN ('player_cash', 'player_escrow')", (player,))

    # ------------------------------------------------------------ responsible play

    @_locked
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

    @_locked
    def break_until(self, player: str, at: int | None = None) -> int | None:
        at = self.now() if at is None else at
        until = self.db.execute("SELECT MAX(until) FROM breaks WHERE player = ?", (player,)).fetchone()[0]
        return int(until) if until and until > at else None

    @_locked
    def set_limit(self, player, value) -> dict:
        player = self.require(player)
        value = as_points(value, "dailyLoss")
        if not 0 < value <= MAX_LOSS_LIMIT:
            raise ValueError(f"dailyLoss must be between 1 and {MAX_LOSS_LIMIT}")
        self.db.execute("INSERT INTO limit_changes (player, requested_at, value) VALUES (?, ?, ?)",
                        (player, self.now(), value))
        return self.wallet(player)

    @_locked
    def take_break(self, player, hours) -> dict:
        player = self.require(player)
        if hours not in BREAK_HOURS or isinstance(hours, bool):
            raise ValueError("hours must be 1 or 24")
        now = self.now()
        until = max(self.break_until(player, now) or now, now + int(hours) * 3600 * 1000)
        self.db.execute("INSERT INTO breaks (player, started_at, until) VALUES (?, ?, ?)", (player, now, until))
        return self.wallet(player)

    @_locked
    def pnl_since(self, player: str, since: int) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(CASE s.outcome WHEN 'won' THEN b.payout - b.stake WHEN 'lost' THEN -b.stake "
            "ELSE 0 END), 0) FROM settlements s JOIN bets b ON b.id = s.bet_id "
            "WHERE b.player = ? AND s.settled_at >= ?", (player, since)).fetchone()
        return int(row[0])

    @_locked
    def lost_since(self, player: str, since: int) -> int:
        """Gross losses: the stakes of bets lost since `since`. Wins never buy back room under the daily limit."""
        row = self.db.execute("SELECT COALESCE(SUM(b.stake), 0) FROM settlements s JOIN bets b ON b.id = s.bet_id "
                              "WHERE b.player = ? AND s.outcome = 'lost' AND s.settled_at >= ?",
                              (player, since)).fetchone()
        return int(row[0])

    def _confirm_sig(self, player: str, market: str, settles_at: int, option: str, stake: int, exp: int) -> str:
        msg = f"{player}|{market}|{int(settles_at)}|{option}|{stake}|{exp}".encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()

    @_locked
    def confirm_token(self, player: str, market: str, settles_at: int, option: str, stake: int) -> str:
        """A 60 s token for exactly this player, market, option and stake. Only the server can mint one."""
        exp = self.now() + CONFIRM_TTL_MS
        return f"{exp}.{self._confirm_sig(player, market, settles_at, option, int(stake), exp)}"

    def _confirmed(self, token, player: str, market: str, settles_at: int, option: str, stake: int, now: int) -> bool:
        exp, _, sig = str(token or "").partition(".")
        if not exp.isdigit() or int(exp) < now:
            return False
        return hmac.compare_digest(sig, self._confirm_sig(player, market, settles_at, option, stake, int(exp)))

    def _guard(self, player: str, stake: int, now: int, confirm: Callable[[], bool], mint: Callable[[], str]) -> None:
        until = self.break_until(player, now)
        if until:
            raise WalletError("on_break", f"You are taking a break until {_clock(until)}. Bets are paused.")
        cash = self.cash(player)
        if stake > cash:
            raise WalletError("insufficient_funds", f"Insufficient funds: stake {stake}, available {cash}")
        limit, _ = self.loss_limit(player, now)
        lost_today = self.lost_since(player, london_midnight(now))
        room = limit - lost_today - self.escrow(player)
        if stake > room:
            raise WalletError("loss_limit", f"Daily loss limit: you can risk {max(0, room)} more points today "
                                            f"(limit {limit}, lost {lost_today}, in play {self.escrow(player)})")
        if stake > CONFIRM_SHARE * cash and not confirm():
            raise WalletError("confirm_required",
                              f"This stake is {round(100 * stake / cash)}% of your balance. Confirm to place it.",
                              {"confirmToken": mint()})

    # ------------------------------------------------------------ house

    def _worst_case(self, extra: tuple | None = None) -> int:
        """Worst-case house loss over open bets: per market, the worst single outcome, netting the other stakes."""
        per: dict[tuple, dict[str, list[int]]] = {}
        rows = [tuple(r) for r in self.db.execute(
            "SELECT b.market, b.settles_at, b.option, b.stake, b.payout FROM bets b "
            "LEFT JOIN settlements s ON s.bet_id = b.id WHERE s.bet_id IS NULL")]
        for market, settles_at, option, stake, payout in rows + ([extra] if extra else []):
            acc = per.setdefault((market, settles_at), {}).setdefault(option, [0, 0])
            acc[0] += payout - stake
            acc[1] += stake
        total = 0
        for options in per.values():
            staked = sum(v[1] for v in options.values())
            total += max(0, max(win - (staked - st) for win, st in options.values()))
        return total

    @_locked
    def house(self) -> dict:
        pnl = self._sum("account = 'house'", ())
        return {"bankroll": self.bankroll, "pnl": pnl, "exposure": self._worst_case(),
                "cap": min(self.exposure_cap, self.bankroll + pnl)}

    # ------------------------------------------------------------ bets

    @staticmethod
    def scoped_key(key: str, market: str, settles_at: int) -> str:
        """Idempotency keys live per market round, so a market id reused after a restart or the next day never
        returns an old bet."""
        return f"{market}@{int(settles_at)}:{key}"

    @_locked
    def find_bet(self, player: str, key: str, market: str, settles_at: int, option: str, stake) -> dict | None:
        """The bet already booked under this key for this market round, or None. A key reused for a different bet
        in the same round is refused."""
        row = self.db.execute("SELECT * FROM bets WHERE player = ? AND idempotency_key = ?",
                              (player, self.scoped_key(key, market, settles_at))).fetchone()
        if row is None:
            return None
        if (row["option"], row["stake"]) != (option, as_points(stake)):
            raise ValueError("idempotency_key was already used for a different bet")
        return self._booked(dict(row), replayed=True)

    def _booked(self, bet: dict, replayed: bool) -> dict:
        return {"betId": bet["id"], "market": bet["market"], "option": bet["option"], "stake": bet["stake"],
                "odds": bet["odds"], "shares": bet["shares"], "payout": bet["payout"], "replayed": replayed,
                "balance": self.cash(bet["player"]), "escrow": self.escrow(bet["player"])}

    @_locked
    def place_bet(self, player, key, stake, *, market: str, question: str, option: str, label: str,
                  settles_at: int, price: Callable[[], tuple[float, float]], commit: Callable[[], None] | None = None,
                  confirm_token: str | None = None, record: dict | None = None, bet_id: str | None = None) -> dict:
        """Moves the stake from cash to escrow in one transaction. `price()` quotes (shares, locked odds) without
        touching the market; it runs inside the transaction after the player checks. `commit()` books the shares on
        the market only after the transaction has committed. Replaying an idempotency key returns the original bet
        and changes nothing."""
        player = self.require(player)
        key = str(key or "")
        if not 0 < len(key) <= 128:
            raise ValueError("idempotency_key is required (1-128 chars)")
        stake = as_points(stake)
        if stake < MIN_STAKE:
            raise ValueError(f"stake must be at least {MIN_STAKE} points")
        prior = self.find_bet(player, key, market, settles_at, option, stake)
        if prior:
            return prior
        with self._tx():
            now = self.now()
            self._guard(player, stake, now,
                        lambda: self._confirmed(confirm_token, player, market, settles_at, option, stake, now),
                        lambda: self.confirm_token(player, market, settles_at, option, stake))
            shares, odds = price()
            if odds < MIN_ODDS:
                raise WalletError("price_too_short", f"Price too short: under {MIN_ODDS}x pays less than the stake")
            payout = payout_of(stake, odds)
            house = self.house()
            worst = self._worst_case((market, int(settles_at), option, stake, payout))
            if worst > house["cap"]:
                raise WalletError("house_limit", "The exchange has taken all the risk it can on open markets. "
                                                 "Try a smaller stake or wait for markets to settle.")
            bet_id = bet_id or uuid.uuid4().hex
            if record is not None:
                self.db.execute("INSERT OR IGNORE INTO markets (id, settles_at, record) VALUES (?, ?, ?)",
                                (market, int(settles_at), json.dumps(record)))
            self.db.execute("INSERT INTO bets (id, player, market, question, option, label, stake, odds, shares, "
                            "payout, placed_at, settles_at, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (bet_id, player, market, question, option, label, stake, float(odds), float(shares),
                             payout, now, int(settles_at), self.scoped_key(key, market, settles_at)))
            self._post([("player_cash", -stake), ("player_escrow", stake)], player=player, kind="bet_hold",
                       key=f"hold:{bet_id}", ts=now, market=market, bet_id=bet_id)
        if commit:
            commit()
        return self._booked({"id": bet_id, "player": player, "market": market, "option": option, "stake": stake,
                             "odds": float(odds), "shares": float(shares), "payout": payout}, replayed=False)

    @_locked
    def precheck(self, player, key, stake, *, market: str, settles_at: int, option: str,
                 confirm_token: str | None = None) -> None:
        """The player checks place_bet runs, without moving anything: for a bet that fills after a delay."""
        player = self.require(player)
        if not 0 < len(str(key or "")) <= 128:
            raise ValueError("idempotency_key is required (1-128 chars)")
        stake = as_points(stake)
        if stake < MIN_STAKE:
            raise ValueError(f"stake must be at least {MIN_STAKE} points")
        now = self.now()
        self._guard(player, stake, now,
                    lambda: self._confirmed(confirm_token, player, market, settles_at, option, stake, now),
                    lambda: self.confirm_token(player, market, settles_at, option, stake))

    @_locked
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

    @_locked
    def open_bet_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT b.id FROM bets b LEFT JOIN settlements s ON s.bet_id = b.id WHERE s.bet_id IS NULL")]

    @_locked
    def void_open(self, reason: str) -> int:
        """Refunds every open bet."""
        return self.settle_bets(self.open_bet_ids(), None, reason)

    @_locked
    def open_markets(self) -> list[dict]:
        """Each market round that still has open bets: its record (None if it was never recorded) and bet ids."""
        out: dict[tuple, dict] = {}
        for r in self.db.execute("SELECT b.id, b.market, b.settles_at, m.record FROM bets b "
                                 "LEFT JOIN settlements s ON s.bet_id = b.id "
                                 "LEFT JOIN markets m ON m.id = b.market AND m.settles_at = b.settles_at "
                                 "WHERE s.bet_id IS NULL ORDER BY b.settles_at"):
            g = out.setdefault((r["market"], r["settles_at"]), {
                "market": r["market"], "settlesAt": r["settles_at"],
                "record": json.loads(r["record"]) if r["record"] else None, "bets": []})
            g["bets"].append(r["id"])
        return list(out.values())

    # ------------------------------------------------------------ views

    @_locked
    def wallet(self, player, since=None) -> dict:
        player = self.require(player)
        since = _since(since)
        now = self.now()
        p = self.db.execute("SELECT * FROM players WHERE id = ?", (player,)).fetchone()
        cash, escrow = self.cash(player), self.escrow(player)
        limit, pending = self.loss_limit(player, now)
        today = self.pnl_since(player, london_midnight(now))
        lost_today = self.lost_since(player, london_midnight(now))
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
        if since is not None:
            out["pnl"]["session"] = self.pnl_since(player, since)
        return out

    @_locked
    def ledger_rows(self, player, limit=50) -> dict:
        player = self.require(player)
        limit = max(1, min(500, _int(limit or 50, "limit")))
        rows = self.db.execute("SELECT id, ts, account, amount, kind, market, bet_id, idempotency_key FROM entries "
                               "WHERE player = ? ORDER BY id DESC LIMIT ?", (player, limit))
        return {"entries": [{"id": r["id"], "ts": r["ts"], "account": r["account"], "amount": r["amount"],
                             "kind": r["kind"], "market": r["market"], "betId": r["bet_id"],
                             "idempotencyKey": r["idempotency_key"]} for r in rows]}

    @_locked
    def positions(self, player) -> dict:
        player = self.require(player)
        rows = self.db.execute("SELECT b.* FROM bets b LEFT JOIN settlements s ON s.bet_id = b.id "
                               "WHERE b.player = ? AND s.bet_id IS NULL ORDER BY b.settles_at", (player,))
        return {"positions": [{"betId": r["id"], "market": r["market"], "question": r["question"],
                               "option": r["option"], "label": r["label"], "stake": r["stake"], "odds": r["odds"],
                               "toWin": r["payout"], "profit": r["payout"] - r["stake"], "placedAt": r["placed_at"],
                               "settlesAt": r["settles_at"]} for r in rows]}

    @_locked
    def history(self, player, limit=100) -> dict:
        player = self.require(player)
        limit = max(1, min(500, _int(limit or 100, "limit")))
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

    @_locked
    def leaderboard(self, limit: int = 50) -> list[dict]:
        """Players who have bet, ranked by equity (cash + escrow) straight from the ledger."""
        rows = self.db.execute(
            "SELECT p.id, p.nickname, (SELECT COALESCE(SUM(amount), 0) FROM entries e WHERE e.player = p.id "
            "AND e.account IN ('player_cash', 'player_escrow')) AS equity FROM players p "
            "WHERE EXISTS (SELECT 1 FROM bets b WHERE b.player = p.id) ORDER BY equity DESC LIMIT ?", (limit,))
        return [{"name": r["nickname"] or default_name(r["id"]), "points": int(r["equity"]), "kind": "human"}
                for r in rows]

    # ------------------------------------------------------------ audit

    @_locked
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


def _int(value, field: str) -> int:
    try:
        return int(str(value))
    except ValueError:
        raise ValueError(f"{field} must be a whole number") from None


def _since(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        since = float(value)
    except ValueError:
        raise ValueError("since must be a time in ms") from None
    if not math.isfinite(since):
        raise ValueError("since must be a finite time in ms")
    return int(since)


def _exclusive(path: str):
    """One process per ledger db: a second exchange on the same file would refund the first one's live bets."""
    f = open(f"{path}.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise LedgerInUse(f"ledger {path} is in use by another city server (lock {path}.lock). Stop that server, "
                          f"or point CITY_LEDGER_DB at another file.") from None
    return f


def get_routes(ledger: Ledger, q: dict, player: str) -> dict:
    """Wallet reads. `player` has already proved its token."""
    return {"/wallet": lambda: ledger.wallet(player, q.get("since")),
            "/ledger": lambda: ledger.ledger_rows(player, q.get("limit") or 50),
            "/positions": lambda: ledger.positions(player),
            "/history": lambda: ledger.history(player, q.get("limit") or 100)}


def post_routes(ledger: Ledger, body: dict, player: str) -> dict:
    """Wallet writes. `player` has already proved its token."""
    return {"/limits": lambda: ledger.set_limit(player, body.get("dailyLoss")),
            "/break": lambda: ledger.take_break(player, body.get("hours")),
            "/player/name": lambda: ledger.set_nickname(player, body.get("name"))}
