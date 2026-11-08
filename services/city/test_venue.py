"""The parallel exchange venue: matching, money, fairness, orderliness, a full round on city markets, and API usage."""
from __future__ import annotations

import ast
import random
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tops
import traders
import venue
import venue_agents
import venue_ledger
from data import Store
from exchange import Clock
from test_city import MIN, SPOTS, T0, Raw, city, vix_payload
from venue import CitySource, ShadowExchange, Venue
from venue_book import Book, Order
from venue_gateway import AI_LATENCY_MS, BUDGET, COLLAR, MIN_REST_MS, Account
from venue_ledger import FEE_DIV, ceil_div, fee_cap, fee_units

HERE = Path(__file__).resolve().parent
DELAY = 5_000  # city_pulse's speed bump (integrity.AGGREGATE)


def kalshi_fees(case: unittest.TestCase) -> None:
    """Fees are off by default; these tests check the fee and rebate maths with Kalshi's schedule on."""
    p = mock.patch.object(venue_ledger, "FEE_RATE_X100", venue_ledger.KALSHI_FEE_X100)
    p.start()
    case.addCleanup(p.stop)


def order(oid, side, price, qty, account="a", firm=None, tif="gtc", post_only=False) -> Order:
    return Order(oid, account, firm or account, "c", side, price, qty, tif, post_only)


class FakeMarket:
    def __init__(self, mid, options=("over", "under"), sub="city_pulse", probs=None, opened=T0, closes=T0 + 10 * MIN):
        self.id, self.sub, self.kind = mid, sub, "overunder" if len(options) == 2 else "versus"
        self.options = [{"id": o, "label": o.title()} for o in options]
        self.opened_at, self.closes_at, self.settles_at = opened, closes, closes + MIN
        self.question, self.source, self.rule = f"{mid}?", "test", "rule"
        self.result = self.settled_at = self.locked = None
        self.probs = dict(probs or {o: 1 / len(options) for o in options})
        self.futures = SimpleNamespace(probs=lambda: self.probs)

    def option_ids(self):
        return [o["id"] for o in self.options]

    def finish(self, winner, now):
        self.result, self.settled_at = {"winner": winner}, now


class FakeSource:
    def __init__(self, *markets):
        self.markets, self.halted, self.store, self.spots = list(markets), set(), None, {}

    def tick(self, now):
        pass

    def active(self, now):
        return [m for m in self.markets if m.result is None]

    def status(self, m, now):
        if m.result is not None:
            return "void" if m.result["winner"] is None else "settled"
        if now >= m.closes_at:
            return "closed"
        return "halted" if m.id in self.halted else "open"


def venue_with(*markets, people=("a", "b", "c"), points=1000, kind="human") -> Venue:
    v = Venue(FakeSource(*markets))
    for p in people:
        v.open_account(Account(p, kind), points)
    v.tick(T0)
    return v


def settle_all(v: Venue, now: int) -> None:
    for c in v.contracts.values():
        assert not v.clearing.audit(v.gateway.live_orders()), v.clearing.audit(v.gateway.live_orders())
    v.tick(now)


# ---------------------------------------------------------------- matching


