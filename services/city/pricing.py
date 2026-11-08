"""Pricing: 50 simulated futures per market, pruned particle-filter style by real observations, then LMSR.

Every future starts from the last real reading and moves in small per-minute steps fitted from today's real
minute-to-minute changes, with single steps capped at STEP_CAP sigma (an engine that drifted and spiked lost to
"nothing changes" on real data). prob = share of surviving futures; bets move prices on top through LMSR.
"""
from __future__ import annotations

import math
import os
import random
import statistics
from decimal import ROUND_FLOOR, Decimal

N_FUTURES = 50
RULED_OUT_CAP = 0.6
STEP_CAP = 2.5
P_FLOOR, P_CEIL = 0.03, 0.97
LMSR_B = 1200.0
FEE = 0.05
MIN_MS = 60_000
MIN_ODDS, MAX_ODDS = 1.05, 20.0


def odds_of(prob: float) -> float:
    return max(1.05, min(20.0, round(0.95 / prob, 1)))


def locked_odds(shares: float, stake: float) -> float | None:
    """The payout multiple a bet locks: shares per point staked, rounded down to 2 dp and capped at 20, so a bet never
    pays more than the market maker's shares. None when that is under 1.05: the price is too short to bet."""
    odds = min(Decimal(str(MAX_ODDS)), Decimal(shares / stake).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))
    return float(odds) if odds >= Decimal(str(MIN_ODDS)) else None


def capped_gauss(rng: random.Random) -> float:
    return max(-STEP_CAP, min(STEP_CAP, rng.gauss(0.0, 1.0)))


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _capped_var(c: float) -> float:
    tail = 1 - norm_cdf(c)
    return 1 - 2 * c * math.exp(-c * c / 2) / math.sqrt(2 * math.pi) - 2 * tail + 2 * c * c * tail


CAPPED_SD = math.sqrt(_capped_var(STEP_CAP))  # sd of one capped_gauss step: 0.9887 at 2.5


def smooth(counts: dict[str, int]) -> dict[str, float]:
    """Share of futures per option, with a half-future prior and a [3%, 97%] clamp so no side is ever free."""
    total = sum(counts.values()) + 0.5 * len(counts)
    raw = {k: (v + 0.5) / total for k, v in counts.items()}
    clamped = {k: min(P_CEIL, max(P_FLOOR, p)) for k, p in raw.items()}
    s = sum(clamped.values())
    return {k: p / s for k, p in clamped.items()}


# ---------------------------------------------------------------- fitting from today's real history


def fit_log_walk(series: list[tuple[int, float]], default_sigma: float, floor: float, ceil: float,
                 drift_window_ms: int = 3_600_000) -> tuple[float, float]:
    """(drift, sigma) per minute of a log-valued series. Sigma from per-step changes scaled by sqrt(gap);
    drift is the last hour's mean change, halved and capped at a fifth of sigma."""
    steps, recent = [], []
    end = series[-1][0] if series else 0
    for (t0, x0), (t1, x1) in zip(series, series[1:]):
        gap = (t1 - t0) / MIN_MS
        if gap <= 0 or gap > 30:
            continue
        steps.append((x1 - x0) / math.sqrt(gap))
        if t1 >= end - drift_window_ms:
            recent.append((x1 - x0) / gap)
    if len(steps) < 4:
        return 0.0, default_sigma
    sigma = min(ceil, max(floor, statistics.pstdev(steps)))
    drift = 0.5 * statistics.fmean(recent) if recent else 0.0
    return max(-sigma / 5, min(sigma / 5, drift)), sigma


def usual_drift(typ_from: float | None, typ_to: float | None, minutes: int) -> float:
    """Per-minute log drift that carries a reading along TfL's usual-day curve from typ_from to typ_to over `minutes`.
    Without it a walk holds the live % flat while the usual day rises or falls, and copying the usual curve beat the
    house by 32-48% a bet (docs/judge-prep.md)."""
    if not typ_from or not typ_to:
        return 0.0
    return math.log(typ_to / typ_from) / max(1, minutes)


# ---------------------------------------------------------------- simulators


class CrowdSim:
    """Versus: log % of usual per station, a capped random walk from the last real reading."""

    tol = 1.5

    def __init__(self, stations: list[tuple[str, str, float, float, float]], t0: int, minutes: int, rng: random.Random):
        self.stations = stations  # (option id, naptan, last log value, drift, sigma) per station
        self.t0, self.m = t0, minutes
        self.paths = [[self.walk(x0, mu, sd, rng) for _, _, x0, mu, sd in stations] for _ in range(N_FUTURES)]

    def walk(self, x: float, mu: float, sd: float, rng: random.Random) -> list[float]:
        out = [x]
        for _ in range(self.m):
            x += mu + sd * capped_gauss(rng)
            out.append(x)
        return out

    def outcome(self, i: int) -> str:
        finals = [p[-1] for p in self.paths[i]]
        return self.stations[finals.index(max(finals))][0]

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        k = max(0, min(self.m, round((ts - self.t0) / MIN_MS)))
        errs = [abs(math.log(snap[nap]) - self.paths[i][j][k]) / sd
                for j, (_, nap, _, _, sd) in enumerate(self.stations) if nap in snap]
        return max(errs) if errs else None


