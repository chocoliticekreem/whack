"""The order gateway: the only way an order reaches a book on the parallel exchange venue, for a human and for every
AI account alike. Same checks, same speed bump, same fees, same limits.

- New orders are checked, their collateral is reserved, and any order that can take liquidity waits the series'
  speed bump (integrity.py: 45 s on locally observable series, 2-5 s elsewhere) before it reaches the book. Post-only
  orders, which can only rest, skip it: an asymmetric speed bump, as on TSX Alpha, so the people who see the news
  first still can't hit a stale price, while liquidity can be replaced at once. The rule is the same for everyone. AI
  accounts also carry a network latency equal to a typical human's round trip, so being on the same machine as the
  exchange is no advantage.
- Orders released in the same tick go to the book in arrival order; exact ties are shuffled.
- Cancels are never delayed, so a market maker can always pull a stale quote. A resting order can't be cancelled in
  its first second (no flashing quotes); system cancels (halts, close, void) ignore that.
- Each account kind has a message budget per tick (submits plus cancels), as exchanges set messaging limits.
- A price collar stops an order that could trade on arrival from reaching more than COLLAR points past the last trade
  (or the forecast, if nothing traded in the last minute). Post-only orders can't trade on arrival, so they skip it.
"""
from __future__ import annotations

import itertools
import random
from collections import defaultdict
from dataclasses import dataclass

from venue_book import MAX_PRICE, MIN_PRICE, PAYOUT, SIDES, Fill, Order, yes_price

MAX_QTY = 500
COLLAR = 25            # points: an aggressive order may not reach further than this past the reference price
MIN_REST_MS = 1_000
AI_LATENCY_MS = 300    # the median public round trip was 270-450 ms in analysis/live_trading
BUDGET = {"mm": 60, "fund": 10, "retail": 2, "human": 20}  # messages per tick


@dataclass
class Account:
    id: str
    kind: str            # "human" | "mm" | "fund" | "retail"
    firm: str = ""
    tier: str = ""
    name: str = ""
    latency_ms: int = 0

    def __post_init__(self):
        self.firm = self.firm or self.id
        self.name = self.name or self.id

    def label(self) -> str:
        return {"human": "Player", "mm": "AI market maker", "retail": "AI retail"}.get(
            self.kind, f"AI fund · {self.tier}" if self.tier else "AI fund")


