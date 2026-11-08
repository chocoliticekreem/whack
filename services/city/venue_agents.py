"""The AI participants on the parallel exchange venue: market makers, hedge funds in three tiers, and retail.

All of them are rules, not language models, so a thousand agents cost no more API calls than one. Each reads only the
public snapshot (venue.Venue.snapshot) and its own account, and trades only through the gateway, under the same
checks, speed bump, fees and limits as a player. Each carries a typical human network latency.

- Market makers quote both sides around the public forecast, earn the spread, skew their quotes against their
  inventory (Avellaneda & Stoikov), move their fair value after being filled (Glosten & Milgrom), widen when the
  forecast jumps and before close, and stop quoting a side at their inventory limit. A new quote goes in before the
  old one comes off; a quote that has gone stale is pulled at once. Designated market makers have signed up to the
  quoting obligations, so however much they widen, their spread never goes past MAX_SPREAD; the others may widen
  freely and simply earn no rebate.
- Hedge funds turn a strategy into a probability: the rule from traders.py picks an outcome, the fund blends that
  rule's measured hit rate (docs/questions.md, docs/judge-prep.md) with the public forecast, adds its own noise, and
  takes liquidity only when the edge beats the price plus the taker fee and a margin. Size is fractional Kelly on
  its own equity. A fund that loses half its capital is wound down; its cash goes back to its tier's investors, who
  launch a new fund with fresh settings once they hold enough.
- Retail arrives at random, mostly sends small immediate-or-cancel orders, and carries the documented retail biases:
  long shots, favourites, herding, chasing the last move, the hub they're near, or nothing at all.
"""
from __future__ import annotations

import math
import random
import zlib

import traders
from venue import MAX_SPREAD
from venue_book import PAYOUT
from venue_gateway import AI_LATENCY_MS, MAX_QTY, Account
from venue_ledger import FEE_DIV, fee_units

WIDEN_BEFORE_CLOSE_MS = 90_000
FLOW_ALPHA, FLOW_DECAY, FLOW_CAP = 0.05, 0.9, 5.0
REQUOTE_PENDING = 2        # points a pending quote must be off before it's replaced
STALE = 3                  # points: a quote this far on the wrong side of fair value is pulled at once
PICK_TTL_MS = 30_000
# Measured hit rate of each rule when it picks an outcome. None: no measured edge, so no view.
HIT = {("tube", "hub_surge"): 0.36, ("tube", "hub_hour"): 0.56, ("tube", "city_pulse"): 0.55,
       ("cams", "london_vix"): 0.65, ("bus", "heathrow_stack"): 0.60, ("rain", "city_pulse"): 0.55,
       ("contrarian", None): 0.45}
STRATEGIES = ("tube", "cams", "bus", "lines", "rain", "contrarian", "sim", "model")
BIASES = ("random", "longshot", "favourite", "herd", "momentum", "home")


def alive(order) -> bool:
    return order is not None and order.status in ("pending", "open")


def taker_cost(price: int) -> float:
    """Price plus the taker fee, per point of payout."""
    return (price + fee_units(1, price) / FEE_DIV) / PAYOUT


class Agent:
    kind = ""

    def __init__(self, aid: str, rng: random.Random, tier: str = "", name: str = ""):
        self.account = Account(aid, self.kind, tier=tier, name=name, latency_ms=AI_LATENCY_MS)
        self.rng = rng
        self.start = 0
        self.retired = False

    @property
    def id(self) -> str:
        return self.account.id

    def act(self, v, snap: dict, now: int) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------- market makers


