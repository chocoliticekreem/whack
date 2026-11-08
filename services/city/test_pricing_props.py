"""Property tests of the pricing engine (docs/pricing-engine.md): the LMSR invariants over thousands of random bet
sequences, the filter's exact probabilities against simulation, and an adversary against a local Exchange.
Seeded, so every run checks the same cases: python3 -m unittest discover -s services/city"""
from __future__ import annotations

import math
import random
import unittest
import zlib
from unittest import mock

import markets as mk
import pricing
from exchange import Clock, Exchange
from pricing import FEE, LMSR_B, Book, locked_odds
from data import Store
from test_city import MIN, SPOTS, T0, CityCase, city, crowding

CASES = 2000


def random_model(rng: random.Random, n: int) -> dict[str, float]:
    """A model as Futures.probs makes them: every option in [3%, 97%] before renormalising."""
    raw = [rng.uniform(pricing.P_FLOOR, 1.0) ** rng.choice((1, 3)) for _ in range(n)]
    floored = [max(pricing.P_FLOOR / 2, v / sum(raw)) for v in raw]
    return {f"o{k}": v / sum(floored) for k, v in enumerate(floored)}


def payout(stake: float, shares: float) -> int | None:
    """What the ledger pays a winning bet: stake × locked odds, floored to a whole point (None: refused)."""
    odds = locked_odds(shares, stake)
    return None if odds is None else math.floor(stake * odds)


class Bets:
    """A house book under a fixed model: every accepted bet's stake and locked payout, per option."""

    def __init__(self, model: dict[str, float]):
        self.model, self.book = model, Book(list(model))
        self.staked, self.paid = 0.0, {o: 0 for o in model}

    def bet(self, option: str, stake: float) -> bool:
        shares = self.book.quote(self.model, option, stake)
        pay = payout(stake, shares)
        if pay is None:
            return False
        self.book.commit(option, shares)
        self.staked += stake
        self.paid[option] += pay
        return True

    def loss(self, winner: str) -> float:
        return self.paid[winner] - self.staked


