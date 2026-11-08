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

import data
import pricing
import tops
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
CROWD_WAIT_MS = 20 * MIN_MS
CROWD_TOL_MS = 150_000
OXFORD = "oxford-circus"
HUB_ORDER = "Oxford Circus, King's Cross, Piccadilly Circus, Waterloo, Bank"
PULSE_STATIONS = 20
PULSE_MIN_STATIONS = 18
PULSE_TREND_MS = 30 * MIN_MS
PULSE_STEPS = (0.5, 0.25, 0.1, 0.05, 0.01)
IQR_PER_SD = 1.349
READING_MAX_AGE_MS = 2 * MIN_MS
SETTLE_LAG_MS = 30_000
GRACE_MS = 3 * MIN_MS
VIX_WARMUP_MS = 60 * MIN_MS
VIX_MAX_AGE_MS = STALE_S["vix"] * 1000
MIN_DELTAS = 10
ROUND_MS = 5 * MIN_MS
HOUR_MS = 60 * MIN_MS
HEADLINE_CAP = 0.70
HEADLINE_FLOOR = 0.30
LINE_GAP_MS = 90_000
CLEAN_MAX_GAP_MS = 5 * MIN_MS
STACK_MEAN_MS = 30 * MIN_MS
STACK_KAPPA = 0.472
HUB_KAPPA = 0.0161
HUB_POWER = 0.7
HUB_LOOKBACK_MS = 12 * 3_600_000
HUB_LEVEL_PRIOR = 36
HUB_SD_FLOOR = 0.002
HUB_MIN_HUBS = 3
HUB_CORR = {"oxford-circus": {"kings-cross": 0.15, "piccadilly": 0.30, "waterloo": 0.33, "bank": -0.08},
            "kings-cross": {"piccadilly": 0.25, "waterloo": 0.18, "bank": -0.18},
            "piccadilly": {"waterloo": 0.12, "bank": 0.30},
            "waterloo": {"bank": -0.19}}
CLEAN_CLOSE_BEFORE_MS = 55 * MIN_MS


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
    why_none(store, now) -> why build returned None, for /round (default "no fresh reading").
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
    why_none: Callable | None = None


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
        self.tilt: dict[str, float] | None = None
        self.tops: dict | None = None
        self.tops_stances: dict | None = None

    def model(self) -> dict[str, float]:
        """The futures' probabilities with the TOPS tilt from the open (tops.py); the LMSR prices on top of this."""
        return tops.tilted(self.futures.probs(), self.tilt)

    def prices(self) -> dict[str, float]:
        return self.book.prices(self.model())

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
    """(ts, log vs usual) per reading of this station: the usual-day curve is taken out, so a fit sees only the moves
    around it. A poll that repeats the station's previous value is not a new reading of it (TfL publishes station by
    station), so it is left out rather than counted as a zero step."""
    out, last = [], None
    for ts, s in store.between("crowding", start, now):
        if naptan in s and s[naptan] != last:
            out.append((ts, math.log(s[naptan] / (store.usual(naptan, ts) or 1.0))))
            last = s[naptan]
    return out


def crowd_station(store: Store, h: dict, snap: dict, now: int, ts0: int | None = None, t_end: int | None = None,
                  minutes: int = 1) -> tuple[str, str, float, float, float]:
    """(option id, naptan, log live % at ts0, drift, sigma): the walk fitted around the usual curve, plus that curve's
    slope from the open reading (ts0) to t_end when both are given."""
    nap = h["naptan"]
    mu, sd = pricing.fit_log_walk(crowd_series(store, nap, now - HISTORY_MS, now), 0.01, 0.002, 0.05)
    if ts0 is not None and t_end is not None:
        mu += pricing.usual_drift(store.usual(nap, ts0), store.usual(nap, t_end), minutes)
    return h["id"], nap, math.log(snap[nap]), mu, sd


def crowd_horizon(last_ts: int, settles_at: int, now: int) -> int:
    """Minutes of futures to run: up to the first 5-minutely TfL Crowding publish after bets close."""
    closes = settles_at - CLOSE_MS
    expected = last_ts + max(1, math.ceil((closes - last_ts) / PUBLISH_MS)) * PUBLISH_MS
    return max(1, math.ceil((expected - now) / MIN_MS))


def crowd_walk(last_ts: int, settles_at: int, now: int) -> tuple[int, int]:
    """(t_end, minutes): a crowding walk runs from the reading it starts at (last_ts), not from now, so the minutes
    between TfL's reading and the open carry their share of the variance."""
    t_end = now + crowd_horizon(last_ts, settles_at, now) * MIN_MS
    return t_end, max(1, round((t_end - last_ts) / MIN_MS))


def crowd_settle_rule(settles_at: int) -> str:
    closes = settles_at - CLOSE_MS
    return (f"each station's first TfL Crowding reading that TfL stamps (timeUtc) after bets close at "
            f"{local_time(closes, '%H:%M:%S')}, not the time we fetch it (TfL stamps every 5 min and publishes a few "
            f"minutes later). No such reading by {local_time(closes + CROWD_WAIT_MS)} → void, stakes refunded")


