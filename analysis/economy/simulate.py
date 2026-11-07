"""Agent-based simulation of the OverWatch points economy, as the exchange is actually coded.

    python3 analysis/economy/simulate.py              # all scenarios, ~2-4 min, writes out/
    python3 analysis/economy/simulate.py --quick      # fewer seeds and days, for a smoke test

Stdlib only. Every exchange rule below is re-implemented from services/city at commit 1053c8d and cites the lines it
mirrors (file:line). What is NOT the code is the world around it: market truths, the model's error, information
arrival and the players. Those are stated assumptions (see ASSUMPTIONS and docs/market-structure.md section 9).

Outputs (gitignored): out/results.json (every scenario's metrics), out/summary.md (the tables quoted in the doc).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

# ---------------------------------------------------------------- constants mirrored from the code

N_FUTURES = 50                 # pricing.py:14
RULED_OUT_CAP = 0.6            # pricing.py:15
P_FLOOR, P_CEIL = 0.03, 0.97   # pricing.py:17
LMSR_B = 1200.0                # pricing.py:18
FEE = 0.05                     # pricing.py:19
MIN_ODDS, MAX_ODDS = 1.05, 20.0  # pricing.py:21
GRANT = 1000                   # ledger.py:41
DEFAULT_LOSS_LIMIT = 500       # ledger.py:42
MIN_STAKE = 10                 # ledger.py:50
HOUSE_BANKROLL = 100_000       # ledger.py:53
EXPOSURE_CAP = 100_000         # ledger.py:54
POSITION_CAP = 0.25            # exchange.py:20
AI_START = 1000                # exchange.py:19 (DEFAULT_BANKROLL), exchange.py:58
AI_STAKE = 100                 # traders.py:16
ROUNDS_PER_DAY = 144           # 12 playing hours x 12 five-minute rounds (exchange.py:17, ROUND_MS)
STEPS = 5                      # one step per minute of the ~5-minute betting window (README "Rounds and series")

ASSUMPTIONS = {
    "binary favourite at open": "uniform 0.50-0.80 (live /stats open-favourite histogram: 0.20-0.80, 77% in 30-70%)",
    "5-way truth": "Dirichlet(4) for hub_surge (fav p50 0.27 in docs/questions.md), Dirichlet(1.5) for hub_hour",
    "model": "50 futures drawn from the truth (calibrated) or from truth^1.6 renormalised (overconfident scenario;"
             " docs/questions.md measured hub_hour fav 0.89 vs real 56%, clean_sheet 0.85 vs 43%)",
    "information": "each minute, futures whose outcome is wrong are ruled out with p=0.06, right ones with p=0.02,"
                   " under the 60% cap",
    "AI hit rates": "tube surge 0.36 (5-way), tube hour 0.56 (5-way), cams 0.65, bus 0.60, others 0.50"
                    " (docs/questions.md and traders.py docstrings); a miss picks a random other option",
    "rain": "Rain Radar plays City Pulse in 15% of rounds",
    "void rate": "1% of markets void and refund",
    "informed players": "log-odds noise 0.3 on the truth, plus a private signal right 65% (binary) / 40% (5-way)",
    "players": "40 at start (10 per type) + 8 per day; each plays a round with p=0.3 and a market in it with p=0.7",
}

AI_RULES = {  # trader -> {series: hit rate}; exchange.py:234-257 places them, traders.py:199-201 maps them
    "tube": {"hub_surge": 0.36, "city_pulse": 0.50, "hub_hour": 0.56},
    "cams": {"london_vix": 0.65},
    "bus": {"heathrow_stack": 0.60},
    "lines": {"clean_sheet": 0.50},
    "rain": {"city_pulse": 0.50},
}
PLAYER_TYPES = ("random", "informed", "herding", "contrarian")

# ---------------------------------------------------------------- the exchange, mirrored


def smooth(counts: list[int]) -> list[float]:
    """pricing.py:39-45: half-future prior, [3%, 97%] clamp, renormalise."""
    total = sum(counts) + 0.5 * len(counts)
    clamped = [min(P_CEIL, max(P_FLOOR, (c + 0.5) / total)) for c in counts]
    s = sum(clamped)
    return [p / s for p in clamped]


def locked_odds(shares: float, stake: float) -> float | None:
    """pricing.py:28-32: shares per point, floored to 2 dp, capped at 20; None under 1.05."""
    odds = min(Decimal(str(MAX_ODDS)), Decimal(shares / stake).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))
    return float(odds) if odds >= Decimal(str(MIN_ODDS)) else None


def payout_of(stake: int, odds: float) -> int:
    """ledger.py:174-176."""
    return int(Decimal(str(odds)) * stake)


class Book:
    """pricing.py:321-347: LMSR on top of the model, prob_i ∝ model_i·e^(q_i/b); the fee comes off the stake."""

    def __init__(self, n: int, b: float, fee: float):
        self.b, self.fee, self.q = b, fee, [0.0] * n

    def prices(self, model: list[float]) -> list[float]:
        w = [m * math.exp(q / self.b) for m, q in zip(model, self.q)]
        s = sum(w)
        return [x / s for x in w]

    def quote(self, model: list[float], i: int, stake: float) -> float:
        spend = stake * (1 - self.fee)
        w = [m * math.exp(q / self.b) for m, q in zip(model, self.q)]
        s = sum(w)
        return self.b * math.log((w[i] + s * (math.exp(spend / self.b) - 1)) / w[i])


class Market:
    def __init__(self, mid: int, series: str, p_true: list[float], model_p: list[float], rng: random.Random,
                 b: float, fee: float):
        self.id, self.series, self.n = mid, series, len(p_true)
        self.p_true = p_true
        self.winner = rng.choices(range(self.n), weights=p_true)[0]
        self.futures = [rng.choices(range(self.n), weights=model_p)[0] for _ in range(N_FUTURES)]
        self.alive = [True] * N_FUTURES
        self.ruled_out = 0
        self.book = Book(self.n, b, fee)
        self.open_model = self.model()
        self.bets: list[dict] = []   # player bets (ledgered)
        self.ai_bets: list[dict] = []
        self.void = rng.random() < 0.01

    def model(self) -> list[float]:
        """pricing.py:313-318."""
        counts = [0] * self.n
        for i, o in enumerate(self.futures):
            if self.alive[i]:
                counts[o] += 1
        return smooth(counts)

    def observe(self, rng: random.Random) -> None:
        """pricing.py:303-311: rule out disagreeing futures, never more than 60% in total."""
        bad = [i for i, o in enumerate(self.futures) if self.alive[i]
               and rng.random() < (0.02 if o == self.winner else 0.06)]
        room = int(RULED_OUT_CAP * N_FUTURES) - self.ruled_out
        for i in bad[:max(0, room)]:
            self.alive[i] = False
        self.ruled_out += min(len(bad), max(0, room))

    def prices(self) -> list[float]:
        return self.book.prices(self.model())   # markets.py:129-130

    def quote(self, i: int, stake: float) -> tuple[float, float] | None:
        """exchange.py:176-185."""
        shares = self.book.quote(self.model(), i, stake)
        odds = locked_odds(shares, stake)
        return None if odds is None else (shares, odds)


def worst_case(markets: list[Market], extra: tuple | None = None) -> int:
    """ledger.py:432-446: per market, the worst single outcome over open player bets, netting the other stakes."""
    total = 0
    for m in markets:
        per: dict[int, list[int]] = {}
        rows = [(b["option"], b["stake"], b["payout"]) for b in m.bets]
        if extra and extra[0] is m:
            rows.append(extra[1:])
        for opt, stake, payout in rows:
            acc = per.setdefault(opt, [0, 0])
            acc[0] += payout - stake
            acc[1] += stake
        if per:
            staked = sum(v[1] for v in per.values())
            total += max(0, max(win - (staked - st) for win, st in per.values()))
    return total


class Player:
    def __init__(self, pid: int, kind: str, grant: int):
        self.id, self.kind = pid, kind
        self.cash, self.escrow, self.lost_today = grant, 0, 0
        self.granted = grant
        self.staked = self.returned = self.n_bets = 0

    @property
    def equity(self) -> int:
        return self.cash + self.escrow   # ledger.py:330-332


class Sim:
    def __init__(self, cfg: dict, seed: int):
        self.cfg, self.rng = cfg, random.Random(seed)
        self.players: list[Player] = []
        self.house_pnl = 0           # sum of the ledger's 'house' account (ledger.py:449-452)
        self.ai_points = {t: AI_START for t in list(AI_RULES) + ["contrarian"]}
        self.ai_bets = {t: 0 for t in self.ai_points}
        self.book_pnl_ai = 0         # what the house would owe if AI bets were ledgered
        self.refusals: dict[str, int] = {}
        self.round_pnl: list[int] = []
        self.daily: list[dict] = []
        self.minted = 0
        self.brier = {"open": [], "close": []}
        self.next_id = 0
        self.overconfident = cfg.get("overconfident", False)
        for kind in PLAYER_TYPES:
            for _ in range(10):
                self.join(kind)
        for _ in range(cfg.get("oracles", 0)):
            self.join("oracle")

    def join(self, kind: str) -> None:
        p = Player(self.next_id, kind, self.cfg["grant"])
        self.next_id += 1
        self.minted += self.cfg["grant"]   # ledger.py:293: the grant is the only faucet
        self.players.append(p)

    def refuse(self, why: str) -> None:
        self.refusals[why] = self.refusals.get(why, 0) + 1

    # ------------------------------------------------------------ market generation

    def lineup(self, r: int) -> list[tuple[str, list[float]]]:
        rng, out = self.rng, []

        def binary() -> list[float]:
            fav = rng.uniform(0.5, 0.8)
            return [fav, 1 - fav] if rng.random() < 0.5 else [1 - fav, fav]

        def dirichlet(alpha: float, n: int = 5) -> list[float]:
            g = [rng.gammavariate(alpha, 1) for _ in range(n)]
            s = sum(g)
            return [x / s for x in g]

        out.append(("hub_surge", dirichlet(4.0)))
        out.append(("city_pulse", binary()))
        out.append(("london_vix" if rng.random() < 0.5 else "heathrow_stack", binary()))
        if r % 12 == 11:
            out.append(("hub_hour", dirichlet(1.5)))
        if r % 12 == 5:
            out.append(("clean_sheet", binary()))
        return out

    def model_of(self, p: list[float]) -> list[float]:
        if not self.overconfident:
            return p
        w = [x ** 1.6 for x in p]
        s = sum(w)
        return [x / s for x in w]

    # ------------------------------------------------------------ betting

    def place(self, p: Player, m: Market, opt: int, stake: int, open_markets: list[Market]) -> bool:
        """exchange.py:187-226 + ledger.py:479-524, in the code's order."""
        stake = int(stake)
        if stake < MIN_STAKE:                                         # ledger.py:492-493
            self.refuse("min_stake")
            return False
        cap = max(0.0, POSITION_CAP * p.equity)                      # exchange.py:208-212
        held = sum(b["stake"] for b in m.bets if b["player"] is p)
        if held + stake > cap:
            self.refuse("position_cap")
            return False
        if stake > p.cash:                                            # ledger.py:416-418
            self.refuse("insufficient_funds")
            return False
        room = self.cfg["loss_limit"] - p.lost_today - p.escrow       # ledger.py:419-424
        if stake > room:
            self.refuse("loss_limit")
            return False
        # ledger.py:425-428: stakes over 25% of cash need a confirm token; simulated players always confirm.
        got = m.quote(opt, stake)                                     # ledger.py:502-504
        if got is None:
            self.refuse("price_too_short")
            return False
        shares, odds = got
        payout = payout_of(stake, odds)                               # ledger.py:505
        cap_now = min(EXPOSURE_CAP, HOUSE_BANKROLL + self.house_pnl)  # ledger.py:451-452
        if worst_case(open_markets, (m, opt, stake, payout)) > cap_now:  # ledger.py:506-510
            self.refuse("house_limit")
            return False
        p.cash -= stake                                               # ledger.py:519-520 bet_hold
        p.escrow += stake
        p.staked += stake
        p.n_bets += 1
        m.book.q[opt] += shares                                       # ledger.py:521-522 commit
        m.bets.append({"player": p, "option": opt, "stake": stake, "payout": payout, "odds": odds})
        return True

    def ai_bet(self, tid: str, m: Market, opt: int) -> None:
        """exchange.py:252-257: fixed 100 stake, no bankroll, cap or ledger checks; the shares move the book."""
        got = m.quote(opt, AI_STAKE)
        if got is None:
            return
        shares, odds = got
        m.book.q[opt] += shares
        m.ai_bets.append({"trader": tid, "option": opt, "odds": odds})

    def decide(self, p: Player, m: Market) -> tuple[int, int] | None:
        rng, prices = self.rng, m.prices()
        cap = int(POSITION_CAP * p.equity)
        if cap < MIN_STAKE:
            return None
        if p.kind == "random":
            return rng.randrange(m.n), rng.randint(MIN_STAKE, max(MIN_STAKE, min(100, cap)))
        if p.kind == "oracle":
            return m.winner, cap
        if p.kind == "herding":
            staked = [0.0] * m.n
            for b in m.bets:
                staked[b["option"]] += b["stake"]
            for b in m.ai_bets:
                staked[b["option"]] += AI_STAKE
            pick = max(range(m.n), key=lambda i: (staked[i], prices[i]))
            return pick, min(50, cap)
        if p.kind == "contrarian":   # the same rule as traders.py:186-196
            staked = [0.0] * m.n
            for b in m.bets:
                staked[b["option"]] += b["stake"]
            for b in m.ai_bets:
                staked[b["option"]] += AI_STAKE
            fav = max(range(m.n), key=lambda i: staked[i]) if any(staked) else max(range(m.n), key=lambda i: prices[i])
            alt = max((i for i in range(m.n) if i != fav), key=lambda i: prices[i])
            return alt, min(50, cap)
        # informed: knows the truth through noise on the log-odds, plus (unless signal=0) a private read of the feeds
        # that points at the winner with the given accuracy, like a sharp AI trader; bets the best edge, quarter Kelly
        logits = [math.log(max(1e-6, x)) + rng.gauss(0, self.cfg["noise"]) for x in m.p_true]
        acc = self.cfg["signal"][0 if m.n == 2 else 1]
        if acc:
            sig = m.winner if rng.random() < acc else rng.choice([i for i in range(m.n) if i != m.winner])
            logits = [x + math.log(acc if i == sig else (1 - acc) / (m.n - 1)) for i, x in enumerate(logits)]
        mx = max(logits)
        belief = [math.exp(x - mx) for x in logits]
        s = sum(belief)
        belief = [x / s for x in belief]
        best, best_edge = None, 0.05
        for i in range(m.n):
            got = m.quote(i, 50)
            if got is None:
                continue
            edge = belief[i] * got[1] - 1
            if edge > best_edge:
                best, best_edge = (i, got[1]), edge
        if best is None:
            return None
        i, odds = best
        kelly = best_edge / (odds - 1)
        return i, max(MIN_STAKE, min(cap, int(0.25 * kelly * p.equity)))

    # ------------------------------------------------------------ the loop

    def run_round(self, r: int) -> None:
        rng, cfg = self.rng, self.cfg
        markets = [Market(r * 10 + k, s, p, self.model_of(p), rng, cfg["b"], cfg["fee"])
                   for k, (s, p) in enumerate(self.lineup(r))]
        rain = rng.random() < 0.15
        actions = []
        for p in self.players:
            if p.kind != "oracle" and rng.random() >= 0.3:
                continue
            for m in markets:
                if p.kind == "oracle" or rng.random() < 0.7:
                    actions.append((rng.randrange(STEPS), p, m))
        before = self.house_pnl
        for step in range(STEPS):
            for m in markets:
                m.observe(rng)
            if step == 0:                         # specialists bet as the market opens (exchange.py:234-251)
                for m in markets:
                    for tid, plays in AI_RULES.items():
                        if m.series in plays and (tid != "rain" or rain):
                            hit = plays[m.series]
                            opt = m.winner if rng.random() < hit else rng.choice(
                                [i for i in range(m.n) if i != m.winner])
                            self.ai_bet(tid, m, opt)
            if step == STEPS // 2:                # the Contrarian waits until halfway (exchange.py:240-241)
                for m in markets:
                    staked = [0.0] * m.n
                    for b in m.bets:
                        staked[b["option"]] += b["stake"]
                    for b in m.ai_bets:
                        staked[b["option"]] += AI_STAKE
                    prices = m.prices()
                    fav = (max(range(m.n), key=lambda i: staked[i]) if any(staked)
                           else max(range(m.n), key=lambda i: prices[i]))
                    alt = max((i for i in range(m.n) if i != fav), key=lambda i: prices[i])
                    self.ai_bet("contrarian", m, alt)
            todo = [a for a in actions if a[0] == step]
            rng.shuffle(todo)
            for _, p, m in todo:
                got = self.decide(p, m)
                if got:
                    # a player told "max N" by the 409 resizes: fit the stake under the cap, cash and loss-limit room
                    held = sum(b["stake"] for b in m.bets if b["player"] is p)
                    room = self.cfg["loss_limit"] - p.lost_today - p.escrow
                    stake = min(got[1], int(POSITION_CAP * p.equity) - held, p.cash, room)
                    if stake < MIN_STAKE:
                        self.refuse("no_room_to_resize")
                        continue
                    self.place(p, m, got[0], stake, markets)
        for m in markets:
            self.settle(m)
        self.round_pnl.append(self.house_pnl - before)

    def settle(self, m: Market) -> None:
        """ledger.py:543-552 for players; exchange.py:271-279 for the AI traders."""
        if not m.void:
            close = m.prices()
            self.brier["open"].append(sum((x - (i == m.winner)) ** 2 for i, x in enumerate(m.open_model)))
            self.brier["close"].append(sum((x - (i == m.winner)) ** 2 for i, x in enumerate(close)))
        for b in m.bets:
            p, stake, payout = b["player"], b["stake"], b["payout"]
            p.escrow -= stake
            if m.void:
                p.cash += stake
                p.returned += stake
            elif b["option"] == m.winner:
                p.cash += payout
                p.returned += payout
                self.house_pnl -= payout - stake
            else:
                self.house_pnl += stake
                p.lost_today += stake
        for b in m.ai_bets:
            if m.void:
                continue
            won = b["option"] == m.winner
            delta = round((AI_STAKE * b["odds"] if won else 0) - AI_STAKE)
            self.ai_points[b["trader"]] += delta
            self.ai_bets[b["trader"]] += 1
            self.book_pnl_ai -= delta

    def run(self) -> dict:
        days = self.cfg["days"]
        for d in range(days):
            for p in self.players:
                p.lost_today = 0                  # ledger.py:420: the loss limit counts since London midnight
                if self.cfg.get("daily_refill"):
                    p.cash += self.cfg["daily_refill"]
                    p.granted += self.cfg["daily_refill"]
                    self.minted += self.cfg["daily_refill"]
            if d:
                for k in range(8):
                    self.join(PLAYER_TYPES[k % 4])
            for r in range(ROUNDS_PER_DAY):
                self.run_round(d * ROUNDS_PER_DAY + r)
            supply = sum(p.equity for p in self.players)
            self.daily.append({"day": d + 1, "minted": self.minted, "supply": supply, "housePnl": self.house_pnl,
                               "players": len(self.players),
                               "bust": sum(p.equity < MIN_STAKE for p in self.players)})
        return self.metrics()

    def metrics(self) -> dict:
        by: dict[str, dict] = {}
        for kind in PLAYER_TYPES + ("oracle",):
            ps = [p for p in self.players if p.kind == kind]
            if not ps:
                continue
            pnl = [p.equity - p.granted for p in ps]
            staked = sum(p.staked for p in ps)
            by[kind] = {"n": len(ps), "meanPnl": statistics.fmean(pnl), "medianPnl": statistics.median(pnl),
                        "roi": (sum(pnl) / staked) if staked else 0.0, "bets": sum(p.n_bets for p in ps),
                        "shareUp": sum(x > 0 for x in pnl) / len(ps)}
        top = sorted(self.players, key=lambda p: -p.equity)[:10]
        days = [sum(self.round_pnl[i:i + ROUNDS_PER_DAY]) for i in range(0, len(self.round_pnl), ROUNDS_PER_DAY)]
        staked = sum(p.staked for p in self.players)
        return {"housePnl": self.house_pnl, "houseMargin": self.house_pnl / staked if staked else 0.0,
                "staked": staked, "worstRound": min(self.round_pnl), "bestRound": max(self.round_pnl),
                "worstDay": min(days), "houseDays": days, "minted": self.minted,
                "supply": self.daily[-1]["supply"], "daily": self.daily, "byType": by,
                "top10": {k: sum(p.kind == k for p in top) for k in PLAYER_TYPES + ("oracle",)},
                "ai": {t: {"points": v, "bets": self.ai_bets[t]} for t, v in self.ai_points.items()},
                "bookPnlIncludingAi": self.house_pnl + self.book_pnl_ai, "refusals": self.refusals,
                "brierOpen": statistics.fmean(self.brier["open"]), "brierClose": statistics.fmean(self.brier["close"])}