class LevelSim(CrowdSim):
    """Bracket ladder on one station's % of usual: the CrowdSim walk for a single station."""

    def __init__(self, station: tuple[str, str, float, float, float], t0: int, minutes: int, rng: random.Random):
        super().__init__([station], t0, minutes, rng)
        self.edges: list[float] = []

    def finals(self) -> list[float]:
        return [math.exp(p[0][-1]) * 100 for p in self.paths]

    def outcome(self, i: int) -> str:
        return bracket_of(math.exp(self.paths[i][0][-1]) * 100, self.edges)


class RaceSim(CrowdSim):
    """Versus on "vs usual" (live % / the typical % for that weekday and 15-min band), one log walk per hub.

    mode "gain": biggest rise in vs-usual since the frozen open reading (hub_surge). mode "level": highest vs-usual
    at settle (hub_hour). typ0 / typ_t: each hub's typical value at open and at the expected settle reading.
    Ties go to the first hub in the fixed order."""

    def __init__(self, stations, t0: int, minutes: int, rng: random.Random, typ0: list[float],
                 typ_t: list[float], mode: str):
        super().__init__(stations, t0, minutes, rng)
        self.typ0, self.typ_t, self.mode = typ0, typ_t, mode

    def score(self, i: int, j: int) -> float:
        vs = math.exp(self.paths[i][j][-1]) / self.typ_t[j]
        return vs - math.exp(self.stations[j][2]) / self.typ0[j] if self.mode == "gain" else vs

    def outcome(self, i: int) -> str:
        scores = [self.score(i, j) for j in range(len(self.stations))]
        return self.stations[scores.index(max(scores))][0]


Z_STEP = 0.25
Z_GRID = [k * Z_STEP for k in range(-24, 25)]
Z_PDF = [math.exp(-z * z / 2) / math.sqrt(2 * math.pi) * Z_STEP for z in Z_GRID]


def race_level_probs(sim: RaceSim, xs: list[float], k: int) -> dict[str, float]:
    """P(each hub wins) given every hub's log live % at minute k, with the m - k steps left integrated out (Rao-
    Blackwellised): each hub's log vs usual at settle is Gaussian, mean x_k + mu·r - log typ_t, sd sd·CAPPED_SD·√r, the
    hubs independent (CrowdSim walks them independently). P(j) = ∫ φ(z) Π_{l≠j} Φ((a_j + s_j z - a_l) / s_l) dz by the
    trapezoid rule on a 0.25 grid over ±6 (exponentially accurate on a Gaussian-weighted smooth integrand: under 1e-9
    off a 0.01 grid). With no minutes left the highest vs usual wins, ties to the first."""
    r = sim.m - k
    a = [x + mu * r - math.log(t) for x, (_, _, _, mu, _), t in zip(xs, sim.stations, sim.typ_t)]
    if r <= 0:
        return {sim.stations[a.index(max(a))][0]: 1.0}
    sd = [s * CAPPED_SD * math.sqrt(r) for *_, s in sim.stations]
    out = {}
    for j, (oid, *_) in enumerate(sim.stations):
        acc = 0.0
        for z, w in zip(Z_GRID, Z_PDF):
            y = a[j] + sd[j] * z
            prod = w
            for l in range(len(a)):
                if l != j:
                    prod *= norm_cdf((y - a[l]) / sd[l])
            acc += prod
        out[oid] = acc
    total = sum(out.values())
    return {o: p / total for o, p in out.items()}


def cholesky(corr: list[list[float]]) -> list[list[float]]:
    """Lower-triangular L with L·Lᵀ = corr (a positive-definite correlation matrix)."""
    n = len(corr)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(low[i][k] * low[j][k] for k in range(j))
            low[i][j] = math.sqrt(corr[i][i] - s) if i == j else (corr[i][j] - s) / low[j][j]
    return low