def first_after_close(store: Store, naptans: dict[str, str], closes_at: int, until: int) -> tuple[dict, dict, int]:
    """Per key of `naptans`: the first value TfL stamped after closes_at, polled by `until`. ({key: value},
    {key: TfL stamp}, poll ts of the last one found). A row without a TfL stamp counts at its poll time."""
    vals, stamps, last = {}, {}, 0
    for ts, snap in store.between("crowding", closes_at + 1, until):
        for key, nap in naptans.items():
            if key in vals or nap not in snap:
                continue
            stamp = store.tfl_stamp(ts, nap) or ts
            if stamp > closes_at:
                vals[key], stamps[key], last = snap[nap], stamp, ts
        if len(vals) == len(naptans):
            break
    return vals, stamps, last


def measured(stamps: dict) -> int | None:
    """TfL's own time for a settle value read from several stations: the earliest timeUtc used, so measuredAt >
    closesAt holds for every station in it."""
    return min(stamps.values()) if stamps else None


def crowd_values(m: Market, store: Store, now: int):
    """('ok', ts, {option-or-hotspot: fraction of usual}, {same: TfL stamp}) once every station in
    m.spec['stations'] has a reading TfL stamped after bets close; ('wait', ms) until CROWD_WAIT_MS after the close,
    then ('missing',)."""
    deadline = m.closes_at + CROWD_WAIT_MS
    vals, stamps, ts = first_after_close(store, m.spec["stations"], m.closes_at, min(now, deadline))
    if len(vals) == len(m.spec["stations"]):
        return ("ok", ts, vals, stamps)
    return ("wait", deadline - now) if now < deadline else ("missing",)


# ---------------------------------------------------------------- series: oxford-busy


def build_oxford_busy(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """Four brackets of Oxford Circus's % of usual, placed on the futures' spread."""
    spot = next(h for h in spots if h["id"] == OXFORD)
    last = store.latest("crowding", now)
    if not last or spot["naptan"] not in last[1]:
        return None
    m0 = store.measured_at(last[0])
    t_end, minutes = crowd_walk(m0, settles_at, now)
    station = crowd_station(store, spot, last[1], now, m0, t_end, minutes)
    sim = pricing.LevelSim(station, m0, minutes, random.Random(seed_of(mid)))
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
            {"value": round(pct, 2), "source": "TfL Crowding API", "measuredAt": measured(got[3]), "fetchedAt": ts})


# ---------------------------------------------------------------- vs usual (TfL live % ÷ the typical day)


def typical_at(store: Store, naptan: str, ts: int) -> float | None:
    """The typical value for the band TfL measured the station in, for the crowding reading polled at ts."""
    return store.typical(naptan, store.tfl_stamp(ts, naptan) or ts)


def vs_usual(store: Store, spots: list[dict], ts: int, snap: dict) -> dict[str, float] | None:
    """{hub id: live % ÷ typical % for that weekday and 15-min band}, or None unless every hub has both."""
    out = {}
    for h in spots:
        typ = typical_at(store, h["naptan"], ts)
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
    """('ok', ts, {hub: vs usual}, {hub: TfL stamp}) from the first full TfL reading after close; else wait /
    missing."""
    got = crowd_values(m, store, now)
    if got[0] != "ok":
        return got
    ts, vals, stamps = got[1], got[2], got[3]
    vs = {}
    for hid, v in vals.items():
        typ = store.typical(m.spec["stations"][hid], stamps[hid])
        if not typ:
            return ("missing",)
        vs[hid] = v / typ
    return ("ok", ts, vs, stamps)


def fmt_vs(v: float) -> str:
    return f"{v * 100:.0f}%"


# ---------------------------------------------------------------- the central-London agent simulation

SIM_DRAWS = 4000
SIM_SALT = 0x5157


def race_pick(order: list[str], vs: dict[str, float], open_vs: dict[str, float] | None) -> str:
    """The race winner by the settle rules: highest vs usual (hub_hour) or biggest rise since open (hub_surge, when
    open_vs is given); a tie goes to the first in `order`."""
    score = {h: vs[h] - (open_vs[h] if open_vs else 0.0) for h in order}
    return max(order, key=lambda h: (score[h], -order.index(h)))


def pulse_from(draw: dict[str, float], snap: dict, store: Store, ts0: int, t_end: int) -> float | None:
    """City Pulse at t_end if each station the sim covers lands on its drawn vs usual and every other station keeps
    its vs usual from the reading at ts0 (live % scaled by its typical day)."""
    vals = []
    for nap, live in snap.items():
        typ_t = store.typical(nap, t_end)
        if nap in draw and typ_t:
            vals.append(draw[nap] * typ_t)
            continue
        typ0 = store.typical(nap, ts0)
        vals.append(live * typ_t / typ0 if typ_t and typ0 else live)
    return pulse_of(dict(zip(snap, vals)))


