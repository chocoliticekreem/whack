"""Does the market structure work? A controlled test of the parallel exchange venue on markets with a known truth.

Every contract has a true probability p*. The public forecast, which market makers anchor on, is p* plus an error that
shrinks by half over the contract's life (the way the 50 futures narrow as data arrives). Informed funds read p* with a
little noise, uninformed funds with a lot, model funds just use the forecast, and retail trades with its biases.
Because p* is known, we can measure directly whether trading moves the price from the forecast towards the truth, who
earns and who pays, and whether capital moves to the funds that know more. Every agent and rule is the venue's own
code (services/city/venue*.py); only the markets are synthetic.

Profit is measured two ways. Realised P&L depends on luck, since each contract settles at 0 or 100. Expected P&L marks
every fill to the truth instead (a YES bought at x is worth 100·p* − x), minus fees plus rebates, so it shows who
really earns from whom.

    python3 analysis/exchange/simulate.py           # every scenario, 3 seeds each (about 5 min)
    python3 analysis/exchange/simulate.py --quick   # 1 seed, 6 rounds
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import venue_ledger  # noqa: E402
from venue import Venue  # noqa: E402
from venue_agents import Fund, MarketMaker, Retail  # noqa: E402
from venue_book import PAYOUT  # noqa: E402

MIN = 60_000
ROUND_MS, LIFE_MS = 5 * MIN, 4 * MIN
T0 = 1_794_060_000_000
LEARN = 0.5  # share of the forecast's error gone by close


class Futures:
    def __init__(self, m):
        self.m = m

    def probs(self) -> dict[str, float]:
        return {"yes": self.m.forecast, "no": 1 - self.m.forecast}


class SynthMarket:
    """A binary contract with a known truth. The venue sees it exactly as it sees a city market."""

    def __init__(self, mid: str, opened: int, truth: float, error: float, biased: bool):
        self.id, self.sub, self.kind = mid, "synth", "overunder"
        self.options = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]
        self.opened_at, self.closes_at, self.settles_at = opened, opened + LIFE_MS, opened + ROUND_MS
        self.question, self.source, self.rule = f"{mid}?", "simulation", "YES with probability p*"
        self.truth, self.error, self.biased = truth, error, biased
        self.forecast = self.clamp(truth + error)
        self.result = self.settled_at = self.locked = None
        self.futures = Futures(self)

    @staticmethod
    def clamp(p: float) -> float:
        return min(0.97, max(0.03, p))

    def option_ids(self) -> list[str]:
        return ["yes", "no"]

    def signal(self, option: str, rng) -> float:
        """What an informed fund reads (each fund adds its own noise in Fund.belief)."""
        return self.truth if option == "yes" else 1 - self.truth

    def learn(self, now: int) -> None:
        done = min(1.0, max(0.0, (now - self.opened_at) / LIFE_MS))
        self.forecast = self.clamp(self.truth + self.error * (1 - LEARN * done))


class SynthSource:
    def __init__(self, rng: random.Random, rounds: int, per_round: int, sigma: float, bias: float, bias_share: float):
        self.rng, self.rounds, self.per_round = rng, rounds, per_round
        self.sigma, self.bias, self.bias_share = sigma, bias, bias_share
        self.markets: list[SynthMarket] = []
        self.opened, self.store, self.spots = 0, None, {}

    def tick(self, now: int) -> None:
        while self.opened < self.rounds and T0 + self.opened * ROUND_MS <= now:
            at = T0 + self.opened * ROUND_MS
            for k in range(self.per_round):
                truth = min(0.95, max(0.05, self.rng.betavariate(2, 2)))
                biased = self.rng.random() < self.bias_share
                error = self.rng.gauss(0, self.sigma) + (self.bias if biased else 0.0)
                self.markets.append(SynthMarket(f"r{self.opened:02d}-c{k}", at, truth, error, biased))
            self.opened += 1
        for m in self.markets:
            if m.result is not None:
                continue
            if now >= m.settles_at:
                m.result, m.settled_at = {"winner": "yes" if self.rng.random() < m.truth else "no"}, now
            elif now < m.closes_at:
                m.learn(now)

    def active(self, now: int) -> list[SynthMarket]:
        return [m for m in self.markets if m.result is None and m.opened_at <= now]

    def status(self, m: SynthMarket, now: int) -> str:
        if m.result is not None:
            return "settled"
        return "closed" if now >= m.closes_at else "open"


SCENARIOS = {
    "baseline": dict(sigma=0.10, bias=0.0, bias_share=0.0, informed=20, crowd=0, noisy=20,
                     note="forecast off by ~10 points at open; 20 informed and 20 uninformed funds"),
    "no informed funds": dict(sigma=0.10, bias=0.0, bias_share=0.0, informed=0, crowd=0, noisy=20,
                              note="nobody knows more than the forecast: trading should do no harm"),
    "few informed funds": dict(sigma=0.10, bias=0.0, bias_share=0.0, informed=4, crowd=0, noisy=20,
                               note="only 4 informed funds"),
    "unpriced edge": dict(sigma=0.05, bias=-0.10, bias_share=0.5, informed=20, crowd=0, noisy=20,
                          note="half the forecasts are 10 points too low, like the measured VIX-fade leak"),
    "noise traders only": dict(sigma=0.10, bias=0.0, bias_share=0.0, informed=0, crowd=0, noisy=60,
                               note="lots of uninformed money and no informed money"),
    "wisdom of a crowd": dict(sigma=0.10, bias=0.0, bias_share=0.0, informed=0, crowd=40, noisy=0,
                              note="no expert, but 40 funds that each read the truth badly (noise 0.20-0.30)"),
}
GROUPS = ("mm", "informed", "crowd", "model", "noisy", "retail", "random")


def build(v: Venue, rng: random.Random, informed: int, noisy: int, crowd: int) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {g: [] for g in GROUPS}

    def add(group: str, agent, points: int) -> None:
        v.add_agent(agent, points, T0)
        ids[group].append(agent.id)

    for i in range(6):
        add("mm", MarketMaker(f"mm{i}", random.Random(rng.random()), half=rng.choice((1.5, 2.0, 2.5)),
                              size=rng.choice((20, 25, 30)), designated=i < 4), 200_000)

    def fund(prefix: str, n: int, strategy: str, noise: tuple[float, float]) -> None:
        for k in range(n):
            add(prefix, Fund(f"{prefix}{k}", random.Random(rng.random()), "tier 2", (strategy,), weight=0.0,
                             noise=rng.uniform(*noise), kelly=rng.uniform(0.10, 0.20), margin=rng.uniform(0.01, 0.03),
                             every=rng.randint(2, 6), start_after=rng.uniform(0.0, 0.5)), 8_000)

    fund("informed", informed, "signal", (0.02, 0.04))     # the truth, read well
    fund("crowd", crowd, "signal", (0.20, 0.30))           # the truth, read badly
    fund("model", 10, "model", (0.0, 0.0))                 # the public forecast, nothing more
    fund("noisy", noisy, "model", (0.20, 0.30))            # the forecast plus noise: no information at all
    for k in range(300):
        bias = ("longshot", "favourite", "herd", "momentum", "random")[k % 5]
        add("retail", Retail(f"retail{k}", random.Random(rng.random()), bias, rate_per_min=rng.uniform(0.05, 0.15)),
            rng.randint(200, 1000))
    for k in range(20):
        add("random", Retail(f"random{k}", random.Random(rng.random()), "random", rate_per_min=1.0), 2_000)
    return ids


def run(name: str, spec: dict, seed: int, rounds: int, step: int = 2_000) -> dict:
    rng = random.Random(seed)
    src = SynthSource(random.Random(seed + 100), rounds, 6, spec["sigma"], spec["bias"], spec["bias_share"])
    v = Venue(src, seed=seed)
    ids = build(v, rng, spec["informed"], spec["noisy"], spec["crowd"])
    fund_ids = ids["informed"] + ids["crowd"] + ids["model"] + ids["noisy"]
    start_share = (sum(v.clearing.equity_at_cost(a) for a in ids["informed"] + ids["crowd"]) /
                   max(1, sum(v.clearing.equity_at_cost(a) for a in fund_ids)))
    spreads = []
    for now in range(T0, T0 + rounds * ROUND_MS + ROUND_MS, step):
        v.tick(now)
        if (now - T0) % 10_000 == 0:  # liquidity: the quoted spread on every open, two-sided book
            for cid, c in v.contracts.items():
                b = v.books[cid]
                if not c.done and src.status(c.market, now) == "open" and b.bid() is not None and b.ask() is not None:
                    spreads.append(b.ask() - b.bid())
    truth = {m.id: m for m in src.markets}
    rows = []
    for r in v.settled:
        m = truth[r["contract"]]
        price = r["closeMid"] if r["closeMid"] is not None else r["closeLast"]
        rows.append({"truth": m.truth, "forecast": r["forecast"], "biased": m.biased,
                     "price": None if price is None else price / PAYOUT, "yes": r["outcome"] == "yes"})
    # every fill marked to the truth: the YES side gains 100·p* − price, the NO side the opposite
    group_of = {a: g for g, members in ids.items() for a in members}
    ev = {g: 0.0 for g in GROUPS}
    qty = {g: 0 for g in GROUPS}
    for cid, book in v.books.items():
        p = truth[cid].truth * PAYOUT
        for f in book.tape:
            gain = (p - f.yes_price) * f.qty
            ev[group_of[f.yes_account]] += gain
            ev[group_of[f.no_account]] -= gain
            qty[group_of[f.taker.account]] += f.qty
            qty[group_of[f.maker.account]] += f.qty
    groups = {}
    for g, members in ids.items():
        capital = sum(v.clearing.granted.get(a, 0) for a in members)
        pnl = sum(v.clearing.pnl(a) for a in members)
        fees = sum(v.clearing.fees_paid.get(a, 0) for a in members)
        rebates = sum(e[3] for e in v.clearing.ledger.entries if e[4].startswith("maker rebate")
                      and e[2][5:] in set(members))
        expected = ev[g] - fees + rebates
        groups[g] = {"accounts": len(members), "pnl": pnl, "roi": pnl / capital if capital else None,
                     "expected": expected, "expectedRoi": expected / capital if capital else None,
                     "contracts": qty[g], "expectedPerContract": expected / qty[g] if qty[g] else None}
    end_share = (sum(v.clearing.equity_at_cost(a) for a in ids["informed"] + ids["crowd"]) /
                 max(1, sum(v.clearing.equity_at_cost(a) for a in fund_ids)))
    return {"scenario": name, "seed": seed, "rows": rows, "groups": groups,
            "mmMarkoutPerContract": groups["mm"]["expectedPerContract"],
            "randomPnlPerContract": groups["random"]["expectedPerContract"],
            "informedShare": [start_share, end_share], "trades": sum(len(b.tape) for b in v.books.values()),
            "spread": statistics.mean(spreads) if spreads else None,
            "audit": v.clearing.audit(v.gateway.live_orders()) or "ok"}


def mean_of(runs: list[dict], g: str, key: str) -> float | None:
    vals = [run["groups"][g][key] for run in runs if run["groups"][g][key] is not None]
    return statistics.mean(vals) if vals else None


def summarise(runs: list[dict], spec: dict) -> dict:
    rows = [r for run in runs for r in run["rows"] if r["price"] is not None]
    mae = lambda key, rs: statistics.mean(abs(r[key] - r["truth"]) for r in rs) if rs else None  # noqa: E731
    brier = lambda key, rs: statistics.mean((r[key] - r["yes"]) ** 2 for r in rs) if rs else None  # noqa: E731
    biased = [r for r in rows if r["biased"]]
    out = {
        "contracts": len(rows),
        "maeForecast": mae("forecast", rows), "maePrice": mae("price", rows),
        "closerToTruth": statistics.mean(abs(r["price"] - r["truth"]) < abs(r["forecast"] - r["truth"])
                                         for r in rows) if rows else None,
        "brierPrice": brier("price", rows), "brierForecast": brier("forecast", rows), "brierTruth": brier("truth", rows),
        "biasedForecastError": statistics.mean(r["forecast"] - r["truth"] for r in biased) if biased else None,
        "biasedPriceError": statistics.mean(r["price"] - r["truth"] for r in biased) if biased else None,
        "groups": {g: {"pnl": sum(run["groups"][g]["pnl"] for run in runs),
                       "roi": mean_of(runs, g, "roi"), "expectedRoi": mean_of(runs, g, "expectedRoi"),
                       "expectedPerContract": mean_of(runs, g, "expectedPerContract")}
                   for g in GROUPS},
        "mmMarkoutPerContract": statistics.mean(r["mmMarkoutPerContract"] for r in runs
                                                if r["mmMarkoutPerContract"] is not None),
        "randomPnlPerContract": statistics.mean(r["randomPnlPerContract"] for r in runs
                                                if r["randomPnlPerContract"] is not None),
        "informedShare": [statistics.mean(r["informedShare"][0] for r in runs),
                          statistics.mean(r["informedShare"][1] for r in runs)],
        "trades": sum(r["trades"] for r in runs), "audit": sorted({str(r["audit"]) for r in runs}),
        "spread": statistics.mean(r["spread"] for r in runs if r["spread"] is not None),
        "note": spec["note"],
    }
    if out["maeForecast"]:
        out["errorCut"] = 1 - out["maePrice"] / out["maeForecast"]
    return out


def table(results: dict) -> str:
    pct = lambda x: "—" if x is None else f"{x:+.1%}"  # noqa: E731
    pts = lambda x: "—" if x is None else f"{100 * x:.1f}"  # noqa: E731
    per = lambda g: "—" if g["expectedPerContract"] is None else f"{g['expectedPerContract']:+.1f}"  # noqa: E731
    lines = ["| Scenario | Contracts | Error vs truth at close: forecast → price (points) | Price closer than forecast |"
             " Quoted spread (points) |"
             " Expected P&L per contract traded (points): informed / crowd / uninformed / market makers / random taker |"
             " Informed + crowd share of fund capital |", "|" + "---|" * 7]
    for name, s in results.items():
        g = s["groups"]
        lines.append(
            f"| {name} | {s['contracts']} | {pts(s['maeForecast'])} → {pts(s['maePrice'])} ({pct(-s['errorCut'])}) | "
            f"{s['closerToTruth']:.0%} | {s['spread']:.1f} | {per(g['informed'])} / {per(g['crowd'])} / {per(g['noisy'])} / "
            f"{per(g['mm'])} / {per(g['random'])} | {s['informedShare'][0]:.0%} → {s['informedShare'][1]:.0%} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--fees", choices=("off", "kalshi"), default="off")
    args = ap.parse_args()
    venue_ledger.FEE_RATE_X100 = venue_ledger.KALSHI_FEE_X100 if args.fees == "kalshi" else 0
    seeds, rounds = (1, 6) if args.quick else (args.seeds, args.rounds)
    results, t0 = {}, time.time()
    for name, spec in SCENARIOS.items():
        runs = [run(name, spec, 7 + s, rounds) for s in range(seeds)]
        results[name] = summarise(runs, spec)
        print(f"{name}: {results[name]['contracts']} contracts, {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=1))
    md = table(results)
    (out / "summary.md").write_text(md + "\n")
    print(md)
    e = results["unpriced edge"]
    if e["biasedForecastError"] is not None:
        print(f"\nUnpriced edge: on the biased contracts the forecast was {100 * e['biasedForecastError']:+.1f} points "
              f"off the truth at close; the traded price {100 * e['biasedPriceError']:+.1f}.")
    print("\nBrier against outcomes (lower is better; 'truth' is the floor no forecast can beat):")
    for name, s in results.items():
        print(f"  {name}: forecast {s['brierForecast']:.4f}, price {s['brierPrice']:.4f}, truth {s['brierTruth']:.4f}; "
              f"{s['trades']} trades; audit {s['audit']}")


if __name__ == "__main__":
    main()