class BookTest(unittest.TestCase):
    def test_best_price_first_then_first_come_first_served_at_the_resting_price(self):
        b = Book("c")
        b.submit(order("m1", "yes", 60, 10, "m1"), 1)
        b.submit(order("m2", "yes", 60, 10, "m2"), 2)
        b.submit(order("m3", "yes", 61, 10, "m3"), 3)
        fills = b.submit(order("t", "no", 40, 25, "t"), 4)
        self.assertEqual([(f.maker.id, f.yes_price, f.qty) for f in fills], [("m3", 61, 10), ("m1", 60, 10),
                                                                           ("m2", 60, 5)])
        self.assertEqual(b.bid(), 60)
        self.assertEqual(b.size_at("yes", 60), 5)

    def test_a_no_bid_at_40_is_a_yes_offer_at_60(self):
        b = Book("c")
        b.submit(order("n", "no", 40, 5, "n"), 1)
        self.assertEqual((b.bid(), b.ask()), (None, 60))
        self.assertEqual(b.submit(order("low", "yes", 59, 5, "x"), 2), [])
        fills = b.submit(order("hit", "yes", 70, 5, "y"), 3)
        self.assertEqual([(f.yes_price, f.qty, f.yes_account, f.no_account) for f in fills], [(60, 5, "y", "n")])

    def test_post_only_never_takes_and_ioc_never_rests(self):
        b = Book("c")
        b.submit(order("n", "no", 40, 5, "n"), 1)
        po = order("po", "yes", 60, 5, "x", post_only=True)
        self.assertEqual(b.submit(po, 2), [])
        self.assertEqual(po.status, "cancelled")
        ioc = order("ioc", "yes", 60, 8, "y", tif="ioc")
        self.assertEqual(sum(f.qty for f in b.submit(ioc, 3)), 5)
        self.assertEqual((ioc.status, b.bid()), ("cancelled", None))

    def test_a_firm_cannot_trade_with_itself(self):
        b = Book("c")
        b.submit(order("n", "no", 40, 5, "desk1", firm="bank"), 1)
        wash = order("w", "yes", 60, 5, "desk2", firm="bank")
        self.assertEqual(b.submit(wash, 2), [])
        self.assertEqual(wash.reason, "self-trade prevented")

    def test_the_book_is_never_left_crossed(self):
        rng, b = random.Random(3), Book("c")
        for k in range(3000):
            b.submit(order(f"o{k}", rng.choice(("yes", "no")), rng.randint(1, 99), rng.randint(1, 20),
                           f"a{rng.randrange(30)}", tif=rng.choice(("gtc", "ioc"))), k)
            if k % 7 == 0 and b.resting:
                b.cancel(rng.choice(list(b.resting)), "test")
            self.assertFalse(b.locked_or_crossed(), (b.bid(), b.ask()))


# ---------------------------------------------------------------- money


