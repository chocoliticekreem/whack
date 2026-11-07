"""Pricing: 50 simulated futures per market, pruned particle-filter style by real observations, then LMSR.

Every future starts from the last real reading and moves in small per-minute steps fitted from today's real
minute-to-minute changes, with single steps capped at STEP_CAP sigma (an engine that drifted and spiked lost to
"nothing changes" on real data). prob = share of surviving futures; bets move prices on top through LMSR.
"""
from __future__ import annotations

import math
import random
import statistics

N_FUTURES = 50
RULED_OUT_CAP = 0.6
STEP_CAP = 2.5
P_FLOOR, P_CEIL = 0.03, 0.97
LMSR_B = 1200.0
FEE = 0.05
GOOD_SERVICE = 10
MIN_MS = 60_000


def odds_of(prob: float) -> float:
    return max(1.05, min(20.0, round(0.95 / prob, 1)))


def capped_gauss(rng: random.Random) -> float:
    return max(-STEP_CAP, min(STEP_CAP, rng.gauss(0.0, 1.0)))


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


def fit_counts(series: list[tuple[int, float]]) -> tuple[float, float, int]:
    """(change probability per minute, sigma of log(1+people) on a change, changes seen)."""
    minutes, changes, sizes = 0.0, 0, []
    for (t0, v0), (t1, v1) in zip(series, series[1:]):
        gap = (t1 - t0) / MIN_MS
        if gap <= 0 or gap > 10:
            continue
        minutes += gap
        if v1 != v0:
            changes += 1
            sizes.append(math.log1p(v1) - math.log1p(v0))
    if minutes < 20 or changes < 3:
        return 0.08, 0.4, changes
    sigma = min(1.0, max(0.15, statistics.pstdev(sizes) if len(sizes) > 1 else 0.4))
    return min(0.9, changes / minutes), sigma, changes


def fit_line_hazards(history: list[tuple[int, dict]], modes: tuple[str, ...] = ("tube",)) -> tuple[float, float]:
    """(worsening hazard per line-minute, recovery-to-Good-Service hazard per delayed-line-minute)."""
    exposure = delayed = 0.0
    worse = recover = 0
    for (t0, a), (t1, b) in zip(history, history[1:]):
        gap = (t1 - t0) / MIN_MS
        if gap <= 0 or gap > 10:
            continue
        for lid, s0 in a.items():
            s1 = b.get(lid)
            if s1 is None or s0["mode"] not in modes:
                continue
            exposure += gap
            if s0["sev"] != GOOD_SERVICE:
                delayed += gap
                if s1["sev"] == GOOD_SERVICE:
                    recover += 1
            if s1["sev"] < s0["sev"]:
                worse += 1
    return (worse + 0.5) / (exposure + 120), (recover + 0.5) / (delayed + 60)


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


class CountSim:
    """Over/under: sticky integer people counts; each minute the count changes with the fitted probability.
    The line is set after the futures run (see choose_line)."""

    tol = 0.5

    def __init__(self, cam: str, v0: float, p_change: float, sigma: float, t0: int, minutes: int,
                 rng: random.Random):
        self.cam, self.line, self.t0, self.m = cam, 0.0, t0, minutes
        self.paths = []
        for _ in range(N_FUTURES):
            v, path = v0, [v0]
            for _ in range(minutes):
                if rng.random() < p_change:
                    v = max(0.0, float(round(math.expm1(math.log1p(v) + sigma * capped_gauss(rng)))))
                path.append(v)
            self.paths.append(path)

    def finals(self) -> list[float]:
        return [p[-1] for p in self.paths]

    def outcome(self, i: int) -> str:
        return "over" if self.paths[i][-1] > self.line else "under"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        if self.cam not in snap:
            return None
        k = max(0, min(self.m, round((ts - self.t0) / MIN_MS)))
        return abs(snap[self.cam] - self.paths[i][k])


