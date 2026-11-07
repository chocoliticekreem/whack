"""City markets: the series registry (the recurring questions every 5-minute round lists), how each series is built
(question, rule, simulated futures) and settled from real readings, plus the shared bracket-ladder helpers.

Adding a series is self-contained: write a `build` and a `settle` function and `register(Series(...))` them below.
The exchange, /round, /stats, halts, voids, LMSR and the AI traders' freshness checks pick it up by id.

Series today: oxford-busy (bracket ladder on TfL Crowding % of usual at Oxford Circus). Crowding series settle on the
first TfL reading published after bets close, so nobody can bet on a reading that is already out.
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass
from typing import Callable

import pricing
from data import STALE_S, Store, local_time
from pricing import MIN_MS

HISTORY_MS = 3 * 3_600_000
JUMP_SIGMA = 3.0
CLEAN_TICKS = 2
CLOSE_MS = 60_000
FEED_LABEL = {"crowding": "TfL Crowding API", "jamcams": "TfL JamCam + YOLO people count",
              "line_status": "TfL Line Status API", "rain_now": "Open-Meteo precipitation",
              "bus": "TfL Bus Arrivals API"}
BUSY_WIDTHS = (0.5, 1, 2, 4, 5, 10)
PUBLISH_MS = 5 * MIN_MS
CROWD_WAIT_MS = STALE_S["crowding"] * 1000
OXFORD = "oxford-circus"


def seed_of(market_id: str) -> int:
    return zlib.crc32(market_id.encode())


def settle_stamp(ms: int) -> str:
    return f"settles {local_time(ms, '%H:%M:%S')} Europe/London"


@dataclass(frozen=True)
class Series:
    """One recurring question, listed once per round.

    build(mid, store, spots, now, settles_at) -> Market | None (None = no fresh data, skipped this round).
    settle(market, store, now) -> ('wait', ms) | ('done', winner, reading, settlement) | ('missing',) (→ void).
    skip(market) -> a reason to leave this round's market unlisted, or None (beyond the 85% bracket rule).
    """

    id: str
    feed: str
    build: Callable
    settle: Callable
    close_before_ms: int = CLOSE_MS
    skip: Callable | None = None


SERIES: dict[str, Series] = {}


def register(series: Series) -> Series:
    SERIES[series.id] = series
    return series


class Market:
    def __init__(self, mid: str, sub: str, kind: str, question: str, hotspots: list[str], opened_at: int,
                 settles_at: int, options: list[dict], sim, source: str, rule: str, spec: dict):
        self.id, self.sub, self.kind, self.question = mid, sub, kind, question
        self.hotspots, self.opened_at, self.settles_at = hotspots, opened_at, settles_at
        self.closes_at = settles_at - SERIES[sub].close_before_ms
        self.options, self.source, self.rule, self.spec = options, source, rule, spec
        self.feed = SERIES[sub].feed
        ids = [o["id"] for o in options]
        self.futures = pricing.Futures(sim, ids)
        self.book = pricing.Book(ids)
        self.open_model = self.futures.probs()
        self.ai_bets: list[dict] = []
        self.bets: list[dict] = []
        self.decided: set[str] = set()
        self.last_obs_ts = opened_at
        self.prev_obs: dict | None = None
        self.halt_reason: str | None = None
        self.clean_ticks = 0
        self.close_prices: dict[str, float] | None = None
        self.close_model: dict[str, float] | None = None
        self.result: dict | None = None
        self.settled_at: int | None = None

    def prices(self) -> dict[str, float]:
        return self.book.prices(self.futures.probs())

    def option_ids(self) -> list[str]:
        return [o["id"] for o in self.options]

    def label(self, oid: str) -> str:
        return next(o["label"] for o in self.options if o["id"] == oid)


# ---------------------------------------------------------------- bracket ladders


def pct_labels(edges: list[float]) -> list[str]:
    e = [f"{x:g}" for x in edges]
    return [f"Under {e[0]}%", f"{e[0]}–{e[1]}%", f"{e[1]}–{e[2]}%", f"Over {e[2]}%"]


def pct_rule_edges(edges: list[float]) -> str:
    labels, e = pct_labels(edges), [f"{x:g}%" for x in edges]
    return (f"{labels[0]} = below {e[0]}; {labels[1]} = {e[0]} or more and below {e[1]}; {labels[2]} = {e[1]} or "
            f"more and below {e[2]}; {labels[3]} = {e[2]} or more")


def bracket_options(labels: list[str], spot: dict) -> list[dict]:
    return [{"id": f"b{k}", "label": lab, "hotspot": spot["id"]} for k, lab in enumerate(labels)]


# ---------------------------------------------------------------- TfL Crowding helpers


def crowd_series(store: Store, naptan: str, start: int, now: int) -> list[tuple[int, float]]:
    return [(ts, math.log(s[naptan])) for ts, s in store.between("crowding", start, now) if naptan in s]


def crowd_station(store: Store, h: dict, snap: dict, now: int) -> tuple[str, str, float, float, float]:
    mu, sd = pricing.fit_log_walk(crowd_series(store, h["naptan"], now - HISTORY_MS, now), 0.01, 0.002, 0.05)
    return h["id"], h["naptan"], math.log(snap[h["naptan"]]), mu, sd


def crowd_horizon(last_ts: int, settles_at: int, now: int) -> int:
    """Minutes of futures to run: up to the first 5-minutely TfL Crowding publish after bets close."""
    closes = settles_at - CLOSE_MS
    expected = last_ts + max(1, math.ceil((closes - last_ts) / PUBLISH_MS)) * PUBLISH_MS
    return max(1, math.ceil((expected - now) / MIN_MS))


def crowd_settle_rule(settles_at: int) -> str:
    closes = settles_at - CLOSE_MS
    return (f"the first TfL Crowding reading published after bets close at {local_time(closes, '%H:%M:%S')} (TfL "
            f"publishes every 5 min). No reading by {local_time(closes + CROWD_WAIT_MS)} → void, stakes refunded")


def crowd_values(m: Market, store: Store, now: int):
    """('ok', ts, {option-or-hotspot: fraction of usual}) from the first reading after bets close that has every
    station in m.spec['stations']; ('wait', ms) until CROWD_WAIT_MS after the close, then ('missing',)."""
    deadline = m.closes_at + CROWD_WAIT_MS
    for ts, snap in store.between("crowding", m.closes_at + 1, min(now, deadline)):
        vals = {oid: snap.get(nap) for oid, nap in m.spec["stations"].items()}
        if all(v is not None for v in vals.values()):
            return ("ok", ts, vals)
    return ("wait", deadline - now) if now < deadline else ("missing",)


# ---------------------------------------------------------------- series: oxford-busy


def build_oxford_busy(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """Four brackets of Oxford Circus's % of usual, placed on the futures' spread."""
    spot = next(h for h in spots if h["id"] == OXFORD)
    last = store.latest("crowding", now)
    if not last or spot["naptan"] not in last[1]:
        return None
    station = crowd_station(store, spot, last[1], now)
    sim = pricing.LevelSim(station, now, crowd_horizon(last[0], settles_at, now), random.Random(seed_of(mid)))
    sim.edges = pricing.place_pct_brackets(sim.finals(), BUSY_WIDTHS)
    hhmm = local_time(settles_at)
    rule = f"{spot['short']}'s % of usual busyness in {crowd_settle_rule(settles_at)}. {pct_rule_edges(sim.edges)}."
    return Market(mid, "oxford-busy", "bracket", f"{spot['short']} busyness at {hhmm}", [spot["id"]], now,
                  settles_at, bracket_options(pct_labels(sim.edges), spot), sim,
                  f"TfL Crowding API · {spot['short']} · {settle_stamp(settles_at)}", rule,
                  {"naptan": spot["naptan"], "edges": sim.edges, "name": spot["short"],
                   "sigmas": {spot["naptan"]: station[4]}, "stations": {spot["id"]: spot["naptan"]},
                   "names": {spot["id"]: spot["short"]}})


def settle_oxford_busy(m: Market, store: Store, now: int):
    got = crowd_values(m, store, now)
    if got[0] != "ok":
        return got
    ts, vals = got[1], got[2]
    pct = round(next(iter(vals.values())) * 100, 4)  # 0.29 * 100 must stay 29, not 28.999…
    winner = pricing.bracket_of(pct, m.spec["edges"])
    return ("done", winner, f"{m.spec['name']} {pct:.1f}% of usual → {m.label(winner)}",
            {"value": round(pct, 2), "source": "TfL Crowding API", "fetchedAt": ts})


register(Series("oxford-busy", "crowding", build_oxford_busy, settle_oxford_busy))


# ---------------------------------------------------------------- settlement


def try_settle(m: Market, store: Store, now: int):
    """('wait', ms) | ('done', winner, reading, settlement) | ('void', reading, settlement)."""
    if now < m.settles_at:
        return ("wait", m.settles_at - now)
    got = SERIES[m.sub].settle(m, store, now)
    if got[0] == "missing":
        return ("void", "No data — void", {"value": None, "source": FEED_LABEL[m.feed], "fetchedAt": None})
    return got


# ---------------------------------------------------------------- observations + jump halts


def observe(m: Market, store: Store, now: int) -> None:
    """Feed new readings of the market's feed (up to settlesAt) to its futures and its jump detector."""
    for ts, snap in store.between(m.feed, m.last_obs_ts + 1, min(now, m.settles_at)):
        m.futures.observe(ts, snap)
        jump = jump_of(m, ts, snap)
        if jump:
            m.halt_reason, m.clean_ticks = jump, 0
        elif m.halt_reason and m.halt_reason.startswith("jump"):
            m.clean_ticks += 1
            if m.clean_ticks >= CLEAN_TICKS:
                m.halt_reason = None
        m.last_obs_ts = ts


def jump_of(m: Market, ts: int, snap: dict) -> str | None:
    """A 3σ one-tick jump in any TfL Crowding station the market reads (spec 'stations' + 'sigmas')."""
    prev, m.prev_obs = m.prev_obs, {"ts": ts, "snap": snap}
    if prev is None or m.feed != "crowding":
        return None
    gap = max(1.0, (ts - prev["ts"]) / MIN_MS)
    for oid, nap in m.spec.get("stations", {}).items():
        a, b = prev["snap"].get(nap), snap.get(nap)
        sd = m.spec["sigmas"].get(nap)
        if a and b and sd:
            z = (math.log(b) - math.log(a)) / (sd * math.sqrt(gap))
            if abs(z) > JUMP_SIGMA:
                return f"jump: {m.spec['names'][oid]} {z:+.1f}σ"
    return None