class MoneyTest(unittest.TestCase):
    def setUp(self):
        kalshi_fees(self)

    def test_fees_are_off_unless_switched_on(self):
        with mock.patch.object(venue_ledger, "FEE_RATE_X100", 0):
            m = FakeMarket("pulse")
            v = venue_with(m, points=10_000)
            self.trade(v, T0, ("b", "pulse", "no", 40, 10))
            self.trade(v, T0 + DELAY + MIN_REST_MS, ("a", "pulse", "yes", 60, 10))
            self.assertEqual((v.clearing.cash("a"), v.clearing.cash("b")), (9_400, 9_600))
            self.assertEqual(v.clearing.ledger.get("fees"), 0)
            self.assertEqual(fee_cap(10), 0)

    def test_fees_follow_kalshis_curve(self):
        self.assertEqual(ceil_div(fee_units(1, 50), FEE_DIV), 2)      # 1.75 rounds up
        self.assertEqual(ceil_div(fee_units(100, 50), FEE_DIV), 175)  # $1.75 per 100 contracts at 50c
        self.assertEqual(ceil_div(fee_units(100, 1), FEE_DIV), 7)     # 7c per 100 at 1c
        self.assertEqual(fee_cap(10), 18)

    def trade(self, v, now, *orders):
        got = [v.gateway.submit(*o, now) for o in orders]
        v.tick(now + DELAY)
        return got

    def test_a_winning_contract_pays_100_and_only_the_taker_pays_a_fee(self):
        m = FakeMarket("pulse")
        v = venue_with(m, points=10_000)
        self.trade(v, T0, ("b", "pulse", "no", 40, 10))       # B rests NO at 40 (a YES offer at 60)
        self.trade(v, T0 + DELAY + MIN_REST_MS, ("a", "pulse", "yes", 65, 10))  # A takes it at 60
        fee = ceil_div(fee_units(10, 60), FEE_DIV)
        self.assertEqual((v.clearing.cash("a"), v.clearing.cash("b")), (10_000 - 600 - fee, 10_000 - 400))
        m.finish("over", T0 + 11 * MIN)
        v.tick(T0 + 11 * MIN)
        self.assertEqual((v.clearing.cash("a"), v.clearing.cash("b")), (10_400 - fee, 9_600))
        self.assertEqual(v.clearing.ledger.get("fees"), fee)
        self.assertEqual(v.clearing.ledger.get("pool:pulse"), 0)
        self.assertEqual(v.clearing.audit(v.gateway.live_orders()), [])

    def test_a_holder_sells_by_buying_the_other_side_and_the_pair_pays_out_at_once(self):
        m = FakeMarket("pulse")
        v = venue_with(m, points=10_000)
        t = T0
        self.trade(v, t, ("b", "pulse", "no", 40, 10))
        t += DELAY + MIN_REST_MS
        self.trade(v, t, ("a", "pulse", "yes", 60, 10))        # A buys 10 YES at 60
        t += DELAY + MIN_REST_MS
        self.trade(v, t, ("c", "pulse", "yes", 70, 10))        # C bids YES at 70
        t += DELAY + MIN_REST_MS
        self.trade(v, t, ("a", "pulse", "no", 30, 10))         # A sells: buys NO at 30 against it
        f1, f2 = ceil_div(fee_units(10, 60), FEE_DIV), ceil_div(fee_units(10, 30), FEE_DIV)
        self.assertEqual(v.clearing.holding("a", "pulse"), (0, 0))
        self.assertEqual(v.clearing.cash("a"), 10_000 - 600 + 700 - f1 - f2)  # bought at 60, sold at 70
        self.assertEqual(v.clearing.audit(v.gateway.live_orders()), [])

    def test_void_returns_what_positions_cost_and_refunds_fees(self):
        m = FakeMarket("pulse")
        v = venue_with(m, points=10_000)
        self.trade(v, T0, ("b", "pulse", "no", 40, 10))
        self.trade(v, T0 + DELAY + MIN_REST_MS, ("a", "pulse", "yes", 60, 10))
        self.assertEqual(v.clearing.holding("a", "pulse"), (10, 0))
        m.finish(None, T0 + 11 * MIN)
        v.tick(T0 + 11 * MIN)
        self.assertEqual((v.clearing.cash("a"), v.clearing.cash("b")), (10_000, 10_000))
        self.assertEqual(v.clearing.ledger.get("fees"), 0)

    def test_random_trading_keeps_every_invariant_through_settle_and_void(self):
        binary = FakeMarket("pulse")
        race = FakeMarket("hubs", options=("kx", "oxford", "bank"), sub="hub_surge")
        people = [f"p{k}" for k in range(25)]
        v = venue_with(binary, race, people=people, points=10_000)
        rng = random.Random(9)
        cids = list(v.contracts)
        for step in range(240):
            now = T0 + step * 2_000
            for p in rng.sample(people, 6):
                v.gateway.submit(p, rng.choice(cids), rng.choice(("yes", "no")), rng.randint(30, 70),
                                 rng.randint(1, 20), now, rng.choice(("gtc", "ioc")))
            mine = [o for o in v.gateway.live_orders() if o.status == "open"]
            for o in rng.sample(mine, min(3, len(mine))):
                v.gateway.cancel(o.account, o.id, now)
            v.tick(now)
            self.assertEqual(v.clearing.audit(v.gateway.live_orders()), [], f"step {step}")
        self.assertGreater(sum(len(b.tape) for b in v.books.values()), 50)
        binary.finish("under", T0 + 11 * MIN)
        race.finish(None, T0 + 11 * MIN)
        v.tick(T0 + 11 * MIN)
        cl = v.clearing
        self.assertEqual(cl.audit(v.gateway.live_orders()), [])
        self.assertEqual(sum(cl.cash(p) for p in people) + cl.ledger.get("fees"), 10_000 * len(people))
        self.assertEqual(sum(cl.pnl(p) for p in people), -cl.ledger.get("fees"), "zero-sum apart from fees")
        self.assertTrue(all(v == 0 for k, v in cl.ledger.bal.items() if k.startswith(("pool:", "held:"))))


