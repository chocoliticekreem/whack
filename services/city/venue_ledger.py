"""Money on the parallel exchange venue: a double-entry ledger in whole points, and the clearing rules on top of it.

The exchange never takes a side. Every point sits in one of these accounts, and the balances always sum to zero:
- `mint`: where grants come from (negative by the total ever granted);
- `cash:<account>`: free points;
- `held:<account>`: reserved for that account's open and pending orders (price × contracts, plus the largest
  taker fee the order could pay);
- `pool:<contract>`: collateral for matched contracts. Every trade puts in exactly 100 per contract, the YES side's
  price plus the NO side's, and settlement pays exactly 100 per contract to the winning side;
- `fees`: taker fees, the exchange's only income, out of which qualifying market makers are paid rebates.

Fees follow Kalshi's published schedule: a taker pays ceil(0.07 · C · P · (1 − P)) for C contracts at price P,
worked in points here (1 point = 1 cent, a contract pays 100). Makers pay nothing. An account holding both YES and
NO on one contract is paid 100 per pair at once, which is how a holder sells: buying NO against a YES you hold
returns the YES price.
"""
from __future__ import annotations

from collections import defaultdict

from venue_book import PAYOUT, Fill, Order

FEE_RATE_X100 = 7     # Kalshi's 0.07, with prices in points: fee = ceil(7 · C · P · (100 − P) / 10_000)
FEE_DIV = 10_000
REBATE_SHARE = 0.25   # of a contract's taker fees, to the market makers that met their obligations on it


def fee_units(qty: int, price: int) -> int:
    return FEE_RATE_X100 * qty * price * (PAYOUT - price)


