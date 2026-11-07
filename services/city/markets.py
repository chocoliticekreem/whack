"""City markets: the series registry (the recurring questions every 5-minute round lists), how each series is built
(question, rule, simulated futures) and settled from real readings, plus the shared bracket-ladder helpers.

Adding a series is self-contained: write a `build` and a `settle` function and `register(Series(...))` them below.
The exchange, /round, /stats, halts, voids, LMSR and the AI traders' freshness checks pick it up by id.

The lineup (docs/questions.md §5). Every 5-min round: hub_surge (S1), city_pulse (S2) and london_vix (S3), with
heathrow_stack (S4) listed in place of S3 while the multiverse VIX is down or warming up. Hourly headlines: hub_hour
(S5, opens with the round spanning :00, settles at the next :00) and clean_sheet (S6, :30 to :30, resolves NO the
moment a Good line breaks). oxford-busy is retired from the lineup but stays registered. Crowding series settle on
the first TfL reading published after bets close, so nobody can bet on a reading that is already out.
"""
from __future__ import annotations

import math
import random
import statistics
import zlib
from dataclasses import dataclass
from typing import Callable

import pricing
from data import STALE_S, Store, day_name, local_time
from pricing import MIN_MS

HISTORY_MS = 3 * 3_600_000
JUMP_SIGMA = 3.0
CLEAN_TICKS = 2
CLOSE_MS = 60_000
FEED_LABEL = {"crowding": "TfL Crowding API", "jamcams": "TfL JamCam + YOLO people count",
              "line_status": "TfL Line Status API", "rain_now": "Open-Meteo precipitation",
              "bus": "TfL Bus Arrivals API", "aircraft": "adsb.lol Heathrow holding stacks",
              "vix": "OverWatch multiverse London VIX"}
BUSY_WIDTHS = (0.5, 1, 2, 4, 5, 10)
PUBLISH_MS = 5 * MIN_MS
CROWD_WAIT_MS = STALE_S["crowding"] * 1000
CROWD_TOL_MS = 150_000
OXFORD = "oxford-circus"
HUB_ORDER = "Oxford Circus, King's Cross, Piccadilly Circus, Waterloo, Bank"
PULSE_STATIONS = 20
PULSE_MIN_STATIONS = 18
READING_MAX_AGE_MS = 2 * MIN_MS
SETTLE_LAG_MS = 30_000
GRACE_MS = 3 * MIN_MS
VIX_WARMUP_MS = 60 * MIN_MS
MIN_DELTAS = 10
ROUND_MS = 5 * MIN_MS
HOUR_MS = 60 * MIN_MS
HEADLINE_CAP = 0.70
HEADLINE_FLOOR = 0.30
LINE_GAP_MS = 90_000
CLEAN_MAX_GAP_MS = 5 * MIN_MS


def seed_of(market_id: str) -> int:
    return zlib.crc32(market_id.encode())


def settle_stamp(ms: int) -> str:
    return f"settles {local_time(ms, '%H:%M:%S')} Europe/London"


@dataclass(frozen=True)
class Series:
    """One recurring question, listed once per round.

    build(mid, store, spots, now, settles_at) -> Market | None (None = no fresh data, skipped this round). A
    series may settle later than the round (an hourly headline) and name its market itself.
    settle(market, store, now) -> ('wait', ms) | ('done', winner, reading, settlement) | ('void', reading,
    settlement) | ('missing',) (→ void, "No data").
        The exchange also calls it while bets are open: return 'done' only when the answer is final, and the market
        closes at once. It is called on markets rebuilt after a restart too (see Restored), so read only
        market.id/kind/closes_at/settles_at/opened_at/spec/options/label()/option_ids().
    skip(market) -> a reason to leave this round's market unlisted, or None (beyond the 85% bracket rule).
    due(round_settles_at) -> whether this round lists the series at all (None = every round).
    settles_early: settle() is also asked before settlesAt (a clean sheet resolves NO the moment a line breaks).
        Without it, a final answer found while bets are open still closes the market, which then settles at
        settlesAt.
    enabled=False keeps a series registered (buildable, settleable, testable) but out of the default lineup.
    """

    id: str
    feed: str
    build: Callable
    settle: Callable
    close_before_ms: int = CLOSE_MS
    skip: Callable | None = None
    due: Callable | None = None
    settles_early: bool = False
    enabled: bool = True


