"""hub_surge model study: diagnose the old model, compare candidates walk-forward, score the leak rules.

    python3 analysis/judge/hub_model.py [raw_dir] [start ISO] [end ISO] [draws]
    python3 analysis/judge/hub_model.py data/raw 2026-11-07T12:00:00Z 2026-11-08T02:35:00Z 4000   # ~45 s

Every round from start + 20 min to end - 25 min is rebuilt as the exchange would build it (opens 6 min before its
5-minute mark, closes 1 min before, settles on each hub's first TfL reading stamped after close) and settled with
markets.settle_hub_surge. Three kinds of price per race:

- legacy: the model on main before this study (per-minute relative OU, HUB_KAPPA 0.0161, fixed HUB_CORR, 50 plain
  futures, power 0.5), copied below so the comparison stays reproducible;
- deployed: markets.build_hub_surge as it is now (fits on readings polled up to `now` only, so every round is
  walk-forward);
- research candidates (`zoo`), each refitted at every round on the readings polled up to its open, drawn `draws` times.

Out of sample = rounds opened from 15:00 (the first 3 h only fit); legacy's constants came from 12:00-17:00, so
15:00-17:00 is in sample for it. Blocks: selection 15:00-20:00, test 20:00-end. Differences are paired per race,
mean ± 1 SE. Leak ROIs: a 10-point bet at the open model price, odds 0.95/p (the LMSR with b = 1200 barely moves on
10 points), 2000-resample bootstrap 95% CI. Read-only: never writes the recorder data.
"""
from __future__ import annotations

import math
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import data  # noqa: E402
import markets as mk  # noqa: E402
import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402

MIN = 60_000
STEP = pricing.HUB_STEP_MS
BOOT = 2000


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def hour_of(t: int) -> int:
    h = t // 3_600_000 % 24
    return h + 24 if h < 6 else h


# ---------------------------------------------------------------- legacy model (main before this study)

LEGACY_KAPPA, LEGACY_POWER = 0.0161, 0.5


class LegacySim(pricing.RaceSim):
    """The per-minute relative OU of #77/#86, verbatim but for the name."""

    def __init__(self, stations, t0, minutes, rng, typ0, typ_t, kappa, chol, power):
        self.stations, self.t0, self.m = stations, t0, minutes
        self.typ0, self.typ_t, self.mode, self.power = typ0, typ_t, "gain", power
        self.kappa, self.chol, self.rng, self.seen = kappa, chol, rng, t0
        self.paths = [self.walk_all(kappa, chol, rng) for _ in range(pricing.N_FUTURES)]

    def restart(self, ts, snap):
        if ts <= self.seen or any(nap not in snap for _, nap, *_ in self.stations):
            return False
        self.seen, self.power = ts, 1.0
        k = max(0, min(self.m, round((ts - self.t0) / MIN)))
        now = [math.log(snap[nap]) for _, nap, *_ in self.stations]
        self.paths = [self.walk_all(self.kappa, self.chol, self.rng, now, k) for _ in range(pricing.N_FUTURES)]
        return True

    def walk_all(self, kappa, chol, rng, start=None, k0=0):
        log_typ = [math.log(t) for t in self.typ0]
        x_open = [s[2] for s in self.stations]
        xs = start or x_open
        paths = [[a + (b - a) * i / k0 for i in range(k0)] + [b] for a, b in zip(x_open, xs)]
        for _ in range(self.m - k0):
            dev = [x - lt for x, lt in zip(xs, log_typ)]
            mean = sum(dev) / len(dev)
            e = [pricing.capped_gauss(rng) for _ in xs]
            xs = [x + mu - kappa * (d - mean) + sd * sum(c * v for c, v in zip(row, e))
                  for x, d, (_, _, _, mu, sd), row in zip(xs, dev, self.stations, chol)]
            for path, x in zip(paths, xs):
                path.append(x)
        return paths