def fee_cap(qty: int) -> int:
    """The most a taker can pay for qty contracts: the fee curve peaks at 50."""
    return -(-fee_units(qty, 50) // FEE_DIV)


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


class Ledger:
    def __init__(self):
        self.bal: dict[str, int] = defaultdict(int)
        self.entries: list[tuple[int, str, str, int, str]] = []

    def move(self, src: str, dst: str, amount: int, memo: str, at: int = 0) -> None:
        if not isinstance(amount, int) or amount < 0:
            raise ValueError(f"amount must be a whole number of points >= 0, got {amount!r}")
        if amount == 0:
            return
        self.bal[src] -= amount
        self.bal[dst] += amount
        self.entries.append((at, src, dst, amount, memo))

    def get(self, name: str) -> int:
        return self.bal.get(name, 0)


class Position:
    __slots__ = ("yes", "no", "cost_yes", "cost_no", "fees")

    def __init__(self):
        self.yes = self.no = self.cost_yes = self.cost_no = self.fees = 0

    def cost(self) -> int:
        return self.cost_yes + self.cost_no


class Clearing:
    """Reserves, fills, pair redemption, settlement and voids, each as balanced ledger moves."""

    def __init__(self):
        self.ledger = Ledger()
        self.positions: dict[str, dict[str, Position]] = defaultdict(dict)  # account -> contract -> Position
        self.fees_by_contract: dict[str, int] = defaultdict(int)
        self.maker_volume: dict[tuple[str, str], int] = defaultdict(int)  # (account, contract) -> contracts made
        self.granted: dict[str, int] = defaultdict(int)
        self.realised: dict[str, int] = defaultdict(int)  # account -> settled P&L in points, before fees
        self.fees_paid: dict[str, int] = defaultdict(int)  # account -> taker fees paid, net of refunds

    def pos(self, account: str, contract: str) -> Position:
        mine = self.positions[account]
        if contract not in mine:
            mine[contract] = Position()
        return mine[contract]

    # ------------------------------------------------------------ accounts

    def grant(self, account: str, points: int, at: int = 0) -> None:
        self.ledger.move("mint", f"cash:{account}", points, "grant", at)
        self.granted[account] += points

    def transfer(self, src: str, dst: str, points: int, memo: str, at: int = 0) -> None:
        """Cash from one account to another (an investor pool topping up or redeeming a fund)."""
        self.ledger.move(f"cash:{src}", f"cash:{dst}", points, memo, at)
        self.granted[dst] += points
        self.granted[src] -= points

    def cash(self, account: str) -> int:
        return self.ledger.get(f"cash:{account}")

    def held(self, account: str) -> int:
        return self.ledger.get(f"held:{account}")

    def equity_at_cost(self, account: str) -> int:
        """Cash + reserved + what the open positions cost: what an account has put at risk or could spend."""
        held_in = sum(p.cost() for p in self.positions.get(account, {}).values())
        return self.cash(account) + self.held(account) + held_in

    def exposure(self, account: str, contract: str) -> int:
        p = self.positions.get(account, {}).get(contract)
        return p.cost() if p else 0

    def pnl(self, account: str) -> int:
        """Settled P&L after taker fees (rebates are already in `realised`)."""
        return self.realised.get(account, 0) - self.fees_paid.get(account, 0)

    def holding(self, account: str, contract: str) -> tuple[int, int]:
        p = self.positions.get(account, {}).get(contract)
        return (p.yes, p.no) if p else (0, 0)

    # ------------------------------------------------------------ orders

    @staticmethod
    def reservation(order: Order) -> int:
        return order.price * order.qty + fee_cap(order.qty)

    def reserve(self, order: Order, at: int) -> bool:
        need = self.reservation(order)
        if self.cash(order.account) < need:
            return False
        self.ledger.move(f"cash:{order.account}", f"held:{order.account}", need, f"reserve {order.id}", at)
        order.held = need
        return True

    def release(self, order: Order, keep: int, at: int) -> None:
        """Return an order's reservation above `keep` to cash."""
        extra = order.held - keep
        if extra > 0:
            self.ledger.move(f"held:{order.account}", f"cash:{order.account}", extra, f"release {order.id}", at)
            order.held = keep

    def after_taking(self, order: Order, at: int) -> None:
        """Once an arriving order has matched, it can never pay a taker fee again: keep only price × contracts left
        (nothing if it's finished)."""
        keep = order.price * order.left if order.status == "open" else 0
        self.release(order, keep, at)

    def fill(self, f: Fill) -> None:
        for order, paid in ((f.taker, self._side_price(f, f.taker)), (f.maker, self._side_price(f, f.maker))):
            pay = paid * f.qty
            self.ledger.move(f"held:{order.account}", f"pool:{f.contract}", pay, f"fill {order.id}", f.at)
            order.held -= pay
            pos = self.pos(order.account, f.contract)
            if order.side == "yes":
                pos.yes += f.qty
                pos.cost_yes += pay
            else:
                pos.no += f.qty
                pos.cost_no += pay
        taker, paid = f.taker, self._side_price(f, f.taker)
        improvement = (taker.price - paid) * f.qty
        if improvement > 0:  # a taker pays the resting price, which can be better than its own limit
            self.ledger.move(f"held:{taker.account}", f"cash:{taker.account}", improvement, f"improve {taker.id}",
                             f.at)
            taker.held -= improvement
        taker.fee_units += fee_units(f.qty, paid)
        due = ceil_div(taker.fee_units, FEE_DIV) - taker.fee_paid
        if due > 0:
            self.ledger.move(f"held:{taker.account}", "fees", due, f"taker fee {taker.id}", f.at)
            taker.held -= due
            taker.fee_paid += due
            self.fees_paid[taker.account] += due
            self.fees_by_contract[f.contract] += due
            self.pos(taker.account, f.contract).fees += due
        self.maker_volume[(f.maker.account, f.contract)] += f.qty
        for account in {f.taker.account, f.maker.account}:
            self.redeem_pairs(account, f.contract, f.at)

    @staticmethod
    def _side_price(f: Fill, order: Order) -> int:
        return f.yes_price if order.side == "yes" else PAYOUT - f.yes_price

    def redeem_pairs(self, account: str, contract: str, at: int) -> None:
        pos = self.pos(account, contract)
        pairs = min(pos.yes, pos.no)
        if pairs <= 0:
            return
        self.ledger.move(f"pool:{contract}", f"cash:{account}", PAYOUT * pairs, f"redeem {pairs} pairs", at)
        cut_yes = round(pos.cost_yes * pairs / pos.yes)
        cut_no = round(pos.cost_no * pairs / pos.no)
        self.realised[account] += PAYOUT * pairs - cut_yes - cut_no
        pos.cost_yes -= cut_yes
        pos.cost_no -= cut_no
        pos.yes -= pairs
        pos.no -= pairs

    # ------------------------------------------------------------ settlement

    def holders(self, contract: str) -> list[tuple[str, Position]]:
        return [(a, ps[contract]) for a, ps in self.positions.items()
                if contract in ps and (ps[contract].yes or ps[contract].no)]

    def settle(self, contract: str, winner: str, at: int, rebate_to: dict[str, int] | None = None) -> None:
        """Pay 100 per winning contract out of the pool, then a REBATE_SHARE of the contract's fees to the market makers
        in `rebate_to` (account -> contracts made), pro rata."""
        for account, pos in self.holders(contract):
            won = pos.yes if winner == "yes" else pos.no
            self.ledger.move(f"pool:{contract}", f"cash:{account}", PAYOUT * won, f"settle {contract} {winner}", at)
            self.realised[account] += PAYOUT * won - pos.cost()
            pos.yes = pos.no = pos.cost_yes = pos.cost_no = 0
        self.pay_rebates(contract, rebate_to or {}, at)

    def void(self, contract: str, at: int) -> None:
        """Return the pool to the holders in proportion to what their positions cost (exactly their cost unless they
        redeemed pairs bought at different prices), and refund the contract's taker fees."""
        held = self.holders(contract)
        pool = self.ledger.get(f"pool:{contract}")
        total = sum(p.cost() for _, p in held)
        shares = [(a, pool * p.cost() // total if total else 0, p) for a, p in held]
        left = pool - sum(s for _, s, _ in shares)
        for i, (account, share, pos) in enumerate(sorted(shares, key=lambda s: -s[2].cost())):
            extra = 1 if i < left else 0
            self.ledger.move(f"pool:{contract}", f"cash:{account}", share + extra, f"void {contract}", at)
            self.realised[account] += share + extra - pos.cost()
            pos.yes = pos.no = pos.cost_yes = pos.cost_no = 0
        for account, ps in self.positions.items():
            pos = ps.get(contract)
            if pos and pos.fees:
                self.ledger.move("fees", f"cash:{account}", pos.fees, f"refund fees {contract}", at)
                self.fees_by_contract[contract] -= pos.fees
                self.fees_paid[account] -= pos.fees
                pos.fees = 0

    def pay_rebates(self, contract: str, rebate_to: dict[str, int], at: int) -> None:
        pot = int(self.fees_by_contract.get(contract, 0) * REBATE_SHARE)
        made = sum(rebate_to.values())
        if pot <= 0 or made <= 0:
            return
        for account, qty in sorted(rebate_to.items()):
            share = pot * qty // made
            self.ledger.move("fees", f"cash:{account}", share, f"maker rebate {contract}", at)
            self.realised[account] += share

    # ------------------------------------------------------------ checks

    def audit(self, live_orders: list[Order] = ()) -> list[str]:
        """Every invariant, rechecked from the balances. An empty list means the books balance."""
        bad = []
        if sum(self.ledger.bal.values()) != 0:
            bad.append(f"balances sum to {sum(self.ledger.bal.values())}, not 0")
        for name, v in self.ledger.bal.items():
            if v < 0 and name != "mint":
                bad.append(f"{name} is negative ({v})")
        held: dict[str, int] = defaultdict(int)
        for o in live_orders:
            held[o.account] += o.held
        for name, v in self.ledger.bal.items():
            if name.startswith("held:") and v != held.get(name[5:], 0):
                bad.append(f"{name} is {v} but its open orders reserve {held.get(name[5:], 0)}")
        yes: dict[str, int] = defaultdict(int)
        no: dict[str, int] = defaultdict(int)
        for ps in self.positions.values():
            for c, p in ps.items():
                yes[c] += p.yes
                no[c] += p.no
        for name, v in self.ledger.bal.items():
            if name.startswith("pool:"):
                c = name[5:]
                if yes[c] != no[c] or v != PAYOUT * yes[c]:
                    bad.append(f"{name} holds {v} for {yes[c]} YES and {no[c]} NO contracts")
        return bad
