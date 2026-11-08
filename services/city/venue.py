"""The parallel exchange venue (docs/exchange-design.md): OverWatch's markets traded on order books by AI market makers,
hedge funds and retail, and by players, with no house.

It runs next to the live LMSR exchange and touches none of it: its own process (venue_server.py, :8062), its own
throwaway ledger and order books, reading the same recorder files read-only. A shadow copy of the live exchange lists,
observes and settles the same markets with the same futures, integrity rules and settlement sources; its LMSR book is
never traded. Each market becomes Kalshi-style binary contracts: one for a two-way question (YES = its first option),
one per option otherwise.

Each tick: the shadow exchange moves on; new markets get contracts; a halted or suspended market has every order
cancelled, a closed one stops trading, a settled one pays 100 per winning contract and a void one refunds; orders that
have waited out the speed bump reach the books; then every agent, in shuffled order, reads the same public snapshot
and may send orders through the gateway.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

import integrity
from exchange import Exchange
from ledger import Ledger
from venue_book import PAYOUT, Book, Fill
from venue_gateway import Account, Gateway
from venue_ledger import Clearing

FLOW_WINDOW_MS = 60_000
MAX_SPREAD = 6      # market-maker obligations (Kalshi Market Maker Program style): widest two-sided quote,
MIN_SIZE = 10       # smallest size on each side,
UPTIME = 0.9        # and the share of open time quoted, to earn the rebate
GRACE_MS = 10_000   # obligations start 10 s after a contract opens or reopens: no quote reaches a book sooner
MAX_SPREAD_FAST = 12  # fast-market relief: the widest quote the obligation allows while a contract is fast
FAST_IMBALANCE, FAST_VOLUME = 0.6, 20  # fast = one-sided flow (|net taker YES| / volume, last minute, >= 20 traded)
FAST_MOVE = 0.03                       # or the forecast moved 3 points in the last minute


class ShadowExchange(Exchange):
    """The live exchange's market lifecycle on a throwaway in-memory ledger. Its AI traders never bet here: on the
    venue the same strategies trade as funds."""

    def __init__(self, store, clock, hotspots=None):
        super().__init__(store, clock, hotspots, Ledger(":memory:"))

    def ai_bets(self, m, now: int) -> None:
        return


class CitySource:
    """The venue's view of the city markets: what's listed, each market's status, and the public data store."""

    def __init__(self, ex: Exchange):
        self.ex, self.store, self.spots = ex, ex.store, ex.by_id

    def tick(self, now: int) -> None:
        self.ex.tick(now)

    def active(self, now: int) -> list:
        return self.ex.active()

    def status(self, m, now: int) -> str:
        return self.ex.status(m, now)


@dataclass
class Contract:
    id: str
    market: object
    option: str          # YES pays if this option wins
    label: str
    complement: str
    series: str
    halted: bool = False
    reopened_at: int = 0
    closed: bool = False
    done: bool = False
    outcome: str | None = None   # "yes" | "no" | "void"
    close_mid: float | None = None
    close_last: int | None = None
    close_forecast: float | None = None
    open_ticks: int = 0
    fast_ticks: int = 0
    quoted_ok: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    forecasts: list = field(default_factory=list)  # (ts, forecast) over the last minute

    def public(self) -> dict:
        m = self.market
        return {"id": self.id, "market": m.id, "series": self.series, "question": m.question, "yes": self.label,
                "no": self.complement, "opensAt": m.opened_at, "closesAt": m.closes_at, "settlesAt": m.settles_at,
                "source": m.source, "rule": m.rule}


