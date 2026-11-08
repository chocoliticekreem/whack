"""Seven rule-based AI traders. Each reads exactly one feed and bets only on the series that feed informs (PLAYS),
saying why in one line that cites the real number it saw. A trader with nothing to go on sits the round out and says
so. Deterministic: no LLM calls. The rules are the cues measured in docs/questions.md.

Adding a series: add `{series_id: decide_fn}` to the PLAYS entry of each trader whose feed informs it. The Contrarian
plays every series."""
from __future__ import annotations

import re
import sys

import signals
from data import Store, day_name, local_time
from markets import Market, pulse_of, sim_view
from pricing import MIN_MS, RAIN_MM, bracket_of

STAKE = 100
STAKE_SHARE = 0.10
MIN_STAKE = 10
TROUBLE = re.compile(r"cancellations|signal|faulty|points|customer|incident", re.I)

TRADERS = [
    {"id": "tube", "name": "Tube Crowd", "emoji": "🚇", "feed": "crowding",
     "watches": "Live busyness at 20 Tube stations against TfL's typical day (TfL Crowding API)",
     "style": "Backs the hub furthest below its usual to catch up, rides City Pulse with the usual curve, and backs "
              "the hour's leader unless a final whistle points at a hub."},
    {"id": "cams", "name": "Camera Eye", "emoji": "📷", "feed": "vix",
     "watches": "The London VIX from the OverWatch multiverse, built on the JamCam people counts",
     "style": "Fades the VIX's last 5-min move: spikes fade (65% in today's replay)."},
    {"id": "lines", "name": "Line Watcher", "emoji": "🚦", "feed": "line_status",
     "watches": "TfL status of every Tube line, minute by minute",
     "style": "Bets the clean sheet breaks when fresh unplanned trouble is on the board; otherwise backs it."},
    {"id": "rain", "name": "Rain Radar", "emoji": "🌧️", "feed": "rain_now",
     "watches": "Open-Meteo precipitation now for central London",
     "style": "Sits out unless it rains: wet weather sends people underground, so it backs City Pulse Over."},
    {"id": "bus", "name": "Plane Spotter", "emoji": "✈️", "feed": "aircraft",
     "watches": "Aircraft in the four Heathrow holding stacks (adsb.lol)",
     "style": "Stacks drain after they build: fades the last 10 minutes' move."},
    {"id": "contrarian", "name": "The Contrarian", "emoji": "🙃", "feed": "bets",
     "watches": "Where players and the other traders are putting their points",
     "style": "Fades the favourite of any series and takes the best-priced alternative."},
    {"id": "sim", "name": "Simulation", "emoji": "🧭", "feed": "sim",
     "watches": "Agent simulation of central London (50 worlds on the street graph)",
     "style": "Backs the option the simulation's forecast rates most above the market's price, only with an edge of "
              "5 points or more."},
]
BY_ID = {t["id"]: t for t in TRADERS}


def pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def stake_for(points: int, open_stakes: int = 0) -> int:
    """min(100, 10% of the points not already staked on unsettled markets); 0 under 10, so a busted trader sits out.
    Sizing on points alone let the Contrarian go to -181 live: points only move at settle, and it held a dozen open
    bets, each sized on the same pre-loss points, on markets that settle minutes after they close. With the open
    stakes taken off first, every open stake together is at most the points, so losing all of them ends at >= 0."""
    stake = min(STAKE, int(STAKE_SHARE * (points - open_stakes)))
    return stake if stake >= MIN_STAKE else 0


# ---------------------------------------------------------------- track records, kept in the ledger's SQLite

RECORDS_SCHEMA = """CREATE TABLE IF NOT EXISTS ai_records (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  trader  TEXT NOT NULL,
  ts      INTEGER NOT NULL,
  won     INTEGER NOT NULL,
  lost    INTEGER NOT NULL,
  points  INTEGER NOT NULL,
  why     TEXT NOT NULL
)"""


