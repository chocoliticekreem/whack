"""City Pulse and hub_surge on TfL's own 5-min stamp grid: which forecast holds up out of sample.

    python3 analysis/judge/fit_crowd.py <raw_dir> <start ISO> <split ISO> <end ISO>

Readings are binned by their TfL stamp (timeUtc). Coefficients are fitted on [start, split) and every model is
scored on [split, end).

City Pulse: the log pulse at the settle stamp, 15 and 20 min after the open stamp (the span a market's futures run,
markets.crowd_walk). Models: flat; the usual-day curve; the usual curve plus the walk drift the futures used before
(0.5 x the last hour's mean vs-usual change, capped at sigma/5); the usual curve plus momentum on the last 10 min's
vs-usual change. Scored by Gaussian log score (sd fitted in sample), Brier and log loss of Over the open value, and
how often the outcome fell under each model's own median.

hub_surge: the reading at open, the first in-round publish and the settle stamp. Tests momentum against reversion
in each hub's gain relative to the five hubs' mean, then scores the race (Brier, log loss over 5 hubs) from the
in-round publish for each model of the rest of the round. Also: how often the top riser at that publish won.
Read-only.
"""
from __future__ import annotations

import math
import random
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

from data import Store, load_hotspots  # noqa: E402

P = 5 * 60_000
HISTORY = 3 * 3_600_000


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def load(raw: Path, start: int, end: int) -> tuple[dict, dict]:
    """{stamp slot: {naptan: (live, usual, typical)}}, {hub id: naptan}."""
    spots = load_hotspots()
    st = Store(raw, spots)
    st.refresh(start - HISTORY, end + 30 * 60_000)
    grid: dict[int, dict] = {}
    for ts, snap in st.between("crowding", start - HISTORY, end + 30 * 60_000):
        for nap, v in snap.items():
            k = round((st.tfl_stamp(ts, nap) or ts) / P)
            grid.setdefault(k, {}).setdefault(nap, (v, st.usual(nap, k * P), st.typical(nap, k * P)))
    return grid, {h["id"]: h["naptan"] for h in spots}


def phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ---------------------------------------------------------------- City Pulse


def pulse_points(grid: dict) -> dict[int, tuple[float, float, float]]:
    """{slot: (log pulse, log pulse vs usual, log usual pulse)} for slots with 18+ stations and their usuals."""
    out = {}
    for k, row in grid.items():
        if len(row) < 18 or not all(r[1] for r in row.values()):
            continue
        p = 100 * sum(r[0] for r in row.values()) / len(row)
        u = 100 * sum(r[1] for r in row.values()) / len(row)
        out[k] = (math.log(p), math.log(p / u), math.log(u))
    return out


def walk_drift(pts: dict, k: int) -> float:
    """Per-minute drift as pricing.fit_log_walk gave it: 0.5 x the last hour's mean change, capped at sigma/5."""
    seq = [(j, pts[j][1]) for j in sorted(pts) if k - 36 <= j <= k]
    pairs = [(j0, a, j1, b) for (j0, a), (j1, b) in zip(seq, seq[1:]) if 0 < j1 - j0 <= 6]
    steps = [(b - a) / math.sqrt(5 * (j1 - j0)) for j0, a, j1, b in pairs]
    recent = [(b - a) / (5 * (j1 - j0)) for j0, a, j1, b in pairs if j1 >= k - 12]
    if len(steps) < 4:
        return 0.0
    sig = min(0.05, max(0.002, statistics.pstdev(steps)))
    d = 0.5 * statistics.fmean(recent) if recent else 0.0
    return max(-sig / 5, min(sig / 5, d))


def pulse_table(pts: dict, lo: int, split: int, hi: int, lag: int) -> None:
    rows = [{"k": k, "x": pts[k][0], "y": pts[k + lag][0], "du": pts[k + lag][2] - pts[k][2],
             "mom": pts[k][1] - pts[k - 2][1], "drift": walk_drift(pts, k) * 5 * lag}
            for k in sorted(pts) if lo <= k < hi and k + lag in pts and k - 2 in pts]
    fit, oos = [q for q in rows if q["k"] < split], [q for q in rows if q["k"] >= split]
    coef = sum(q["mom"] * (q["y"] - q["x"] - q["du"]) for q in fit) / sum(q["mom"] ** 2 for q in fit)
    models = {"flat": lambda q: q["x"], "usual curve": lambda q: q["x"] + q["du"],
              "usual + walk drift (before)": lambda q: q["x"] + q["du"] + q["drift"],
              f"usual + momentum {coef:+.2f}": lambda q: q["x"] + q["du"] + coef * q["mom"]}
    print(f"\nCity Pulse, settle stamp {lag * 5} min after the open stamp; fit n={len(fit)}, "
          f"out of sample n={len(oos)}")
    print(f"  {'model':30} {'mean err':>8} {'Gauss LL':>8} {'Brier@open':>10} {'LL@open':>8} {'under own median':>16}")
    for name, f in models.items():
        sd = statistics.pstdev([q["y"] - f(q) for q in fit]) or 0.01
        err = [q["y"] - f(q) for q in oos]
        gll = statistics.mean(0.5 * (e / sd) ** 2 + math.log(sd * math.sqrt(2 * math.pi)) for e in err)
        b = ll = 0.0
        for q in oos:
            p, y = min(.97, max(.03, 1 - phi((q["x"] - f(q)) / sd))), q["y"] > q["x"]
            b += (p - y) ** 2
            ll -= math.log(p if y else 1 - p)
        print(f"  {name:30} {statistics.mean(err) * 100:+7.2f}% {gll:8.3f} {b / len(oos):10.3f} {ll / len(oos):8.3f}"
              f" {sum(e < 0 for e in err):9d}/{len(oos)}")