def legacy_futures(store, spots, now, settles_at, mid) -> pricing.Futures | None:
    last = store.latest("crowding", now)
    if not last or mk.vs_usual(store, spots, last[0], last[1]) is None:
        return None
    ts0, snap = last
    m0 = store.measured_at(ts0)
    t_end, minutes = mk.crowd_walk(m0, settles_at, now)
    stations = []
    for h in spots:
        oid, nap, x0, _, sd = mk.crowd_station(store, h, snap, now)
        stations.append((oid, nap, x0, pricing.usual_drift(store.usual(nap, m0), store.usual(nap, t_end), minutes), sd))
    typ0 = [mk.typical_at(store, h["naptan"], ts0) for h in spots]
    typ_t = [store.typical(h["naptan"], t_end) or t0 for h, t0 in zip(spots, typ0)]
    chol = pricing.cholesky(mk.hub_corr([h["id"] for h in spots]))
    sim = LegacySim(stations, m0, minutes, random.Random(mk.seed_of(mid)), typ0, typ_t, LEGACY_KAPPA, chol,
                    LEGACY_POWER)
    return pricing.Futures(sim, [h["id"] for h in spots])


# ---------------------------------------------------------------- races


def first_publish(store, spots, m, now):
    """(poll ts, {hub: vs usual}) of the first reading TfL stamped after the open reading and polled before close."""
    for ts, snap in store.between("crowding", now + 1, m.closes_at - 1):
        if store.measured_at(ts) > store.measured_at(m.spec["openTs"]):
            vs = mk.vs_usual(store, [h for h in spots if h["id"] in m.spec["stations"]], ts, snap)
            if vs:
                return ts, snap, vs
    return None


def build_races(store, spots, start, end):
    ids = [h["id"] for h in spots]
    races = []
    at = start + 20 * MIN
    while at <= end - 25 * MIN:
        now = at - 6 * MIN + 30_000
        mid = f"hub_surge-{at}"
        m = mk.build_hub_surge(mid, store, spots, now, at)
        if m is not None:
            res = mk.settle_hub_surge(m, store, at + 25 * MIN)
            if res[0] == "done":
                legacy = legacy_futures(store, spots, now, at, mid) if m.option_ids() == ids else None
                r = {"at": at, "now": now, "closes": m.closes_at, "opts": m.option_ids(), "open": m.spec["open"],
                     "openTs": m.spec["openTs"], "winner": res[1], "deployed": m.futures.probs(),
                     "legacy": legacy.probs() if legacy else None,
                     "stamps0": {h: store.tfl_stamp(m.spec["openTs"], m.spec["stations"][h]) or m.spec["openTs"]
                                 for h in m.option_ids()}}
                pub = first_publish(store, spots, m, now)
                if pub:
                    ts, snap, vs = pub
                    r["riser_in"] = max(m.option_ids(), key=lambda h: (vs[h] - r["open"][h], -ids.index(h)))
                    m.futures.observe(store.measured_at(ts), snap)
                    r["deployed_in"] = m.futures.probs()
                    if legacy:
                        legacy.observe(store.measured_at(ts), snap)
                        r["legacy_in"] = legacy.probs()
                races.append(r)
        at += 5 * MIN
    return races


# ---------------------------------------------------------------- diagnostics


def corr(xs, ys):
    return statistics.correlation(xs, ys) if len(xs) > 2 else float("nan")


def rel_devs(grid, ids):
    """{slot: [dev_i]} for slots where every hub read: log live - log usual, minus the hubs' mean."""
    out = {}
    for s, row in grid.items():
        if all(h in row for h in ids):
            v = [row[h][0] - row[h][1] for h in ids]
            mean = sum(v) / len(v)
            out[s] = [x - mean for x in v]
    return out


def ols_b(pairs):
    num, den = sum(d * y for d, y in pairs), sum(d * d for d, _ in pairs)
    b = -num / den
    se = statistics.pstdev([y + b * d for d, y in pairs]) / math.sqrt(den)
    return b, se