class Gateway:
    def __init__(self, venue, rng: random.Random):
        self.v = venue
        self.rng = rng
        self.orders: dict[str, Order] = {}
        self.pending: list[Order] = []
        self.release_at: dict[str, int] = {}
        self.ids = itertools.count(1)
        self.used: dict[str, int] = defaultdict(int)
        self.messages: dict[str, int] = defaultdict(int)
        self.fills: dict[str, int] = defaultdict(int)
        self.rejects: dict[str, int] = defaultdict(int)

    def new_tick(self) -> None:
        self.used.clear()

    def live_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status in ("pending", "open")]

    def open_for(self, account: str, contract: str) -> list[Order]:
        return [o for o in self.orders.values()
                if o.account == account and o.contract == contract and o.status in ("pending", "open")]

    # ------------------------------------------------------------ orders in

    def _spend_message(self, acct: Account) -> bool:
        self.messages[acct.id] += 1
        if self.used[acct.id] >= BUDGET.get(acct.kind, BUDGET["human"]):
            return False
        self.used[acct.id] += 1
        return True

    def submit(self, account: str, contract: str, side: str, price, qty, now: int, tif: str = "gtc",
               post_only: bool = False) -> Order:
        acct = self.v.accounts.get(account)
        order = Order(f"o{next(self.ids)}", account, acct.firm if acct else account, contract, side,
                      price if isinstance(price, int) else 0, qty if isinstance(qty, int) else 0, tif,
                      bool(post_only), submitted_at=now)
        reason = self._check(acct, order, price, qty, now)
        if reason is None and not self.v.clearing.reserve(order, now):
            reason = f"not enough points: this order needs {self.v.clearing.reservation(order)} reserved"
        if reason:
            order.status, order.reason = "rejected", reason
            self.rejects[account] += 1
            self.orders[order.id] = order
            return order
        self.orders[order.id] = order
        self.pending.append(order)
        bump = 0 if order.post_only else self.v.delay_ms(contract)
        self.release_at[order.id] = now + bump + acct.latency_ms
        return order

    def _check(self, acct: Account | None, order: Order, price, qty, now: int) -> str | None:
        if acct is None:
            return "unknown account"
        if not self._spend_message(acct):
            return "message budget for this second used up"
        if order.contract not in self.v.contracts:
            return "unknown contract"
        if order.side not in SIDES:
            return "side must be yes or no"
        if order.tif not in ("gtc", "ioc"):
            return "tif must be gtc or ioc"
        if isinstance(price, bool) or not isinstance(price, int) or not MIN_PRICE <= price <= MAX_PRICE:
            return f"price must be a whole number from {MIN_PRICE} to {MAX_PRICE}"
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
            return f"quantity must be a whole number from 1 to {MAX_QTY}"
        status = self.v.contract_status(order.contract, now)
        if status != "open":
            return f"market {status}"
        if not order.post_only:  # a post-only order can never trade on arrival, so it can't trade at a bad price
            ref = self.v.reference(order.contract, now)
            reach = yes_price(order.side, price)
            if (order.side == "yes" and reach > ref + COLLAR) or (order.side == "no" and reach < ref - COLLAR):
                return f"outside the price collar: the reference is {ref}, at most {COLLAR} points away"
        cap = int(self.v.cap_pct(order.contract) * self.v.clearing.equity_at_cost(acct.id))
        mine = self.open_for(acct.id, order.contract)
        exposure = self.v.clearing.exposure(acct.id, order.contract) + sum(o.price * o.left for o in mine)
        # The limit is on net risk: contracts this order would pair off against what the account holds (selling)
        # add nothing, so a holder can always close out.
        yes_held, no_held = self.v.clearing.holding(acct.id, order.contract)
        closing = (no_held if order.side == "yes" else yes_held) - sum(o.left for o in mine if o.side == order.side)
        added = price * (qty - max(0, min(qty, closing)))
        if added and exposure + added > cap:
            return f"position limit: at most {cap} points at risk on this contract ({exposure} already)"
        return None

    def cancel(self, account: str, order_id: str, now: int, reason: str = "cancelled by the account",
               system: bool = False) -> tuple[bool, str]:
        order = self.orders.get(order_id)
        if order is None or (not system and order.account != account):
            return False, "unknown order"
        acct = self.v.accounts.get(order.account)
        if not system and not self._spend_message(acct):
            return False, "message budget for this second used up"
        if order.status == "pending":
            self.pending.remove(order)
            self.release_at.pop(order.id, None)
            order.status, order.reason = "cancelled", reason
            self.v.clearing.release(order, 0, now)
            return True, "cancelled before it reached the book"
        if order.status == "open":
            if not system and now - order.placed_at < MIN_REST_MS:
                return False, f"an order must rest {MIN_REST_MS // 1000} s before it can be cancelled"
            self.v.books[order.contract].cancel(order.id, reason)
            self.v.clearing.release(order, 0, now)
            return True, "cancelled"
        return False, f"order {order.status}"

    def cancel_contract(self, contract: str, reason: str, now: int) -> int:
        n = 0
        for o in [o for o in self.live_orders() if o.contract == contract]:
            n += self.cancel(o.account, o.id, now, reason, system=True)[0]
        return n

    # ------------------------------------------------------------ to the book

    def release_due(self, now: int) -> list[Fill]:
        due = [o for o in self.pending if self.release_at[o.id] <= now]
        if not due:
            return []
        self.pending = [o for o in self.pending if self.release_at[o.id] > now]
        due.sort(key=lambda o: (self.release_at[o.id], self.rng.random()))
        fills: list[Fill] = []
        for o in due:
            self.release_at.pop(o.id, None)
            status = self.v.contract_status(o.contract, now)
            if status != "open":
                o.status, o.reason = "cancelled", f"market {status} before the order reached the book"
                self.v.clearing.release(o, 0, now)
                continue
            got = self.v.books[o.contract].submit(o, now)
            for f in got:
                self.v.clearing.fill(f)
                self.fills[f.taker.account] += 1
                self.fills[f.maker.account] += 1
            self.v.clearing.after_taking(o, now)
            fills += got
        return fills

    def order_to_trade(self, account: str) -> float | None:
        return None if not self.fills.get(account) else round(self.messages[account] / self.fills[account], 1)


def side_cost(side: str, yes: int) -> int:
    """What one contract costs on `side` when YES trades at `yes`."""
    return yes if side == "yes" else PAYOUT - yes