class BusSim(CountSim):
    """Over/under buses due at a hotspot's stops: each minute the count either holds (fitted persistence) or is
    redrawn from today's real counts at those stops."""

    def __init__(self, spot: str, c0: float, hold: float, pool: list[float], t0: int, minutes: int,
                 rng: random.Random):
        self.cam, self.line, self.t0, self.m = spot, 0.0, t0, minutes
        self.paths = []
        for _ in range(N_FUTURES):
            v, path = c0, [c0]
            for _ in range(minutes):
                if rng.random() >= hold:
                    v = rng.choice(pool)
                path.append(v)
            self.paths.append(path)


class LevelSim(CrowdSim):
    """Over/under one station's % of usual: the CrowdSim walk for a single station, settled against a line (%)."""

    def __init__(self, station: tuple[str, str, float, float, float], t0: int, minutes: int, rng: random.Random):
        super().__init__([station], t0, minutes, rng)
        self.line = 0.0

    def finals(self) -> list[float]:
        return [math.exp(p[0][-1]) * 100 for p in self.paths]

    def outcome(self, i: int) -> str:
        return "over" if math.exp(self.paths[i][0][-1]) * 100 > self.line else "under"


def choose_line(finals: list[float], step: float = 1.0) -> float:
    """Kalshi-style strike: start at the futures' median (rounded to the grid), then take the grid line whose
    over/under split is closest to 50/50 ('over' = strictly above). Ties keep the line nearest the median."""
    med = statistics.median(finals)
    base = round(med / step) * step
    best, best_gap = base, 2.0
    for k in range(-4, 5):
        line = round(base + k * step, 6)
        if line < 0:
            continue
        over = sum(v > line for v in finals) / len(finals)
        gap = abs(over - 0.5)
        if gap < best_gap - 1e-9 or (abs(gap - best_gap) < 1e-9 and abs(line - med) < abs(best - med)):
            best, best_gap = line, gap
    return best


def fit_hold(series: list[tuple[int, float]]) -> tuple[float, list[float]]:
    """(per-minute chance a count holds beyond what independent redraws would give, the pool of real counts)."""
    pool = [v for _, v in series]
    if len(pool) < 10:
        return 0.5, pool or [0.0]
    same = [v1 == v0 for (t0, v0), (t1, v1) in zip(series, series[1:]) if 0 < t1 - t0 <= 2 * MIN_MS]
    q = sum(same) / len(same) if same else 0.5
    counts: dict[float, int] = {}
    for v in pool:
        counts[v] = counts.get(v, 0) + 1
    s = sum((c / len(pool)) ** 2 for c in counts.values())
    hold = (q - s) / (1 - s) if s < 1 else 0.95
    return max(0.0, min(0.95, hold)), pool


class LineAnySim:
    """Any Tube line worse at T than at open: count of lines worse than baseline, per minute."""

    tol = 0.5

    def __init__(self, baseline: dict[str, int], lam_w: float, lam_r: float, t0: int, minutes: int,
                 rng: random.Random):
        self.baseline, self.t0, self.m = baseline, t0, minutes
        self.paths = []
        for _ in range(N_FUTURES):
            worse, path = 0, [0]
            for _ in range(minutes):
                worse += sum(rng.random() < lam_w for _ in range(len(baseline) - worse))
                worse -= sum(rng.random() < lam_r for _ in range(worse))
                path.append(worse)
            self.paths.append(path)

    def outcome(self, i: int) -> str:
        return "yes" if self.paths[i][-1] > 0 else "no"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        obs = any(snap[lid]["sev"] < sev for lid, sev in self.baseline.items() if lid in snap)
        k = max(0, min(self.m, round((ts - self.t0) / MIN_MS)))
        return 0.0 if (self.paths[i][k] > 0) == obs else 1.0


