"""Fit and choose heathrow_stack's count model on the recorded aircraft feed, out of sample.

    python3 analysis/judge/fit_heathrow.py <raw_dir> <fit start ISO> <split ISO> <end ISO>

Every reading with one 5 minutes later is a forecast: the distribution of the count at t+5. Constants are fitted on
[fit start, split) only; every model is scored on [split, end) by Brier and log loss of Over at two lines (the old
open + 0.5, and the futures' median on a .5, pricing.count_line) and by log loss of the exact count. Probabilities
are clamped to [3%, 97%] like the market's. Residual pools are the trailing 3 h before each forecast, as live.

    M0  bootstrap of the trailing 3 h of 5-min changes (pricing.DeltaSim, the model before this fit)
    M1  AR(1) on the 5-min change, phi fitted
    M2  reversion to the trailing 30-min mean, kappa fitted, bootstrap residuals
    M3  M2 with residuals scaled by sqrt(level)
    M4  Poisson around the M2 mean (pricing.CountSim; markets.STACK_KAPPA)

Read-only.
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import markets as mk  # noqa: E402
import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402

MIN = 60_000
H = 5
POOL_MS = 3 * 3_600_000


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def nearest(series: list[tuple[int, int]], t: int, tol: int = 40_000) -> int | None:
    lo, hi = 0, len(series) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (mid + 1, hi) if series[mid][0] < t else (lo, mid)
    best = min((j for j in (lo - 1, lo) if 0 <= j < len(series)), key=lambda j: abs(series[j][0] - t))
    return series[best][1] if abs(series[best][0] - t) <= tol else None


def forecasts(series: list[tuple[int, int]]) -> list[dict]:
    rows = []
    for ts, x in series:
        y, p5 = nearest(series, ts + H * MIN), nearest(series, ts - 5 * MIN)
        recent = [v for t, v in series if ts - mk.STACK_MEAN_MS <= t <= ts]
        if y is None or p5 is None or len(recent) < mk.MIN_DELTAS:
            continue
        level = sum(recent) / len(recent)
        rows.append({"t": ts, "x": x, "y": y, "d5": x - p5, "dev": x - level, "level": level})
    return rows


def slope(xs: list[float], ys: list[float]) -> float:
    return sum(a * b for a, b in zip(xs, ys)) / sum(a * a for a in xs)


def empirical(finals) -> dict[int, float]:
    c = Counter(max(0, round(f)) for f in finals)
    n = sum(c.values())
    return {k: v / n for k, v in c.items()}


def poisson_pmf(lam: float) -> dict[int, float]:
    return {k: math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1)) for k in range(60)}


def models(kappa: float, phi: float) -> dict:
    def resid2(q):
        return q["y"] - q["x"] + kappa * q["dev"]

    return {
        "M0 bootstrap of changes": lambda r, pool: empirical(r["x"] + q["y"] - q["x"] for q in pool),
        "M1 AR(1) on the change": lambda r, pool: empirical(r["x"] + phi * r["d5"] + q["y"] - q["x"] - phi * q["d5"]
                                                            for q in pool),
        "M2 revert to 30-min mean": lambda r, pool: empirical(r["x"] - kappa * r["dev"] + resid2(q) for q in pool),
        "M3 M2, resid x sqrt(lvl)": lambda r, pool: empirical(
            r["x"] - kappa * r["dev"] + resid2(q) * math.sqrt(max(.5, r["level"]) / max(.5, q["level"])) for q in pool),
        "M4 Poisson (CountSim)": lambda r, pool: poisson_pmf(max(pricing.CountSim.LAMBDA_FLOOR,
                                                                 r["x"] - kappa * r["dev"])),
    }


def p_over(dist: dict[int, float], line: float) -> float:
    return sum(p for k, p in dist.items() if k > line)


def centred_line(dist: dict[int, float]) -> float:
    acc, med = 0.0, 0
    for k, p in sorted(dist.items()):
        acc += p
        if acc >= 0.5:
            med = k
            break
    return min((c for c in (med - .5, med + .5) if c >= .5), key=lambda c: (abs(p_over(dist, c) - .5), c))


def score(rows: list[dict], everything: list[dict], model, centred: bool) -> tuple[int, float, float, float, list]:
    b = ll = cll = 0.0
    cal = []
    for r in rows:
        pool = [q for q in everything if r["t"] - POOL_MS <= q["t"] + H * MIN <= r["t"]]
        if len(pool) < mk.MIN_DELTAS:
            continue
        dist = model(r, pool)
        line = centred_line(dist) if centred else r["x"] + .5
        p, y = min(.97, max(.03, p_over(dist, line))), r["y"] > line
        b += (p - y) ** 2
        ll -= math.log(p if y else 1 - p)
        cll -= math.log(max(0.01, dist.get(r["y"], 0.0)))
        cal.append((p, y))
    n = len(cal)
    return n, b / n, ll / n, cll / n, cal


def main() -> None:
    raw, start, split, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3]), ms(sys.argv[4])
    store = Store(raw, load_hotspots())
    store.refresh(start - POOL_MS, end + H * MIN)
    series = [(ts, s["total"]) for ts, s in store.between("aircraft", start - POOL_MS, end + H * MIN)]
    everything = forecasts(series)
    fit = [r for r in everything if start <= r["t"] < split]
    oos = [r for r in everything if split <= r["t"] < end]
    kappa = -slope([r["dev"] for r in fit], [r["y"] - r["x"] for r in fit])
    phi = slope([r["d5"] for r in fit], [r["y"] - r["x"] for r in fit])
    print(f"fit n={len(fit)}, out of sample n={len(oos)} (overlapping 5-min forecasts, one per reading)")
    print(f"kappa = {kappa:.3f} per 5 min toward the trailing {mk.STACK_MEAN_MS // MIN}-min mean "
          f"(markets.STACK_KAPPA = {mk.STACK_KAPPA}); phi = {phi:.3f}")
    ms_ = models(kappa, phi)
    for label, rows in (("in sample", fit), ("OUT OF SAMPLE", oos)):
        for centred in (False, True):
            print(f"\n{label}, line = {'futures median on a .5' if centred else 'open count + 0.5'}")
            print(f"  {'model':26} {'n':>4} {'Brier':>6} {'LL':>6} {'count LL':>8} {'Over rate':>9}")
            cals = {}
            for name, model in ms_.items():
                n, b, ll, cll, cal = score(rows, everything, model, centred)
                cals[name] = cal
                print(f"  {name:26} {n:4d} {b:6.3f} {ll:6.3f} {cll:8.3f} {sum(y for _, y in cal) / n:9.2f}")
            if label == "OUT OF SAMPLE":
                for name in ("M0 bootstrap of changes", "M4 Poisson (CountSim)"):
                    print(f"  calibration, {name}: P(Over) bucket, n, mean P, hit rate")
                    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
                        bb = [(p, y) for p, y in cals[name] if lo <= p < hi]
                        if bb:
                            print(f"    {lo:.1f}-{min(hi, 1):.1f}  n={len(bb):3d}  "
                                  f"p={statistics.mean(p for p, _ in bb):.2f}  hit={statistics.mean(y for _, y in bb):.2f}")


if __name__ == "__main__":
    main()