# ---------------------------------------------------------------- fairness and orderliness


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.m = FakeMarket("pulse", probs={"over": 0.5, "under": 0.5})
        self.v = venue_with(self.m)
        self.v.open_account(Account("bot", "retail", latency_ms=AI_LATENCY_MS), 1000)

    def test_humans_and_ai_wait_the_same_speed_bump_and_the_ai_also_carries_latency(self):
        v = self.v
        h = v.gateway.submit("a", "pulse", "yes", 40, 1, T0)
        ai = v.gateway.submit("bot", "pulse", "yes", 40, 1, T0)
        self.assertEqual((h.status, ai.status), ("pending", "pending"))
        v.tick(T0 + DELAY - 1)
        self.assertEqual((h.status, ai.status), ("pending", "pending"))
        v.tick(T0 + DELAY)
        self.assertEqual((h.status, ai.status), ("open", "pending"))
        v.tick(T0 + DELAY + AI_LATENCY_MS)
        self.assertEqual(ai.status, "open")

    def test_post_only_quotes_skip_the_speed_bump_but_orders_that_can_take_still_wait(self):
        v = self.v
        quote = v.gateway.submit("bot", "pulse", "yes", 40, 5, T0, post_only=True)
        taker = v.gateway.submit("a", "pulse", "no", 60, 4, T0, tif="ioc")
        v.tick(T0 + AI_LATENCY_MS)
        self.assertEqual((quote.status, taker.status), ("open", "pending"), "the quote rests at once, latency only")
        v.tick(T0 + DELAY)
        self.assertEqual((taker.status, taker.filled), ("filled", 4))

    def test_a_holder_can_always_sell_even_at_the_position_limit(self):
        v = self.v
        v.open_account(Account("maker", "human"), 100_000)
        v.gateway.submit("maker", "pulse", "no", 50, 50, T0)               # YES offered at 50
        v.gateway.submit("maker", "pulse", "yes", 48, 50, T0)              # and bid at 48
        v.tick(T0 + DELAY)
        buy = v.gateway.submit("a", "pulse", "yes", 50, 4, T0 + DELAY, tif="ioc")   # 200 of a's 250 limit
        v.tick(T0 + 2 * DELAY)
        self.assertEqual((buy.status, v.clearing.holding("a", "pulse")), ("filled", (4, 0)))
        more = v.gateway.submit("a", "pulse", "yes", 50, 2, T0 + 2 * DELAY, tif="ioc")
        self.assertIn("position limit", more.reason, "adding risk past the limit is refused")
        sell = v.gateway.submit("a", "pulse", "no", 52, 4, T0 + 2 * DELAY, tif="ioc")  # sell all 4 at 48
        self.assertEqual(sell.status, "pending", sell.reason)
        v.tick(T0 + 3 * DELAY)
        self.assertEqual(v.clearing.holding("a", "pulse"), (0, 0))
        self.assertEqual(v.clearing.audit(v.gateway.live_orders()), [])

    def test_cancels_are_immediate_but_a_resting_order_must_rest_a_second(self):
        v = self.v
        pending = v.gateway.submit("a", "pulse", "yes", 40, 1, T0)
        self.assertTrue(v.gateway.cancel("a", pending.id, T0)[0])
        rest = v.gateway.submit("a", "pulse", "yes", 40, 1, T0)
        v.tick(T0 + DELAY)
        self.assertFalse(v.gateway.cancel("a", rest.id, T0 + DELAY + MIN_REST_MS - 1)[0])
        self.assertTrue(v.gateway.cancel("a", rest.id, T0 + DELAY + MIN_REST_MS)[0])
        self.assertFalse(v.gateway.cancel("b", rest.id, T0 + DELAY + MIN_REST_MS)[0], "only the owner cancels")

    def test_bad_orders_are_refused_before_anything_is_reserved(self):
        v, g = self.v, self.v.gateway
        cases = {
            "price must be": ("a", "pulse", "yes", 0, 1), "quantity must be": ("a", "pulse", "yes", 50, 0),
            "side must be": ("a", "pulse", "maybe", 50, 1), "unknown contract": ("a", "nope", "yes", 50, 1),
            "price collar": ("a", "pulse", "yes", 50 + COLLAR + 1, 1),
            "position limit": ("a", "pulse", "yes", 60, 5),      # 300 points > 25% of 1000
            "unknown account": ("ghost", "pulse", "yes", 50, 1),
        }
        for why, args in cases.items():
            o = g.submit(*args, T0)
            self.assertEqual(o.status, "rejected", why)
            self.assertIn(why, o.reason)
        self.assertEqual(v.clearing.held("a"), 0)
        self.assertEqual(g.submit("a", "pulse", "yes", 50 - COLLAR - 5, 1, T0).status, "pending", "passive is fine")

    def test_message_budgets_cap_each_account_per_tick(self):
        g = self.v.gateway
        got = [g.submit("bot", "pulse", "yes", 10, 1, T0).status for _ in range(BUDGET["retail"] + 1)]
        self.assertEqual(got[-1], "rejected")
        self.v.tick(T0 + 1)
        self.assertEqual(g.submit("bot", "pulse", "yes", 10, 1, T0 + 1).status, "pending")

    def test_a_halt_cancels_every_order_and_a_close_stops_trading(self):
        v = self.v
        rest = v.gateway.submit("a", "pulse", "yes", 40, 1, T0)
        queued = v.gateway.submit("b", "pulse", "no", 40, 1, T0 + DELAY)
        v.tick(T0 + DELAY)
        v.src.halted.add("pulse")
        v.tick(T0 + DELAY + 1)
        self.assertEqual((rest.status, queued.status), ("cancelled", "cancelled"))
        self.assertEqual(v.gateway.submit("a", "pulse", "yes", 40, 1, T0 + DELAY + 2).reason, "market halted")
        v.src.halted.clear()
        late = v.gateway.submit("a", "pulse", "yes", 40, 1, self.m.closes_at - 1_000)
        v.tick(self.m.closes_at)
        self.assertEqual(late.status, "cancelled")
        self.assertEqual(v.gateway.submit("a", "pulse", "yes", 40, 1, self.m.closes_at).reason, "market closed")
        self.assertEqual(v.clearing.held("a") + v.clearing.held("b"), 0)

    def test_agents_see_the_public_book_and_never_orders_or_identities(self):
        v = self.v
        v.gateway.submit("a", "pulse", "yes", 40, 3, T0)
        v.tick(T0 + DELAY)
        v.gateway.submit("b", "pulse", "no", 45, 2, T0 + DELAY)  # pending: must not show
        c = v.snapshot(T0 + DELAY)["contracts"]["pulse"]
        self.assertEqual((c["bid"], c["bidSize"], c["ask"]), (40, 3, None))
        self.assertFalse({"account", "accounts", "orders", "pending", "firm"} & set(c))