class MarketMaker(Agent):
    kind = "mm"

    def __init__(self, aid: str, rng: random.Random, half: float = 2.0, size: int = 20, inventory: int = 200,
                 skew: float = 0.02, coverage: float = 1.0, designated: bool = False):
        super().__init__(aid, rng, name=aid)
        self.half, self.size, self.inventory, self.skew, self.coverage = half, size, inventory, skew, coverage
        self.designated = designated
        self.quotes: dict[str, dict] = {}
        self.flow: dict[str, float] = {}
        self.last_f: dict[str, float] = {}

    def covers(self, cid: str) -> bool:
        return self.coverage >= 1 or zlib.crc32(f"{self.id}|{cid}".encode()) % 1000 < self.coverage * 1000

    def on_fill(self, f) -> None:
        if f.maker.account != self.id:
            return
        shift = self.flow.get(f.contract, 0.0) + FLOW_ALPHA * f.qty * (1 if f.taker_bought_yes else -1)
        self.flow[f.contract] = max(-FLOW_CAP, min(FLOW_CAP, shift))

    def quote_for(self, v, c: dict, now: int) -> dict[str, int | None]:
        cid = c["id"]
        f = PAYOUT * c["forecast"]
        yes, no = v.clearing.holding(self.id, cid)
        inv = yes - no
        moved = abs(f - self.last_f.get(cid, f))
        self.last_f[cid] = f
        half = self.half + min(3.0, moved) + (2 if c["closesAt"] - now < WIDEN_BEFORE_CLOSE_MS else 0)
        if self.designated:  # floor/ceil can add a point, so (MAX_SPREAD - 1) / 2 keeps the spread <= MAX_SPREAD
            half = min(half, (MAX_SPREAD - 1) / 2)
        fair = f + self.flow.get(cid, 0.0) - self.skew * inv
        bid = max(1, min(98, math.floor(fair - half)))
        ask = max(bid + 1, min(99, math.ceil(fair + half)))
        return {"yes": None if inv >= self.inventory else bid,
                "no": None if inv <= -self.inventory else PAYOUT - ask}

    def act(self, v, snap: dict, now: int) -> None:
        for cid, c in snap["contracts"].items():
            if not self.covers(cid):
                continue
            for side, price in self.quote_for(v, c, now).items():
                self.requote(v, cid, side, price, now)
            self.flow[cid] = self.flow.get(cid, 0.0) * FLOW_DECAY

    def requote(self, v, cid: str, side: str, price: int | None, now: int) -> None:
        """Keep one resting quote per side (`cur`) and at most one replacement waiting out the speed bump (`nxt`)."""
        q = self.quotes.setdefault(cid, {}).setdefault(side, {"cur": None, "nxt": None})
        cur, nxt = q["cur"], q["nxt"]
        if nxt is not None and nxt.status == "open":  # the replacement landed: retire the old quote
            if alive(cur):
                v.gateway.cancel(self.id, cur.id, now, "replaced")
            q["cur"], q["nxt"], cur, nxt = nxt, None, nxt, None
        elif not alive(nxt):
            q["nxt"] = nxt = None
        if price is None:  # at the inventory limit: stop quoting this side
            for o in (cur, nxt):
                if alive(o):
                    v.gateway.cancel(self.id, o.id, now, "inventory limit")
            return
        if alive(cur) and cur.price - price >= STALE:
            # Fair value moved against the quote (a YES bid now too high, or a NO bid, i.e. a YES offer, too low):
            # pull it now. A quote that is merely less competitive stays until its replacement lands.
            v.gateway.cancel(self.id, cur.id, now, "stale quote")
        healthy = alive(cur) and cur.price == price and cur.left * 4 >= self.size * 3  # refill at 25% filled
        if healthy:
            if alive(nxt):
                v.gateway.cancel(self.id, nxt.id, now, "not needed")
                q["nxt"] = None
            return
        if alive(nxt):
            if abs(nxt.price - price) < REQUOTE_PENDING:
                return
            v.gateway.cancel(self.id, nxt.id, now, "requote")
        o = v.gateway.submit(self.id, cid, side, price, self.size, now, post_only=True)
        q["nxt"] = o if o.status == "pending" else None


# ---------------------------------------------------------------- hedge funds