def blend_hubs(sim: pricing.RaceSim, store: Store, spots: list[dict], now: int, t_end: int, mid: str) -> None:
    fc = store.sim(now)
    n = pricing.sim_count()
    if not fc or not n:
        return
    rng = random.Random(seed_of(mid) ^ SIM_SALT)
    draws = pricing.sim_draws(fc, [h["naptan"] for h in spots], t_end, rng, n)
    if draws:
        pricing.blend_race(sim, [{h["naptan"]: d[h["naptan"]] for h in spots} for d in draws], rng)


def blend_pulse(sim: pricing.ScalarSim, store: Store, snap_at: tuple[int, dict], now: int, t_end: int,
                mid: str) -> None:
    fc = store.sim(now)
    n = pricing.sim_count()
    if not fc or not n:
        return
    ts0, snap = snap_at
    naps = [nap for nap in snap if nap in fc["stations"]]
    rng = random.Random(seed_of(mid) ^ SIM_SALT)
    draws = pricing.sim_draws(fc, naps, t_end, rng, n) if naps else None
    finals = [p for d in draws if (p := pulse_from(d, snap, store, ts0, t_end))] if draws else []
    if len(finals) == n:
        pricing.blend_scalar(sim, finals, rng)


def spread(fc: dict, nap: str, t_end: int) -> str:
    mu, sd = pricing.sim_dist(fc, nap, t_end)
    return (f"{fc['stations'][nap]['name']} {mu * 100:.0f}% of usual at {local_time(t_end)} "
            f"(p10 {(mu - pricing.Z90 * sd) * 100:.0f}, p90 {(mu + pricing.Z90 * sd) * 100:.0f})")


def sim_view(m: Market, store: Store, now: int) -> dict | None:
    """What the simulation says about a market now: {probs {option: P under the sim's forecast}, detail {option:
    the forecast in words}}, or None when the sim is stale or cannot inform this series."""
    fc = store.sim(now)
    last = store.latest("crowding", now)
    if not fc:
        return None
    rng = random.Random(seed_of(m.id) ^ SIM_SALT ^ now)
    if m.sub in ("hub_surge", "hub_hour") and last:
        t_end = now + crowd_horizon(store.measured_at(last[0]), m.settles_at, now) * MIN_MS
        hubs = m.option_ids()
        naps = [m.spec["stations"][h] for h in hubs]
        draws = pricing.sim_draws(fc, naps, t_end, rng, SIM_DRAWS)
        if not draws:
            return None
        open_vs = m.spec["open"] if m.sub == "hub_surge" else None
        counts = {h: 0 for h in hubs}
        for d in draws:
            counts[race_pick(hubs, {h: d[nap] for h, nap in zip(hubs, naps)}, open_vs)] += 1
        detail = {}
        for h, nap in zip(hubs, naps):
            frm = f", from {fmt_vs(open_vs[h])} at open" if open_vs else ""
            detail[h] = spread(fc, nap, t_end) + frm
        return {"probs": {h: c / len(draws) for h, c in counts.items()}, "detail": detail}
    if m.sub == "city_pulse" and last and pulse_of(last[1]):
        t_end = now + crowd_horizon(store.measured_at(last[0]), m.settles_at, now) * MIN_MS
        naps = [nap for nap in last[1] if nap in fc["stations"]]
        draws = pricing.sim_draws(fc, naps, t_end, rng, SIM_DRAWS) if naps else None
        pulses = sorted(p for d in draws or [] if (p := pulse_from(d, last[1], store, last[0], t_end)))
        if not pulses:
            return None
        over = sum(p > m.spec["line"] for p in pulses) / len(pulses)
        q = lambda f: pulses[int(f * (len(pulses) - 1))]  # noqa: E731
        text = (f"City Pulse {q(0.5):.1f} at {local_time(t_end)} (p10 {q(0.1):.1f}, p90 {q(0.9):.1f}; {len(naps)} "
                f"of {len(last[1])} stations simulated) vs a {m.spec['line']:.1f} line")
        return {"probs": {"over": over, "under": 1 - over}, "detail": {"over": text, "under": text}}
    if m.sub == "london_vix" and fc.get("vix") is not None and data.MULTIVERSE_URL == data.SIM_URL:
        st = vix_state(store, now)
        deltas = pricing.deltas_at(st.get("history") or [], 5)
        if len(deltas) < MIN_DELTAS:
            return None
        sd = max(statistics.pstdev(deltas), 0.05)
        over = 1 - 0.5 * (1 + math.erf((m.spec["line"] - fc["vix"]) / (sd * math.sqrt(2))))
        text = f"its VIX is {fc['vix']:.2f} now (5-min moves sd {sd:.2f}) vs a {m.spec['line']:.2f} line"
        return {"probs": {"over": over, "under": 1 - over}, "detail": {"over": text, "under": text}}
    return None


# ---------------------------------------------------------------- S1 hub_surge (every round)