SERIES: dict[str, Series] = {}
REGISTRY: dict[str, Series] = {}


def register(series: Series) -> Series:
    REGISTRY[series.id] = series
    if series.enabled:
        SERIES[series.id] = series
    return series


class Market:
    def __init__(self, mid: str, sub: str, kind: str, question: str, hotspots: list[str], opened_at: int,
                 settles_at: int, options: list[dict], sim, source: str, rule: str, spec: dict):
        self.id, self.sub, self.kind, self.question = mid, sub, kind, question
        self.hotspots, self.opened_at, self.settles_at = hotspots, opened_at, settles_at
        self.closes_at = settles_at - REGISTRY[sub].close_before_ms
        self.options, self.source, self.rule, self.spec = options, source, rule, spec
        self.feed = REGISTRY[sub].feed
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
        self.locked: tuple | None = None

    def prices(self) -> dict[str, float]:
        return self.book.prices(self.futures.probs())

    def option_ids(self) -> list[str]:
        return [o["id"] for o in self.options]

    def label(self, oid: str) -> str:
        return next(o["label"] for o in self.options if o["id"] == oid)


def record_of(m: Market) -> dict:
    """What the ledger keeps of a market so it can still be settled from recorded data after a restart."""
    return {"id": m.id, "series": m.sub, "kind": m.kind, "openedAt": m.opened_at, "closesAt": m.closes_at,
            "settlesAt": m.settles_at, "spec": m.spec, "options": m.options}


class Restored:
    """A market rebuilt from its ledger record: enough to settle it, not to trade it."""

    def __init__(self, rec: dict):
        self.id, self.sub, self.kind = rec["id"], rec["series"], rec["kind"]
        self.opened_at, self.closes_at, self.settles_at = rec["openedAt"], rec["closesAt"], rec["settlesAt"]
        self.spec, self.options = rec["spec"], rec["options"]
        self.feed = REGISTRY[self.sub].feed
        self.locked: tuple | None = None

    label = Market.label
    option_ids = Market.option_ids


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


# ---------------------------------------------------------------- vs usual (TfL live % ÷ the typical day)


def vs_usual(store: Store, spots: list[dict], ts: int, snap: dict) -> dict[str, float] | None:
    """{hub id: live % ÷ typical % for that weekday and 15-min band}, or None unless every hub has both."""
    out = {}
    for h in spots:
        typ = store.typical(h["naptan"], ts)
        if h["naptan"] not in snap or not typ:
            return None
        out[h["id"]] = snap[h["naptan"]] / typ
    return out


def hub_spec(store: Store, spots: list[dict], stations: list, ts0: int, snap: dict) -> dict:
    return {"stations": {h["id"]: h["naptan"] for h in spots}, "names": {h["id"]: h["short"] for h in spots},
            "sigmas": {s[1]: s[4] for s in stations}, "openTs": ts0, "day": day_name(ts0),
            "open": {k: round(v, 4) for k, v in vs_usual(store, spots, ts0, snap).items()}}


def hub_options(spots: list[dict]) -> list[dict]:
    return [{"id": h["id"], "label": h["short"], "hotspot": h["id"]} for h in spots]


def settled_vs(m: Market, store: Store, now: int):
    """('ok', ts, {hub: vs usual}) from the first full TfL reading after close; else wait / missing."""
    got = crowd_values(m, store, now)
    if got[0] != "ok":
        return got
    ts, vals = got[1], got[2]
    vs = {}
    for hid, v in vals.items():
        typ = store.typical(m.spec["stations"][hid], ts)
        if not typ:
            return ("missing",)
        vs[hid] = v / typ
    return ("ok", ts, vs)


def fmt_vs(v: float) -> str:
    return f"{v * 100:.0f}%"


# ---------------------------------------------------------------- S1 hub_surge (every round)