def pick(v, strategy: str, m, store, now: int) -> str | None:
    """The outcome a traders.py rule backs on market m, shared by every fund running it for PICK_TTL_MS."""
    key = (strategy, m.id, now // PICK_TTL_MS)
    if key not in v.cache:
        ctx = {"spots": getattr(v.src, "spots", {}), "stakes": v.option_volume(m)}
        try:
            got = traders.decide(strategy, m, store, now, ctx)
        except Exception:  # noqa: BLE001 - a rule that can't read its feed has no view
            got = None
        v.cache[key] = got[0] if got else None
    return v.cache[key]


class Fund(Agent):
    kind = "fund"

    def __init__(self, aid: str, rng: random.Random, tier: str, strategies: tuple[str, ...], weight: float,
                 noise: float, kelly: float, margin: float, every: int, start_after: float, stop: float = 0.5):
        super().__init__(aid, rng, tier=tier, name=f"{aid} ({'+'.join(strategies)})")
        self.strategies, self.weight, self.noise, self.kelly = strategies, weight, noise, kelly
        self.margin, self.every, self.start_after, self.stop = margin, every, start_after, stop
        self.n = rng.randrange(every)

    def belief(self, v, c: dict, snap: dict, now: int) -> float | None:
        """This fund's probability that the contract's YES happens, or None without a view."""
        f, m = c["forecast"], c["market"]
        views = []
        for s in self.strategies:
            if s == "model":
                views.append(f)
                continue
            if s == "signal":  # synthetic markets (analysis/exchange): a noisy read of the truth
                if hasattr(m, "signal"):
                    views.append(m.signal(c["option"], self.rng))
                continue
            chosen = pick(v, s, m, snap["store"], now)
            hit = HIT.get((s, m.sub), HIT.get((s, None)))
            if chosen is None or hit is None:
                continue
            fp = float(m.futures.probs()[chosen])
            qp = (1 - self.weight) * fp + self.weight * hit
            if chosen == c["option"]:
                views.append(qp)
            elif c["outcomes"] == 2:
                views.append(1 - qp)
            else:
                views.append(f * (1 - qp) / max(1e-9, 1 - fp))
        if not views:
            return None
        q = sum(views) / len(views) + self.rng.gauss(0, self.noise)
        return min(0.97, max(0.03, q))

    def act(self, v, snap: dict, now: int) -> None:
        if self.retired:
            return
        equity = v.clearing.equity_at_cost(self.id)
        if self.start and equity < self.stop * self.start:
            self.retire(v, now)
            return
        self.n += 1
        if self.n % self.every:
            return
        for cid, c in snap["contracts"].items():
            life = (now - c["openedAt"]) / max(1, c["closesAt"] - c["openedAt"])
            if life < self.start_after:
                continue
            q = self.belief(v, c, snap, now)
            if q is None:
                continue
            self.take(v, cid, "yes", q, c["ask"], c["askSize"], equity, now)
            no_price = None if c["bid"] is None else PAYOUT - c["bid"]
            self.take(v, cid, "no", 1 - q, no_price, c["bidSize"], equity, now)

    def take(self, v, cid: str, side: str, q: float, price: int | None, size: int, equity: int, now: int) -> None:
        if price is None or size <= 0 or q - taker_cost(price) < self.margin:
            return
        p = price / PAYOUT
        qty = min(size, MAX_QTY, int(self.kelly * (q - p) / (1 - p) * equity / price))
        qty = min(qty, v.clearing.cash(self.id) // (price + 2))  # only what it can pay for, fee included
        if qty >= 1:
            v.gateway.submit(self.id, cid, side, price, qty, now, tif="ioc")

    def retire(self, v, now: int) -> None:
        for o in v.gateway.live_orders():
            if o.account == self.id:
                v.gateway.cancel(self.id, o.id, now, "fund wound down", system=True)
        cash = v.clearing.cash(self.id)
        if cash > 0:
            v.clearing.transfer(self.id, investors(self.account.tier), cash, "wind-down", now)
        self.retired = True


def investors(tier: str) -> str:
    return f"investors:{tier}"


class Investors:
    """A tier's capital pool: launches a new fund, with fresh settings, whenever it holds a fund's starting capital.
    It sends no orders."""

    def __init__(self, tier: str, capital: int, make, rng: random.Random):
        self.tier, self.capital, self.make, self.rng = tier, capital, make, rng
        self.launched = 0

    def act(self, v, snap: dict, now: int) -> None:
        pool = investors(self.tier)
        while v.clearing.cash(pool) >= self.capital:
            self.launched += 1
            fund = self.make(f"{self.tier.replace(' ', '')}-r{self.launched}", self.rng)
            v.add_agent(fund, 0, now)
            v.clearing.transfer(pool, fund.id, self.capital, "launch", now)
            fund.start = self.capital


# ---------------------------------------------------------------- retail


class Retail(Agent):
    kind = "retail"

    def __init__(self, aid: str, rng: random.Random, bias: str, rate_per_min: float = 0.1, home: str | None = None):
        super().__init__(aid, rng, name=f"{aid} ({bias})")
        self.bias, self.rate, self.home = bias, rate_per_min, home
        self.last = 0

    def act(self, v, snap: dict, now: int) -> None:
        dt, self.last = (now - self.last if self.last else 0), now
        if dt <= 0 or self.rng.random() >= 1 - math.exp(-self.rate * dt / 60_000):
            return
        quoted = [c for c in snap["contracts"].values() if c["ask"] is not None or c["bid"] is not None]
        if not quoted:
            return
        c, side = self.choose(quoted)
        best = c["ask"] if side == "yes" else (None if c["bid"] is None else PAYOUT - c["bid"])
        if best is None:
            return
        limit = min(99, best + self.rng.randint(0, 2))
        qty = min(10, int(self.rng.uniform(0.03, 0.10) * v.clearing.cash(self.id) / limit))
        if qty >= 1:
            v.gateway.submit(self.id, c["id"], side, limit, qty, now, tif="ioc")

    def choose(self, cs: list[dict]) -> tuple[dict, str]:
        rng, bias = self.rng, self.bias
        if bias == "longshot":
            return min(cs, key=lambda c: c["forecast"]), "yes"
        if bias == "favourite":
            return max(cs, key=lambda c: c["forecast"]), "yes"
        if bias == "herd":
            c = max(cs, key=lambda c: abs(c["flow"]))
            return c, "yes" if c["flow"] >= 0 else "no"
        if bias == "momentum":
            c = rng.choice(cs)
            return c, "yes" if c["flow"] > 0 or (c["flow"] == 0 and rng.random() < 0.5) else "no"
        if bias == "home":
            mine = [c for c in cs if c["option"] == self.home]
            if mine:
                return rng.choice(mine), "yes"
        return rng.choice(cs), rng.choice(("yes", "no"))


# ---------------------------------------------------------------- the population

TIERS = {  # name: (funds, starting capital, Kelly fraction range, noise range, rule weight range)
    "tier 1": (4, 30_000, (0.15, 0.25), (0.01, 0.02), (0.3, 0.5)),
    "tier 2": (60, 8_000, (0.10, 0.20), (0.02, 0.05), (0.2, 0.4)),
    "tier 3": (120, 2_000, (0.05, 0.15), (0.05, 0.10), (0.1, 0.3)),
}


def fund_maker(tier: str, strategies: tuple[str, ...]):
    _, _, kelly, noise, weight = TIERS[tier]

    def make(aid: str, rng: random.Random, k: int | None = None) -> Fund:
        if tier == "tier 1":
            strat = tuple(strategies)  # a platform runs every strategy as a pod and averages their views
        else:
            strat = (strategies[(k if k is not None else rng.randrange(len(strategies))) % len(strategies)],)
        return Fund(aid, rng, tier, strat, weight=rng.uniform(*weight), noise=rng.uniform(*noise),
                    kelly=rng.uniform(*kelly), margin=rng.uniform(0.01, 0.04), every=rng.randint(2, 8),
                    start_after=rng.uniform(0.0, 0.6))

    return make


def populate(v, *, market_makers: int = 12, designated: int = 4, funds: dict[str, int] | None = None,
             retail: int = 2000, seed: int = 11, strategies: tuple[str, ...] = STRATEGIES, mm_capital: int = 200_000,
             homes: list[str] | None = None, now: int = 0) -> None:
    """Open every AI account on the venue's ledger (never through the public sign-up) with its starting capital."""
    rng = random.Random(seed)
    for i in range(market_makers):
        mm = MarketMaker(f"mm{i + 1}", random.Random(rng.random()), half=rng.choice((1.5, 2.0, 2.5)),
                         size=rng.choice((20, 25, 30)), coverage=1.0 if i < designated else 0.6,
                         designated=i < designated)
        v.add_agent(mm, mm_capital, now)
    counts = funds if funds is not None else {t: spec[0] for t, spec in TIERS.items()}
    for tier, n in counts.items():
        make = fund_maker(tier, strategies)
        capital = TIERS[tier][1]
        for k in range(n):
            v.add_agent(make(f"{tier.replace(' ', '')}-f{k + 1}", random.Random(rng.random()), k), capital, now)
        v.agents.append(Investors(tier, capital, make, random.Random(rng.random())))
    homes = homes or list(getattr(v.src, "spots", {}) or [])
    for k in range(retail):
        bias = BIASES[k % len(BIASES)]
        home = rng.choice(homes) if homes else None
        r = Retail(f"r{k + 1}", random.Random(rng.random()), bias, rate_per_min=rng.uniform(0.05, 0.15), home=home)
        v.add_agent(r, rng.randint(200, 1000), now)