# ---------------------------------------------------------------- agents


class AgentTest(unittest.TestCase):
    def test_market_makers_quote_both_sides_lean_against_inventory_and_meet_obligations(self):
        m = FakeMarket("pulse", probs={"over": 0.6, "under": 0.4})
        v = venue_with(m, people=())
        mm = venue_agents.MarketMaker("mm1", random.Random(1), half=2, size=20)
        v.add_agent(mm, 50_000)
        for k in range(1, 16):
            v.tick(T0 + k * 1_000)
        book = v.books["pulse"]
        self.assertEqual((book.bid(), book.ask()), (58, 62))
        self.assertGreater(v.contracts["pulse"].quoted_ok["mm1"], 0)
        v.clearing.pos("mm1", "pulse").yes = 150  # long YES: it should shade both quotes down
        bid, ask = mm.quote_for(v, v.snapshot(T0 + 9_000)["contracts"]["pulse"], T0 + 9_000).values()
        v.clearing.pos("mm1", "pulse").yes = 0
        self.assertLess(bid, 58)
        self.assertLess(100 - ask, 62)

    def test_one_sided_flow_flags_a_fast_market_and_market_makers_widen_against_it(self):
        m = FakeMarket("pulse", probs={"over": 0.5, "under": 0.5})
        v = venue_with(m, people=("maker", "informed"), points=100_000)
        bound = venue_agents.MarketMaker("bound", random.Random(1), half=2.0, designated=True)
        v.add_agent(bound, 200_000)
        v.gateway.submit("maker", "pulse", "no", 45, 200, T0)          # deep YES offer at 55
        v.tick(T0 + DELAY)
        calm = bound.quote_for(v, v.snapshot(T0 + DELAY)["contracts"]["pulse"], T0 + DELAY)
        self.assertFalse(v.fast("pulse", T0 + DELAY))
        for k in range(5):                                             # someone keeps buying YES
            v.gateway.submit("informed", "pulse", "yes", 60, 10, T0 + DELAY + k * 1_000, tif="ioc")
        v.tick(T0 + 2 * DELAY + 5_000)
        now = T0 + 2 * DELAY + 5_000
        self.assertTrue(v.fast("pulse", now), "one-sided flow is announced as a fast market")
        c = v.snapshot(now)["contracts"]["pulse"]
        bound.last_f["pulse"] = 50.0
        toxic = bound.quote_for(v, c, now)
        spread = lambda q: (100 - q["no"]) - q["yes"]  # noqa: E731
        self.assertGreater(spread(toxic), spread(calm))
        self.assertLessEqual(spread(toxic), venue.MAX_SPREAD_FAST)

    def test_a_designated_market_maker_never_quotes_wider_than_its_obligation(self):
        m = FakeMarket("pulse", probs={"over": 0.5, "under": 0.5}, closes=T0 + 60_000)
        v = venue_with(m, people=())
        free = venue_agents.MarketMaker("free", random.Random(1), half=2.5)
        bound = venue_agents.MarketMaker("bound", random.Random(1), half=2.5, designated=True)
        for mm in (free, bound):
            v.add_agent(mm, 200_000)
        c = v.snapshot(T0 + 1_000)["contracts"]["pulse"]
        for mm in (free, bound):
            mm.last_f["pulse"] = 44.0  # the forecast just jumped 6 points, and the market closes within 90 s
        spread = {mm.id: (100 - q["no"]) - q["yes"] for mm in (free, bound)
                  for q in [mm.quote_for(v, c, T0 + 1_000)]}
        self.assertGreater(spread["free"], venue.MAX_SPREAD)
        self.assertLessEqual(spread["bound"], venue.MAX_SPREAD)

    def test_a_market_maker_that_meets_its_obligations_earns_a_share_of_the_taker_fees(self):
        kalshi_fees(self)
        m = FakeMarket("pulse", probs={"over": 0.5, "under": 0.5})
        v = venue_with(m, people=("taker",), points=100_000)
        steady = venue_agents.MarketMaker("steady", random.Random(1), half=2, size=40)
        v.add_agent(steady, 200_000)
        t = T0
        for _ in range(30):
            t += 1_000
            v.tick(t)
        for k in range(6):
            v.gateway.submit("taker", "pulse", "yes" if k % 2 else "no", 60, 5, t, tif="ioc")
            for _ in range(8):
                t += 1_000
                v.tick(t)
        c = v.contracts["pulse"]
        self.assertGreaterEqual(c.quoted_ok["steady"] / c.open_ticks, venue.UPTIME)
        fees = v.clearing.fees_by_contract["pulse"]
        self.assertGreater(fees, 0)
        before = v.clearing.cash("steady")
        m.finish("over", m.settles_at)
        v.tick(m.settles_at)
        rebate = sum(e[3] for e in v.clearing.ledger.entries if e[4] == "maker rebate pulse")
        self.assertEqual(rebate, int(fees * 0.25))
        self.assertGreater(v.clearing.cash("steady"), before)
        self.assertEqual(v.clearing.audit(v.gateway.live_orders()), [])

    def test_a_fund_buys_only_when_its_edge_beats_price_plus_fee(self):
        m = FakeMarket("pulse", probs={"over": 0.5, "under": 0.5})
        v = venue_with(m, people=("maker",), points=50_000)
        v.gateway.submit("maker", "pulse", "no", 45, 50, T0)  # YES offered at 55
        v.tick(T0 + DELAY)
        f = venue_agents.Fund("f1", random.Random(1), "tier 2", ("model",), weight=0, noise=0, kelly=0.3,
                              margin=0.01, every=1, start_after=0)
        v.add_agent(f, 8_000)
        m.probs = {"over": 0.56, "under": 0.44}  # 56% < 55 + fee + margin: no trade
        v.tick(T0 + DELAY + 1_000)
        self.assertFalse(v.gateway.open_for("f1", "pulse"))
        m.probs = {"over": 0.70, "under": 0.30}
        v.tick(T0 + DELAY + 2_000)
        self.assertTrue(v.gateway.open_for("f1", "pulse"))

    def test_a_fund_that_loses_half_its_capital_is_wound_down_and_replaced_once_investors_can(self):
        m = FakeMarket("pulse")
        v = venue_with(m, people=())
        venue_agents.populate(v, market_makers=1, funds={"tier 3": 2}, retail=0, seed=1)
        fund = next(a for a in v.agents if isinstance(a, venue_agents.Fund))
        pool = venue_agents.investors("tier 3")
        v.clearing.transfer(fund.id, "elsewhere", 1500, "test loss")
        v.tick(T0 + 1_000)
        self.assertTrue(fund.retired)
        self.assertEqual((v.clearing.cash(fund.id), v.clearing.cash(pool)), (0, 500))
        relaunched = lambda: [a for a in v.agents if isinstance(a, venue_agents.Fund) and "-r" in a.id]  # noqa: E731
        self.assertFalse(relaunched(), "500 points can't start a 2,000-point fund")
        v.clearing.grant(pool, 1500)  # other funds' redemptions
        v.tick(T0 + 2_000)
        self.assertEqual(len(relaunched()), 1)
        self.assertEqual((v.clearing.cash(relaunched()[0].id), v.clearing.cash(pool)), (2000, 0))