def hub_corr(ids: list[str]) -> list[list[float]]:
    """The hubs' shock correlation in `ids` order (0 for a pair HUB_CORR doesn't list)."""
    def rho(a: str, b: str) -> float:
        return 1.0 if a == b else HUB_CORR.get(a, {}).get(b, HUB_CORR.get(b, {}).get(a, 0.0))
    return [[rho(a, b) for b in ids] for a in ids]


def hub_grid(store: Store, spots: list[dict], start: int, now: int) -> dict[int, dict[str, tuple[float, float]]]:
    """{TfL 5-min slot: {hub id: (log live %, log usual %)}} from the readings polled in [start, now], each at the
    slot of its own TfL stamp, first poll wins."""
    grid: dict[int, dict[str, tuple[float, float]]] = {}
    for ts, snap in store.between("crowding", start, now):
        for h in spots:
            nap = h["naptan"]
            stamp = store.tfl_stamp(ts, nap) or ts
            usual = store.usual(nap, stamp) if nap in snap else None
            if usual:
                grid.setdefault(stamp // pricing.HUB_STEP_MS, {}).setdefault(h["id"], (math.log(snap[nap]),
                                                                                       math.log(usual)))
    return grid


def hub_prior(stations: list, k: int, ids: list[str]) -> pricing.HubFit:
    """The fit to fall back on when today's history holds too few k-step pairs (the first hour after a cold start):
    HUB_KAPPA compounded over k steps, no hub levels, HUB_CORR, each station's walk sd over 5k minutes."""
    return pricing.HubFit(1 - math.exp(-HUB_KAPPA * 5 * k), [0.0] * len(ids), hub_corr(ids),
                          [s[4] * math.sqrt(5 * k) for s in stations], 0)


def hub_fits(store: Store, spots: list[dict], stations: list, now: int, steps: int) -> dict[int, pricing.HubFit]:
    """{k: HubFit} for k = 1..steps, fitted on today's readings up to now (no look-ahead: only polls at or before
    now, so each round is fitted walk-forward)."""
    ids = [h["id"] for h in spots]
    grid = hub_grid(store, spots, now - HUB_LOOKBACK_MS, now)
    recent = (now - HISTORY_MS) // pricing.HUB_STEP_MS
    out = {}
    for k in range(1, steps + 1):
        floor = HUB_SD_FLOOR * math.sqrt(5 * k)
        out[k] = (pricing.fit_relative_ou(grid, ids, k, recent, HUB_LEVEL_PRIOR, floor)
                  or hub_prior(stations, k, ids))
    return out


