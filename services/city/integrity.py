"""Integrity against courtsiding (latency arbitrage): someone on the spot (a train that has just stopped) sees the
outcome before the TfL feed that settles the market publishes it, and bets while the market is still open.

Three defences, one table (TABLE, keyed by series):
- delay_s: a bet is accepted as pending and fills delay_s later at the price then. If the market halts, closes or
  is suspended in between, the bet is cancelled; no escrow is ever held for a pending bet, so a cancel moves nothing.
- suspends_on: when a feed the market depends on publishes a changed value past a threshold, the market halts for
  cool_off_s, pending bets are cancelled and the price restarts from the futures (the LMSR book is reset).
- cap_pct: the per-player position cap, lower on locally observable series.

AI traders (the Simulation trader included) go through the same delay and cancels.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Callable

import markets as mk
import pricing
from data import local_time
from ledger import Ledger

HUB_MOVE = 0.10    # 99th pct of one-publish hub moves on 2026-11-07 was 10.8%; 7 of 100 publishes trip it
PULSE_MOVE = 0.03  # 8 of 100 publishes moved City Pulse more than 3%
VIX_MOVE = 0.30    # VIX points in one frame
STACK_MOVE = 4     # aircraft; 95th pct of one-minute holding changes


@dataclass(frozen=True)
class Trigger:
    feed: str
    says: str  # plain English, completes "The market pauses whenever ..."
    changed: Callable[[mk.Market, dict, dict], str | None]


@dataclass(frozen=True)
class Rule:
    delay_s: int
    suspends_on: tuple[Trigger, ...]
    cool_off_s: int
    cap_pct: float


def hub_moved(m, prev: dict, snap: dict) -> str | None:
    for oid, nap in m.spec.get("stations", {}).items():
        a, b = prev.get(nap), snap.get(nap)
        if a and b and abs(b / a - 1) > HUB_MOVE:
            return f"{m.spec.get('names', {}).get(oid, oid)} crowding {b / a - 1:+.0%}"
    return None


def pulse_moved(m, prev: dict, snap: dict) -> str | None:
    a, b = mk.pulse_of(prev), mk.pulse_of(snap)
    return f"City Pulse {b / a - 1:+.1%}" if a and b and abs(b / a - 1) > PULSE_MOVE else None


def vix_moved(m, prev: dict, snap: dict) -> str | None:
    a, b = prev.get("vix"), snap.get("vix")
    return f"London VIX {a:.2f} → {b:.2f}" if a is not None and b is not None and abs(b - a) > VIX_MOVE else None


def stack_moved(m, prev: dict, snap: dict) -> str | None:
    a, b = prev.get("total"), snap.get("total")
    return f"Heathrow holding {a} → {b}" if a is not None and b is not None and abs(b - a) >= STACK_MOVE else None


def line_changed(m, prev: dict, snap: dict) -> str | None:
    for lid in mk.tube_lines(snap):
        if lid in prev and prev[lid]["sev"] != snap[lid]["sev"]:
            return f"{snap[lid]['name']} now {snap[lid].get('desc') or snap[lid]['sev']}"
    return None


HUBS = Trigger("crowding", f"a hub's TfL crowding reading moves more than {HUB_MOVE:.0%}", hub_moved)
PULSE = Trigger("crowding", f"City Pulse moves more than {PULSE_MOVE:.0%} in one TfL reading", pulse_moved)
VIX = Trigger("vix", f"the London VIX moves more than {VIX_MOVE:.2f} in one frame", vix_moved)
STACK = Trigger("aircraft", f"planes holding over Heathrow change by {STACK_MOVE} or more", stack_moved)
LINES = Trigger("line_status", "any Tube line's status changes", line_changed)

LOCAL = Rule(45, (LINES,), 20, 0.10)       # one person on a platform can see it first
AGGREGATE = Rule(5, (HUBS,), 15, 0.25)     # many stations; nobody sees the whole number early
MODEL = Rule(2, (), 10, 0.25)              # a model output or a radar count: no one sees it before the feed
TABLE: dict[str, Rule] = {
    "clean_sheet": LOCAL,
    "hub_surge": AGGREGATE,
    "hub_hour": AGGREGATE,
    "city_pulse": replace(AGGREGATE, suspends_on=(PULSE,)),
    "london_vix": replace(MODEL, suspends_on=(VIX,)),
    "heathrow_stack": replace(MODEL, suspends_on=(STACK,)),
}
BY_FEED = {"line_status": LOCAL, "crowding": AGGREGATE}


def rule_for(series: str) -> Rule:
    """The series' row, else by its feed: any line-status series is locally observable."""
    if series in TABLE:
        return TABLE[series]
    feed = mk.REGISTRY[series].feed if series in mk.REGISTRY else None
    return BY_FEED.get(feed, MODEL)


