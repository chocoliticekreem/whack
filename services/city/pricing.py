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