def load_records(ledger) -> dict[str, dict]:
    """{trader: {won, lost, points, why}} from each trader's newest row (append-only table, one row per settle).
    A negative balance, written before open stakes were reserved (stake_for), loads as 0 and is logged: the row
    stays as history, and the trader sits out as busted rather than showing points it can never have."""
    with ledger.lock:
        ledger.db.execute(RECORDS_SCHEMA)
        rows = ledger.db.execute("SELECT trader, won, lost, points, why FROM ai_records WHERE id IN "
                                 "(SELECT MAX(id) FROM ai_records GROUP BY trader)").fetchall()
    out = {r[0]: {"won": r[1], "lost": r[2], "points": r[3], "why": r[4]} for r in rows}
    for tid, rec in out.items():
        if rec["points"] < 0:
            print(f"ai record {tid}: {rec['points']} points on file, loaded as 0", file=sys.stderr)
            rec["points"] = 0
    return out


def save_records(ledger, ts: int, records: dict[str, dict]) -> None:
    with ledger.lock:
        ledger.db.execute(RECORDS_SCHEMA)
        ledger.db.executemany("INSERT INTO ai_records (trader, ts, won, lost, points, why) VALUES (?, ?, ?, ?, ?, ?)",
                              [(tid, ts, r["won"], r["lost"], r["points"], r["why"]) for tid, r in records.items()])


# ---------------------------------------------------------------- Tube Crowd


def tube_surge(m: Market, store: Store, now: int, ctx: dict):
    """The hub furthest below its usual at open catches up (35% vs 20% for luck today)."""
    vs = m.spec["open"]
    hid = min(m.option_ids(), key=lambda h: vs[h])
    return hid, (f"{m.spec['names'][hid]} is at {pct(vs[hid])} of a usual {m.spec['day']}, the furthest below — "
                 f"it catches up")