class RelativeRaceSim(RaceSim):
    """hub_surge: each hub's log live busyness moves by its usual-day slope (station drift), is pulled toward the five
    hubs' mean log vs usual (at the open band's typicals) at `kappa` per minute, and takes shocks correlated across hubs
    through `chol` (Cholesky factor of their correlation). `power` tempers the futures' shares in Futures.probs
    (p ∝ share^power). Settles like RaceSim "gain": vs usual at the settle band minus vs usual at open."""

    def __init__(self, stations, t0: int, minutes: int, rng: random.Random, typ0: list[float], typ_t: list[float],
                 kappa: float, chol: list[list[float]], power: float):
        self.stations, self.t0, self.m = stations, t0, minutes
        self.typ0, self.typ_t, self.mode, self.power = typ0, typ_t, "gain", power
        self.kappa, self.chol, self.rng, self.seen = kappa, chol, rng, t0
        self.paths = [self.walk_all(kappa, chol, rng) for _ in range(N_FUTURES)]

    def restart(self, ts: int, snap: dict) -> bool:
        """A TfL reading stamped after the last one: walk every future again from it for the minutes left (the open
        values still anchor each hub's gain). Pruning fixed paths on it priced the hub leading since open at 0.27
        while it won 46% of the time (analysis/judge/fit_crowd.py). The tempering power is Platt-fitted on open
        prices only; after a restart the shares stand untempered (better in-round Brier and log loss, both windows)."""
        if ts <= self.seen or any(nap not in snap for _, nap, *_ in self.stations):
            return False
        self.seen, self.power = ts, 1.0
        k = max(0, min(self.m, round((ts - self.t0) / MIN_MS)))
        now = [math.log(snap[nap]) for _, nap, *_ in self.stations]
        self.paths = [self.walk_all(self.kappa, self.chol, self.rng, now, k) for _ in range(N_FUTURES)]
        return True

    def walk_all(self, kappa: float, chol: list[list[float]], rng: random.Random, start: list[float] | None = None,
                 k0: int = 0) -> list[list[float]]:
        log_typ = [math.log(t) for t in self.typ0]
        x_open = [s[2] for s in self.stations]
        xs = start or x_open
        paths = [[a + (b - a) * i / k0 for i in range(k0)] + [b] for a, b in zip(x_open, xs)]
        for _ in range(self.m - k0):
            dev = [x - lt for x, lt in zip(xs, log_typ)]
            mean = sum(dev) / len(dev)
            e = [capped_gauss(rng) for _ in xs]
            xs = [x + mu - kappa * (d - mean) + sd * sum(c * v for c, v in zip(row, e))
                  for x, d, (_, _, _, mu, sd), row in zip(xs, dev, self.stations, chol)]
            for path, x in zip(paths, xs):
                path.append(x)
        return paths


def fit_relative_ou(grid: dict[int, dict[str, tuple[float, float]]], ids: list[str], step_min: float = 5.0):
    """Fit RelativeRaceSim on readings binned into consecutive slots step_min apart: {slot: {hub: (log live,
    log vs usual)}}. From every slot where all hubs read, d log live_i to the next slot = -k * dev_i + e_i, dev_i =
    log vs usual_i minus the hubs' mean. Returns (kappa per minute, {hub: innovation sd per minute},
    innovation correlation in `ids` order, joint steps used)."""
    xs, ys, joint = [], [], []
    for t, row in sorted(grid.items()):
        nxt = grid.get(t + 1, {})
        if any(h not in row for h in ids) or not nxt:
            continue
        mean = sum(row[h][1] for h in ids) / len(ids)
        step = {h: (row[h][1] - mean, nxt[h][0] - row[h][0]) for h in ids if h in nxt}
        for h, (d, dx) in step.items():
            xs.append((h, d))
            ys.append(dx)
        if len(step) == len(ids):
            joint.append(step)
    k = -sum(d * dx for (_, d), dx in zip(xs, ys)) / sum(d * d for _, d in xs)
    resid = {h: [] for h in ids}
    for (h, d), dx in zip(xs, ys):
        resid[h].append(dx + k * d)
    sds = {h: statistics.pstdev(r) / math.sqrt(step_min) for h, r in resid.items()}
    e = {h: [s[h][1] + k * s[h][0] for s in joint] for h in ids}
    corr = [[statistics.correlation(e[a], e[b]) if a != b else 1.0 for b in ids] for a in ids]
    return -math.log(1 - k) / step_min, sds, corr, len(joint)


PULSE_MOMENTUM = 0.30
PULSE_TREND_MIN = 10
STAMP_TOL_MS = 150_000


def fit_pulse(devs: list[tuple[int, float]], t0: int, minutes: int) -> float | None:
    """Per-minute drift of City Pulse around the usual day: PULSE_MOMENTUM x the change in log pulse vs usual over
    the PULSE_TREND_MIN before the open reading, spread over the walk's `minutes`. devs = (TfL stamp, log pulse vs
    usual) readings polled before the open; t0 = the open reading's stamp. None (the caller keeps the plain walk)
    without a reading at t0 and one within STAMP_TOL_MS of 10 min before it.

    TfL's readings trend: 0.30 of the last 10 min's move in vs usual carried on to the settle reading 15-20 min later
    (fitted on markets opened 12:00-18:00 by analysis/judge/pulse_model.py). The plain walk's drift, half the last
    hour's mean change capped at sigma/5, carried too little of it: the evening's slides ran past even-money lines."""
    target = t0 - PULSE_TREND_MIN * MIN_MS
    before = min(devs, key=lambda d: abs(d[0] - target), default=None)
    last = dict(devs).get(t0)
    if last is None or before is None or abs(before[0] - target) > STAMP_TOL_MS:
        return None
    return PULSE_MOMENTUM * (last - before[1]) / max(1, minutes)