class LineOneSim:
    """One delayed line back to Good Service at T."""

    tol = 0.5

    def __init__(self, line: str, lam_r: float, lam_w: float, t0: int, minutes: int, rng: random.Random):
        self.line, self.t0, self.m = line, t0, minutes
        self.paths = []
        for _ in range(N_FUTURES):
            good, path = False, [False]
            for _ in range(minutes):
                good = (rng.random() >= lam_w) if good else (rng.random() < lam_r)
                path.append(good)
            self.paths.append(path)

    def outcome(self, i: int) -> str:
        return "yes" if self.paths[i][-1] else "no"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        if self.line not in snap:
            return None
        k = max(0, min(self.m, round((ts - self.t0) / MIN_MS)))
        return 0.0 if self.paths[i][k] == (snap[self.line]["sev"] == GOOD_SERVICE) else 1.0


SLOT_MS = 15 * MIN_MS
RAIN_MM = 0.1


def slot_rain_prob(fc_mm: float | None, now_mm: float | None, current: bool) -> float:
    if current and now_mm is not None and now_mm > RAIN_MM:
        return 0.95
    if fc_mm is None:
        return 0.05
    return 0.8 if fc_mm > RAIN_MM else 0.25 if fc_mm > 0 else 0.03


class RainSim:
    """Rain before T: each 15-min Open-Meteo slot in [open, T] is wet or dry, odds from its forecast."""

    tol = 0.5

    def __init__(self, slot_probs: dict[int, float], t0: int, minutes: int, rng: random.Random):
        self.t0, self.m = t0, minutes
        self.paths = [{slot: rng.random() < p for slot, p in slot_probs.items()} for _ in range(N_FUTURES)]

    def outcome(self, i: int) -> str:
        return "yes" if any(self.paths[i].values()) else "no"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        slot = ts - ts % SLOT_MS
        if slot not in self.paths[i]:
            return None
        return 0.0 if self.paths[i][slot] == (snap["mm"] > RAIN_MM) else 1.0


# ---------------------------------------------------------------- particle filter + LMSR


class Futures:
    """Rule out futures that disagree with each real observation; never more than 60% of them in total."""

    def __init__(self, sim, option_ids: list[str]):
        self.sim, self.option_ids = sim, option_ids
        self.alive = [True] * N_FUTURES
        self.ruled_out = 0

    def observe(self, ts: int, snap: dict) -> int:
        errs = [(self.sim.error(i, ts, snap), i) for i in range(N_FUTURES) if self.alive[i]]
        bad = sorted(((e, i) for e, i in errs if e is not None and e > self.sim.tol), reverse=True)
        room = int(RULED_OUT_CAP * N_FUTURES) - self.ruled_out
        for _, i in bad[:max(0, room)]:
            self.alive[i] = False
        cut = min(len(bad), max(0, room))
        self.ruled_out += cut
        return cut

    def probs(self) -> dict[str, float]:
        counts = {o: 0 for o in self.option_ids}
        for i in range(N_FUTURES):
            if self.alive[i]:
                counts[self.sim.outcome(i)] += 1
        return smooth(counts)


class Book:
    """LMSR on top of the model price: prob_i ∝ model_i · exp(q_i / b). A winning share pays 1 point."""

    def __init__(self, option_ids: list[str], b: float = LMSR_B):
        self.b = b
        self.q = {o: 0.0 for o in option_ids}

    def prices(self, model: dict[str, float]) -> dict[str, float]:
        w = {o: model[o] * math.exp(self.q[o] / self.b) for o in self.q}
        s = sum(w.values())
        return {o: v / s for o, v in w.items()}

    def buy(self, model: dict[str, float], option: str, stake: float) -> float:
        """Spend `stake` (5% house fee) on `option`; returns the shares bought."""
        spend = stake * (1 - FEE)
        w = {o: model[o] * math.exp(self.q[o] / self.b) for o in self.q}
        s = sum(w.values())
        new_wi = w[option] + s * (math.exp(spend / self.b) - 1)
        shares = self.b * math.log(new_wi / w[option])
        self.q[option] += shares
        return shares