class LmsrInvariantTest(unittest.TestCase):
    def test_prices_sum_to_one_and_each_is_a_probability(self):
        rng = random.Random(1)
        for _ in range(CASES):
            model = random_model(rng, rng.randint(2, 6))
            book = Book(list(model))
            for o in book.q:
                book.q[o] = rng.choice((0.0, rng.uniform(-5e3, 5e3), rng.uniform(-1e9, 1e9)))
            p = book.prices(model)
            self.assertAlmostEqual(sum(p.values()), 1.0, places=12)
            self.assertTrue(all(0.0 <= v <= 1.0 for v in p.values()), p)

    def test_a_bet_costs_exactly_its_stake_net_of_the_fee_on_the_cost_function(self):
        rng = random.Random(2)
        for _ in range(CASES):
            model = random_model(rng, rng.randint(2, 6))
            book = Book(list(model))
            for o in book.q:
                book.q[o] = rng.uniform(0, 3000)
            option, stake = rng.choice(list(model)), rng.uniform(1, 5000)
            before = book.cost(model)
            book.buy(model, option, stake)
            self.assertAlmostEqual(book.cost(model) - before, stake * (1 - FEE), delta=1e-9 * stake + 1e-9)

    def test_cost_is_path_independent(self):
        """Any order of the same share purchases costs the same, and so does buying them all at once:
        Σ spends = C(q_end) − C(0) whatever the path."""
        rng = random.Random(3)
        for _ in range(CASES // 4):
            model = random_model(rng, rng.randint(2, 5))
            book = Book(list(model))
            lots = []
            for _ in range(rng.randint(1, 12)):
                o = rng.choice(list(model))
                lots.append((o, book.buy(model, o, rng.uniform(1, 400))))
            spent = book.cost(model)
            shuffled, total = Book(list(model)), 0.0
            for o, shares in rng.sample(lots, len(lots)):
                c0 = shuffled.cost(model)
                shuffled.commit(o, shares)
                total += shuffled.cost(model) - c0
            self.assertAlmostEqual(total, spent, delta=1e-7 * spent + 1e-9)
            joint = Book(list(model))
            for o, shares in lots:
                joint.q[o] += shares
            self.assertAlmostEqual(joint.cost(model), spent, delta=1e-7 * spent + 1e-9)

    def test_house_loss_never_exceeds_b_ln_one_over_the_winners_prior_less_the_fees(self):
        """Worst case on outcome j: b·ln(1/m_j) − FEE·Σstakes, so at most b·ln(n) on a uniform prior. Sequences mix
        random bets with an adversary piling onto the longest shot, where the bound binds."""
        rng = random.Random(4)
        worst = 0.0
        for case in range(CASES):
            n = rng.randint(2, 6)
            model = {f"o{k}": 1 / n for k in range(n)} if case % 3 == 0 else random_model(rng, n)
            bets = Bets(model)
            target = min(model, key=model.get)
            for _ in range(rng.randint(1, 40)):
                o = target if rng.random() < 0.5 else rng.choice(list(model))
                bets.bet(o, rng.choice((rng.uniform(1, 250), rng.uniform(250, 20000))))
            for j in model:
                bound = LMSR_B * math.log(1 / model[j]) - FEE * bets.staked
                self.assertLessEqual(bets.loss(j), bound + 1e-6, (model, j))
                worst = max(worst, bets.loss(j) / (LMSR_B * math.log(1 / model[j])))
            if case % 3 == 0:
                self.assertLessEqual(max(bets.loss(j) for j in model), LMSR_B * math.log(n))
        self.assertGreater(worst, 0.5, "the adversary gets near the bound, so the test can see a breach")

    def test_the_bound_is_tight_on_shares_without_the_odds_cap(self):
        """Liability in shares (no 20x cap, no rounding): buying j without limit takes the house to b·ln(1/m_j)."""
        model = {"a": 0.03, "b": 0.97}
        book = Book(list(model))
        book.buy(model, "a", 1e6)
        loss = book.q["a"] - book.cost(model)
        self.assertAlmostEqual(loss, LMSR_B * math.log(1 / 0.03), delta=1e-6)
        self.assertAlmostEqual(LMSR_B * math.log(2), 831.8, places=1)

    def test_no_bundle_of_bets_locks_a_profit(self):
        """Buying every option, in any amounts and order, pays less than it costs on every outcome: the fee, the
        floor-rounded odds, the 20x cap and the 1.05x floor only ever lower payouts below the shares bought."""
        rng = random.Random(5)
        for _ in range(CASES):
            model = random_model(rng, rng.randint(2, 6))
            bets = Bets(model)
            for _ in range(rng.randint(len(model), 30)):
                bets.bet(rng.choice(list(model)), rng.uniform(1, 2000))
            if bets.staked:
                self.assertLess(min(bets.paid.values()), bets.staked, model)

    def test_a_locked_payout_never_exceeds_the_shares_bought(self):
        rng = random.Random(6)
        for _ in range(CASES * 5):
            stake = rng.choice((rng.randint(1, 100), rng.randint(100, 100_000)))
            shares = stake * rng.uniform(0.5, 40)
            pay = payout(stake, shares)
            if shares / stake < pricing.MIN_ODDS:
                self.assertIsNone(pay)
            elif pay is not None:
                self.assertLessEqual(pay, shares)
                self.assertLessEqual(pay, stake * pricing.MAX_ODDS)

    def test_extreme_positions_and_stakes_stay_finite_and_monotone(self):
        model = {"a": 0.03, "b": 0.5, "c": 0.47}
        for qa in (0.0, 1e6, 1e9, -1e9):
            book = Book(list(model))
            book.q["a"] = qa
            p = book.prices(model)
            self.assertAlmostEqual(sum(p.values()), 1.0, places=12)
            prev = 0.0
            for stake in (1e-3, 1, 100, 1e4, 1e6, 1e9, 1e12):
                for o in model:
                    self.assertTrue(math.isfinite(book.quote(model, o, stake)), (qa, o, stake))
                shares = book.quote(model, "b", stake)
                self.assertGreater(shares, prev)
                prev = shares

    def test_a_moving_model_adds_b_ln_of_the_expected_likelihood_ratio_per_update(self):
        """When data moves the model under a fixed q, the house's worst case on j becomes
        b·ln(1/m_end,j) + Σ_updates b·ln E_price[m'/m] − FEE·Σstakes (each term 0 when the book is flat)."""
        rng = random.Random(7)
        exceeded = 0
        for _ in range(CASES // 2):
            n = rng.randint(2, 5)
            model = random_model(rng, n)
            bets, extra = Bets(model), 0.0
            for _ in range(rng.randint(1, 6)):
                for _ in range(rng.randint(0, 6)):
                    bets.bet(rng.choice(list(model)), rng.uniform(1, 3000))
                new = random_model(rng, n)
                price = bets.book.prices(model)
                extra += LMSR_B * math.log(sum(price[o] * new[o] / model[o] for o in model))
                bets.model = model = new
            for j in model:
                bound = LMSR_B * math.log(1 / model[j]) + extra - FEE * bets.staked
                self.assertLessEqual(bets.loss(j), bound + 1e-6)
                exceeded += bets.loss(j) > LMSR_B * math.log(1 / model[j]) - FEE * bets.staked + 1e-6
        self.assertGreater(exceeded, 0, "the update term is needed: the fixed-prior bound alone is breached")

    def test_each_book_reset_grants_a_fresh_loss_bound(self):
        """integrity.suspend replaces the book (q = 0): k resets let one outcome cost up to (k + 1)·b·ln(1/m_j)."""
        model = {"a": 0.5, "b": 0.5}
        bets = Bets(model)
        for reset in range(4):
            bets.book = Book(list(model))
            for _ in range(50):
                bets.bet("a", 1000)
        one_book = LMSR_B * math.log(2)
        self.assertGreater(bets.loss("a"), 3 * one_book * 0.9)
        self.assertLessEqual(bets.loss("a"), 4 * one_book)


class ExactProbabilityTest(unittest.TestCase):
    def test_capped_step_sd_matches_the_draws(self):
        rng = random.Random(8)
        draws = [pricing.capped_gauss(rng) for _ in range(200_000)]
        sd = math.sqrt(sum(d * d for d in draws) / len(draws))
        self.assertAlmostEqual(pricing.CAPPED_SD, sd, delta=0.004)

    def test_hub_hour_race_probabilities_match_simulated_futures(self):
        stations = [("a", "A", math.log(0.50), 0.001, 0.010), ("b", "B", math.log(0.45), 0.0, 0.012),
                    ("c", "C", math.log(0.40), 0.002, 0.008), ("d", "D", math.log(0.30), 0.0, 0.020),
                    ("e", "E", math.log(0.35), -0.001, 0.015)]
        typ0, typ_t = [0.5, 0.4, 0.42, 0.3, 0.33], [0.52, 0.41, 0.40, 0.31, 0.34]
        n = 6000
        with mock.patch.object(pricing, "N_FUTURES", n):
            sim = pricing.RaceSim(stations, T0, 12, random.Random(9), typ0, typ_t, "level")
            shares = {o: sum(sim.outcome(i) == o for i in range(n)) / n for o in "abcde"}
        exact = pricing.race_level_probs(sim, [s[2] for s in stations], 0)
        self.assertAlmostEqual(sum(exact.values()), 1.0, places=9)
        for o in "abcde":
            se = math.sqrt(max(shares[o] * (1 - shares[o]), 1e-4) / n)
            self.assertLess(abs(exact[o] - shares[o]), 4 * se, (o, exact[o], shares[o]))

    def test_hub_hour_prices_from_a_full_reading_and_ignores_the_paths(self):
        stations = [(h["id"], h["naptan"], math.log(0.4), 0.0, 0.01) for h in SPOTS]
        ids = [h["id"] for h in SPOTS]
        sim = pricing.RaceSim(stations, T0, 60, random.Random(10), [0.4] * 5, [0.4] * 5, "level")
        fut = pricing.Futures(sim, ids)
        snap = {h["naptan"]: v for h, v in zip(SPOTS, (0.40, 0.44, 0.38, 0.41, 0.39))}
        fut.observe(T0 + 20 * MIN, snap)
        want = pricing.smooth({o: p * pricing.N_FUTURES for o, p in pricing.race_level_probs(
            sim, [math.log(snap[h["naptan"]]) for h in SPOTS], 20).items()})
        for o, p in fut.probs().items():
            self.assertAlmostEqual(p, want[o], places=12)
        self.assertEqual(max(fut.probs(), key=fut.probs().get), SPOTS[1]["id"])

    def test_count_sim_prices_the_poisson_tail_its_futures_estimate(self):
        n = 20000
        with mock.patch.object(pricing, "N_FUTURES", n):
            sim = pricing.CountSim(9.0, [4.0] * 12, 0.47, T0, 5, random.Random(11), lambda s: s["total"], 0.0)
            sim.strike = pricing.poisson_line(sim.lam)
            share = sum(f > sim.strike for f in sim.finals) / n
        over = sim.exact(None)["over"]
        self.assertLess(abs(over - share), 4 * math.sqrt(share * (1 - share) / n))
        self.assertLess(abs(over - 0.5), 0.2, "the line sits next to the median")
        sim.posterior(T0 + 2 * MIN, {"total": 15.0})
        self.assertGreater(sim.exact(None)["over"], over)

    def test_break_sim_is_memoryless(self):
        sim = pricing.BreakSim(0.5, ["a"], T0, 60, random.Random(12))
        fut = pricing.Futures(sim, ["yes", "no"])
        self.assertAlmostEqual(sim.exact(None)["no"], 0.5, places=12)
        fut.observe(T0 + 40 * MIN, {"a": {"sev": 10}})
        self.assertAlmostEqual(sim.exact(fut.seen)["no"], 1 - 0.5 ** (20 / 60), places=12)
        fut.observe(T0 + 41 * MIN, {"a": {"sev": 6}})
        self.assertEqual(sim.exact(fut.seen), {"yes": 0.0, "no": 1.0})

    def test_poisson_line_is_the_half_point_nearest_even(self):
        for lam in (0.3, 1.0, 4.2, 9.7, 25.0):
            line = pricing.poisson_line(lam)
            self.assertEqual(line % 1, 0.5)
            over = lambda c: 1 - sum(pricing.poisson_pmf(lam, k) for k in range(int(c) + 1))  # noqa: E731
            for other in (line - 1, line + 1):
                if other >= 0.5:
                    self.assertLessEqual(abs(over(line) - 0.5), abs(over(other) - 0.5))


class SeedTest(CityCase):
    def test_a_market_id_always_builds_the_same_futures(self):
        city(self.raw, T0)
        store = self.raw.store(T0)
        for sid in ("hub_surge", "city_pulse", "heathrow_stack"):
            a = mk.SERIES[sid].build(f"{sid}-1505", store, SPOTS, T0, T0 + 5 * MIN)
            b = mk.SERIES[sid].build(f"{sid}-1505", store, SPOTS, T0, T0 + 5 * MIN)
            self.assertEqual(a.futures.probs(), b.futures.probs(), sid)

    def test_heathrow_opens_at_the_same_price_whatever_the_seed(self):
        city(self.raw, T0)
        store = self.raw.store(T0)
        got = set()
        for k in range(8):
            with mock.patch.object(mk, "seed_of", lambda mid, k=k: zlib.crc32(f"{mid}{k}".encode())):
                m = mk.build_heathrow_stack("heathrow_stack-1505", store, SPOTS, T0, T0 + 5 * MIN)
            got.add((m.spec["line"], round(m.futures.probs()["over"], 12)))
        self.assertEqual(len(got), 1, got)


class AdversaryTest(CityCase):
    """A bot that knows a TfL reading before the exchange prices it, against hub_surge (5 s delay, 10% suspend)."""

    def setUp(self):
        super().setUp()
        city(self.raw, T0)
        self.ex = Exchange(Store(self.raw.root, SPOTS), Clock(T0), SPOTS)
        self.addCleanup(self.ex.ledger.close)
        self.ex.ledger.open_account("bot")
        self.ex.tick(T0)
        self.m = next(m for m in self.ex.active() if m.sub == "hub_surge")
        self.ex.tick(T0 + 30_000)  # the integrity scan's first pass with a market listed sets its baseline reading
        self.hub = "piccadilly"

    def reading(self, ts: int, rise: float) -> None:
        """A TfL reading at ts with the bot's hub `rise` above the open reading, the others flat."""
        snap = self.ex.store.latest("crowding", T0)[1]
        vals = {h["id"]: snap[h["naptan"]] for h in SPOTS}
        vals[self.hub] *= 1 + rise
        self.raw.put("crowding", ts, crowding(vals))

    def bet(self, now: int) -> dict:
        return self.ex.bet({"player": "bot", "market": self.m.id, "option": self.hub, "stake": 100,
                            "idempotency_key": f"k{now}"}, now)

    def order(self, out: dict) -> dict:
        return self.ex.integrity.lookup(self.ex.ledger, "bot", out["betId"])

    def test_a_stale_quote_fills_at_the_price_after_the_update(self):
        t = T0 + 60_000
        stale = self.ex.quote(self.m, self.hub, 100)[1]
        self.reading(t + 2000, 0.04)
        out = self.bet(t)
        with mock.patch.object(mk, "JUMP_SIGMA", 1e9):
            self.ex.tick(t + 2000)
            fresh = self.ex.quote(self.m, self.hub, 100)[1]
            self.ex.tick(t + 5000)
        got = self.order(out)
        self.assertEqual(got["status"], "filled")
        self.assertEqual(got["odds"], fresh)
        self.assertLess(fresh, stale, "the reading made the hub likelier, so the fresh price is shorter")

    def test_a_big_move_inside_the_delay_cancels_the_bet(self):
        t = T0 + 60_000
        self.reading(t + 2000, 0.15)
        out = self.bet(t)
        self.ex.tick(t + 2000)
        self.ex.tick(t + 5000)
        self.assertEqual(self.order(out)["status"], "cancelled")
        self.assertEqual((self.ex.ledger.cash("bot"), self.ex.ledger.audit()), (1000, []))

    def test_known_limit_a_reading_ingested_after_the_fill_leaves_the_old_price(self):
        """The delay only covers readings the exchange ingests within it. TfL stamps a reading 4-7 min before the
        recorder polls it (analysis/judge/crowd_latency.py), so a bot reading TfL directly fills here at the stale
        price: the edge is the ingest lag, not the book (docs/pricing-engine.md, Known limits)."""
        t = T0 + 60_000
        stale_price, stale = self.m.prices()[self.hub], self.ex.quote(self.m, self.hub, 100)[1]
        out = self.bet(t)
        self.ex.tick(t + 5000)
        self.reading(t + 60_000, 0.04)
        with mock.patch.object(mk, "JUMP_SIGMA", 1e9):
            self.ex.tick(t + 60_000)
        self.assertEqual(self.order(out)["odds"], stale)
        self.assertGreater(self.m.prices()[self.hub], stale_price + 0.2, "the bot knew a big move before the book")


if __name__ == "__main__":
    unittest.main()