def tube_pulse(m: Market, store: Store, now: int, ctx: dict):
    """If the usual curve's 15-min band changes before settle, bet its slope; else bet the last 5-min change."""
    last = store.latest("crowding", now)
    if not last or not pulse_of(last[1]):
        return None
    ts, snap = last
    end = m.settles_at
    if (ts // (15 * MIN_MS)) != (end // (15 * MIN_MS)):
        a = [t for nap in snap if (t := store.typical(nap, ts))]
        b = [t for nap in snap if (t := store.typical(nap, end))]
        if a and b:
            ua, ub = 100 * sum(a) / len(a), 100 * sum(b) / len(b)
            if abs(ub - ua) > 1e-9:
                side = "over" if ub > ua else "under"
                verb = "rises" if ub > ua else "falls"
                return side, (f"A usual {day_name(ts)}'s pulse {verb} from {ua:.1f} to {ub:.1f} at "
                              f"{local_time(end - end % (15 * MIN_MS))}; City Pulse follows it")
    hist = [(t, p) for t, s in store.between("crowding", ts - 8 * MIN_MS, ts) if (p := pulse_of(s))]
    prev = next(((t, p) for t, p in reversed(hist) if ts - t >= 4 * MIN_MS), None)
    if not prev:
        return None
    p1 = pulse_of(snap)
    if abs(p1 - prev[1]) < 1e-9:
        return None
    side = "over" if p1 > prev[1] else "under"
    return side, (f"City Pulse went {prev[1]:.2f} → {p1:.2f} between {local_time(prev[0])} and {local_time(ts)}; "
                  f"it keeps going against a {m.spec['line']:.2f} line")


def tube_hour(m: Market, store: Store, now: int, ctx: dict):
    """Back the leader (held the hour 9/16 today) unless a fixture's crowd heads for a hub."""
    fx = signals.fixture_signal(store, signals.SPOTS, now)
    vs = m.spec["open"]
    if fx and fx["data"]["hub"] in vs:
        hid = fx["data"]["hub"]
        d = fx["data"]
        return hid, (f"{d['home']} {d['score']} {d['away']} ended ~{local_time(d['egressEst'])}; fans head for "
                     f"{m.spec['names'][hid]}, now {pct(vs[hid])} of a usual {m.spec['day']}")
    hid = max(m.option_ids(), key=lambda h: vs[h])
    return hid, (f"{m.spec['names'][hid]} leads at {pct(vs[hid])} of a usual {m.spec['day']}; the leader held the "
                 f"hour 9 times in 16 today")


def tube_busy(m: Market, store: Store, now: int, ctx: dict):
    """Momentum: carry half the last 15 min's change per minute forward to the round and take that bracket."""
    nap = m.spec["naptan"]
    hist = [(ts, s[nap] * 100) for ts, s in store.between("crowding", now - 16 * MIN_MS, now) if nap in s]
    if len(hist) < 2:
        return None
    (t0, a), (t1, b) = hist[0], hist[-1]
    rate = (b - a) / max(1.0, (t1 - t0) / MIN_MS)
    proj = b + 0.5 * rate * (m.settles_at - t1) / MIN_MS
    return bracket_of(proj, m.spec["edges"]), (
        f"{m.spec['name']} went from {a:.1f}% to {b:.1f}% of usual between {local_time(t0)} and {local_time(t1)}; "
        f"that trend puts it near {proj:.1f}% at {local_time(m.settles_at)}")


# ---------------------------------------------------------------- Camera Eye, Plane Spotter, Line Watcher, Rain


def fade(series: list[tuple[int, float]], minutes: int):
    """(start ts, start value, end ts, end value) over the last `minutes`, or None."""
    if not series:
        return None
    t1, b = series[-1]
    prev = next(((t, v) for t, v in reversed(series) if t1 - t >= minutes * MIN_MS), None)
    return (prev[0], prev[1], t1, b) if prev else None


def cams_vix(m: Market, store: Store, now: int, ctx: dict):
    """Fade the last 5-min move: rose → Lower (65%, 26/40 today), fell → Higher."""
    hist = [(ts, s) for ts, s in store.between("vix", now - 10 * MIN_MS, now)]
    got = fade([(ts, s["vix"]) for ts, s in hist], 5)
    if not got or abs(got[3] - got[1]) < 1e-9:
        return None
    _, a, _, b = got
    driver = hist[-1][1].get("driver") or "the engine"
    side, verb = ("under", "jumped") if b > a else ("over", "dropped")
    return side, f"VIX {verb} {a:.2f} → {b:.2f} in 5 min on {driver}; moves fade"


def bus_stack(m: Market, store: Store, now: int, ctx: dict):
    """Stacks drain after they build: rose over 10 min → Under (60%, 28/47 today), fell → Over, flat → no bet."""
    hist = [(ts, s) for ts, s in store.between("aircraft", now - 15 * MIN_MS, now)]
    got = fade([(ts, float(s["total"])) for ts, s in hist], 10)
    if not got or got[3] == got[1]:
        return None
    _, a, _, b = got
    stacks = " · ".join(f"{k} {v}" for k, v in hist[-1][1]["stacks"].items())
    if b > a:
        return "under", f"Stacks built {a:.0f} → {b:.0f} in 10 min ({stacks}); stacks drain after they build"
    return "over", f"Stacks drained {a:.0f} → {b:.0f} in 10 min ({stacks}); the next wave stacks up again"


def lines_clean(m: Market, store: Store, now: int, ctx: dict):
    """NO when an unplanned reason is on the board and it changed under 30 min ago; otherwise YES."""
    board = signals.line_board(store, now)
    if not board:
        return None
    for d in board["disrupted"]:
        if TROUBLE.search(d["reason"]) and d["sinceMin"] is not None and d["sinceMin"] < 30:
            return "no", (f"{d['name']} went to {d['desc']} {d['sinceMin']} min ago "
                          f"({signals.short_reason(d['reason'])}); fresh trouble spreads")
    quiet = board["sinceLastChangeMin"]
    since = f"nothing changed in {quiet} min" if quiet is not None else "nothing changed in 3 h"
    return "yes", f"{board['good']} of {board['of']} Tube lines Good and {since}; the board is settling"


def rain_pulse(m: Market, store: Store, now: int, ctx: dict):
    """Rain pushes people onto the Tube: City Pulse Over."""
    wet = store.latest("rain_now", now)
    if not wet or wet[1]["mm"] <= RAIN_MM:
        return None
    return "over", (f"{wet[1]['mm']:g} mm of rain at {local_time(wet[0])} (Open-Meteo): rain sends people "
                    f"underground, City Pulse above {m.spec['line']:.2f}")


def rain_busy(m: Market, store: Store, now: int, ctx: dict):
    """Rain pushes people onto the Tube: back the bracket above the one the station is in now."""
    wet, crowd = store.latest("rain_now", now), store.latest("crowding", now)
    if not wet or wet[1]["mm"] <= RAIN_MM or not crowd or m.spec["naptan"] not in crowd[1]:
        return None
    v = crowd[1][m.spec["naptan"]] * 100
    k = min(len(m.spec["edges"]), int(bracket_of(v, m.spec["edges"])[1:]) + 1)
    return f"b{k}", (f"{wet[1]['mm']:g} mm of rain at {local_time(wet[0])} (Open-Meteo) and {m.spec['name']} at "
                     f"{v:.1f}%: rain sends people underground")


def contrarian(m: Market, store: Store, now: int, ctx: dict):
    stakes = ctx["stakes"]
    prices = m.prices()
    if any(stakes.values()):
        fav = max(stakes, key=lambda o: stakes[o])
        reason = f"players and traders put {stakes[fav]:g} points on {m.label(fav)}"
    else:
        fav = max(prices, key=lambda o: prices[o])
        reason = f"the market has {m.label(fav)} at {pct(prices[fav])}"
    alt = max((o for o in prices if o != fav), key=lambda o: prices[o])
    return alt, f"Fading the crowd: {reason}; taking {m.label(alt)} at {pct(prices[alt])}"


SIM_EDGE = 0.05


def sim_trade(m: Market, store: Store, now: int, ctx: dict):
    """The option whose probability under the simulation's forecast beats its price by the most, if by 5+ points."""
    view = ctx["sim_view"] if "sim_view" in ctx else sim_view(m, store, now)
    if not view:
        return None
    prices = m.prices()
    best = max(prices, key=lambda o: view["probs"][o] - prices[o])
    if view["probs"][best] - prices[best] < SIM_EDGE:
        return None
    return best, (f"Simulation: {view['detail'][best]}; {m.label(best)} in {pct(view['probs'][best])} of its draws "
                  f"vs the market's {pct(prices[best])}")


PLAYS = {"tube":{"hub_surge": tube_surge, "city_pulse": tube_pulse, "hub_hour": tube_hour, "oxford-busy": tube_busy},
         "cams": {"london_vix": cams_vix}, "lines": {"clean_sheet": lines_clean},
         "rain": {"city_pulse": rain_pulse, "oxford-busy": rain_busy}, "bus": {"heathrow_stack": bus_stack},
         "sim": {"hub_surge": sim_trade, "city_pulse": sim_trade, "hub_hour": sim_trade, "london_vix": sim_trade}}


def decide(tid: str, m: Market, store: Store, now: int, ctx: dict):
    """(option, why) or None. Only the series in the trader's PLAYS entry (all series for the Contrarian)."""
    if tid == "contrarian":
        return contrarian(m, store, now, ctx)
    play = PLAYS[tid].get(m.sub)
    return play(m, store, now, ctx) if play else None


def sit_out(tid: str, store: Store, now: int, current: list[Market]) -> str:
    """Why a trader has no bet in the current round, citing the number it looked at."""
    if tid == "contrarian":
        return "Waiting until halfway through the round to fade the favourites"
    if not any(m.sub in PLAYS[tid] for m in current):
        return f"Sitting out: no series this round is informed by its feed ({BY_ID[tid]['watches']})"
    if tid == "sim":
        if store.sim(now) is None:
            return f"Sitting out: {store.sim_why_none(now)}"
        return "Sitting out: no option this round is priced 5+ points below the simulation's probability"
    if tid == "rain":
        last = store.latest("rain_now", now)
        if not last:
            return "Sitting out: no Open-Meteo reading yet"
        return (f"Sitting out: {last[1]['mm']:g} mm of rain at {local_time(last[0])} (Open-Meteo); it only plays "
                f"City Pulse when it rains")
    if tid == "bus":
        last = store.latest("aircraft", now)
        if last:
            return f"Sitting out: {last[1]['total']} planes holding, no change over 10 min to fade"
    if tid == "cams":
        last = store.latest("vix", now)
        if last:
            return f"Sitting out: London VIX {last[1]['vix']:.2f}, no 5-min move to fade"
    return "Sitting out this round: its feed has no fresh reading"