def diagnose(grid, ids, races, store, spots):
    devs = rel_devs(grid, ids)
    ch = {s: [b - a for a, b in zip(devs[s], devs[s + 1])] for s in devs if s + 1 in devs}
    print("\n== 1. Diagnosis")
    print("\n-- residual autocorrelation: 5-min change in each hub's relative deviation (log vs usual minus the hubs' "
          "mean) vs the change k later, pooled over hubs")
    windows = (("all", 0, 99), ("12-17", 12, 17), ("17-21", 17, 21), ("21-03", 21, 27))
    print("   window   " + "  ".join(f"{5 * k:>2d} min" for k in (1, 2, 3, 4)))
    for name, lo, hi in windows:
        cells = []
        for k in (1, 2, 3, 4):
            xs, ys = [], []
            for s in ch:
                if s + k in ch and lo <= hour_of(s * STEP) < hi:
                    xs += ch[s]
                    ys += ch[s + k]
            cells.append(f"{corr(xs, ys):+.2f} (n{len(xs)})")
        print(f"   {name:7}  " + "  ".join(cells))
    print("\n-- reversion over k TfL steps: change in relative dev = -b_k * dev (k = 3 is the usual race horizon)")
    for k in (1, 2, 3, 4):
        pairs = [(d, devs[s + k][i] - d) for s in devs if s + k in devs for i, d in enumerate(devs[s])]
        b, se = ols_b(pairs)
        geo = 1 - (1 - ols_b([(d, devs[s + 1][i] - d) for s in devs if s + 1 in devs
                                for i, d in enumerate(devs[s])])[0]) ** k
        print(f"   k={k}: b = {b:.3f} ± {se:.3f} (n={len(pairs)}); 1-step b compounded: {geo:.3f}")
    print("   by hub (k=3): " + "  ".join(
        f"{h} {ols_b([(devs[s][i], devs[s + 3][i] - devs[s][i]) for s in devs if s + 3 in devs])[0]:.2f}"
        f"±{ols_b([(devs[s][i], devs[s + 3][i] - devs[s][i]) for s in devs if s + 3 in devs])[1]:.2f}"
        for i, h in enumerate(ids)))
    print("   by time (k=3): " + "  ".join(
        f"{name} {b:.2f}±{se:.2f}" for name, lo, hi in windows[1:]
        for b, se in [ols_b([(devs[s][i], devs[s + 3][i] - devs[s][i]) for s in devs
                             if s + 3 in devs and lo <= hour_of(s * STEP) < hi for i in range(len(ids))])]))
    print("   hub levels (mean relative dev over the day): " + "  ".join(
        f"{h} {statistics.fmean(d[i] for d in devs.values()):+.3f}" for i, h in enumerate(ids)))
    moves = [x for c in ch.values() for x in c]
    sd = statistics.pstdev(moves)
    kurt = statistics.fmean([(x / sd) ** 4 for x in moves]) - 3
    print(f"\n-- shocks: 5-min relative moves sd {sd:.3f}, excess kurtosis {kurt:.1f}; sd by window: " + "  ".join(
        f"{name} {statistics.pstdev([x for s, c in ch.items() if lo <= hour_of(s * STEP) < hi for x in c]):.3f}"
        for name, lo, hi in windows[1:]))
    print("\n-- TfL publication lag")
    lag = [(r["now"] - s) / MIN for r in races for s in r["stamps0"].values()]
    steps = [r["closes"] for r in races]
    print(f"   open reading's TfL stamp is {statistics.median(lag):.0f} min old at open (median; "
          f"{sum(x > 10 for x in lag) / len(lag):.0%} of hub readings are one publish older, ~14 min)")
    ks = []
    for r in races:
        latest = max(r["stamps0"].values())
        n = max(1, math.floor((r["closes"] - latest) / STEP) + 1)
        ks += [round((latest + n * STEP - s) / STEP) for s in r["stamps0"].values()]
    print("   TfL steps from each hub's open reading to its settle reading: " + ", ".join(
        f"{k}: {ks.count(k)}" for k in sorted(set(ks))) + f" (n={len(steps)} races)")
    stale = [(r["now"] - r["stamps0"][h] > 10 * MIN, r["legacy"][h], r["winner"] == h)
             for r in races if r["legacy"] for h in r["opts"]]
    for flag, name in ((False, "fresh (8 min)"), (True, "stale (14 min)")):
        part = [(p, w) for f, p, w in stale if f == flag]
        print(f"   {name}: legacy price {statistics.fmean(p for p, _ in part):.3f}, won "
              f"{statistics.fmean(w for _, w in part):.3f} (n={len(part)})")