class ScalarSim:
    """Over/under on one positive number (City Pulse): a capped log walk from the last real reading.
    value_of(snapshot) reads the number from a feed snapshot (None = not enough data in it)."""

    tol = 1.5

    def __init__(self, x0: float, mu: float, sd: float, t0: int, minutes: int, rng: random.Random,
                 value_of, strike: float):
        self.t0, self.m, self.sd, self.value_of, self.strike = t0, minutes, sd, value_of, strike
        self.paths = []
        for _ in range(N_FUTURES):
            x, path = math.log(x0), [math.log(x0)]
            for _ in range(minutes):
                x += mu + sd * capped_gauss(rng)
                path.append(x)
            self.paths.append(path)

    def outcome(self, i: int) -> str:
        return "over" if math.exp(self.paths[i][-1]) > self.strike else "under"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        v = self.value_of(snap)
        if not v:
            return None
        k = max(0, min(self.m, round((ts - self.t0) / MIN_MS)))
        return abs(math.log(v) - self.paths[i][k]) / self.sd


class DeltaSim:
    """Over/under from today's own h-minute changes (a bootstrap): each future = the open value plus one past
    change of the same horizon (London VIX, Heathrow stacks). Observations mid-round rule out futures whose
    straight-line path to their final value disagrees with the reading."""

    tol = 1.5

    def __init__(self, x0: float, deltas: list[float], t0: int, minutes: int, rng: random.Random,
                 value_of, strike: float):
        self.x0, self.t0, self.m, self.value_of, self.strike = x0, t0, minutes, value_of, strike
        self.finals = [x0 + rng.choice(deltas) for _ in range(N_FUTURES)]
        self.sd = max(statistics.pstdev(deltas), 0.05)

    def outcome(self, i: int) -> str:
        return "over" if self.finals[i] > self.strike else "under"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        v = self.value_of(snap)
        if v is None:
            return None
        frac = max(0.0, min(1.0, (ts - self.t0) / (self.m * MIN_MS)))
        expected = self.x0 + (self.finals[i] - self.x0) * frac
        return abs(v - expected) / (self.sd * max(0.5, math.sqrt(frac)))


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method; fine for the small means a holding count has (under ~30)."""
    limit, k, p = math.exp(-lam), 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


class CountSim(DeltaSim):
    """Over/under on a count that reverts to its recent level (Heathrow stacks). Each future's final count is a
    Poisson draw around the open count pulled kappa of the way to the trailing mean per 5 minutes. A bootstrap of
    raw changes ignored where the count stood against that mean, so a stack that had just built was priced as
    likely to keep building as to drain, and fading it won (analysis/judge/fit_heathrow.py).

    In-round readings set the weights to the same model restarted from that reading (posterior), not DeltaSim's
    straight-line pruning: the count is close to a fresh Poisson draw every minute (unchanged minute to minute only
    16-27% of the time today), so pruning on one reading put the halfway price at 3/97 and it was wrong a third of
    the time."""

    LAMBDA_FLOOR = 0.3

    def __init__(self, x0: float, recent: list[float], kappa_5min: float, t0: int, minutes: int, rng: random.Random,
                 value_of, strike: float):
        self.x0, self.t0, self.m, self.value_of, self.strike = x0, t0, minutes, value_of, strike
        self.kappa, self.level_sum, self.level_n = kappa_5min, sum(recent), len(recent)
        self.lam = self.mean_after(x0, sum(recent) / len(recent), minutes)
        self.lam_now: float | None = self.lam  # None once the settle reading is in: the count is then known
        self.known: float | None = None
        self.finals = [float(poisson(rng, self.lam)) for _ in range(N_FUTURES)]
        self.prior = [poisson_pmf(self.lam, f) for f in self.finals]
        self.sd = max(math.sqrt(self.lam), 0.5)

    def mean_after(self, x: float, level: float, minutes: float) -> float:
        return max(self.LAMBDA_FLOOR, x - (1 - (1 - self.kappa) ** (minutes / 5)) * (x - level))

    def posterior(self, ts: int, snap: dict) -> list[float] | None:
        """Importance weights that turn the open's Poisson futures into the model's distribution given this reading:
        the count reverting from it, toward the trailing mean with the round's readings added, for the minutes left."""
        v = self.value_of(snap)
        if v is None:
            return None
        self.level_sum, self.level_n = self.level_sum + v, self.level_n + 1
        left = (self.t0 + self.m * MIN_MS - ts) / MIN_MS
        if left <= 0:
            self.lam_now, self.known = None, v
            return [1.0 if f == v else 1e-9 for f in self.finals]
        lam = self.lam_now = self.mean_after(v, self.level_sum / self.level_n, left)
        return [poisson_pmf(lam, f) / q for f, q in zip(self.finals, self.prior)]

    def exact(self, seen) -> dict[str, float]:
        """P(over) = P(Poisson(λ) > strike) under the current λ, in closed form: what the importance-weighted futures
        estimate, without their sampling error."""
        if self.lam_now is None:
            over = float(self.known > self.strike)
        else:
            over = 1 - sum(poisson_pmf(self.lam_now, n) for n in range(int(math.floor(self.strike)) + 1))
        return {"over": over, "under": 1 - over}


