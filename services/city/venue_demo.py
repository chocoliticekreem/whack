"""Demo of the parallel exchange venue: the full AI population, one scripted player, and a pass/fail health report.

    python3 services/city/venue_demo.py                                    # generated city data, 25 minutes
    CITY_RAW=data/raw python3 services/city/venue_demo.py --replay-from 2026-11-07T15:00:00Z   # recorded data

The player buys YES on City Pulse, sells it back, then holds a hub-surge favourite to settlement. Every step is
printed with the book as the player saw it. The report then checks the market's structure: books never crossed, no
trade after close, nothing left on closed books, the ledger audit, the same speed bump for players and AI, spreads,
market-maker uptime and rebates, fees, P&L by participant type, and calibration at close.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict

import venue_agents
from data import Store, feeds, load_hotspots, raw_root
from exchange import Clock
from venue import UPTIME, CitySource, ShadowExchange, Venue
from venue_book import PAYOUT
from venue_gateway import Account

MIN = 60_000


def synthetic_store():
    """Generated recorder data shaped like the real feeds (the city-test fixture), from T0."""
    import test_city as tc
    raw = tc.Raw()
    tc.city(raw, tc.T0 + 40 * MIN)
    store = Store(raw.root, tc.SPOTS)
    store.refresh(tc.T0 - 3 * 3_600_000, tc.T0)
    store.ingest_vix(tc.vix_payload(tc.T0 - 2 * 3_600_000, tc.T0 + 40 * MIN))
    return store, tc.T0, tc.SPOTS, raw


class Player:
    """One scripted player: buy, sell back, then hold to settlement."""

    def __init__(self, v: Venue, t0: int):
        self.v, self.t0, self.trace, self.step = v, t0, [], "buy"
        self.order = self.pulse = self.hub = None

    def say(self, now: int, msg: str) -> None:
        self.trace.append(f"+{(now - self.t0) // 1000:>4d}s  {msg}")

    def book(self, cid: str) -> str:
        b, v = self.v.books[cid], self.v
        bid, ask = b.bid(), b.ask()
        size = lambda side, p: b.size_at(side, p) if p is not None else 0  # noqa: E731
        return (f"bid {bid} x{size('yes', bid)} / ask {ask} x{size('no', None if ask is None else PAYOUT - ask)}, "
                f"last {b.last}, forecast {100 * v.forecast(cid):.0f}")

    def sent(self, now: int, o, what: str) -> None:
        if o.status == "rejected":
            self.say(now, f"{what} -> REFUSED: {o.reason}")
        else:
            self.say(now, f"{what} -> {o.status}; {o.held} points reserved; "
                          f"speed bump {self.v.delay_ms(o.contract) // 1000} s")

    def landed(self, now: int, o, then: str) -> bool:
        if o.status == "pending":
            return False
        fills = [f for f in self.v.books[o.contract].tape if f.taker is o]
        yes, no = self.v.clearing.holding("you", o.contract)
        self.say(now, f"reached the book {o.placed_at - o.submitted_at} ms after sending; filled {o.filled} at "
                      f"{sorted({f.yes_price for f in fills})}, taker fee {o.fee_paid}; you hold {yes} YES / {no} NO; "
                      f"cash {self.v.clearing.cash('you')}")
        self.step = then
        return True

    def act(self, now: int) -> None:
        v = self.v
        if self.step == "buy" and now >= self.t0 + MIN:
            self.pulse = next((c for c in v.contracts if c.startswith("city_pulse")
                               and v.contract_status(c, now) == "open" and v.books[c].ask()), None)
            if self.pulse:
                ask = v.books[self.pulse].ask()
                self.say(now, f'{self.pulse}: YES = "{v.contracts[self.pulse].label}". Book: {self.book(self.pulse)}')
                # the book can move during the speed bump, so allow 2 points of slippage, as a real trader would
                self.order = v.gateway.submit("you", self.pulse, "yes", min(99, ask + 2), 3, now, tif="ioc")
                self.sent(now, self.order, f"BUY 3 YES, limit {min(99, ask + 2)} (ask {ask} + 2 slippage)")
                self.step = "bought" if self.order.status == "pending" else "hub"
        elif self.step == "bought":
            self.landed(now, self.order, "sell")
        elif self.step == "sell" and now >= self.t0 + 150_000:
            held = v.clearing.holding("you", self.pulse)[0]
            if not held:
                self.step = "buy" if now < self.t0 + 5 * MIN else "hub"  # nothing filled: try the buy again
                return
            if v.contract_status(self.pulse, now) != "open" or not v.books[self.pulse].bid():
                self.step = "hub"
                return
            bid = v.books[self.pulse].bid()
            self.say(now, f"Book: {self.book(self.pulse)}")
            limit = max(1, bid - 2)
            self.order = v.gateway.submit("you", self.pulse, "no", PAYOUT - limit, held, now, tif="ioc")
            self.sent(now, self.order, f"SELL {held} YES, limit {limit} (bid {bid} - 2) = buy {held} NO at "
                                       f"{PAYOUT - limit}")
            self.step = "sold" if self.order.status == "pending" else "hub"
        elif self.step == "sold":
            self.landed(now, self.order, "hub")
        elif self.step == "hub" and now >= self.t0 + 7 * MIN:
            opens = [c for c in v.contracts if c.startswith("hub_surge") and v.contract_status(c, now) == "open"
                     and v.books[c].ask()]
            if opens:
                self.hub = max(opens, key=v.forecast)
                ask = v.books[self.hub].ask()
                self.say(now, f"{self.hub} (the favourite). Book: {self.book(self.hub)}")
                self.order = v.gateway.submit("you", self.hub, "yes", min(99, ask + 2), 2, now, tif="ioc")
                self.sent(now, self.order, f"BUY 2 YES, limit {min(99, ask + 2)}, to hold to settlement")
                self.step = "holding" if self.order.status == "pending" else "done"
        elif self.step == "holding":
            self.landed(now, self.order, "await")
        elif self.step == "await" and v.contracts[self.hub].done:
            c = v.contracts[self.hub]
            self.say(now, f"{self.hub} settled {c.outcome.upper()}; cash {v.clearing.cash('you')}, "
                          f"P&L after fees {v.clearing.pnl('you')} (started with 1000)")
            self.step = "done"


def run(v: Venue, t0: int, minutes: int) -> dict:
    player = Player(v, t0)
    samples = []
    w0, c0 = time.time(), time.process_time()
    for now in range(t0, t0 + minutes * MIN, 1_000):
        v.tick(now)
        player.act(now)
        if (now - t0) % 10_000:
            continue
        spreads, depth, crossed = [], [], 0
        for cid, c in v.contracts.items():
            if c.done or v.contract_status(cid, now) != "open":
                continue
            b = v.books[cid]
            crossed += b.locked_or_crossed()
            if b.bid() is not None and b.ask() is not None:
                spreads.append(b.ask() - b.bid())
                depth.append(b.size_at("yes", b.bid()) + b.size_at("no", PAYOUT - b.ask()))
        samples.append({"t": (now - t0) // 1000, "open": len(spreads), "crossed": crossed,
                        "medianSpread": statistics.median(spreads) if spreads else None,
                        "medianTopDepth": statistics.median(depth) if depth else None})
    cpu = time.process_time() - c0
    return {"player": player.trace, "samples": samples,
            "run": {"accounts": len(v.accounts), "ticks": v.ticks, "wallS": round(time.time() - w0, 1),
                    "cpuMsPerTick": round(1000 * cpu / max(1, v.ticks), 1)}}


def report(v: Venue, got: dict) -> dict:
    kinds = {a.id: a.kind for a in v.accounts.values()}
    bump = defaultdict(list)
    for o in v.gateway.orders.values():
        if o.placed_at and not o.post_only:
            bump[(kinds[o.account], v.contracts[o.contract].series)].append(o.placed_at - o.submitted_at)
    s = v.stats()
    spreads = [x["medianSpread"] for x in got["samples"] if x["medianSpread"] is not None]
    rebates = sum(e[3] for e in v.clearing.ledger.entries if e[4].startswith("maker rebate"))
    after_close = sum(1 for cid, c in v.contracts.items() for f in v.books[cid].tape if f.at >= c.market.closes_at)
    left = sum(len(v.books[cid].resting) for cid, c in v.contracts.items() if c.done or c.closed)
    designated = {a.id for a in v.agents if getattr(a, "designated", False)}
    uptime = [u for mm, u in s["marketMakerUptime"].items() if u is not None and mm in designated]
    cl = v.clearing
    minted = -cl.ledger.get("mint")
    held_everywhere = sum(b for k, b in cl.ledger.bal.items() if k != "mint")
    by_series = defaultdict(dict)
    for (kind, series), d in bump.items():
        by_series[series][kind] = statistics.median(d)
    fair = all(max(k.values()) - k.get("human", min(k.values())) <= 1_300 for k in by_series.values())
    checks = [
        ("Books never crossed or locked", sum(x["crossed"] for x in got["samples"]) == 0, ""),
        ("No trade after close", after_close == 0, f"{after_close} trades"),
        ("Nothing left on closed or settled books", left == 0, f"{left} orders"),
        ("Ledger audit (balances, collateral, reservations)", s["audit"] == "ok", str(s["audit"])),
        ("Same speed bump for players and AI (AI +300 ms latency, then the next 1 s tick)", fair,
         json.dumps({k: {kk: f"{vv / 1000:.1f}s" for kk, vv in d.items()} for k, d in sorted(by_series.items())})),
        ("Two-sided markets: median quoted spread <= 6", bool(spreads) and statistics.median(spreads) <= 6,
         f"median {statistics.median(spreads) if spreads else None}"),
        (f"Designated market makers meet the {UPTIME:.0%} quoting obligation", bool(uptime) and min(uptime) >= UPTIME,
         f"uptime {min(uptime):.2f}-{max(uptime):.2f}" if uptime else "none"),
        ("Rebates paid to market makers that met it", rebates > 0, f"{rebates} points"),
        ("Points conserved: every point granted is in a wallet, a reservation, a contract pool or the fee account",
         minted == held_everywhere, f"{minted} granted, {held_everywhere} accounted for"),
    ]
    return {"checks": [{"check": c, "pass": bool(ok), "detail": d} for c, ok, d in checks], "groups": s["groups"],
            "calibration": s["calibration"], "trades": s["trades"], "messages": s["messages"],
            "rejects": s["rejects"], "feesCollected": s["feesCollected"], "rebates": rebates}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--replay-from", help="ISO start time on recorded data (CITY_RAW); default: generated data")
    ap.add_argument("--minutes", type=int, default=25)
    ap.add_argument("--retail", type=int, default=2000)
    ap.add_argument("--json", help="also write the full result here")
    args = ap.parse_args()
    cleanup = None
    if args.replay_from:
        t0 = feeds.parse_ts(args.replay_from)
        spots = load_hotspots()
        store = Store(raw_root(), spots)
        store.refresh(t0 - 3 * 3_600_000, t0)
        source = "recorded data in " + str(raw_root())
    else:
        store, t0, spots, cleanup = synthetic_store()
        source = "generated city data (not real London)"
    v = Venue(CitySource(ShadowExchange(store, Clock(t0), spots)), seed=3)
    venue_agents.populate(v, retail=args.retail, now=t0)
    v.open_account(Account("you", "human", name="You"), 1000, t0)
    print(f"Running {args.minutes} market minutes on {source}: {len(v.accounts) - 1} AI accounts + you", flush=True)
    got = run(v, t0, args.minutes)
    out = got | report(v, got)
    if cleanup:
        cleanup.dir.cleanup()
    print("\nYour trades:")
    print("\n".join(out["player"]) or "  (no trade: no market was open)")
    print("\nHealth checks:")
    for c in out["checks"]:
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {c['check']}  {c['detail']}")
    print("\nP&L after fees, by participant type:")
    for g, r in out["groups"].items():
        print(f"  {g:22} {r['accounts']:>5} accounts  P&L {r['pnl']:>8}  fees paid {r['feesPaid']:>7}  "
              f"return on capital {r['roiOnGranted']:+.1%}")
    cal = out["calibration"]
    print(f"\nCalibration at close ({cal['n']} contracts, lower is better): traded price {cal['price']}, "
          f"forecast alone {cal['forecast']}, uninformed {cal['uninformed']}")
    print(f"{out['trades']} trades, {out['messages']} messages, {out['rejects']} refused, fees "
          f"{out['feesCollected']}, rebates {out['rebates']}; {out['run']['cpuMsPerTick']} ms CPU per tick")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    sys.exit(main())