def meta(series: str) -> dict:
    r = rule_for(series)
    return {"delayS": r.delay_s, "suspendsOn": [t.says for t in r.suspends_on], "coolOffS": r.cool_off_s,
            "capPct": round(r.cap_pct * 100)}


def plain(series: str) -> str:
    r = rule_for(series)
    out = (f"Bets fill {r.delay_s} s after you place them, at the price then; if the market pauses or closes first "
           f"the bet is cancelled and your stake is untouched.")
    if r.suspends_on:
        out += f" The market pauses for {r.cool_off_s} s whenever {' or '.join(t.says for t in r.suspends_on)}."
    return out + f" Max {r.cap_pct:.0%} of your points in this market."


class Integrity:
    """Suspensions and the pending-order desk for one exchange."""

    def __init__(self):
        self.last: dict[str, tuple[int, dict]] = {}
        self.until: dict[str, int] = {}
        self.why: dict[str, str] = {}
        self.epoch: dict[str, int] = {}
        # debt: pending orders live in memory, so a restart forgets them (no points move, GET /bet then 404s).
        # ceiling: orders up to delay_s old. upgrade: real money, where a pending order must survive a restart.
        self.orders: dict[str, dict] = {}
        self.keys: dict[tuple[str, str], str] = {}
        self.ai: list[dict] = []

    # ------------------------------------------------------------ rules

    @staticmethod
    def delay_ms(series: str) -> int:
        return rule_for(series).delay_s * 1000

    @staticmethod
    def cap(series: str) -> float:
        return rule_for(series).cap_pct

    # ------------------------------------------------------------ suspend on change

    def scan(self, store, markets: list, now: int) -> None:
        """Compare each new reading of every watched feed with the one before; a change past a market's trigger
        suspends that market."""
        feeds = {t.feed for m in markets for t in rule_for(m.sub).suspends_on}
        for feed in feeds:
            prev = self.last.get(feed) or store.latest(feed, now)
            if prev is None:
                continue
            for ts, snap in store.between(feed, prev[0] + 1, now):
                for m in markets:
                    for t in rule_for(m.sub).suspends_on:
                        what = t.feed == feed and t.changed(m, prev[1], snap)
                        if what:
                            self.suspend(m, what, now)
                prev = (ts, snap)
            self.last[feed] = prev

    def suspend(self, m, what: str, now: int) -> None:
        self.until[m.id] = max(self.until.get(m.id, 0), now + rule_for(m.sub).cool_off_s * 1000)
        self.why[m.id] = what
        self.epoch[m.id] = self.epoch.get(m.id, 0) + 1
        m.book = pricing.Book(m.option_ids())
        self.cancel_market(m, f"market paused: {what}")

    def suspended(self, m, now: int) -> str | None:
        until = self.until.get(m.id)
        if until is None or now >= until:
            return None
        return f"suspended: {self.why[m.id]}; reopens {local_time(until, '%H:%M:%S')}"

    # ------------------------------------------------------------ pending orders

    def find(self, player: str, key: str, m, option: str, stake: int) -> dict | None:
        bid = self.keys.get((player, Ledger.scoped_key(key, m.id, m.settles_at)))
        if bid is None:
            return None
        o = self.orders[bid]
        if (o["option"], o["stake"]) != (option, stake):
            raise ValueError("idempotency_key was already used for a different bet")
        return o

    def held(self, player: str, market: str) -> int:
        return sum(o["stake"] for o in self.orders.values()
                   if o["player"] == player and o["market"] == market and o["status"] == "pending")

    def pending_stakes(self, player: str, before: dict | None = None) -> int:
        """The player's pending stakes on every market; with `before`, only those placed ahead of that order."""
        return sum(o["stake"] for o in self.orders.values() if o["player"] == player and o["status"] == "pending"
                   and o is not before and (before is None or o["seq"] < before["seq"]))

    def accept(self, player: str, key: str, m, option: str, stake: int, now: int, confirmed: bool = False) -> dict:
        o = {"betId": uuid.uuid4().hex, "player": player, "key": key, "market": m.id, "option": option,
             "label": m.label(option), "stake": stake, "placedAt": now, "fillsAt": now + self.delay_ms(m.sub),
             "status": "pending", "epoch": self.epoch.get(m.id, 0), "seq": len(self.orders), "confirmed": confirmed}
        self.orders[o["betId"]] = o
        self.keys[(player, Ledger.scoped_key(key, m.id, m.settles_at))] = o["betId"]
        return o

    def accept_ai(self, tid: str, m, option: str, why: str, now: int) -> None:
        self.ai.append({"trader": tid, "market": m.id, "option": option, "why": why,
                        "fillsAt": now + self.delay_ms(m.sub), "epoch": self.epoch.get(m.id, 0)})

    def ai_pending(self, tid: str, market: str) -> int:
        return sum(1 for a in self.ai if a["trader"] == tid and a["market"] == market)

    def cancel_market(self, m, reason: str) -> None:
        for o in self.orders.values():
            if o["market"] == m.id and o["status"] == "pending":
                o.update(status="cancelled", reason=reason)
        self.drop_ai(m)

    def drop_ai(self, m) -> None:
        for a in [a for a in self.ai if a["market"] == m.id]:
            self.ai.remove(a)
            m.decided.discard(a["trader"])  # it may decide again once the market reopens

    def fill_due(self, ex, now: int) -> None:
        """Cancel pending orders on a market that is no longer open; fill the due ones at the price now, in placement
        order. A fill keeps the player's earlier pending stakes aside, so a bet placed later (on a market with a
        shorter delay) can never spend the room an earlier one was accepted with. A fill gets a confirm token only if
        the player confirmed that bet when placing it; past the 25% line without one, the fill is cancelled."""
        for o in sorted((o for o in self.orders.values() if o["status"] == "pending"), key=lambda o: o["seq"]):
            m = ex.markets.get(o["market"])
            status = ex.status(m, now)
            if status != "open" or self.epoch.get(m.id, 0) != o["epoch"]:
                why = ex.halt_reason(m, now) if status == "halted" else f"market {status}"
                o.update(status="cancelled", reason=why or "market paused")
                continue
            if now < o["fillsAt"]:
                continue
            try:
                token = ex.ledger.confirm_token(o["player"], m.id, m.settles_at, o["option"], o["stake"]) \
                    if o["confirmed"] else None
                o["fill"] = ex.book(m, o["player"], o["key"], o["option"], o["stake"], now, token, o["betId"],
                                    reserved=self.pending_stakes(o["player"], before=o))
                o.update(status="filled", filledAt=now)
            except PermissionError as exc:  # WalletError included: nothing moved, the bet is cancelled
                o.update(status="cancelled", reason=str(exc))
        for a in list(self.ai):
            m = ex.markets.get(a["market"])
            if ex.status(m, now) != "open" or self.epoch.get(m.id, 0) != a["epoch"]:
                self.drop_ai(m)
            elif now >= a["fillsAt"]:
                self.ai.remove(a)
                ex.ai_fill(m, a["trader"], a["option"], a["why"])

    def reply(self, o: dict, ledger, replayed: bool) -> dict:
        return {"ok": True, "replayed": replayed, "balance": ledger.cash(o["player"]),
                "escrow": ledger.escrow(o["player"])} | self.order_json(o)

    @staticmethod
    def order_json(o: dict) -> dict:
        out = {k: o[k] for k in ("betId", "market", "option", "label", "stake", "status", "placedAt", "fillsAt")}
        if o["status"] == "cancelled":
            out["reason"] = o.get("reason") or "cancelled"
        if o["status"] == "filled":
            out |= {"filledAt": o["filledAt"]} | {k: o["fill"][k] for k in ("odds", "shares", "payout", "price")}
        return out

    def pending_json(self, player: str) -> list[dict]:
        return [self.order_json(o) for o in self.orders.values() if o["player"] == player and o["status"] == "pending"]

    def lookup(self, ledger, player: str, bet_id) -> dict:
        """GET /bet: a pending, cancelled or filled order, with its result once settled. A bet placed before a
        restart is found in the ledger."""
        bet_id = str(bet_id or "")
        o = self.orders.get(bet_id)
        out = self.order_json(o) if o and o["player"] == player else None
        if out is not None and out["status"] != "filled":
            return out
        settled = next((h for h in ledger.history(player, 500)["history"] if h["betId"] == bet_id), None)
        if settled:
            base = out or {"betId": bet_id, "status": "filled"}
            return base | {"result": settled["result"], "pnl": settled["pnl"], "reading": settled["reading"]}
        if out is not None:
            return out
        held = next((p for p in ledger.positions(player)["positions"] if p["betId"] == bet_id), None)
        if held is None:
            raise LookupError("unknown bet")
        return held | {"status": "filled"}
