"""Out-of-sample check of the TOPS loop (services/city/tops.py) on the recorded day.

    python3 analysis/judge/tops_replay.py <raw_dir> <start ISO> <end ISO> [step_s] [split ISO]

Runs the real Exchange over the recorded data with an empty in-memory ledger, so the traders' track records start at
zero and every opening blend uses only markets settled before it opened. For every settled market it compares the
opening price with the TOPS blend against the plain futures prior it was blended from (same market, same futures):
Brier and log loss per series, paired difference ± 1 SE. Then the two known leaks, each 10 points at the opening quote
on an empty book: the copycat (back Tube Crowd's hub_surge pick, the hub furthest below its usual) and the City Pulse
leak (back Tube Crowd's city_pulse pick), priced on the prior and on the blend. Rows are given for the whole window and
for markets opened at or after `split` (default 17:00 UTC, after the futures' fit window).
Read-only: never writes the recorder data.
"""
from __future__ import annotations

import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402
from exchange import Clock, Exchange  # noqa: E402
from ledger import Ledger  # noqa: E402

BOOT = 2000
STAKE = 10
LEAKS = {"copycat": "hub_surge", "pulse leak": "city_pulse"}


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def brier(p: dict, w: str) -> float:
    return sum((v - (o == w)) ** 2 for o, v in p.items())


def logloss(p: dict, w: str) -> float:
    return -math.log(max(1e-9, p[w]))


def mean_se(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    return m, (sum((x - m) ** 2 for x in xs) / max(1, n - 1) / n) ** 0.5


def roi_ci(rets: list[float]) -> str:
    if not rets:
        return "-"
    rng = random.Random(0)
    boot = sorted(sum(rng.choice(rets) for _ in rets) / len(rets) for _ in range(BOOT))
    return f"{sum(rets) / len(rets):+.1%} [{boot[int(.025 * BOOT)]:+.0%}, {boot[int(.975 * BOOT)]:+.0%}]"


def odds_on(model: dict, option: str) -> float | None:
    book = pricing.Book(list(model))
    return pricing.locked_odds(book.quote(model, option, STAKE), STAKE)


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    step = int(sys.argv[4]) * 1000 if len(sys.argv) > 4 else 30_000
    split = ms(sys.argv[5]) if len(sys.argv) > 5 else ms(sys.argv[2][:10] + "T17:00:00Z")
    spots = load_hotspots()
    ex = Exchange(Store(raw, spots), Clock(start), spots, Ledger(":memory:"))
    seen: dict[str, dict] = {}
    t = start
    while t <= end:
        ex.tick(t)
        for m in ex.active():
            if m.id in seen or m.tops is None:
                continue
            row = {"series": m.sub, "opened": m.opened_at, "prior": m.tops["prior"], "tops": dict(m.open_model),
                   "lam": m.tops["lambda"], "leak": None}
            stance = (m.tops_stances or {}).get("tube")
            if m.sub in LEAKS.values() and stance and stance[0] == "pick":
                row["leak"] = (stance[1], odds_on(row["prior"], stance[1]), odds_on(row["tops"], stance[1]))
            seen[m.id] = row
        t += step

    done = [(r, ex.markets[mid].result["winner"]) for mid, r in seen.items()
            if ex.markets[mid].result and ex.markets[mid].result["winner"] is not None]
    print(f"TOPS replay {sys.argv[2]} -> {sys.argv[3]}, step {step // 1000}s, {len(seen)} markets opened with a "
          f"TOPS record, {len(done)} settled")
    after = f"opened >= {datetime.fromtimestamp(split / 1000, timezone.utc):%H:%M} UTC"
    for label, keep in (("all", lambda r: True), (after, lambda r: r["opened"] >= split)):
        rows = [(r, w) for r, w in done if keep(r)]
        print(f"\n[{label}] opening price: futures prior vs TOPS blend (paired, per market; diff = TOPS - prior, "
              f"negative is better)")
        print(f"{'series':15} {'n':>4} {'lam>0':>6} {'mean lam':>8} {'max lam':>7} {'Brier prior':>11} "
              f"{'Brier TOPS':>10} {'diff ± SE':>18} {'LL prior':>8} {'LL TOPS':>8} {'diff ± SE':>18}")
        by = defaultdict(list)
        for r, w in rows:
            by[r["series"]].append((r, w))
        for s, got in sorted(by.items()):
            n = len(got)
            bp = [brier(r["prior"], w) for r, w in got]
            bt = [brier(r["tops"], w) for r, w in got]
            lp = [logloss(r["prior"], w) for r, w in got]
            lt = [logloss(r["tops"], w) for r, w in got]
            db, sb = mean_se([b - a for a, b in zip(bp, bt)])
            dl, sl = mean_se([b - a for a, b in zip(lp, lt)])
            lams = [r["lam"] for r, _ in got]
            print(f"{s:15} {n:4d} {sum(x > 0 for x in lams):6d} {sum(lams) / n:8.4f} {max(lams):7.4f} "
                  f"{sum(bp) / n:11.4f} {sum(bt) / n:10.4f} {db:+.4f} ± {sb:.4f}   {sum(lp) / n:8.4f} "
                  f"{sum(lt) / n:8.4f} {dl:+.4f} ± {sl:.4f}")
        print(f"\n[{label}] leaks: {STAKE} points on Tube Crowd's pick at the opening quote, ROI [95% bootstrap CI]")
        for name, s in LEAKS.items():
            bets = [(r["leak"], w) for r, w in rows if r["series"] == s and r["leak"]]
            prior = [(o1 if pick == w else 0) - 1 for (pick, o1, _), w in bets if o1]
            blend = [(o2 if pick == w else 0) - 1 for (pick, _, o2), w in bets if o2]
            wins = sum(pick == w for (pick, _, _), w in bets)
            print(f"  {name:11} {s:11} n={len(bets):3d} won={wins:3d}  prior {roi_ci(prior):26}  "
                  f"TOPS {roi_ci(blend)}")

    print("\nTrack records at the end (GET /tops): score = shrunk decayed log-score skill vs the futures prior")
    for s, got in ex.tops.json()["series"].items():
        print(f"  {s:15} lambda now {got['lambdaNow']:.4f}")
        for tid, v in sorted(got["traders"].items(), key=lambda kv: -kv[1]["weight"]):
            print(f"    {tid:11} score {v['score']:+.4f}  n {v['n']:4d}  nEff {v['nEff']:6.2f}  "
                  f"weight {v['weight']:.3f}")


if __name__ == "__main__":
    main()