def build_hub_surge(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """Race between the hubs: the one whose vs-usual rises most from the reading at open to the first reading after
    close.

    Each hub's settle reading is drawn from a model fitted on today's readings up to now (pricing.HubFit,
    pricing.RelativeRaceSim): its usual-day slope, a pull toward its own level against the other hubs at the race's
    horizon, and correlated shocks. The futures' shares are tempered by HUB_POWER. Out of sample (fitted walk-forward,
    133 races 15:00-02:10 on 2026-11-07/08) it beat the previous model by 0.039 ± 0.024 log loss and 0.017 ± 0.011
    Brier (analysis/judge/hub_model.py, docs/markets.md "hub_surge: the model").

    A hub without a reading or a usual-day value at open is dropped from the race (at least HUB_MIN_HUBS stay):
    settlement needs every listed hub's first reading after close, so a hub already missing would most likely void the
    whole market for everyone; a widened guess on it would still price it at 3% or more.
    debt: HUB_POWER = 0.7 is the walk-forward Platt power at the end of one recorded day (0.25-0.75 through it), and
    HUB_KAPPA / HUB_CORR are the cold-start prior. ceiling: one Saturday 12:00 to Sunday 02:35. upgrade: refit when
    the recorder holds a week."""
    last = store.latest("crowding", now)
    if not last:
        return None
    ts0, snap = last
    stamp_of = {h["id"]: store.tfl_stamp(ts0, h["naptan"]) or ts0 for h in spots}
    live = [h for h in spots if h["naptan"] in snap and typical_at(store, h["naptan"], ts0)
            and store.usual(h["naptan"], stamp_of[h["id"]])]
    if len(live) < HUB_MIN_HUBS:
        return None
    m0 = store.measured_at(ts0)
    t_end, minutes = crowd_walk(m0, settles_at, now)
    stations = [crowd_station(store, h, snap, now) for h in live]
    stamps = [stamp_of[h["id"]] for h in live]
    step = pricing.HUB_STEP_MS
    latest = max(stamps)
    settle_ms = latest + max(1, math.floor((settles_at - CLOSE_MS - latest) / step) + 1) * step
    settle_slot = settle_ms // step
    slots = [s // step for s in stamps]
    phase = latest % step
    log_usual = [{s: math.log(store.usual(h["naptan"], s * step + phase) or store.usual(h["naptan"], st))
                  for s in range(min(slots), settle_slot + 1)} for h, st in zip(live, stamps)]
    fits = hub_fits(store, live, stations, now, settle_slot - min(slots))
    typ0 = [typical_at(store, h["naptan"], ts0) for h in live]
    typ_t = [store.typical(h["naptan"], settle_ms) or t0 for h, t0 in zip(live, typ0)]
    sim = pricing.RelativeRaceSim(stations, m0, minutes, random.Random(seed_of(mid)), typ0, typ_t, fits, slots,
                                  settle_slot, log_usual, HUB_POWER)
    blend_hubs(sim, store, live, now, t_end, mid)
    spec = hub_spec(store, live, stations, ts0, snap)
    day = spec["day"]
    order = ", ".join(h["short"] for h in live)
    rule = (f"Each hub's vs usual = its live TfL busyness ÷ its typical {day} for that 15-min band (TfL Crowding "
            f"profile). The winner is the hub whose vs usual rises most from the reading at open "
            f"({local_time(ts0)}) to {crowd_settle_rule(settles_at)}. A tie for the top goes to the first in the "
            f"order {order}. If no hub moved, the market is void and stakes are refunded.")
    return Market(mid, "hub_surge", "versus", f"Which hub surges most by {local_time(settles_at)}?",
                  [h["id"] for h in live], now, settles_at, hub_options(live), sim,
                  f"TfL Crowding API · {len(live)} hubs vs a usual {day} · {settle_stamp(settles_at)}", rule, spec)


def settle_hub_surge(m: Market, store: Store, now: int):
    got = settled_vs(m, store, now)
    if got[0] != "ok":
        return got
    ts, vs = got[1], got[2]
    gains = {hid: vs[hid] - m.spec["open"][hid] for hid in m.option_ids()}
    snap = {"value": None, "source": "TfL Crowding API", "measuredAt": measured(got[3]), "fetchedAt": ts,
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


def pulse_vs_usual(store: Store, start: int, now: int) -> list[tuple[int, float]]:
    """(ts, log City Pulse ÷ a usual day's pulse over the same stations) per reading; the raw log pulse when the
    usual-day profile is missing."""
    raw, out = [], []
    for ts, s in store.between("crowding", start, now):
        if not (p := pulse_of(s)):
            continue
        raw.append((ts, math.log(p)))
        typ = [store.usual(nap, ts) for nap in s]
        if all(typ):
            out.append((ts, math.log(p / (100 * sum(typ) / len(typ)))))
    return out if len(out) * 2 >= len(raw) else raw


def pulse_along_usual(store: Store, snap_at: tuple[int, dict], t_end: int) -> float | None:
    """City Pulse at t_end if every station follows its usual-day curve from the reading at snap_at."""
    ts0, snap = snap_at
    vals = {}
    for nap, live in snap.items():
        a, b = store.usual(nap, ts0), store.usual(nap, t_end)
        vals[nap] = live * b / a if a and b else live
    return pulse_of(vals)


def pulse_devs(store: Store, start: int, now: int) -> list[tuple[int, float]]:
    """(TfL stamp, log City Pulse ÷ the usual day's pulse at that stamp) per stamp polled in (start, now], oldest first;
    a reading with any station's usual missing is left out."""
    out: dict[int, float] = {}
    for ts, s in store.between("crowding", start, now):
        stamp = store.measured_at(ts)
        typ = [store.usual(nap, stamp) for nap in s]
        if stamp not in out and (p := pulse_of(s)) and all(typ):
            out[stamp] = math.log(p / (100 * sum(typ) / len(typ)))
    return sorted(out.items())


def pulse_line(sim: pricing.ScalarSim) -> float:
    """The futures' median final City Pulse rounded to the coarsest step in PULSE_STEPS no wider than the futures'
    spread (IQR / 1.349, an SD robust to the few sim-blended outliers), else the finest. The line then sits at most half
    an SD from the median, so the opening favourite is at most ~69%: a quiet night's futures span ~0.2 and a fixed .5
    line put all 50 on one side (3%/97% at 03:20 on 8 Nov). Daytime futures spread ~0.6-0.9, so .0/.5 lines stay;
    0.01 is only reached when the spread is under 0.05."""
    finals = [math.exp(p[-1]) for p in sim.paths]
    q1, _, q3 = statistics.quantiles(finals, n=4)
    spread = (q3 - q1) / IQR_PER_SD
    step = next((s for s in PULSE_STEPS if s <= spread), PULSE_STEPS[-1])
    return round(round(statistics.median(finals) / step) * step, 2)


def line_label(line: float) -> str:
    """2.35 -> "2.35", 2.5 -> "2.5", 3.0 -> "3.0": the line as written, never float noise."""
    text = f"{line:.2f}"
    return text[:-1] if text.endswith("0") else text


def build_city_pulse(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """Over/under on City Pulse; line = the futures' median City Pulse at the settle reading, on a step that fits their
    spread (pulse_line), so the market opens near 50/50 (the old line, open + the median 5-min change, missed the
    usual-day slope the futures drift along and opened evening markets at a 94% favourite). The futures run from the
    newest reading's TfL stamp to the first stamp after close along the usual day, plus the last 10 min's trend in vs
    usual (pricing.fit_pulse; analysis/judge/pulse_model.py). Without the usual profile at those two readings they fall
    back to the plain walk's drift, half the last hour's mean change."""
    last = store.latest("crowding", now)
    p0 = pulse_of(last[1]) if last else None
    if not p0:
        return None
    mu, sd = pricing.fit_log_walk(pulse_vs_usual(store, now - HISTORY_MS, now), 0.01, 0.002, 0.05)
    m0 = store.measured_at(last[0])
    t_end, minutes = crowd_walk(m0, settles_at, now)
    trend = pricing.fit_pulse(pulse_devs(store, now - PULSE_TREND_MS, now), m0, minutes)
    if trend is not None:
        mu = trend
    mu += pricing.usual_drift(p0, pulse_along_usual(store, (m0, last[1]), t_end), minutes)
    sim = pricing.ScalarSim(p0, mu, sd, m0, minutes, random.Random(seed_of(mid)), pulse_of, p0)
    blend_pulse(sim, store, last, now, t_end, mid)
    line = sim.strike = pulse_line(sim)
    label = line_label(line)
    hhmm = local_time(settles_at)
    rule = (f"City Pulse = 100 × the mean live TfL busyness (% of baseline) over the {PULSE_STATIONS} TfL Crowding "
            f"stations, in {crowd_settle_rule(settles_at)}. Fewer than {PULSE_MIN_STATIONS} stations reporting → "
            f"void. Over = above {label}; exactly {label} or below counts as Under.")
    return Market(mid, "city_pulse", "overunder", f"City Pulse at {hhmm}: over or under {label}?", [], now,
                  settles_at, over_under_options(label), sim,
                  f"TfL Crowding API · {PULSE_STATIONS} stations · {settle_stamp(settles_at)}", rule,
                  {"line": line, "open": round(p0, 2), "openTs": last[0], "sigma": sd})


def settle_city_pulse(m: Market, store: Store, now: int):
    """Each station that reports at the close counts at its first reading TfL stamped after the close. Settles once
    all of them have one, or at the deadline on the ones that do (18 of 20 needed)."""
    deadline = m.closes_at + CROWD_WAIT_MS
    after = store.between("crowding", m.closes_at + 1, min(now, deadline))
    if not after:
        return ("wait", deadline - now) if now < deadline else ("missing",)
    vals, stamps, ts = first_after_close(store, {nap: nap for nap in after[0][1]}, m.closes_at, min(now, deadline))
    if len(vals) < len(after[0][1]) and now < deadline:
        return ("wait", deadline - now)
    pulse = pulse_of(vals)
    if pulse is None:
        return ("void", f"Only {len(vals)} of {PULSE_STATIONS} stations reported — void",
                {"value": None, "source": "TfL Crowding API", "measuredAt": measured(stamps),
                 "fetchedAt": ts or None})
    pulse = round(pulse, 4)
    winner = "over" if pulse > m.spec["line"] else "under"
    return ("done", winner, f"City Pulse {pulse:.2f} vs line {m.spec['line']:.2f} → {m.label(winner)}",
            {"value": round(pulse, 2), "source": "TfL Crowding API", "measuredAt": measured(stamps),
             "fetchedAt": ts})


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
    """Whether the London VIX can be traded now: a multiverse frame no older than the feed's 3-min staleness (the
    engine stamps frames 60-120 s apart and serves them up to ~70 s late, so 2 min failed on its normal cadence), the
    engine past its first hour since spawn (the VIX decays from ~16 to ~2.3 over it) and enough post-warm-up moves to
    price from. {ok, reason, vix, ts, spawnTs, history [(ts, vix)] since warm-up within 3 h, driver}."""
    last = store.latest("vix", now)
    if not last or now - last[0] > VIX_MAX_AGE_MS:
        return {"ok": False, "reason": f"the multiverse engine is unreachable (no VIX frame in "
                                       f"{VIX_MAX_AGE_MS // MIN_MS} min)"}
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
                {"value": None, "source": FEED_LABEL["vix"], "measuredAt": ts, "fetchedAt": ts})
    v = round(snap["vix"], 4)
    winner = "over" if v > m.spec["line"] else "under"
    return ("done", winner, f"London VIX {v:.2f} vs {m.spec['line']:.2f} → {m.label(winner).split()[0]}",
            {"value": round(v, 2), "source": FEED_LABEL["vix"], "measuredAt": ts, "fetchedAt": ts})


# ---------------------------------------------------------------- S4 heathrow_stack (stands in for S3)


def build_heathrow_stack(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    last = store.latest("aircraft", now)
    if not last or not store.fresh("aircraft", now):
        return None
    n0 = last[1]["total"]
    recent = [float(s["total"]) for _, s in store.between("aircraft", now - STACK_MEAN_MS, now)]
    if len(recent) < MIN_DELTAS:
        return None
    minutes = max(1, round((settles_at - now) / MIN_MS))
    sim = pricing.CountSim(float(n0), recent, STACK_KAPPA, now, minutes, random.Random(seed_of(mid)),
                           lambda s: float(s["total"]), n0 + 0.5)
    line = sim.strike = pricing.poisson_line(sim.lam)
    vix = vix_state(store, now)
    hhmm = local_time(settles_at)
    rule = (f"adsb.lol holding_total (aircraft in the BNN, LAM, BIG and OCK stacks over Heathrow) in the last reading "
            f"at or before {local_time(settles_at, '%H:%M:%S')}, at most 2 min old; otherwise void. Over = "
            f"{line + 0.5:g} or more. Listed in place of the London VIX while {vix['reason'] or 'it is down'}.")
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
            {"value": n, "source": FEED_LABEL["aircraft"], "measuredAt": ts, "fetchedAt": ts})


# ---------------------------------------------------------------- S5 hub_hour (hourly headline, opens at :00)


def hourly(offset_min: int) -> Callable[[int], bool]:
    """Due in the one round per hour whose betting window spans :offset (round settling at :offset+5)."""
    return lambda at: at % HOUR_MS == (offset_min + 5) * MIN_MS


def headline_end(at: int) -> int:
    return at - ROUND_MS + HOUR_MS


def headline_round(end: int) -> int:
    """The round a headline settling at `end` opened in (headline_end's inverse)."""
    return end + ROUND_MS - HOUR_MS


def build_hub_hour(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    """5-way race on vs-usual at the hour. Sigma is fitted on 60-min changes and widened until the favourite at
    open is at most 70% (the 5-min fit priced it at 89% against a real hit rate of 56%)."""
    last = store.latest("crowding", now)
    if not last or vs_usual(store, spots, last[0], last[1]) is None:
        return None
    ts0, snap = last
    end = headline_end(settles_at)
    mid = f"hub_hour-{local_time(end, '%H%M')}"
    m0 = store.measured_at(ts0)
    t_end, minutes = crowd_walk(m0, end, now)
    typ0 = [typical_at(store, h["naptan"], ts0) for h in spots]
    typ_t = [store.typical(h["naptan"], t_end) or t0 for h, t0 in zip(spots, typ0)]
    stations = []
    for h in spots:
        a, b = store.usual(h["naptan"], m0), store.usual(h["naptan"], t_end)
        series = crowd_series(store, h["naptan"], now - HISTORY_MS, now)
        d60 = pricing.deltas_at(series, 60, CROWD_TOL_MS)
        sd = (statistics.pstdev(d60) / math.sqrt(60)) if len(d60) >= 6 else \
            crowd_station(store, h, snap, now, m0, t_end, minutes)[4]
        stations.append((h["id"], h["naptan"], math.log(snap[h["naptan"]]), pricing.usual_drift(a, b, minutes),
                         max(sd, 0.002)))
    rng = random.Random(seed_of(mid))
    for _ in range(20):
        sim = pricing.RaceSim(stations, m0, minutes, rng, typ0, typ_t, "level")
        if max(pricing.Futures(sim, [h["id"] for h in spots]).probs().values()) <= HEADLINE_CAP:
            break
        stations = [(a, b, x, mu, sd * 1.3) for a, b, x, mu, sd in stations]
    blend_hubs(sim, store, spots, now, t_end, mid)
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
            f"{name}", {"value": round(vs[winner], 4), "source": "TfL Crowding API", "measuredAt": measured(got[3]),
                        "fetchedAt": ts,
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
            f"Line Status reading until {local_time(end)}. Betting closes at "
            f"{local_time(end - CLEAN_CLOSE_BEFORE_MS)}, with the opening round. The first reading that shows any of "
            f"them worse settles NO at once. Readings missing for more than 5 min in total → void, stakes refunded.")
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
                    {"value": len(broke), "source": "TfL Line Status API", "measuredAt": ts, "fetchedAt": ts,
                     "line": broke[0], "lines": broke})
    if now < m.settles_at + SETTLE_LAG_MS:
        return ("wait", min(max(1, m.settles_at + SETTLE_LAG_MS - now), 5_000))
    stamps = [m.opened_at] + [ts for ts, _ in reads] + [m.settles_at]
    gaps = sum(max(0, b - a - MIN_MS) for a, b in zip(stamps, stamps[1:]) if b - a > LINE_GAP_MS)
    if gaps > CLEAN_MAX_GAP_MS:
        return ("void", f"Line Status readings missing for {gaps / MIN_MS:.0f} min — void",
                {"value": None, "source": "TfL Line Status API", "measuredAt": reads[-1][0] if reads else None,
                 "fetchedAt": reads[-1][0] if reads else None})
    return ("done", "yes", f"All {len(m.spec['good'])} lines stayed Good from {local_time(m.opened_at)} to "
            f"{local_time(m.settles_at)} → Yes", {"value": 0, "source": "TfL Line Status API",
                                                 "measuredAt": reads[-1][0] if reads else None,
                                                 "fetchedAt": reads[-1][0] if reads else None})


# ---------------------------------------------------------------- the lineup (registration order = listing order)

register(Series("hub_surge", "crowding", build_hub_surge, settle_hub_surge))
register(Series("city_pulse", "crowding", build_city_pulse, settle_city_pulse))
register(Series("london_vix", "vix", build_london_vix, settle_london_vix,
                why_none=lambda store, now: vix_state(store, now)["reason"]))
register(Series("heathrow_stack", "aircraft", build_heathrow_stack, settle_heathrow_stack, skip=skip_heathrow_stack))
register(Series("hub_hour", "crowding", build_hub_hour, settle_hub_hour, due=hourly(0)))
register(Series("clean_sheet", "line_status", build_clean_sheet, settle_clean_sheet, due=hourly(30),
                settles_early=True, close_before_ms=CLEAN_CLOSE_BEFORE_MS))
register(Series("oxford-busy", "crowding", build_oxford_busy, settle_oxford_busy, enabled=False))


# ---------------------------------------------------------------- settlement


MEASURED_AFTER_CLOSE = ("crowding", "jamcams")


def try_settle(m: Market, store: Store, now: int):
    """('wait', ms) | ('done', winner, reading, settlement) | ('void', reading, settlement). A market locked on a
    final answer settles on it at settlesAt, or at once for a settles_early series (betting closed the moment it was
    decided). Otherwise a crowding or camera market settles only on a value its source measured after bets closed
    (settlement measuredAt > closesAt), else voids."""
    series = REGISTRY[m.sub]
    if m.locked is not None and (now >= m.settles_at or series.settles_early):
        return m.locked
    if now < m.settles_at and not series.settles_early:
        return ("wait", m.settles_at - now)
    got = series.settle(m, store, now)
    if got[0] == "missing":
        return ("void", "No data — void", {"value": None, "source": FEED_LABEL[m.feed], "measuredAt": None,
                                          "fetchedAt": None})
    if got[0] == "done" and m.feed in MEASURED_AFTER_CLOSE and now >= m.closes_at:
        at = got[3].get("measuredAt")
        if at is None or at <= m.closes_at:
            return ("void", "The settling value was measured before bets closed — void, stakes refunded", got[3])
    return got


# ---------------------------------------------------------------- observations + jump halts


def observe(m: Market, store: Store, now: int) -> None:
    """Feed new readings of the market's feed (up to settlesAt) to its futures and its jump detector."""
    for ts, snap in store.between(m.feed, m.last_obs_ts + 1, min(now, m.settles_at)):
        if nothing_new(m, snap):
            m.last_obs_ts = ts
            continue
        m.futures.observe(store.measured_at(ts) if m.feed == "crowding" else ts, snap)
        jump = jump_of(m, ts, snap, store)
        if jump:
            m.halt_reason, m.clean_ticks = jump, 0
        elif m.halt_reason and m.halt_reason.startswith("jump"):
            m.clean_ticks += 1
            if m.clean_ticks >= CLEAN_TICKS:
                m.halt_reason = None
        m.last_obs_ts = ts


def nothing_new(m: Market, snap: dict) -> bool:
    """A crowding poll that changed none of the stations this market reads (all of them for City Pulse). TfL publishes
    station by station over a few minutes, so a 30 s poll often moves other stations only; observing it again would
    count this market's unchanged readings twice."""
    if m.feed != "crowding" or m.prev_obs is None:
        return False
    prev, naps = m.prev_obs["snap"], m.spec.get("stations", {}).values()
    return all(prev.get(n) == snap.get(n) for n in naps) if naps else prev == snap


def jump_of(m: Market, ts: int, snap: dict, store: Store | None = None) -> str | None:
    """A 3σ one-tick jump in any TfL Crowding station the market reads (spec 'stations' + 'sigmas'). The gap is TfL's
    own time between the two readings of that station when both are stamped, else the time between our polls."""
    prev, m.prev_obs = m.prev_obs, {"ts": ts, "snap": snap}
    if prev is None or m.feed != "crowding":
        return None
    for oid, nap in m.spec.get("stations", {}).items():
        a, b = prev["snap"].get(nap), snap.get(nap)
        sd = m.spec["sigmas"].get(nap)
        if a and b and sd:
            sa, sb = (store.tfl_stamp(prev["ts"], nap), store.tfl_stamp(ts, nap)) if store else (None, None)
            gap = max(1.0, ((sb - sa) if sa and sb and sb > sa else ts - prev["ts"]) / MIN_MS)
            z = (math.log(b) - math.log(a)) / (sd * math.sqrt(gap))
            if abs(z) > JUMP_SIGMA:
                return f"jump: {m.spec['names'][oid]} {z:+.1f}σ"
    return None