def poisson_pmf(lam: float, k: float) -> float:
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def count_line(finals: list[float]) -> float:
    """The half point next to the futures' median count whose Over share is nearest 50%, never under 0.5: the market
    opens near 50/50 and a whole-number count can never tie it."""
    med = statistics.median_low(finals)

    def miss(line: float) -> tuple[float, float]:
        return abs(sum(f > line for f in finals) / len(finals) - 0.5), line

    return min((c for c in (med - 0.5, med + 0.5) if c >= 0.5), key=miss)


def poisson_line(lam: float) -> float:
    """count_line on the exact Poisson(λ) the futures are drawn from: the half point, never under 0.5, whose
    P(over) is nearest 50% (ties to the lower). Placing it on the 50 draws moved it with the seed, and the opening
    price with it (9 points at worst on the recorded day, analysis/judge/mc_error.py)."""
    cdf, n, best = 0.0, 0, None
    while n <= lam + 10 * math.sqrt(lam) + 2:
        cdf += poisson_pmf(lam, n)
        miss = abs(1 - cdf - 0.5)
        if best is None or miss < best[0]:
            best = (miss, n + 0.5)
        n += 1
    return best[1]


class BreakSim:
    """Clean sheet: every future draws when the first Good line breaks (exponential, P(break by T) = p_break).
    A reading that shows every Good line still Good rules out the futures that had already broken by then
    (and vice versa), so the price drifts toward YES as a clean hour runs down."""

    tol = 0.5

    def __init__(self, p_break: float, good: list[str], t0: int, minutes: int, rng: random.Random):
        lam = self.lam = -math.log(max(1e-6, 1 - p_break)) / max(1, minutes)
        self.good, self.t0, self.m = good, t0, minutes
        self.break_at = [rng.expovariate(lam) for _ in range(N_FUTURES)]

    def outcome(self, i: int) -> str:
        return "no" if self.break_at[i] <= self.m else "yes"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        seen = [snap[l]["sev"] for l in self.good if l in snap]
        if not seen:
            return None
        broke = any(s != GOOD_SEVERITY for s in seen)
        return 0.0 if broke == (self.break_at[i] <= (ts - self.t0) / MIN_MS) else 9.0

    def exact(self, seen: tuple[int, dict] | None) -> dict[str, float]:
        """The exponential break time is memoryless: with every Good line still Good at the newest reading,
        P(no) = 1 - exp(-λ · minutes left); a break already seen makes it 1."""
        elapsed = 0.0
        if seen is not None:
            ts, snap = seen
            if any(snap[l]["sev"] != GOOD_SEVERITY for l in self.good if l in snap):
                return {"yes": 0.0, "no": 1.0}
            elapsed = (ts - self.t0) / MIN_MS
        no = 1 - math.exp(-self.lam * max(0.0, self.m - elapsed))
        return {"yes": 1 - no, "no": no}


GOOD_SEVERITY = 10


def fit_break_hazard(snaps: list[tuple[int, dict]], lines: list[str]) -> tuple[int, float]:
    """(breaks, Good line-minutes) over line-status snapshots: a break is a line going from Good to anything
    else between two readings. breaks / line-minutes is the per-line hazard per minute."""
    breaks, minutes = 0, 0.0
    for (t0, a), (t1, b) in zip(snaps, snaps[1:]):
        gap = min(10.0, (t1 - t0) / MIN_MS)
        for line in lines:
            if line in a and line in b and a[line]["sev"] == GOOD_SEVERITY:
                minutes += gap
                breaks += b[line]["sev"] != GOOD_SEVERITY
    return breaks, minutes


def deltas_at(series: list[tuple[int, float]], minutes: int, tol_ms: int = 40_000) -> list[float]:
    """Every h-minute change in a (ts, value) series: for each reading, the reading closest to ts + h (within tol)."""
    out, j = [], 0
    for ts, v in series:
        target = ts + minutes * MIN_MS
        while j < len(series) - 1 and series[j][0] < target - tol_ms:
            j += 1
        best = min((p for p in series[j:j + 4] if abs(p[0] - target) <= tol_ms),
                   key=lambda p: abs(p[0] - target), default=None)
        if best is not None:
            out.append(best[1] - v)
    return out


MIN_BRACKET = 0.08
FLOOR_BRACKET = 0.05
TARGET_FAVOURITE = 0.40


def bracket_of(value: float, edges: list[float]) -> str:
    """'b0'..'bN': lower edges inclusive, upper exclusive (value == edge goes to the bracket above)."""
    return f"b{sum(value >= e for e in edges)}"


def bracket_probs(finals: list[float], edges: list[float]) -> list[float]:
    counts = {f"b{k}": 0 for k in range(len(edges) + 1)}
    for v in finals:
        counts[bracket_of(v, edges)] += 1
    return list(smooth(counts).values())