class Venue:
    def __init__(self, source, seed: int = 7):
        self.src = source
        self.rng = random.Random(seed)
        self.clearing = Clearing()
        self.books: dict[str, Book] = {}
        self.contracts: dict[str, Contract] = {}
        self.accounts: dict[str, Account] = {}
        self.agents: list = []
        self.by_account: dict[str, object] = {}
        self.gateway = Gateway(self, random.Random(seed + 1))
        self.listed: set[str] = set()
        self.settled: list[dict] = []
        self.ticks = 0
        self.cache: dict = {}  # strategy picks shared by every fund that runs the strategy (venue_agents.pick)

    # ------------------------------------------------------------ accounts

    def open_account(self, acct: Account, points: int, now: int = 0) -> Account:
        if acct.id in self.accounts:
            raise ValueError(f"account {acct.id} exists")
        self.accounts[acct.id] = acct
        if points:
            self.clearing.grant(acct.id, points, now)
        return acct

    def add_agent(self, agent, points: int, now: int = 0) -> None:
        self.open_account(agent.account, points, now)
        agent.start = points
        self.agents.append(agent)
        self.by_account[agent.account.id] = agent

    # ------------------------------------------------------------ contract facts (the gateway's view)

    def contract_status(self, cid: str, now: int) -> str:
        c = self.contracts.get(cid)
        if c is None:
            return "unknown"
        if c.done:
            return "void" if c.outcome == "void" else "settled"
        return self.src.status(c.market, now)

    def delay_ms(self, cid: str) -> int:
        return integrity.Integrity.delay_ms(self.contracts[cid].series)

    def cap_pct(self, cid: str) -> float:
        return integrity.Integrity.cap(self.contracts[cid].series)

    def forecast(self, cid: str) -> float:
        """The public forecast: the share of the market's surviving futures in which YES happens."""
        c = self.contracts[cid]
        return float(c.market.futures.probs()[c.option])

    def reference(self, cid: str, now: int) -> int:
        """The price collar's anchor: the last trade if it's under a minute old, else the public forecast."""
        tape = self.books[cid].tape
        if tape and now - tape[-1].at <= FLOW_WINDOW_MS:
            return tape[-1].yes_price
        return max(1, min(99, round(100 * self.forecast(cid))))

    def flow(self, cid: str, now: int) -> tuple[int, int]:
        """(net taker YES contracts, contracts traded) over the last minute: public, from the tape."""
        flow = volume = 0
        for f in reversed(self.books[cid].tape):
            if now - f.at > FLOW_WINDOW_MS:
                break
            flow += f.qty if f.taker_bought_yes else -f.qty
            volume += f.qty
        return flow, volume

    def vwap(self, cid: str, now: int) -> float | None:
        """Volume-weighted YES price over the last minute, or None if nothing traded."""
        value = volume = 0
        for f in reversed(self.books[cid].tape):
            if now - f.at > FLOW_WINDOW_MS:
                break
            value += f.yes_price * f.qty
            volume += f.qty
        return value / volume if volume else None

    def note_forecast(self, c: Contract, now: int) -> None:
        c.forecasts.append((now, self.forecast(c.id)))
        while c.forecasts and now - c.forecasts[0][0] > FLOW_WINDOW_MS:
            c.forecasts.pop(0)

    def fast(self, cid: str, now: int) -> bool:
        """A fast market, announced to everyone: one-sided flow or a forecast jump in the last minute. Designated market
        makers may then quote up to MAX_SPREAD_FAST wide and still meet their obligation."""
        flow, volume = self.flow(cid, now)
        if volume >= FAST_VOLUME and abs(flow) / volume >= FAST_IMBALANCE:
            return True
        seen = [f for _, f in self.contracts[cid].forecasts]
        return bool(seen) and max(seen) - min(seen) >= FAST_MOVE

    def option_volume(self, m) -> dict[str, float]:
        """Contracts bought on each of a market's options so far (what the Contrarian fades)."""
        out = {o: 0.0 for o in m.option_ids()}
        for cid, c in self.contracts.items():
            if c.market is not m:
                continue
            for f in self.books[cid].tape:
                if len(m.options) == 2:
                    out[m.options[0]["id"] if f.taker_bought_yes else m.options[1]["id"]] += f.qty
                elif f.taker_bought_yes:
                    out[c.option] += f.qty
        return out

    # ------------------------------------------------------------ the loop

    def tick(self, now: int) -> list[Fill]:
        self.ticks += 1
        self.src.tick(now)
        self.gateway.new_tick()
        self.list_contracts(now)
        self.sync(now)
        fills = self.gateway.release_due(now)
        for f in fills:
            for account in (f.maker.account, f.taker.account):
                agent = self.by_account.get(account)
                if agent is not None and hasattr(agent, "on_fill"):
                    agent.on_fill(f)
        self.measure_obligations(now)
        snap = self.snapshot(now)
        agents = list(self.agents)
        self.rng.shuffle(agents)
        for agent in agents:
            agent.act(self, snap, now)
        return fills

    def list_contracts(self, now: int) -> None:
        for m in self.src.active(now):
            if m.id in self.listed:
                continue
            self.listed.add(m.id)
            opts = m.options
            if len(opts) == 2:
                pairs = [(m.id, opts[0]["id"], opts[0]["label"], opts[1]["label"])]
            else:
                pairs = [(f"{m.id}:{o['id']}", o["id"], o["label"], f"Not {o['label']}") for o in opts]
            for cid, option, label, complement in pairs:
                self.contracts[cid] = Contract(cid, m, option, label, complement, m.sub)
                self.books[cid] = Book(cid)

    def sync(self, now: int) -> None:
        for cid, c in self.contracts.items():
            if c.done:
                continue
            m = c.market
            if m.result is not None:
                self.capture_close(c)
                self.gateway.cancel_contract(cid, "market settled", now)
                self.settle(c, m.result.get("winner"), now)
                continue
            status = self.src.status(m, now)
            if status == "closed":
                if not c.closed:
                    self.capture_close(c)
                    self.gateway.cancel_contract(cid, "trading closed", now)
                    c.closed = True
            elif status == "halted":
                if not c.halted:
                    self.gateway.cancel_contract(cid, "market halted: every order cancelled", now)
                    c.halted = True
            elif c.halted:
                c.halted, c.reopened_at = False, now

    def capture_close(self, c: Contract) -> None:
        if c.close_forecast is None:
            book = self.books[c.id]
            c.close_mid, c.close_last, c.close_forecast = book.mid(), book.last, self.forecast(c.id)

    def settle(self, c: Contract, winner: str | None, now: int) -> None:
        if winner is None:
            self.clearing.void(c.id, now)
            c.outcome = "void"
        else:
            c.outcome = "yes" if winner == c.option else "no"
            self.clearing.settle(c.id, c.outcome, now, self.rebate_due(c))
        c.done = True
        self.settled.append({"contract": c.id, "series": c.series, "outcome": c.outcome,
                             "outcomes": len(c.market.options), "closeMid": c.close_mid,
                             "closeLast": c.close_last, "forecast": c.close_forecast,
                             "trades": len(self.books[c.id].tape),
                             "volume": sum(f.qty for f in self.books[c.id].tape)})

    def rebate_due(self, c: Contract) -> dict[str, int]:
        """Contracts made by each market maker that met its quoting obligations on this contract."""
        if not c.open_ticks:
            return {}
        return {mm: self.clearing.maker_volume.get((mm, c.id), 0) for mm, ok in c.quoted_ok.items()
                if ok / c.open_ticks >= UPTIME}

    def measure_obligations(self, now: int) -> None:
        """Each tick a contract is open, note which market makers quote both sides within MAX_SPREAD, MIN_SIZE each."""
        for cid, c in self.contracts.items():
            if c.done or c.closed or c.halted or self.src.status(c.market, now) != "open":
                continue
            self.note_forecast(c, now)
            if now - max(c.market.opened_at, c.reopened_at) < GRACE_MS:
                continue
            c.open_ticks += 1
            fast = self.fast(cid, now)
            c.fast_ticks += fast
            limit = MAX_SPREAD_FAST if fast else MAX_SPREAD
            best: dict[str, list] = {}
            for o in self.books[cid].resting.values():
                if self.accounts[o.account].kind != "mm" or o.left < MIN_SIZE:
                    continue
                q = best.setdefault(o.account, [None, None])
                if o.side == "yes":
                    q[0] = o.price if q[0] is None else max(q[0], o.price)
                else:
                    ask = PAYOUT - o.price
                    q[1] = ask if q[1] is None else min(q[1], ask)
            for mm, (bid, ask) in best.items():
                if bid is not None and ask is not None and ask - bid <= limit:
                    c.quoted_ok[mm] += 1

    def snapshot(self, now: int) -> dict:
        """What every participant can see: contracts, forecasts, top of book, recent trade flow, and the public data
        store. Never another account's orders, pending orders or identities."""
        out = {}
        for cid, c in self.contracts.items():
            if c.done or self.src.status(c.market, now) != "open":
                continue
            book = self.books[cid]
            flow, volume = self.flow(cid, now)
            bid, ask = book.bid(), book.ask()
            out[cid] = {"id": cid, "market": c.market, "series": c.series, "option": c.option,
                        "outcomes": len(c.market.options), "forecast": self.forecast(cid), "bid": bid, "ask": ask,
                        "bidSize": book.size_at("yes", bid) if bid is not None else 0,
                        "askSize": book.size_at("no", PAYOUT - ask) if ask is not None else 0,
                        "last": book.last, "flow": flow, "volume": volume, "vwap": self.vwap(cid, now),
                        "fast": self.fast(cid, now),
                        "closesAt": c.market.closes_at,
                        "openedAt": c.market.opened_at}
        return {"now": now, "contracts": out, "store": self.src.store}

    # ------------------------------------------------------------ views

    def positions(self, account: str) -> dict:
        cl = self.clearing
        rows = [{"contract": cid, "yes": p.yes, "no": p.no, "cost": p.cost()}
                for cid, p in cl.positions.get(account, {}).items() if p.yes or p.no]
        orders = [o.public() for o in self.gateway.orders.values()
                  if o.account == account and o.status in ("pending", "open")]
        return {"account": account, "cash": cl.cash(account), "held": cl.held(account),
                "equityAtCost": cl.equity_at_cost(account), "realised": cl.realised.get(account, 0),
                "feesPaid": cl.fees_paid.get(account, 0), "pnl": cl.pnl(account),
                "positions": rows, "orders": orders}

    def stats(self) -> dict:
        cl = self.clearing
        groups: dict[str, dict] = {}
        for a in self.accounts.values():
            g = groups.setdefault(a.label(), {"accounts": 0, "granted": 0, "equityAtCost": 0, "realised": 0,
                                              "feesPaid": 0, "pnl": 0})
            g["accounts"] += 1
            g["granted"] += cl.granted.get(a.id, 0)
            g["equityAtCost"] += cl.equity_at_cost(a.id)
            g["realised"] += cl.realised.get(a.id, 0)
            g["feesPaid"] += cl.fees_paid.get(a.id, 0)
            g["pnl"] += cl.pnl(a.id)
        for g in groups.values():
            g["roiOnGranted"] = round(g["pnl"] / g["granted"], 4) if g["granted"] else None
        uptime = {}
        for agent in self.agents:
            if getattr(agent, "kind", "") != "mm":
                continue
            mm = agent.id
            seen = [(c.quoted_ok.get(mm, 0), c.open_ticks) for c in self.contracts.values()
                    if c.open_ticks and agent.covers(c.id)]
            ok, total = sum(s[0] for s in seen), sum(s[1] for s in seen)
            uptime[mm] = round(ok / total, 3) if total else None
        return {"ticks": self.ticks, "contracts": len(self.contracts), "settled": len(self.settled),
                "groups": groups, "marketMakerUptime": uptime, "fees": cl.ledger.get("fees"),
                "feesCollected": sum(cl.fees_by_contract.values()),
                "trades": sum(len(b.tape) for b in self.books.values()),
                "messages": sum(self.gateway.messages.values()), "rejects": sum(self.gateway.rejects.values()),
                "calibration": calibration(self.settled),
                "audit": cl.audit(self.gateway.live_orders()) or "ok"}


def calibration(rows: list[dict]) -> dict:
    """Brier score at close of the traded price (book mid, else last trade) against the public forecast alone and an
    uninformed guess (1 / number of outcomes), over contracts that settled on a real reading."""
    scored = [r for r in rows if r["outcome"] in ("yes", "no") and r["forecast"] is not None]
    priced = [r for r in scored if (r["closeMid"] if r["closeMid"] is not None else r["closeLast"]) is not None]

    def brier(rs, prob) -> float | None:
        return round(sum((prob(r) - (r["outcome"] == "yes")) ** 2 for r in rs) / len(rs), 4) if rs else None

    def price(r) -> float:
        return (r["closeMid"] if r["closeMid"] is not None else r["closeLast"]) / PAYOUT

    return {"n": len(scored), "nPriced": len(priced), "price": brier(priced, price),
            "forecastSameContracts": brier(priced, lambda r: r["forecast"]),
            "forecast": brier(scored, lambda r: r["forecast"]),
            "uninformed": brier(scored, lambda r: 1 / r.get("outcomes", 2))}