# ---------------------------------------------------------------- research candidates (walk-forward)


def shrink_corr(corr: list[list[float]], delta: float) -> list[list[float]]:
    """(1 - delta)·corr + delta·(constant correlation at corr's mean off-diagonal value). Both are positive
    semi-definite for a correlation matrix (its mean off-diagonal entry is at least -1/(n-1), since 1ᵀ·corr·1 >= 0),
    so every mix of the two is too."""
    n = len(corr)
    if n < 2:
        return [row[:] for row in corr]
    rbar = sum(corr[i][j] for i in range(n) for j in range(n) if i != j) / (n * (n - 1))
    return [[1.0 if i == j else (1 - delta) * corr[i][j] + delta * rbar for j in range(n)] for i in range(n)]


def ledoit_wolf_corr(rows: list[list[float]]) -> tuple[list[list[float]], float]:
    """(correlation, delta): the sample correlation of `rows` (T observations of n variables) shrunk toward constant
    correlation with Ledoit and Wolf's optimal intensity ("Honey, I shrunk the sample covariance matrix", 2004).
    delta is near 1 when T is small next to the noise in the sample correlations, near 0 when they are pinned down.
    A variable that never moves gets correlation 0 with the others."""
    t, n = len(rows), len(rows[0])
    mean = [sum(r[i] for r in rows) / t for i in range(n)]
    y = [[r[i] - mean[i] for i in range(n)] for r in rows]
    s = [[sum(v[i] * v[j] for v in y) / t for j in range(n)] for i in range(n)]
    live = [i for i in range(n) if s[i][i] > 1e-18]
    out = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    if len(live) < 2:
        return out, 1.0
    sd = {i: math.sqrt(s[i][i]) for i in live}
    corr = [[s[i][j] / (sd[i] * sd[j]) for j in live] for i in live]
    m = len(live)
    rbar = sum(corr[a][b] for a in range(m) for b in range(m) if a != b) / (m * (m - 1))
    pi = {(i, j): sum((v[i] * v[j] - s[i][j]) ** 2 for v in y) / t for i in live for j in live}
    gamma = sum((rbar * sd[i] * sd[j] - s[i][j]) ** 2 for i in live for j in live if i != j)
    rho = sum(pi[i, i] for i in live)
    for i in live:
        for j in live:
            if i != j:
                tii = sum((v[i] ** 2 - s[i][i]) * (v[i] * v[j] - s[i][j]) for v in y) / t
                tjj = sum((v[j] ** 2 - s[j][j]) * (v[i] * v[j] - s[i][j]) for v in y) / t
                rho += rbar / 2 * (sd[j] / sd[i] * tii + sd[i] / sd[j] * tjj)
    delta = max(0.0, min(1.0, (sum(pi.values()) - rho) / gamma / t)) if gamma > 0 else 1.0
    shrunk = shrink_corr(corr, delta)
    for a, i in enumerate(live):
        for b, j in enumerate(live):
            out[i][j] = shrunk[a][b]
    return out, delta