def placement_score(finals: list[float], edges: list[float]) -> tuple[bool, bool, float, float]:
    """Lower is better. No bracket under 5%; then a favourite in a middle bracket (a ladder centred on the best
    guess, not 'Under/Over' tails); then every bracket at or above 8% (else the smallest shortfall); then a
    favourite nearest 40%: a clear best guess without a lock (a flat 25/25/25/25 ladder is a lottery)."""
    probs = bracket_probs(finals, edges)
    outer = probs.index(max(probs)) in (0, len(probs) - 1)
    return (min(probs) < FLOOR_BRACKET, outer, round(max(0.0, MIN_BRACKET - min(probs)), 6),
            round(abs(max(probs) - TARGET_FAVOURITE), 6))


def place_pct_brackets(finals: list[float], widths: tuple[float, ...]) -> list[float]:
    """Three equal-width edges (four brackets) around the futures' median, on whole numbers (half points only for
    the 0.5-wide ladder of a very quiet station). Ties go to the wider, rounder width."""
    med = statistics.median(finals)
    best = None
    for w in widths:
        grid = min(w, 1)
        base = round(med / grid) * grid
        for k in range(-8, 9):
            mid = base + k * grid
            edges = [round(mid - w, 6), round(mid, 6), round(mid + w, 6)]
            if edges[0] < grid:
                continue
            score = (*placement_score(finals, edges), -w)
            if best is None or score < best[0]:
                best = (score, edges)
    return best[1]


RAIN_MM = 0.1


# ---------------------------------------------------------------- the central-London agent simulation

# Default 0: the central sim loses to persistence (docs/judge-prep.md), so it trades as the Simulation trader but
# stays out of the price unless SIM_WEIGHT is set.
SIM_WEIGHT = max(0.0, min(1.0, float(os.environ.get("SIM_WEIGHT", "0"))))
SIM_RHO = 0.5
Z90 = 1.2816


def sim_dist(fc: dict, naptan: str, target_ms: int) -> tuple[float, float] | None:
    """(mean, sd) of a station's vs usual (fraction, 1.0 = usual) at target_ms from the sim's +5/+15/+60 forecasts,
    linear between horizons and held flat outside them; p10/p90 read as a normal's (sd = (p90 - p10) / 2.563)."""
    st = fc["stations"].get(naptan)
    if not st:
        return None
    pts = sorted((int(h), f) for h, f in st["h"].items())
    h = (target_ms - fc["base"]) / MIN_MS
    lo = max((p for p in pts if p[0] <= h), default=pts[0], key=lambda p: p[0])
    hi = min((p for p in pts if p[0] >= h), default=pts[-1], key=lambda p: p[0])
    a = 0.0 if hi[0] == lo[0] else (h - lo[0]) / (hi[0] - lo[0])
    mix = {k: (lo[1][k] + a * (hi[1][k] - lo[1][k])) / 100 for k in ("mean", "p10", "p90")}
    return mix["mean"], max(1e-3, (mix["p90"] - mix["p10"]) / (2 * Z90))


def sim_draws(fc: dict, naptans: list[str], target_ms: int, rng: random.Random, n: int) -> list[dict] | None:
    """n joint draws {naptan: vs usual} of the given stations, or None unless the sim covers every one.
    debt: the sim publishes marginals only; stations share one city-wide shock with correlation SIM_RHO.
    ceiling: real cross-station correlation unknown. upgrade: when the sim publishes per-world station values."""
    dists = {nap: d for nap in naptans if (d := sim_dist(fc, nap, target_ms))}
    if len(dists) < len(naptans):
        return None
    out = []
    for _ in range(n):
        common = rng.gauss(0.0, 1.0)
        out.append({nap: max(0.01, mu + sd * (math.sqrt(SIM_RHO) * common + math.sqrt(1 - SIM_RHO) * rng.gauss(0, 1)))
                    for nap, (mu, sd) in dists.items()})
    return out


def sim_count() -> int:
    """Futures sampled from the simulation: SIM_WEIGHT of the 50 (env SIM_WEIGHT, default 0)."""
    return round(SIM_WEIGHT * N_FUTURES)


def bridge(x0: float, x1: float, minutes: int, sd: float, rng: random.Random) -> list[float]:
    """A capped-step random walk from x0 pinned to x1 after `minutes` (a Brownian bridge), so a sim-sampled future
    has a path the particle filter can score against mid-round readings like any other."""
    w = [0.0]
    for _ in range(minutes):
        w.append(w[-1] + sd * capped_gauss(rng))
    m = max(1, minutes)
    return [x0 + (x1 - x0) * k / m + (w[k] - w[-1] * k / m) for k in range(minutes + 1)]


def tag_sim(sim, n: int) -> range:
    """Mark the last n of the sim's futures as sim-sampled; returns their indices."""
    sim.origins = ["stat"] * (N_FUTURES - n) + ["sim"] * n
    return range(N_FUTURES - n, N_FUTURES)