# ---------------------------------------------------------------- scenarios


BASE = {"b": LMSR_B, "fee": FEE, "grant": GRANT, "loss_limit": DEFAULT_LOSS_LIMIT, "noise": 0.3,
        "signal": (0.65, 0.40), "days": 7}


def impact(b: float, fee: float, stake: int = 100) -> float:
    """Price move of a stake on a 50/50 binary with an empty book, in points of probability."""
    book = Book(2, b, fee)
    book.q[0] += book.quote([0.5, 0.5], 0, stake)
    return 100 * (book.prices([0.5, 0.5])[0] - 0.5)


def run_many(cfg: dict, seeds: int) -> dict:
    runs = [Sim(cfg, 1000 + s).run() for s in range(seeds)]
    pnl = sorted(r["housePnl"] for r in runs)
    days = sorted(d for r in runs for d in r["houseDays"])

    def mean(path):
        vals = []
        for r in runs:
            v = r
            for k in path:
                v = v[k]
            vals.append(v)
        return statistics.fmean(vals)

    by = {k: {f: mean(("byType", k, f)) for f in ("meanPnl", "medianPnl", "roi", "shareUp", "bets")}
          for k in runs[0]["byType"]}
    refusals: dict[str, float] = {}
    for r in runs:
        for k, v in r["refusals"].items():
            refusals[k] = refusals.get(k, 0) + v / seeds
    return {"cfg": cfg, "seeds": seeds,
            "housePnl": {"mean": statistics.fmean(pnl), "min": pnl[0], "p05": pnl[max(0, len(pnl) // 20)],
                         "max": pnl[-1], "se": statistics.stdev(pnl) / math.sqrt(len(pnl))},
            "houseMargin": mean(("houseMargin",)), "staked": mean(("staked",)),
            "houseDay": {"mean": statistics.fmean(days), "p05": days[len(days) // 20], "min": days[0],
                         "shareNegative": sum(d < 0 for d in days) / len(days)},
            "worstRound": min(r["worstRound"] for r in runs),
            "minted": mean(("minted",)), "supply": mean(("supply",)),
            "dailySupply": [statistics.fmean(r["daily"][i]["supply"] for r in runs) for i in range(cfg["days"])],
            "dailyMinted": [statistics.fmean(r["daily"][i]["minted"] for r in runs) for i in range(cfg["days"])],
            "bustShare": statistics.fmean(r["daily"][-1]["bust"] / r["daily"][-1]["players"] for r in runs),
            "byType": by,
            "top10": {k: mean(("top10", k)) for k in runs[0]["top10"]},
            "ai": {t: {"points": mean(("ai", t, "points")), "bets": mean(("ai", t, "bets"))} for t in runs[0]["ai"]},
            "bookPnlIncludingAi": mean(("bookPnlIncludingAi",)), "refusals": refusals,
            "brierOpen": mean(("brierOpen",)), "brierClose": mean(("brierClose",)),
            "impact100": impact(cfg["b"], cfg["fee"])}


def lmsr_bound(b: float) -> dict:
    """Worst case per market for the house, LMSR seeded at the model: b·ln(1/π_winner) (Hanson 2003)."""
    floor5 = P_FLOOR / (P_CEIL + 4 * P_FLOOR)   # 5-way: one option at 97%, four clamped to 3%, renormalised
    return {"binary_at_50": b * math.log(2), "binary_at_floor": b * math.log(1 / P_FLOOR),
            "fiveway_uniform": b * math.log(5), "fiveway_at_floor": b * math.log(1 / floor5)}


def courtsider(trials: int, seed: int = 7) -> dict:
    """A courtsider on clean_sheet: someone on a platform sees a line stop before the TfL Line Status API shows it.

    Mirrors: betting stays open until 60 s before the hour's end (markets.py:109 closes_at = settles_at - 60 s, with
    settles_at = the next :30, markets.py:569-620), so the window is ~59 min; P(break) at open is held to 30-70%
    (markets.py:585); a break reading settles NO and lock_if_decided closes the market on the next tick
    (exchange.py:86-93, server.py:30 TICK_S = 1 s). The exchange sees a break only after TfL publishes it AND the
    recorder polls it (every 60 s, recorder/README.md:52). Assumed: TfL publish lag U(0, 180) s, recorder poll phase
    U(0, 60) s, a 10 s human reaction, one account at the 25% cap of a 1000-point grant (a 250-point stake).
    A bet delay D (Betfair-style) re-checks the market after D s and refuses the bet if it closed meanwhile.
    Variants: a 5-min betting window (the hourly market closes with its first round), and a suspension when a faster
    related feed moves."""
    out = {}
    variants = [("as coded (59-min window)", 59, False, d) for d in (0, 15, 30, 60, 90, 120)]
    variants += [("suspend on a faster related feed", 59, True, d) for d in (30, 60, 90)]
    variants += [("5-min betting window", 4, False, d) for d in (0, 30)]
    variants += [("5-min window + suspend + 30 s delay", 4, True, 30)]
    for label, window, fast, d in variants:
        rng = random.Random(seed)
        won = hits = breaks = 0
        for _ in range(trials):
            p_h = rng.uniform(0.3, 0.7)
            lam = -math.log(1 - p_h) / 60
            t = rng.expovariate(lam)
            if t >= window:
                continue
            breaks += 1
            lag = rng.uniform(0, 180) + rng.uniform(0, 60) + 1
            if fast:   # assumed: a related feed (Tube arrivals, ~30 s cadence) shows the stop within U(30, 90) s
                lag = min(lag, rng.uniform(30, 90))
            react = 10
            if react + d >= lag or t + (react + d) / 60 >= window:
                continue
            p_no = min(P_CEIL, max(P_FLOOR, 1 - math.exp(-lam * (60 - t))))
            stake = int(POSITION_CAP * GRANT)
            got = locked_odds(Book(2, LMSR_B, FEE).quote([1 - p_no, p_no], 1, stake), stake)
            if got is None:
                continue
            hits += 1
            won += payout_of(stake, got) - stake
        out[f"{label} | {d}"] = {"variant": label, "delay": d, "pBreakInWindow": breaks / trials,
                                 "pAcceptedGivenBreak": hits / breaks if breaks else 0,
                       "profitPerMarket": won / trials, "profitPerDay12": 12 * won / trials,
                       "profitPerDay12x10accounts": 120 * won / trials}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    seeds, days = (3, 2) if args.quick else (16, 7)
    base = dict(BASE, days=days)
    scenarios: dict[str, dict] = {}

    def go(name: str, **over) -> dict:
        cfg = dict(base, **over)
        res = run_many(cfg, seeds)
        scenarios[name] = res
        print(f"{name:28s} house {res['housePnl']['mean']:>9.0f}  margin {res['houseMargin']:6.3f}  "
              f"supply {res['supply']:>8.0f}/{res['minted']:>8.0f}  "
              f"informed roi {res['byType']['informed']['roi']:6.3f}"
              f"  random roi {res['byType']['random']['roi']:6.3f}", flush=True)
        return res

    go("base (as coded)")
    for b in (300, 600, 2400):
        go(f"b={b}", b=float(b))
    for fee in (0.0, 0.01, 0.02):
        go(f"fee={fee:.0%}", fee=fee)
    for b in (300, 600, 2400):
        go(f"b={b} fee=2%", b=float(b), fee=0.02)
    for g in (500, 2000):
        go(f"grant={g}", grant=g)
    go("overconfident model", overconfident=True)
    go("overconfident fee=2%", overconfident=True, fee=0.02)
    go("informed, no private signal", signal=(0, 0))
    go("informed, sharp signal", signal=(0.75, 0.55))
    go("daily refill +100", daily_refill=100)
    go("10 oracles (perfect info)", oracles=10)
    go("10 oracles, b=600", oracles=10, b=600.0)

    OUT.mkdir(exist_ok=True)
    payload = {"assumptions": ASSUMPTIONS, "lmsrBound": {str(b): lmsr_bound(b) for b in (300, 600, 1200, 2400)},
               "impact100": {str(b): {f"{f:.0%}": round(impact(b, f), 2) for f in (0, 0.01, 0.02, 0.05)}
                             for b in (300, 600, 1200, 2400)},
               "courtsider": courtsider(200_000), "scenarios": scenarios}
    (OUT / "results.json").write_text(json.dumps(payload, indent=1))
    (OUT / "summary.md").write_text(summary(payload))
    print(f"wrote {OUT / 'results.json'} and {OUT / 'summary.md'}")


def summary(payload: dict) -> str:
    sc = payload["scenarios"]
    lines = ["# Economy simulation summary", "",
             "| scenario | house P&L (mean ± 95% CI / worst run) | house margin on stakes | worst day | "
             "days house lost | "
             "supply / minted | informed ROI | random ROI | herding ROI | contrarian ROI | Brier open → close |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in sc.items():
        bt = r["byType"]
        hp = r["housePnl"]
        lines.append(f"| {name} | {hp['mean']:,.0f} ± {1.96 * hp['se']:,.0f} / {hp['min']:,.0f} | "
                     f"{r['houseMargin']:.1%} | {r['houseDay']['min']:,.0f} | {r['houseDay']['shareNegative']:.0%} | "
                     f"{r['supply']:,.0f} / {r['minted']:,.0f} | {bt['informed']['roi']:+.1%} | "
                     f"{bt['random']['roi']:+.1%} | {bt['herding']['roi']:+.1%} | {bt['contrarian']['roi']:+.1%} | "
                     f"{r['brierOpen']:.3f} → {r['brierClose']:.3f} |")
    lines += ["", "## AI traders (points after the run, from 1000; bets settled)", "",
              "| scenario | " + " | ".join(sc["base (as coded)"]["ai"]) + " | house book incl. AI |",
              "|---|" + "---|" * (len(sc["base (as coded)"]["ai"]) + 1)]
    for name in ("base (as coded)", "b=300", "b=2400", "fee=0%", "overconfident model"):
        r = sc[name]
        lines.append(f"| {name} | " + " | ".join(f"{v['points']:,.0f} ({v['bets']:.0f})" for v in r["ai"].values())
                     + f" | {r['bookPnlIncludingAi']:,.0f} |")
    lines += ["", "## Leaderboard top 10 by type (mean over seeds)", "", "| scenario | random | informed | herding | "
              "contrarian | oracle |", "|---|---|---|---|---|---|"]
    for name in ("base (as coded)", "fee=0%", "b=300", "b=2400", "10 oracles (perfect info)"):
        t = sc[name]["top10"]
        lines.append(f"| {name} | " + " | ".join(f"{t.get(k, 0):.1f}" for k in
                                                ("random", "informed", "herding", "contrarian", "oracle")) + " |")
    lines += ["", "## Refusals per run (mean)", ""]
    for name in ("base (as coded)", "10 oracles (perfect info)"):
        lines.append(f"- {name}: " + ", ".join(f"{k} {v:,.0f}" for k, v in sorted(sc[name]["refusals"].items())))
    lines += ["", "## Price impact of a 100-point bet on a 50% binary (points of probability)", "",
              "| b | fee 0% | 1% | 2% | 5% |", "|---|---|---|---|---|"]
    for b, row in payload["impact100"].items():
        lines.append(f"| {b} | " + " | ".join(f"{v:.2f}" for v in row.values()) + " |")
    lines += ["", "## LMSR worst-case loss per market (points)", "",
              "| b | binary at 50% | binary, winner at the 3% floor | 5-way uniform | 5-way, winner at floor |",
              "|---|---|---|---|---|"]
    for b, row in payload["lmsrBound"].items():
        lines.append(f"| {b} | " + " | ".join(f"{v:,.0f}" for v in row.values()) + " |")
    base = sc["base (as coded)"]
    lines += ["", "## Point supply by day, base", "", "| day | minted | player equity | house P&L |",
              "|---|---|---|---|"]
    for i, (s, mnt) in enumerate(zip(base["dailySupply"], base["dailyMinted"])):
        lines.append(f"| {i + 1} | {mnt:,.0f} | {s:,.0f} | {mnt - s:,.0f} |")
    refill = sc["daily refill +100"]
    lines += ["", "## Point supply by day, daily refill +100", "", "| day | minted | player equity |", "|---|---|---|"]
    for i, (s, mnt) in enumerate(zip(refill["dailySupply"], refill["dailyMinted"])):
        lines.append(f"| {i + 1} | {mnt:,.0f} | {s:,.0f} |")
    lines += ["", "## Courtsider on clean_sheet (1 account, 250-point stake, per hourly market listed)", "",
              "| variant | bet delay (s) | P(break in betting window) | P(bet accepted given a break) | "
              "profit per market | "
              "per 12-hour day | per day, 10 accounts |", "|---|---|---|---|---|---|---|"]
    for dly, r in payload["courtsider"].items():
        lines.append(f"| {r['variant']} | {r['delay']} | {r['pBreakInWindow']:.0%} | {r['pAcceptedGivenBreak']:.0%} | "
                     f"{r['profitPerMarket']:.0f} | {r['profitPerDay12']:,.0f} | "
                     f"{r['profitPerDay12x10accounts']:,.0f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
