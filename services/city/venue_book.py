"""The order book for one binary contract on the parallel exchange venue (docs/exchange-design.md).

A YES contract pays 100 points if its outcome happens and a NO contract pays 100 if it doesn't. One book serves both
sides, as on Kalshi: every order is a bid to buy YES or to buy NO, and a NO bid at r is an offer to sell YES at
100 - r. A YES bid at p and a NO bid at r cross when p + r >= 100; the trade happens at the resting order's price, so
the YES side pays that YES price and the NO side pays the rest of the 100. Price-time priority: best price first,
then first come first served within a price. Self-trade prevention cancels the incoming order's remainder when the
next resting order belongs to the same firm, so no firm can trade with itself (wash trades).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

PAYOUT = 100
MIN_PRICE, MAX_PRICE = 1, 99
SIDES = ("yes", "no")


def other(side: str) -> str:
    return "no" if side == "yes" else "yes"


def yes_price(side: str, price: int) -> int:
    """The YES-equivalent of an order's price: a NO bid at r is a YES offer at 100 - r."""
    return price if side == "yes" else PAYOUT - price


@dataclass
class Order:
    id: str
    account: str
    firm: str
    contract: str
    side: str             # "yes" or "no": what the order buys
    price: int            # points paid per contract for that side, 1..99
    qty: int
    tif: str = "gtc"      # "gtc" rests on the book; "ioc" cancels whatever doesn't fill at once
    post_only: bool = False
    submitted_at: int = 0
    placed_at: int = 0    # when it reached the book (after the speed bump)
    filled: int = 0
    status: str = "pending"  # pending | open | filled | cancelled | rejected
    reason: str | None = None
    held: int = 0         # points reserved in the ledger for this order (venue_ledger.Clearing)
    fee_units: int = 0    # running 7·x·P·(100−P) sum for this order's taker fills
    fee_paid: int = 0

    @property
    def left(self) -> int:
        return self.qty - self.filled

    def public(self) -> dict:
        return {"id": self.id, "contract": self.contract, "side": self.side, "price": self.price, "qty": self.qty,
                "filled": self.filled, "status": self.status, "reason": self.reason, "tif": self.tif,
                "postOnly": self.post_only, "submittedAt": self.submitted_at, "placedAt": self.placed_at}


@dataclass(frozen=True)
class Fill:
    contract: str
    taker: Order
    maker: Order
    yes_price: int  # what the YES side paid per contract; the NO side paid PAYOUT - yes_price
    qty: int
    at: int

    @property
    def yes_account(self) -> str:
        return self.taker.account if self.taker.side == "yes" else self.maker.account

    @property
    def no_account(self) -> str:
        return self.taker.account if self.taker.side == "no" else self.maker.account

    @property
    def taker_bought_yes(self) -> bool:
        return self.taker.side == "yes"

    def public(self) -> dict:
        """The tape shows prices and sizes, never who traded."""
        return {"contract": self.contract, "yesPrice": self.yes_price, "qty": self.qty, "at": self.at,
                "aggressor": self.taker.side}


class Book:
    def __init__(self, contract: str):
        self.contract = contract
        self.levels: dict[str, dict[int, deque[Order]]] = {"yes": {}, "no": {}}
        self.resting: dict[str, Order] = {}
        self.tape: list[Fill] = []
        self.last: int | None = None  # last YES trade price

    # ------------------------------------------------------------ reading

    def best(self, side: str) -> int | None:
        levels = self.levels[side]
        return max(levels) if levels else None

    def bid(self) -> int | None:
        """Best YES bid."""
        return self.best("yes")

    def ask(self) -> int | None:
        """Best YES ask, from the best NO bid."""
        b = self.best("no")
        return None if b is None else PAYOUT - b

    def mid(self) -> float | None:
        b, a = self.bid(), self.ask()
        return None if b is None or a is None else (b + a) / 2

    def size_at(self, side: str, price: int) -> int:
        return sum(o.left for o in self.levels[side].get(price, ()))

    def crosses(self, side: str, price: int) -> bool:
        b = self.best(other(side))
        return b is not None and b + price >= PAYOUT

    def depth(self, n: int = 5) -> dict:
        """Top n YES bids (high to low) and YES asks (low to high), as [price, contracts]."""
        bids = [[p, self.size_at("yes", p)] for p in sorted(self.levels["yes"], reverse=True)[:n]]
        asks = [[PAYOUT - r, self.size_at("no", r)] for r in sorted(self.levels["no"], reverse=True)[:n]]
        return {"bids": bids, "asks": asks, "last": self.last}

    def locked_or_crossed(self) -> bool:
        b, a = self.bid(), self.ask()
        return b is not None and a is not None and b >= a

    # ------------------------------------------------------------ matching

    def submit(self, order: Order, now: int) -> list[Fill]:
        """Match an arriving order, then rest (gtc) or cancel (ioc) what's left. Returns the fills, taker first."""
        if order.post_only and self.crosses(order.side, order.price):
            order.status, order.reason = "cancelled", "post-only order would have crossed the book"
            return []
        order.status, order.placed_at = "open", now
        opp, fills = other(order.side), []
        while order.left > 0:
            best = self.best(opp)
            if best is None or best + order.price < PAYOUT:
                break
            queue = self.levels[opp][best]
            maker = queue[0]
            if maker.firm == order.firm:
                order.status, order.reason = "cancelled", "self-trade prevented"
                break
            qty = min(order.left, maker.left)
            price = best if opp == "yes" else PAYOUT - best
            maker.filled += qty
            order.filled += qty
            fill = Fill(self.contract, order, maker, price, qty, now)
            fills.append(fill)
            self.tape.append(fill)
            self.last = price
            if maker.left == 0:
                maker.status = "filled"
                self._remove(maker)
        if order.left == 0:
            order.status = "filled"
        elif order.status == "open":
            if order.tif == "ioc":
                order.status, order.reason = "cancelled", "immediate-or-cancel: the rest did not fill"
            else:
                self.levels[order.side].setdefault(order.price, deque()).append(order)
                self.resting[order.id] = order
        return fills

    def cancel(self, order_id: str, reason: str) -> Order | None:
        order = self.resting.get(order_id)
        if order is None:
            return None
        self._remove(order)
        order.status, order.reason = "cancelled", reason
        return order

    def cancel_all(self, reason: str) -> list[Order]:
        return [o for oid in list(self.resting) if (o := self.cancel(oid, reason))]

    def _remove(self, order: Order) -> None:
        self.resting.pop(order.id, None)
        level = self.levels[order.side].get(order.price)
        if level is None:
            return
        try:
            level.remove(order)
        except ValueError:
            pass
        if not level:
            del self.levels[order.side][order.price]