def blend_race(race: RaceSim, draws: list[dict], rng: random.Random) -> int:
    """Replace the last len(draws) futures of a race with sim draws: each station's path ends at its drawn vs usual
    times its typical value at settle."""
    for i, d in zip(tag_sim(race, len(draws)), draws):
        race.paths[i] = [bridge(x0, math.log(d[nap] * race.typ_t[j]), race.m, sd, rng)
                         for j, (_, nap, x0, _, sd) in enumerate(race.stations)]
    return len(draws)


def blend_scalar(sim: ScalarSim, finals: list[float], rng: random.Random) -> int:
    """Replace the last len(finals) futures of a scalar walk with paths ending at the sim-derived finals."""
    for i, v in zip(tag_sim(sim, len(finals)), finals):
        sim.paths[i] = bridge(sim.paths[i][0], math.log(v), sim.m, sim.sd, rng)
    return len(finals)


# ---------------------------------------------------------------- particle filter + LMSR


MIN_ESS = 0.5


def ess(weights) -> float:
    w = list(weights)
    return sum(w) ** 2 / sum(x * x for x in w) if any(w) else 0.0


def temper(weights: dict[int, float], floor: float) -> dict[int, float]:
    """Raise the weights to the largest power in (0, 1] that keeps their effective sample size at `floor` or more,
    so 50 fixed paths never collapse onto two or three (no resampling to refresh them)."""
    if ess(weights.values()) >= floor:
        return weights
    top = max(weights.values())
    rel = {i: w / top for i, w in weights.items()}
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if ess(r ** mid for r in rel.values()) >= floor:
            lo = mid
        else:
            hi = mid
    return {i: r ** lo for i, r in rel.items()}


class Futures:
    """A weighted particle filter over the 50 futures. Every reading multiplies each surviving future's weight by its
    likelihood exp(-e²/2), e = the future's error in sigmas. Futures off by more than `tol` are also ruled out, never
    more than 60% in total, so the fan keeps survivors; after that cap the weights still move the price (before, 80% of
    markets stopped learning at the cap)."""

    def __init__(self, sim, option_ids: list[str]):
        self.sim, self.option_ids = sim, option_ids
        self.alive = [True] * N_FUTURES
        self.weights = [1.0] * N_FUTURES
        self.ruled_out = 0
        self.origins: list[str] = getattr(sim, "origins", None) or ["stat"] * N_FUTURES
        self.seen: tuple[int, dict] | None = None  # the newest reading that told the futures anything
        self.predicted: dict[tuple, dict[str, float]] = {}

    @property
    def from_sim(self) -> int:
        return self.origins.count("sim")

    def observe(self, ts: int, snap: dict) -> int:
        if hasattr(self.sim, "restart") and self.sim.restart(ts, snap):
            self.alive, self.weights, self.ruled_out = [True] * N_FUTURES, [1.0] * N_FUTURES, 0
            return 0
        if hasattr(self.sim, "posterior"):
            weights = self.sim.posterior(ts, snap)
            if weights is not None:
                scale = N_FUTURES / sum(weights)
                self.weights = [w * scale for w in weights]
            return 0
        errs = [(self.sim.error(i, ts, snap), i) for i in range(N_FUTURES) if self.alive[i]]
        if any(e is not None for e, _ in errs) and (self.seen is None or ts >= self.seen[0]):
            self.seen = (ts, snap)
        self.reweight([(e, i) for e, i in errs if e is not None])
        bad = sorted(((e, i) for e, i in errs if e is not None and e > self.sim.tol), reverse=True)
        room = int(RULED_OUT_CAP * N_FUTURES) - self.ruled_out
        for _, i in bad[:max(0, room)]:
            self.alive[i] = False
        cut = min(len(bad), max(0, room))
        self.ruled_out += cut
        return cut

    def reweight(self, errs: list[tuple[float, int]]) -> None:
        """Multiply in each future's likelihood (in log space, relative to the best future, so nothing underflows),
        temper to the ESS floor, renormalise to mean 1."""
        if not errs:
            return
        logs = {i: math.log(max(self.weights[i], 1e-300)) - 0.5 * min(e / self.sim.tol, 30.0) ** 2 for e, i in errs}
        top = max(logs.values())
        new = temper({i: math.exp(v - top) for i, v in logs.items()}, MIN_ESS * len(logs))
        total = sum(new.values())
        scale = len(new) / total
        for i, w in new.items():
            self.weights[i] = w * scale

    def race_state(self) -> tuple[int, list[float]] | None:
        """hub_hour's state where it is known: every hub's log live % at the open, or at the newest reading that has
        all of them (TfL readings carry no measurable noise: the lag-1 autocovariance of their changes puts it at
        0-2%, analysis/judge/pf_audit.py). None when the newest reading misses a hub."""
        sim = self.sim
        if self.seen is None:
            return 0, [x for _, _, x, _, _ in sim.stations]
        ts, snap = self.seen
        if any(nap not in snap for _, nap, *_ in sim.stations):
            return None
        return max(0, min(sim.m, round((ts - sim.t0) / MIN_MS))), [math.log(snap[nap]) for _, nap, *_ in sim.stations]

    def race_probs(self, i: int, k: int) -> dict[str, float]:
        key = (k, tuple(p[k] for p in self.sim.paths[i]))
        if key not in self.predicted:
            self.predicted[key] = race_level_probs(self.sim, list(key[1]), k)
        return self.predicted[key]

    def exact(self) -> dict[str, float] | None:
        """The closed-form probabilities where the model has them: the sim's own posterior (CountSim, BreakSim), or
        hub_hour's race from its known state. Futures sampled from the city sim are bridges with other dynamics, so
        a market with any keeps the futures' shares."""
        if self.from_sim:
            return None
        if hasattr(self.sim, "exact"):
            return self.sim.exact(self.seen)
        if type(self.sim) is RaceSim and self.sim.mode == "level" and (state := self.race_state()):
            key = (state[0], tuple(state[1]))
            if key not in self.predicted:
                self.predicted[key] = race_level_probs(self.sim, state[1], state[0])
            return {o: self.predicted[key].get(o, 0.0) for o in self.option_ids}
        return None

    def probs(self) -> dict[str, float]:
        """Weighted share of the surviving futures per option, through `smooth`, or the exact probabilities as
        N_FUTURES futures' worth where the model has them (`exact`, zero Monte Carlo error). hub_hour after a reading
        that misses a hub counts each future with its Rao-Blackwellised win probabilities from its own walk."""
        exact = self.exact()
        if exact is not None:
            counts = {o: exact[o] * N_FUTURES for o in self.option_ids}
        else:
            counts = {o: 0.0 for o in self.option_ids}
            alive = [i for i in range(N_FUTURES) if self.alive[i]]
            total = sum(self.weights[i] for i in alive)
            race = type(self.sim) is RaceSim and self.sim.mode == "level" and not self.from_sim
            k = max(0, min(self.sim.m, round((self.seen[0] - self.sim.t0) / MIN_MS))) if race else 0
            for i in alive:
                share = self.weights[i] * len(alive) / total if total > 0 else 1.0
                if race:
                    for o, p in self.race_probs(i, k).items():
                        counts[o] += share * p
                else:
                    counts[self.sim.outcome(i)] += share
        probs = smooth(counts)
        power = getattr(self.sim, "power", 1.0)
        if power == 1.0:
            return probs
        tempered = {o: p ** power for o, p in probs.items()}
        s = sum(tempered.values())
        return {o: p / s for o, p in tempered.items()}