def build_hub_surge(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """5-way race: the hub whose vs-usual rises most from the reading at open to the first reading after close."""
    last = store.latest("crowding", now)
    if not last or vs_usual(store, spots, last[0], last[1]) is None:
        return None
    ts0, snap = last
    stations = [crowd_station(store, h, snap, now) for h in spots]
    minutes = crowd_horizon(ts0, settles_at, now)
    t_end = now + minutes * MIN_MS
    typ0 = [store.typical(h["naptan"], ts0) for h in spots]
    typ_t = [store.typical(h["naptan"], t_end) or t0 for h, t0 in zip(spots, typ0)]
    sim = pricing.RaceSim(stations, now, minutes, random.Random(seed_of(mid)), typ0, typ_t, "gain")
    spec = hub_spec(store, spots, stations, ts0, snap)
    day = spec["day"]
    rule = (f"Each hub's vs usual = its live TfL busyness ÷ its typical {day} for that 15-min band (TfL Crowding "
            f"profile). The winner is the hub whose vs usual rises most from the reading at open "
            f"({local_time(ts0)}) to {crowd_settle_rule(settles_at)}. A tie for the top goes to the first in the "
            f"order {HUB_ORDER}. If no hub moved, the market is void and stakes are refunded.")
    return Market(mid, "hub_surge", "versus", f"Which hub surges most by {local_time(settles_at)}?",
                  [h["id"] for h in spots], now, settles_at, hub_options(spots), sim,
                  f"TfL Crowding API · 5 hubs vs a usual {day} · {settle_stamp(settles_at)}", rule, spec)


def settle_hub_surge(m: Market, store: Store, now: int):
    got = settled_vs(m, store, now)
    if got[0] != "ok":
        return got
    ts, vs = got[1], got[2]
    gains = {hid: vs[hid] - m.spec["open"][hid] for hid in m.option_ids()}
    snap = {"value": None, "source": "TfL Crowding API", "fetchedAt": ts,
            "values": {hid: round(v, 4) for hid, v in vs.items()}}
    if all(abs(g) < 1e-9 for g in gains.values()):
        return ("void", "No hub moved since open — void, stakes refunded", snap)
    winner = max(m.option_ids(), key=lambda h: gains[h])
    snap["value"] = round(gains[winner], 4)
    name = m.spec["names"][winner]
    return ("done", winner, f"{name} rose most: {fmt_vs(m.spec['open'][winner])} → {fmt_vs(vs[winner])} of a usual "
            f"{m.spec['day']} ({gains[winner] * 100:+.1f} pts) → {name}", snap)


# ---------------------------------------------------------------- S2 city_pulse (every round)


def pulse_of(snap: dict) -> float | None:
    """City Pulse = 100 × mean live % of baseline over the TfL Crowding stations reporting (18 of 20 needed)."""
    return 100 * sum(snap.values()) / len(snap) if len(snap) >= PULSE_MIN_STATIONS else None


def pulse_series(store: Store, start: int, now: int) -> list[tuple[int, float]]:
    return [(ts, p) for ts, s in store.between("crowding", start, now) if (p := pulse_of(s))]


def build_city_pulse(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """Over/under on City Pulse; line = pulse at open + the median 5-min change over the last 3 h, on a .5."""
    last = store.latest("crowding", now)
    p0 = pulse_of(last[1]) if last else None
    if not p0:
        return None
    hist = pulse_series(store, now - HISTORY_MS, now)
    d5 = pricing.deltas_at(hist, 5, CROWD_TOL_MS)
    line = math.floor(p0 + (statistics.median(d5) if len(d5) >= 3 else 0.0)) + 0.5
    mu, sd = pricing.fit_log_walk([(ts, math.log(p)) for ts, p in hist], 0.01, 0.002, 0.05)
    minutes = crowd_horizon(last[0], settles_at, now)
    sim = pricing.ScalarSim(p0, mu, sd, now, minutes, random.Random(seed_of(mid)), pulse_of, line)
    hhmm = local_time(settles_at)
    rule = (f"City Pulse = 100 × the mean live TfL busyness (% of baseline) over the {PULSE_STATIONS} TfL Crowding "
            f"stations, in {crowd_settle_rule(settles_at)}. Fewer than {PULSE_MIN_STATIONS} stations reporting → "
            f"void. Over = above {line:.1f}; Under = below {line:.1f}.")
    return Market(mid, "city_pulse", "overunder", f"City Pulse at {hhmm}: over or under {line:.1f}?", [], now,
                  settles_at, over_under_options(f"{line:.1f}"), sim,
                  f"TfL Crowding API · {PULSE_STATIONS} stations · {settle_stamp(settles_at)}", rule,
                  {"line": line, "open": round(p0, 2), "openTs": last[0], "sigma": sd})


def settle_city_pulse(m: Market, store: Store, now: int):
    deadline = m.closes_at + CROWD_WAIT_MS
    after = store.between("crowding", m.closes_at + 1, min(now, deadline))
    if not after:
        return ("wait", deadline - now) if now < deadline else ("missing",)
    ts, snap = after[0]
    pulse = pulse_of(snap)
    if pulse is None:
        return ("void", f"Only {len(snap)} of {PULSE_STATIONS} stations reported — void",
                {"value": None, "source": "TfL Crowding API", "fetchedAt": ts})
    pulse = round(pulse, 4)
    winner = "over" if pulse > m.spec["line"] else "under"
    return ("done", winner, f"City Pulse {pulse:.2f} vs line {m.spec['line']:.2f} → {m.label(winner)}",
            {"value": round(pulse, 2), "source": "TfL Crowding API", "fetchedAt": ts})


# ---------------------------------------------------------------- shared: last reading at or before T


def reading_at(m: Market, store: Store, now: int, source: str):
    """('ok', ts, snap): the last reading at or before settlesAt, at most 2 min old. Waits GRACE_MS past settlesAt
    for a late write, then ('missing',)."""
    last = store.latest(source, min(now, m.settles_at))
    if last and m.settles_at - last[0] <= READING_MAX_AGE_MS and now >= m.settles_at + SETTLE_LAG_MS:
        return ("ok", last[0], last[1])
    if now < m.settles_at + GRACE_MS:
        return ("wait", min(m.settles_at + GRACE_MS - now, 5_000))
    return ("missing",)


def over_under_options(line_label: str, over: str = "Over", under: str = "Under") -> list[dict]:
    return [{"id": "over", "label": f"{over} {line_label}"}, {"id": "under", "label": f"{under} {line_label}"}]


# ---------------------------------------------------------------- S3 london_vix (every round; S4 stands in)


def vix_state(store: Store, now: int) -> dict:
    """Whether the London VIX can be traded now: a multiverse frame at most 2 min old, the engine past its first
    hour since spawn (the VIX decays from ~16 to ~2.3 over it) and enough post-warm-up moves to price from.
    {ok, reason, vix, ts, spawnTs, history [(ts, vix)] since warm-up within 3 h, driver}."""
    last = store.latest("vix", now)
    if not last or now - last[0] > READING_MAX_AGE_MS:
        return {"ok": False, "reason": "the multiverse engine is unreachable (no VIX frame in 2 min)"}
    ts, snap = last
    spawn = snap.get("spawnTs")
    out = {"vix": snap["vix"], "ts": ts, "spawnTs": spawn, "driver": snap.get("driver") or "",
           "driverDelta": snap.get("driverDelta") or 0.0}
    if spawn is None or now - spawn < VIX_WARMUP_MS:
        mins = "?" if spawn is None else f"{(now - spawn) / MIN_MS:.0f}"
        return out | {"ok": False, "reason": f"the London VIX is warming up ({mins} of the first 60 min since the "
                                            f"engine spawned are ignored)"}
    hist = [(t, s["vix"]) for t, s in store.between("vix", max(now - HISTORY_MS, spawn + VIX_WARMUP_MS), now)
            if s.get("spawnTs") == spawn]
    out["history"] = hist
    if len(pricing.deltas_at(hist, 5)) < MIN_DELTAS:
        return out | {"ok": False, "reason": f"the London VIX has only {len(hist)} frames since its warm-up"}
    return out | {"ok": True, "reason": ""}


def build_london_vix(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    st = vix_state(store, now)
    if not st["ok"]:
        return None
    minutes = max(1, round((settles_at - now) / MIN_MS))
    deltas = pricing.deltas_at(st["history"], minutes) or pricing.deltas_at(st["history"], 5)
    line = round(st["vix"], 2)
    sim = pricing.DeltaSim(st["vix"], deltas, now, minutes, random.Random(seed_of(mid)), lambda s: s["vix"], line)
    hhmm = local_time(settles_at)
    rule = (f"The London VIX the OverWatch multiverse engine publishes at its last frame at or before "
            f"{local_time(settles_at, '%H:%M:%S')} (at most 2 min old). Higher = above {line:.2f}; exactly "
            f"{line:.2f} counts as Lower. No frame in time, or a frame within 60 min of an engine spawn → void, "
            f"stakes refunded.")
    return Market(mid, "london_vix", "overunder", f"London VIX at {hhmm}: higher or lower than {line:.2f}?", [],
                  now, settles_at, over_under_options(f"than {line:.2f}", "Higher", "Lower"), sim,
                  f"OverWatch multiverse · London VIX · {settle_stamp(settles_at)}", rule,
                  {"line": line, "open": st["vix"], "openTs": st["ts"], "spawnTs": st["spawnTs"],
                   "driver": st["driver"]})


def settle_london_vix(m: Market, store: Store, now: int):
    got = reading_at(m, store, now, "vix")
    if got[0] != "ok":
        return got
    ts, snap = got[1], got[2]
    if snap.get("spawnTs") is None or ts - snap["spawnTs"] < VIX_WARMUP_MS:
        return ("void", "The multiverse engine re-spawned during the round — void",
                {"value": None, "source": FEED_LABEL["vix"], "fetchedAt": ts})
    v = round(snap["vix"], 4)
    winner = "over" if v > m.spec["line"] else "under"
    return ("done", winner, f"London VIX {v:.2f} vs {m.spec['line']:.2f} → {m.label(winner).split()[0]}",
            {"value": round(v, 2), "source": FEED_LABEL["vix"], "fetchedAt": ts})


# ---------------------------------------------------------------- S4 heathrow_stack (stands in for S3)


def build_heathrow_stack(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    last = store.latest("aircraft", now)
    if not last or not store.fresh("aircraft", now):
        return None
    n0 = last[1]["total"]
    hist = [(ts, float(s["total"])) for ts, s in store.between("aircraft", now - HISTORY_MS, now)]
    minutes = max(1, round((settles_at - now) / MIN_MS))
    deltas = pricing.deltas_at(hist, minutes)
    if len(deltas) < MIN_DELTAS:
        return None
    line = n0 + 0.5
    sim = pricing.DeltaSim(float(n0), deltas, now, minutes, random.Random(seed_of(mid)),
                           lambda s: float(s["total"]), line)
    vix = vix_state(store, now)
    hhmm = local_time(settles_at)
    rule = (f"adsb.lol holding_total (aircraft in the BNN, LAM, BIG and OCK stacks over Heathrow) in the last reading "
            f"at or before {local_time(settles_at, '%H:%M:%S')}, at most 2 min old; otherwise void. Over = {n0 + 1} "
            f"or more. Listed in place of the London VIX while {vix['reason'] or 'it is down'}.")
    return Market(mid, "heathrow_stack", "overunder", f"Planes holding over Heathrow at {hhmm}: over or under "
                  f"{line:g}?", [], now, settles_at, over_under_options(f"{line:g}"), sim,
                  f"adsb.lol · Heathrow holding stacks · {settle_stamp(settles_at)}", rule,
                  {"line": line, "open": n0, "openTs": last[0], "stacks": last[1]["stacks"], "vixOk": vix["ok"],
                   "vixReason": vix["reason"]})


def skip_heathrow_stack(m: Market) -> str | None:
    return "the London VIX is live this round (Heathrow stacks stand in only when it is down)" if m.spec["vixOk"] \
        else None


def settle_heathrow_stack(m: Market, store: Store, now: int):
    got = reading_at(m, store, now, "aircraft")
    if got[0] != "ok":
        return got
    ts, snap = got[1], got[2]
    n = snap["total"]
    winner = "over" if n > m.spec["line"] else "under"
    stacks = " · ".join(f"{k} {v}" for k, v in snap["stacks"].items())
    return ("done", winner, f"{n} holding ({stacks}) vs {m.spec['line']:g} → {m.label(winner).split()[0]}",
            {"value": n, "source": FEED_LABEL["aircraft"], "fetchedAt": ts})


# ---------------------------------------------------------------- S5 hub_hour (hourly headline, opens at :00)


def hourly(offset_min: int) -> Callable[[int], bool]:
    """Due in the one round per hour whose betting window spans :offset (round settling at :offset+5)."""
    return lambda at: at % HOUR_MS == (offset_min + 5) * MIN_MS


def headline_end(at: int) -> int:
    return at - ROUND_MS + HOUR_MS


def build_hub_hour(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """5-way race on vs-usual at the hour. Sigma is fitted on 60-min changes and widened until the favourite at
    open is at most 70% (the 5-min fit priced it at 89% against a real hit rate of 56%)."""
    last = store.latest("crowding", now)
    if not last or vs_usual(store, spots, last[0], last[1]) is None:
        return None
    ts0, snap = last
    end = headline_end(settles_at)
    mid = f"hub_hour-{local_time(end, '%H%M')}"
    minutes = crowd_horizon(ts0, end, now)
    stations = []
    for h in spots:
        series = crowd_series(store, h["naptan"], now - HISTORY_MS, now)
        d60 = pricing.deltas_at(series, 60, CROWD_TOL_MS)
        sd = (statistics.pstdev(d60) / math.sqrt(60)) if len(d60) >= 6 else crowd_station(store, h, snap, now)[4]
        stations.append((h["id"], h["naptan"], math.log(snap[h["naptan"]]), 0.0, max(sd, 0.002)))
    typ0 = [store.typical(h["naptan"], ts0) for h in spots]
    typ_t = [store.typical(h["naptan"], now + minutes * MIN_MS) or t0 for h, t0 in zip(spots, typ0)]
    rng = random.Random(seed_of(mid))
    for _ in range(20):
        sim = pricing.RaceSim(stations, now, minutes, rng, typ0, typ_t, "level")
        if max(pricing.Futures(sim, [h["id"] for h in spots]).probs().values()) <= HEADLINE_CAP:
            break
        stations = [(a, b, x, mu, sd * 1.3) for a, b, x, mu, sd in stations]
    spec = hub_spec(store, spots, stations, ts0, snap)
    day = spec["day"]
    rule = (f"Each hub's vs usual = its live TfL busyness ÷ its typical {day} for that 15-min band. The hub with the "
            f"highest vs usual in {crowd_settle_rule(end)} wins. A tie goes to the first in the order {HUB_ORDER}.")
    return Market(mid, "hub_hour", "versus", f"Which hub ends the hour furthest above its usual {day}? Settles "
                  f"{local_time(end)}", [h["id"] for h in spots], now, end, hub_options(spots), sim,
                  f"TfL Crowding API · 5 hubs vs a usual {day} · {settle_stamp(end)}", rule, spec)


def settle_hub_hour(m: Market, store: Store, now: int):
    got = settled_vs(m, store, now)
    if got[0] != "ok":
        return got
    ts, vs = got[1], got[2]
    winner = max(m.option_ids(), key=lambda h: vs[h])
    name = m.spec["names"][winner]
    return ("done", winner, f"{name} ends {fmt_vs(vs[winner])} of a usual {m.spec['day']}, the furthest above → "
            f"{name}", {"value": round(vs[winner], 4), "source": "TfL Crowding API", "fetchedAt": ts,
                        "values": {hid: round(v, 4) for hid, v in vs.items()}})


# ---------------------------------------------------------------- S6 clean_sheet (hourly headline, opens at :30)


def tube_lines(snap: dict) -> list[str]:
    return [lid for lid, line in snap.items() if line.get("mode") == "tube"]


def build_clean_sheet(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """Yes/no: every Tube line on Good Service at open stays Good to the next :30. P(break) from a rolling 3-h
    per-line hazard × the Good lines, held to 30–70% at open (the whole-day rate priced 85% against a real 43%)."""
    last = store.latest("line_status", now)
    if not last or not store.fresh("line_status", now):
        return None
    good = [lid for lid in tube_lines(last[1]) if last[1][lid]["sev"] == pricing.GOOD_SEVERITY]
    if not good:
        return None
    end = headline_end(settles_at)
    mid = f"clean_sheet-{local_time(end, '%H%M')}"
    minutes = max(1, round((end - now) / MIN_MS))
    breaks, line_min = pricing.fit_break_hazard(store.between("line_status", now - HISTORY_MS, now),
                                                tube_lines(last[1]))
    lam = (breaks + 0.5) / (line_min + 60.0)
    p_raw = 1 - math.exp(-lam * len(good) * minutes)
    p = min(1 - HEADLINE_FLOOR, max(HEADLINE_FLOOR, p_raw))
    sim = pricing.BreakSim(p, good, now, minutes, random.Random(seed_of(mid)))
    start = local_time(settles_at - ROUND_MS)
    names = [last[1][lid]["name"] for lid in good]
    rule = (f"YES if every Tube line on Good Service at {start} ({', '.join(names)}) reads Good Service on every TfL "
            f"Line Status reading until {local_time(end)}. The first reading that shows any of them worse settles "
            f"NO at once. Readings missing for more than 5 min in total → void, stakes refunded.")
    return Market(mid, "clean_sheet", "yesno", f"Clean sheet: does every Tube line on Good Service at {start} stay "
                  f"Good until {local_time(end)}?", [], now, end,
                  [{"id": "yes", "label": "Yes, all stay Good"}, {"id": "no", "label": "No, a line breaks"}], sim,
                  f"TfL Line Status API · {len(good)} Tube lines · {settle_stamp(end)}", rule,
                  {"good": good, "names": dict(zip(good, names)), "openTs": last[0], "pBreakRaw": round(p_raw, 3),
                   "breaks3h": breaks, "goodLineMinutes3h": round(line_min)})


def settle_clean_sheet(m: Market, store: Store, now: int):
    reads = store.between("line_status", m.opened_at + 1, min(now, m.settles_at))
    for ts, snap in reads:
        broke = [lid for lid in m.spec["good"] if lid in snap and snap[lid]["sev"] != pricing.GOOD_SEVERITY]
        if broke:
            line = snap[broke[0]]
            why = f": {line['reason'][:120]}" if line.get("reason") else ""
            return ("done", "no", f"{line['name']} broke the clean sheet at {local_time(ts)} ({line['desc']}){why}",
                    {"value": len(broke), "source": "TfL Line Status API", "fetchedAt": ts,
                     "line": broke[0], "lines": broke})
    if now < m.settles_at + SETTLE_LAG_MS:
        return ("wait", min(max(1, m.settles_at + SETTLE_LAG_MS - now), 5_000))
    stamps = [m.opened_at] + [ts for ts, _ in reads] + [m.settles_at]
    gaps = sum(max(0, b - a - MIN_MS) for a, b in zip(stamps, stamps[1:]) if b - a > LINE_GAP_MS)
    if gaps > CLEAN_MAX_GAP_MS:
        return ("void", f"Line Status readings missing for {gaps / MIN_MS:.0f} min — void",
                {"value": None, "source": "TfL Line Status API", "fetchedAt": reads[-1][0] if reads else None})
    return ("done", "yes", f"All {len(m.spec['good'])} lines stayed Good from {local_time(m.opened_at)} to "
            f"{local_time(m.settles_at)} → Yes", {"value": 0, "source": "TfL Line Status API",
                                                 "fetchedAt": reads[-1][0] if reads else None})


# ---------------------------------------------------------------- the lineup (registration order = listing order)

register(Series("hub_surge", "crowding", build_hub_surge, settle_hub_surge))
register(Series("city_pulse", "crowding", build_city_pulse, settle_city_pulse))
register(Series("london_vix", "vix", build_london_vix, settle_london_vix))
register(Series("heathrow_stack", "aircraft", build_heathrow_stack, settle_heathrow_stack, skip=skip_heathrow_stack))
register(Series("hub_hour", "crowding", build_hub_hour, settle_hub_hour, due=hourly(0)))
register(Series("clean_sheet", "line_status", build_clean_sheet, settle_clean_sheet, due=hourly(30),
                settles_early=True))
register(Series("oxford-busy", "crowding", build_oxford_busy, settle_oxford_busy, enabled=False))


# ---------------------------------------------------------------- settlement


def try_settle(m: Market, store: Store, now: int):
    """('wait', ms) | ('done', winner, reading, settlement) | ('void', reading, settlement). A market locked on a
    final answer settles on it at settlesAt, or at once for a settles_early series."""
    series = REGISTRY[m.sub]
    if m.locked is not None and (now >= m.settles_at or series.settles_early):
        return m.locked
    if now < m.settles_at and not series.settles_early:
        return ("wait", m.settles_at - now)
    got = series.settle(m, store, now)
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