# ---------------------------------------------------------------- hub_surge


def hub_rows(grid: dict, hubs: dict, lo: int, hi: int, mid: int, end: int) -> list[dict]:
    ids = sorted(hubs)

    def vs(k):
        row = grid.get(k, {})
        if any(hubs[h] not in row or not row[hubs[h]][2] for h in ids):
            return None
        return {h: math.log(row[hubs[h]][0] / row[hubs[h]][2]) for h in ids}

    out = []
    for k in range(lo, hi):
        a, b, c = vs(k), vs(k + mid), vs(k + end)
        if not (a and b and c):
            continue
        ma, m1, m2 = (statistics.mean(v.values()) for v in (a, {h: b[h] - a[h] for h in ids},
                                                              {h: c[h] - b[h] for h in ids}))
        gain = {h: math.exp(c[h]) - math.exp(a[h]) for h in ids}
        out.append({"k": k, "a": a, "dev": {h: a[h] - ma for h in ids}, "g1": {h: b[h] - a[h] - m1 for h in ids},
                    "g2": {h: c[h] - b[h] - m2 for h in ids}, "win": max(ids, key=lambda h: gain[h])})
    return out


def ols(rows: list[dict], feats: list) -> list[float]:
    X = [[f(q, h) for f in feats] for q in rows for h in q["a"]]
    y = [q["g2"][h] for q in rows for h in q["a"]]
    A = [[sum(x[i] * x[j] for x in X) for j in range(len(feats))] for i in range(len(feats))]
    b = [sum(x[i] * v for x, v in zip(X, y)) for i in range(len(feats))]
    if len(feats) == 1:
        return [b[0] / A[0][0]]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    return [(b[0] * A[1][1] - b[1] * A[0][1]) / det, (A[0][0] * b[1] - A[1][0] * b[0]) / det]


def race_score(rows: list[dict], fit: list[dict], feats: list, rng: random.Random) -> tuple[list, float, float]:
    coef = ols(fit, feats) if feats else []

    def pred(q, h):
        return sum(c * f(q, h) for c, f in zip(coef, feats))

    sd = statistics.pstdev([q["g2"][h] - pred(q, h) for q in fit for h in q["a"]])
    b = ll = 0.0
    for q in rows:
        ids = list(q["a"])
        cnt = {h: 0.5 for h in ids}
        for _ in range(400):
            fin = {h: math.exp(q["a"][h] + q["g1"][h] + pred(q, h) + rng.gauss(0, sd)) - math.exp(q["a"][h])
                   for h in ids}
            cnt[max(ids, key=lambda h: fin[h])] += 1
        tot = sum(cnt.values())
        b += sum((cnt[h] / tot - (h == q["win"])) ** 2 for h in ids)
        ll -= math.log(cnt[q["win"]] / tot)
    return coef, b / len(rows), ll / len(rows)


def hub_table(grid: dict, hubs: dict, lo: int, split: int, hi: int, mid: int, end: int) -> None:
    rows = hub_rows(grid, hubs, lo, hi, mid, end)
    fit, oos = [q for q in rows if q["k"] < split], [q for q in rows if q["k"] >= split]
    print(f"\nhub_surge: open stamp, in-round publish +{mid * 5} min, settle stamp +{end * 5} min; fit n={len(fit)}, "
          f"out of sample n={len(oos)}")
    for lab, data in (("in sample", fit), ("out of sample", oos)):
        c = statistics.correlation([q["g1"][h] for q in data for h in q["a"]],
                                   [q["g2"][h] for q in data for h in q["a"]])
        won = sum(max(q["g1"], key=q["g1"].get) == q["win"] for q in data)
        print(f"  {lab}: corr(relative gain open->publish, publish->settle) {c:+.2f}; top riser at the publish won "
              f"{won}/{len(data)}")
    rng = random.Random(0)
    specs = {"no drift": [], "momentum": [lambda q, h: q["g1"][h]],
             "reversion to hubs' mean": [lambda q, h: q["dev"][h] + q["g1"][h]]}
    print(f"  {'rest of the round':26} {'coef (fit)':>12} {'Brier':>6} {'LL':>6}")
    for name, feats in specs.items():
        coef, b, ll = race_score(oos, fit, feats, rng)
        print(f"  {name:26} {str([round(c, 2) for c in coef]):>12} {b:6.3f} {ll:6.3f}")
    print(f"  {'uniform':26} {'':>12} {0.8:6.3f} {math.log(5):6.3f}")


def main() -> None:
    raw, start, split, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3]), ms(sys.argv[4])
    grid, hubs = load(raw, start, end)
    lo, mid, hi = round(start / P), round(split / P), round(end / P)
    pts = pulse_points(grid)
    for lag in (3, 4):
        pulse_table(pts, lo, mid, hi, lag)
    for m, e in ((1, 3), (1, 4), (2, 4)):
        hub_table(grid, hubs, lo, mid, hi, m, e)


if __name__ == "__main__":
    main()