def zoo_fit(grid, ids, k, now, cfg):
    devs = rel_devs(grid, ids)
    n0 = cfg.get("levels")
    levels = [sum(d[i] for d in devs.values()) / (len(devs) + n0) if n0 is not None else 0.0 for i in range(len(ids))]
    rows = []
    for s in devs:
        if s + k not in devs:
            continue
        a, z = grid[s], grid[s + k]
        mv = [(z[h][0] - a[h][0]) - (z[h][1] - a[h][1]) for h in ids]
        last = None
        if s - 1 in grid and all(h in grid[s - 1] for h in ids):
            p = grid[s - 1]
            lm = [(a[h][0] - p[h][0]) - (a[h][1] - p[h][1]) for h in ids]
            last = [x - sum(lm) / len(lm) for x in lm]
        rows.append((s, [d - lv for d, lv in zip(devs[s], levels)], mv, last))
    if len(rows) < 8:
        return None
    with_last = [r for r in rows if r[3] is not None]
    mom = bool(cfg.get("mom")) and len(with_last) >= 8
    use = with_last if mom else rows
    a_mom = 0.0
    xs = [(d[i], (l[i] if l else 0.0), y - sum(mv) / len(mv)) for _, d, mv, l in use for i, y in enumerate(mv)]
    if mom:
        sxx = sum(x * x for x, _, _ in xs)
        sll = sum(m * m for _, m, _ in xs)
        sxl = sum(x * m for x, m, _ in xs)
        sxy = sum(x * y for x, _, y in xs)
        sly = sum(m * y for _, m, y in xs)
        det = sxx * sll - sxl * sxl
        b = -(sxy * sll - sly * sxl) / det
        a_mom = (sly * sxx - sxy * sxl) / det
    else:
        b = -sum(x * y for x, _, y in xs) / sum(x * x for x, _, _ in xs)
    bs = [b] * len(ids)
    if cfg.get("hub_b"):
        est = [ols_b([(d[i], y - sum(mv) / len(mv)) for _, d, mv, _ in rows for i2, y in enumerate(mv) if i2 == i])
               for i in range(len(ids))]
        bs = pricing.shrink_to_mean([e for e, _ in est], [s for _, s in est])
    resid = [(s, [y + bb * x - a_mom * (l[i] if l else 0.0) for i, (x, y, bb) in enumerate(zip(d, mv, bs))])
             for s, d, mv, l in rows]
    e = [r for _, r in resid]
    if cfg.get("corr") == "lw":
        c = ledoit_wolf_corr(e)[0]
    elif cfg.get("corr") == "none":
        c = [[float(i == j) for j in range(len(ids))] for i in range(len(ids))]
    else:
        c = pricing.sample_corr(e)
    recent = [r for s, r in resid if s >= (now - mk.HISTORY_MS) // STEP]
    recent = recent if len(recent) >= 4 else e
    logs = [math.log(max(1e-4, statistics.pstdev([r[i] for r in recent]))) for i in range(len(ids))]
    sds = [math.exp(v) for v in pricing.shrink_to_mean(logs, [1 / math.sqrt(2 * len(recent))] * len(ids))]
    nu = None
    if cfg.get("t"):
        z = [r[i] / statistics.pstdev([q[i] for q in e]) for r in e for i in range(len(ids))]
        k4 = statistics.fmean([x ** 4 for x in z]) - 3
        nu = max(4.5, 4 + 6 / k4) if k4 > 0 else None
    return {"b": bs, "levels": levels, "corr": c, "sds": sds, "mom": a_mom, "nu": nu}


def unit_draw(rng, nu):
    z = rng.gauss(0.0, 1.0)
    return z if nu is None else z / math.sqrt(rng.gammavariate(nu / 2, 2) / nu) * math.sqrt((nu - 2) / nu)


def zoo_probs(r, store, spots, cfg, draws):
    ids = r["opts"]
    hubs = [h for h in spots if h["id"] in ids]
    grid = mk.hub_grid(store, hubs, r["now"] - mk.HUB_LOOKBACK_MS, r["now"])
    stamps = [r["stamps0"][h] for h in ids]
    latest = max(stamps)
    settle = latest + max(1, math.floor((r["closes"] - latest) / STEP) + 1) * STEP
    ks = [round((settle - s) / STEP) for s in stamps]
    fits = {k: f for k in set(ks) | {1, 2, 3, 4, 5} if (f := zoo_fit(grid, ids, k, r["now"], cfg))}
    if not fits:
        return {h: 1 / len(ids) for h in ids}
    near = [min(fits, key=lambda f: (abs(f - k), f)) for k in ks]
    common = fits[max(set(near), key=near.count)]
    low = pricing.cholesky(common["corr"])
    x0 = [grid[s // STEP][h][0] for s, h in zip(stamps, ids)]
    lu0 = [math.log(store.usual(h["naptan"], s)) for s, h in zip(stamps, hubs)]
    lus = [math.log(store.usual(h["naptan"], settle)) for h in hubs]
    typ_s = [store.typical(h["naptan"], settle) for h in hubs]
    dev = [x - u for x, u in zip(x0, lu0)]
    dev = [d - sum(dev) / len(dev) for d in dev]
    last = [0.0] * len(ids)
    if fits[near[0]]["mom"]:
        prev = grid.get(latest // STEP - 1, {})
        if all(h in prev for h in ids):
            lm = [(x - prev[h][0]) - (u - prev[h][1]) for x, u, h in zip(x0, lu0, ids)]
            last = [v - sum(lm) / len(lm) for v in lm]
    mu, sd = [], []
    for i, k in enumerate(near):
        f = fits[k]
        mu.append(x0[i] + lus[i] - lu0[i] - f["b"][i] * (dev[i] - f["levels"][i]) + f["mom"] * last[i])
        sd.append(f["sds"][i])
    base = [r["open"][h] for h in ids]
    wins = [0] * len(ids)
    rng = random.Random(r["at"])
    for _ in range(draws):
        e = [unit_draw(rng, common["nu"]) for _ in ids]
        g = [math.exp(mu[i] + sd[i] * sum(low[i][j] * e[j] for j in range(i + 1))) / typ_s[i] - base[i]
             for i in range(len(ids))]
        wins[g.index(max(g))] += 1
    return {h: (w + 0.5) / (draws + 0.5 * len(ids)) for h, w in zip(ids, wins)}


def tempered(p, a):
    t = {h: v ** a for h, v in p.items()}
    s = sum(t.values())
    return {h: v / s for h, v in t.items()}


def walk_forward_power(races, key):
    """Platt power p ∝ p^a, fitted at each round on the races settled before it opened (a = 1 under 20 races)."""
    out = []
    for i, r in enumerate(races):
        hist = [(q[key], q["winner"]) for q in races[:i] if q["at"] <= r["now"]]
        a = 1.0
        if len(hist) >= 20:
            a = min((x / 20 for x in range(4, 31)),
                    key=lambda a: sum(-math.log(tempered(p, a)[w]) for p, w in hist))
        out.append(tempered(r[key], a))
        r["power_" + key] = a
    return out


# ---------------------------------------------------------------- scoring


def ll_of(p, w):
    return -math.log(p[w])


def brier_of(p, w):
    return sum((v - (h == w)) ** 2 for h, v in p.items())


def paired(a, b):
    d = [x - y for x, y in zip(a, b)]
    return statistics.fmean(d), statistics.stdev(d) / math.sqrt(len(d))


def boot_roi(x):
    rng = random.Random(0)
    bs = sorted(sum(rng.choice(x) for _ in x) / len(x) for _ in range(BOOT))
    return sum(x) / len(x), bs[int(0.025 * BOOT)], bs[int(0.975 * BOOT)]


def leak_rules(races, key, grid, ids):
    def prev_vs(r):
        out = {}
        for h in ids:
            row = grid.get(r["stamps0"][h] // STEP - 1, {})
            if h not in row:
                return None
            out[h] = math.exp(row[h][0]) / math.exp(row[h][1])
        return out

    def riser_open(r):
        pv = prev_vs(r)
        if not pv:
            return None
        now = {h: math.exp(grid[r["stamps0"][h] // STEP][h][0] - grid[r["stamps0"][h] // STEP][h][1]) for h in ids}
        return max(ids, key=lambda h: now[h] - pv[h])

    rules = {"copycat Tube Crowd (furthest below usual)": (lambda r: min(ids, key=lambda h: r["open"][h]), key),
             "back the leader (furthest above usual)": (lambda r: max(ids, key=lambda h: r["open"][h]), key),
             "back the top riser at open (last 5-min move)": (riser_open, key),
             "back the top riser since open, at the first in-round publish": (lambda r: r.get("riser_in"),
                                                                             key + "_in")}
    out = {}
    for name, (pick, price) in rules.items():
        got = [(r, pick(r)) for r in races if r.get(price)]
        got = [(r, h) for r, h in got if h]
        rois = [(0.95 / r[price][h] if r["winner"] == h else 0.0) - 1 for r, h in got]
        out[name] = (len(rois), sum(r["winner"] == h for r, h in got), statistics.fmean(r[price][h] for r, h in got),
                     *boot_roi(rois))
    return out


ZOO = {
    "OU at the race horizon (pooled b, sample corr)": {},
    "+ per-hub b, shrunk to pooled": {"hub_b": True},
    "+ Ledoit-Wolf corr": {"corr": "lw"},
    "+ Student-t shocks (LW corr)": {"corr": "lw", "t": True},
    "+ momentum term (LW corr)": {"corr": "lw", "mom": True},
    "+ hub levels + LW corr": {"levels": mk.HUB_LEVEL_PRIOR, "corr": "lw"},
    "+ hub levels (sample corr): deployed spec": {"levels": mk.HUB_LEVEL_PRIOR},
}


def main() -> None:
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else data.raw_root()
    start = ms(sys.argv[2]) if len(sys.argv) > 2 else ms("2026-11-07T12:00:00Z")
    end = ms(sys.argv[3]) if len(sys.argv) > 3 else ms("2026-11-08T02:35:00Z")
    draws = int(sys.argv[4]) if len(sys.argv) > 4 else 4000
    spots = load_hotspots()
    ids = [h["id"] for h in spots]
    store = Store(raw, spots)
    store.refresh(start, end)
    races = build_races(store, spots, start, end)
    grid = mk.hub_grid(store, spots, start, end)
    print(f"hub_surge {sys.argv[2] if len(sys.argv) > 2 else '2026-11-07T12:00:00Z'} → "
          f"{sys.argv[3] if len(sys.argv) > 3 else '2026-11-08T02:35:00Z'}: {len(races)} settled races "
          f"({sum(r['legacy'] is None for r in races)} listed only by the deployed model, a hub missing at open)")
    both = [r for r in races if r["legacy"]]
    diagnose(grid, ids, both, store, spots)

    print("\n-- calibration of the legacy model (all races): per hub, mean price at open vs share won")
    for h in ids:
        print(f"   {h:14} price {statistics.fmean(r['legacy'][h] for r in both):.3f}  won "
              f"{statistics.fmean(r['winner'] == h for r in both):.3f}")
    print("   by price bucket: " + "  ".join(
        f"{lo:.1f}-{hi:.1f}: n{len(b)} p{statistics.fmean(p for p, _ in b):.2f} "
        f"won{statistics.fmean(w for _, w in b):.2f}"
        for lo, hi in ((0, .1), (.1, .2), (.2, .3), (.3, .45), (.45, 1.01))
        for b in [[(r["legacy"][h], r["winner"] == h) for r in both for h in ids if lo <= r["legacy"][h] < hi]] if b))
    fav = [max(ids, key=lambda h: r["legacy"][h]) for r in both]
    print(f"   favourite: priced {statistics.fmean(r['legacy'][f] for r, f in zip(both, fav)):.3f}, won "
          f"{statistics.fmean(r['winner'] == f for r, f in zip(both, fav)):.3f}")

    t15, t20 = start + 3 * 3_600_000, ms("2026-11-07T20:00:00Z")
    oos = [r for r in both if r["now"] >= t15]
    for r in both:
        r["uniform"] = {h: 0.2 for h in ids}
        done = [q for q in both if q["at"] <= r["now"]]
        r["freq"] = {h: (sum(q["winner"] == h for q in done) + 1) / (len(done) + 5) for h in ids}
    names = {"legacy": "legacy (main before this study)", "deployed": "deployed (markets.build_hub_surge now)",
             "uniform": "uniform (ln 5)", "freq": "frequency each hub has won so far"}
    for i, (name, cfg) in enumerate(ZOO.items()):
        key = f"zoo{i}"
        names[key] = name
        for r in both:
            r[key] = zoo_probs(r, store, spots, cfg, draws) if r["now"] >= t15 - 2 * 3_600_000 else r["uniform"]
        print(f"   ... {name}", file=sys.stderr, flush=True)
    for key in [k for k in names if k.startswith("zoo")] + ["deployed"]:
        names[key + "_pw"] = names[key] + " + walk-forward power"
        for r, p in zip(both, walk_forward_power(both, key)):
            r[key + "_pw"] = p
    print(f"\n== 2. Out of sample: {len(oos)} races opened 15:00 → end (selection 15:00-20:00 n="
          f"{sum(r['now'] < t20 for r in oos)}, test 20:00 → end n={sum(r['now'] >= t20 for r in oos)})")
    print(f"   {'model':58} {'LL':>6} {'Brier':>6}   {'ΔLL vs legacy':>16} {'ΔBrier':>16}   "
          f"{'ΔLL 15-20':>16} {'ΔLL 20-end':>16}")
    for key, name in names.items():
        ll = [ll_of(r[key], r["winner"]) for r in oos]
        br = [brier_of(r[key], r["winner"]) for r in oos]
        lc = [ll_of(r["legacy"], r["winner"]) for r in oos]
        bc = [brier_of(r["legacy"], r["winner"]) for r in oos]
        cells = []
        for pair in (paired(ll, lc), paired(br, bc)):
            cells.append(f"{pair[0]:+.3f} ± {pair[1]:.3f}" if key != "legacy" else "")
        for blk in ((t15, t20), (t20, end)):
            idx = [i for i, r in enumerate(oos) if blk[0] <= r["now"] < blk[1]]
            d = paired([ll[i] for i in idx], [lc[i] for i in idx]) if key != "legacy" else None
            cells.append(f"{d[0]:+.3f} ± {d[1]:.3f}" if d else "")
        print(f"   {name:58} {statistics.fmean(ll):6.3f} {statistics.fmean(br):6.3f}   {cells[0]:>16} {cells[1]:>16}"
              f"   {cells[2]:>16} {cells[3]:>16}")
    print(f"   walk-forward power at the end of the window: deployed × {both[-1]['power_deployed']:.2f} "
          f"(HUB_POWER {mk.HUB_POWER} already in it); deployed spec {both[-1]['power_zoo6']:.2f}")

    print(f"\n== 3. Leak rules, 10 points at the open model price, odds 0.95/p ({len(oos)} out-of-sample races; "
          f"in-round rule at the price after the first in-round publish)")
    for key in ("legacy", "deployed"):
        print(f"   {names[key]}")
        for rule, (n, won, mp, roi, lo, hi) in leak_rules(oos, key, grid, ids).items():
            print(f"      {rule:64} n={n:3d} won {won:3d} mean price {mp:.2f}  ROI {roi:+.1%}  [{lo:+.0%}, {hi:+.0%}]")
    print(f"\n   same rules on every race from {datetime.fromtimestamp(both[0]['now'] / 1000, timezone.utc):%H:%M} "
          f"(n={len(both)}, includes the legacy model's in-sample hours)")
    for key in ("legacy", "deployed"):
        print(f"   {names[key]}")
        for rule, (n, won, mp, roi, lo, hi) in leak_rules(both, key, grid, ids).items():
            print(f"      {rule:64} n={n:3d} won {won:3d} mean price {mp:.2f}  ROI {roi:+.1%}  [{lo:+.0%}, {hi:+.0%}]")
    print("\n   deployed calibration (out of sample): " + "  ".join(
        f"{h} p{statistics.fmean(r['deployed'][h] for r in oos):.2f}/"
        f"won{statistics.fmean(r['winner'] == h for r in oos):.2f}"
        for h in ids))


if __name__ == "__main__":
    main()