def log_sum_exp(xs) -> float:
    xs = list(xs)
    top = max(xs)
    return top + math.log(sum(math.exp(x - top) for x in xs))


class Book:
    """LMSR on top of the model price: prob_i ∝ model_i · exp(q_i / b), the price of Hanson's cost function
    C(q) = b · ln Σ model_i · exp(q_i / b). A winning share pays 1 point. Everything runs in log space, so no position
    overflows (exp(q/b) did past q ≈ 850,000 shares)."""

    def __init__(self, option_ids: list[str], b: float = LMSR_B):
        self.b = b
        self.q = {o: 0.0 for o in option_ids}

    def log_weights(self, model: dict[str, float]) -> dict[str, float]:
        return {o: (math.log(model[o]) if model[o] > 0 else -math.inf) + self.q[o] / self.b for o in self.q}

    def cost(self, model: dict[str, float]) -> float:
        """C(q) under `model`: what the shares sold so far have cost, net of fees, plus the house's subsidy C(0) = 0."""
        return self.b * log_sum_exp(self.log_weights(model).values())

    def prices(self, model: dict[str, float]) -> dict[str, float]:
        lw = self.log_weights(model)
        lse = log_sum_exp(lw.values())
        return {o: math.exp(v - lse) for o, v in lw.items()}

    def quote(self, model: dict[str, float], option: str, stake: float) -> float:
        """Shares that `stake` (5% house fee) buys on `option`. Does not touch the book; `commit` books them.
        Solves C(q + δ·e_i) − C(q) = spend: δ = b · ln(1 + (e^(spend/b) − 1) / p_i), as
        b · (logaddexp(ln(e^x − 1), ln p_i) − ln p_i) with x = spend/b, which holds for any x and any p_i > 0."""
        x = stake * (1 - FEE) / self.b
        if x <= 0:
            return 0.0
        lw = self.log_weights(model)
        log_p = lw[option] - log_sum_exp(lw.values())
        log_em1 = math.log(math.expm1(x)) if x < 30 else x + math.log1p(-math.exp(-x))
        return self.b * (log_sum_exp((log_em1, log_p)) - log_p)

    def commit(self, option: str, shares: float) -> None:
        self.q[option] += shares

    def buy(self, model: dict[str, float], option: str, stake: float) -> float:
        shares = self.quote(model, option, stake)
        self.commit(option, shares)
        return shares