class CityRoundTest(unittest.TestCase):
    """The venue on the real market lifecycle: listing, futures, halts and settlement from recorded data."""

    def setUp(self):
        self.raw = Raw()
        self.addCleanup(self.raw.dir.cleanup)
        city(self.raw, T0 + 25 * MIN)

    def run_venue(self, retail: int, funds: dict, until: int = T0 + 16 * MIN, step: int = 5_000):
        store = Store(self.raw.root, SPOTS)
        store.refresh(T0 - 3 * 3_600_000, T0)
        store.ingest_vix(vix_payload(T0 - 2 * 3_600_000, T0 + 25 * MIN))
        v = Venue(CitySource(ShadowExchange(store, Clock(T0), SPOTS)), seed=3)
        venue_agents.populate(v, market_makers=4, designated=2, funds=funds, retail=retail, seed=5, now=T0)
        for now in range(T0, until, step):
            v.tick(now)
        return v

    def test_a_full_round_lists_trades_closes_and_settles_with_the_books_balanced(self):
        v = self.run_venue(retail=120, funds={"tier 1": 1, "tier 2": 8, "tier 3": 8})
        self.assertTrue(v.contracts)
        self.assertGreater(sum(len(b.tape) for b in v.books.values()), 0)
        self.assertTrue(v.settled, "the first round settled")
        for c in v.contracts.values():
            for f in v.books[c.id].tape:
                self.assertLess(f.at, c.market.closes_at, "no trade after close")
            if c.done or c.closed:
                self.assertFalse(v.books[c.id].resting)
        self.assertEqual(v.clearing.audit(v.gateway.live_orders()), [])
        stats = v.stats()
        self.assertEqual(stats["audit"], "ok")
        self.assertTrue(any(u for u in stats["marketMakerUptime"].values()))

    def test_api_and_data_calls_do_not_grow_with_the_number_of_agents(self):
        calls = {"refresh": 0, "decide": 0}
        refresh, decide, picks = Store.refresh, traders.decide, tops.picks
        in_tops = [False]

        def counting_refresh(self, *a, **k):
            calls["refresh"] += 1
            return refresh(self, *a, **k)

        def counting_decide(*a, **k):
            calls["decide"] += 0 if in_tops[0] else 1
            return decide(*a, **k)

        def tops_picks(*a, **k):  # the TOPS loop's one view per trader per market at open, not the venue's rules
            in_tops[0] = True
            try:
                return picks(*a, **k)
            finally:
                in_tops[0] = False

        seen = []
        with mock.patch.object(Store, "refresh", counting_refresh), mock.patch.object(traders, "decide",
                                                                                      counting_decide), \
                mock.patch.object(tops, "picks", tops_picks):
            for retail, scale in ((20, 1), (400, 5)):
                calls.update(refresh=0, decide=0)
                v = self.run_venue(retail=retail, funds={"tier 1": 1, "tier 2": 2 * scale, "tier 3": 2 * scale},
                                   until=T0 + 6 * MIN)
                seen.append(dict(calls))
                # each rule is evaluated at most once per market per window, however many funds run it
                self.assertEqual(calls["decide"], len(v.cache))
                windows = 6 * MIN // venue_agents.PICK_TTL_MS + 1
                self.assertLessEqual(len(v.cache), len(venue_agents.STRATEGIES) * len(v.listed) * windows)
        self.assertEqual(seen[0]["refresh"], seen[1]["refresh"], "data reads don't depend on the population")

    def test_agent_code_has_no_network_access(self):
        banned = {"urllib", "http", "socket", "requests", "anthropic", "httpx"}
        for name in ("venue.py", "venue_agents.py", "venue_book.py", "venue_gateway.py", "venue_ledger.py"):
            tree = ast.parse((HERE / name).read_text())
            mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertFalse(mods & banned, name)


if __name__ == "__main__":
    unittest.main()
