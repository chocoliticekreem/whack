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


class BreakSim:
    """Clean sheet: every future draws when the first Good line breaks (exponential, P(break by T) = p_break).
    A reading that shows every Good line still Good rules out the futures that had already broken by then
    (and vice versa), so the price drifts toward YES as a clean hour runs down."""

    tol = 0.5

    def __init__(self, p_break: float, good: list[str], t0: int, minutes: int, rng: random.Random):
        lam = -math.log(max(1e-6, 1 - p_break)) / max(1, minutes)
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
