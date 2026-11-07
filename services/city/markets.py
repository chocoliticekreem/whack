"""City markets: how each kind is built (question, rule, simulated futures) and settled from real readings.

Kinds (`sub`): busiest (versus, TfL Crowding), level (overunder, one station's TfL Crowding %), bus (overunder,
TfL bus arrivals due), cam (overunder, JamCam YOLO people), lines_any and line (yesno, TfL line status), rain
(yesno, Open-Meteo). Over/under lines are struck at the futures' median (Kalshi-style). Settlement reads only
recorded data around settlesAt.
"""
from __future__ import annotations

import math
import random
import statistics
import zlib
from itertools import combinations

import pricing
from data import STALE_S, Store, local_time
from pricing import GOOD_SERVICE, MIN_MS, RAIN_MM

HISTORY_MS = 3 * 3_600_000
WINDOW_BEFORE_MS = 2 * MIN_MS
WAIT_AFTER_MS = 3 * MIN_MS
CAM_WINDOW_MS = MIN_MS
JUMP_SIGMA = 3.0
CLEAN_TICKS = 2
FEED_OF = {"busiest": "crowding", "level": "crowding", "bus": "bus", "cam": "jamcams",
           "lines_any": "line_status", "line": "line_status", "rain": "rain_now"}
FEED_LABEL = {"crowding": "TfL Crowding API", "jamcams": "TfL JamCam + YOLO people count",
              "line_status": "TfL Line Status API", "rain_now": "Open-Meteo precipitation",
              "bus": "TfL Bus Arrivals API"}
TUBE = ("tube",)


def seed_of(market_id: str) -> int:
    return zlib.crc32(market_id.encode())


def settle_stamp(ms: int) -> str:
    return f"settles {local_time(ms, '%H:%M:%S')} Europe/London"


def ceil_minute(ms: int) -> int:
    return -(-ms // MIN_MS) * MIN_MS


class Market:
    def __init__(self, mid: str, sub: str, kind: str, question: str, hotspots: list[str], opened_at: int,
                 settles_at: int, options: list[dict], sim, source: str, rule: str, spec: dict):
        self.id, self.sub, self.kind, self.question = mid, sub, kind, question
        self.hotspots, self.opened_at, self.settles_at = hotspots, opened_at, settles_at
        self.closes_at = settles_at - (90_000 if sub == "cam" else 60_000)
        self.options, self.source, self.rule, self.spec = options, source, rule, spec
        self.feed = FEED_OF[sub]
        ids = [o["id"] for o in options]
        self.futures = pricing.Futures(sim, ids)
        self.book = pricing.Book(ids)
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


# ---------------------------------------------------------------- builders


def crowd_series(store: Store, naptan: str, now: int) -> list[tuple[int, float]]:
    return [(ts, math.log(s[naptan])) for ts, s in store.between("crowding", now - HISTORY_MS, now) if naptan in s]


def cam_series(store: Store, cam: str, now: int) -> list[tuple[int, float]]:
    return [(ts, s[cam]) for ts, s in store.between("jamcams", now - HISTORY_MS, now) if cam in s]


def crowd_station(store: Store, h: dict, snap: dict, now: int) -> tuple[str, str, float, float, float]:
    mu, sd = pricing.fit_log_walk(crowd_series(store, h["naptan"], now), 0.01, 0.002, 0.05)
    return h["id"], h["naptan"], math.log(snap[h["naptan"]]), mu, sd


def build_busiest(mid: str, store: Store, spots: list[dict], now: int, settles_at: int, recent: set) -> Market | None:
    """The 2-3 hotspots whose futures overlap most: every trio and pair is simulated and the least lopsided wins.
    A trio is kept when its favourite is at most 75%; otherwise the best head-to-head."""
    last = store.latest("crowding", now)
    if not last:
        return None
    snap = last[1]
    live = [h for h in spots if h["naptan"] in snap]
    if len(live) < 2:
        return None
    minutes = math.ceil((settles_at - now) / MIN_MS)
    stations = {h["id"]: crowd_station(store, h, snap, now) for h in live}
    best: dict[int, tuple] = {}
    for size in (3, 2):
        for group in combinations(live, size):
            if frozenset(h["id"] for h in group) in recent:
                continue
            sim = pricing.CrowdSim([stations[h["id"]] for h in group], now, minutes, random.Random(seed_of(mid)))
            probs = pricing.Futures(sim, [h["id"] for h in group]).probs()
            fav = max(probs.values()) if min(probs.values()) >= 0.1 else 1.0  # no dead third option
            if size not in best or fav < best[size][0]:
                best[size] = (fav, group, sim)
    if not best:
        return None
    pick = best[3] if 3 in best and best[3][0] <= 0.75 else min(best.values(), key=lambda x: x[0])
    _, group, sim = pick
    hhmm, names = local_time(settles_at), [h["short"] for h in group]
    question = (f"Which station is busiest at {hhmm}?" if len(group) == 3
                else f"Busier at {hhmm}: {names[0]} or {names[1]}?")
    rule = (f"The station with the highest % of its usual busyness in the last TfL Crowding reading at or before "
            f"{local_time(settles_at, '%H:%M:%S')} wins; ties go to the first listed. No reading within 2 min "
            f"before (or 3 min after) → void, stakes refunded.")
    return Market(mid, "busiest", "versus", question, [h["id"] for h in group], now,
                  settles_at, [{"id": h["id"], "label": h["short"], "hotspot": h["id"]} for h in group], sim,
                  f"TfL Crowding API · {', '.join(names)} · {settle_stamp(settles_at)}", rule,
                  {"stations": {h["id"]: h["naptan"] for h in group}, "names": {h["id"]: h["short"] for h in group},
                   "sigmas": {stations[h["id"]][1]: stations[h["id"]][4] for h in group}})


def over_under(spot: dict, line: str) -> list[dict]:
    return [{"id": "over", "label": f"Over {line}", "hotspot": spot["id"]},
            {"id": "under", "label": f"Under {line}", "hotspot": spot["id"]}]


def build_level(mid: str, store: Store, spot: dict, now: int, settles_at: int) -> Market | None:
    """Over/under one hotspot's % of usual busyness, struck at the futures' median."""
    last = store.latest("crowding", now)
    if not last or spot["naptan"] not in last[1]:
        return None
    station = crowd_station(store, spot, last[1], now)
    minutes = math.ceil((settles_at - now) / MIN_MS)
    sim = pricing.LevelSim(station, now, minutes, random.Random(seed_of(mid)))
    sim.line = pricing.choose_line(sim.finals(), 0.5)
    line = f"{sim.line:g}%"
    hhmm, t = local_time(settles_at), local_time(settles_at, "%H:%M:%S")
    rule = (f"{spot['short']}'s % of usual busyness in the last TfL Crowding reading at or before {t}. Over {line} = "
            f"strictly above {line}; exactly {line} → Under. No reading within 2 min before (or 3 min after) → void, "
            f"stakes refunded.")
    return Market(mid, "level", "overunder", f"Over/under {line} of usual busyness at {spot['short']} at {hhmm}?",
                  [spot["id"]], now, settles_at, over_under(spot, line), sim,
                  f"TfL Crowding API · {spot['short']} · {settle_stamp(settles_at)}", rule,
                  {"naptan": spot["naptan"], "line": sim.line, "name": spot["short"],
                   "sigmas": {spot["naptan"]: station[4]}, "stations": {spot["id"]: spot["naptan"]},
                   "names": {spot["id"]: spot["short"]}})


def build_bus(mid: str, store: Store, spot: dict, now: int, settles_at: int) -> Market | None:
    """Over/under buses due within 5 min at a hotspot's stops, struck at the futures' median."""
    last = store.latest("bus", now)
    if not last or now - last[0] > STALE_S["bus"] * 1000 or spot["id"] not in last[1]:
        return None
    series = [(ts, s[spot["id"]]) for ts, s in store.between("bus", now - HISTORY_MS, now) if spot["id"] in s]
    hold, pool = pricing.fit_hold(series)
    minutes = math.ceil((settles_at - now) / MIN_MS)
    sim = pricing.BusSim(spot["id"], last[1][spot["id"]], hold, pool, now, minutes, random.Random(seed_of(mid)))
    sim.line = pricing.choose_line(sim.finals())
    n = int(sim.line)
    hhmm, t = local_time(settles_at), local_time(settles_at, "%H:%M:%S")
    rule = (f"Count of TfL bus arrivals due within 5 min at the {len(spot['busStops'])} {spot['short']} stops in the "
            f"last reading at or before {t}. Over {n} = {n + 1} or more; exactly {n} → Under. No reading within 2 min "
            f"before (or 3 min after) → void, stakes refunded.")
    return Market(mid, "bus", "overunder", f"Over/under {n} buses due at {spot['short']} at {hhmm}?", [spot["id"]],
                  now, settles_at, over_under(spot, str(n)), sim,
                  f"TfL Bus Arrivals API · {spot['short']} stops · {settle_stamp(settles_at)}", rule,
                  {"spot": spot["id"], "line": n, "name": spot["short"], "now": last[1][spot["id"]]})


def build_cam(mid: str, store: Store, spot: dict, now: int, settles_at: int) -> Market | None:
    last = store.latest("jamcams", now)
    if not last or now - last[0] > STALE_S["jamcams"] * 1000:
        return None
    cam = next((c for c in spot["cams"] if c in last[1]), None)
    if cam is None:
        return None
    series = cam_series(store, cam, now)
    p_change, sigma, _ = pricing.fit_counts(series)
    v0 = last[1][cam]
    minutes = math.ceil((settles_at - now) / MIN_MS)
    sim = pricing.CountSim(cam, v0, p_change, sigma, now, minutes, random.Random(seed_of(mid)))
    sim.line = pricing.choose_line(sim.finals())
    line = int(sim.line)
    cam_name = store.cam_names.get(cam, cam)
    hhmm = local_time(settles_at)
    rule = (f"Median YOLO people count on JamCam {cam.removeprefix('JamCams_')} ({cam_name}) over the 60 s up to "
            f"{local_time(settles_at, '%H:%M:%S')}. Over {line} = {line + 1} or more; exactly {line} → Under. "
            f"Camera down → next camera at {spot['short']} → first count up to 3 min later → void, refunded.")
    return Market(mid, "cam", "overunder", f"Over/under {line} people on the {spot['short']} camera at {hhmm}?",
                  [spot["id"]], now, settles_at, over_under(spot, str(line)), sim,
                  f"TfL JamCam {cam.removeprefix('JamCams_')} ({cam_name}) YOLO people · {settle_stamp(settles_at)}",
                  rule, {"cam": cam, "cams": [cam] + [c for c in spot["cams"] if c != cam], "line": line,
                         "now": v0, "camName": cam_name, "sigma": sigma})


def line_history(store: Store, now: int) -> list[tuple[int, dict]]:
    return store.between("line_status", now - HISTORY_MS, now)


def build_lines_any(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    last = store.latest("line_status", now)
    if not last or now - last[0] > STALE_S["line_status"] * 1000:
        return None
    base = {lid: s["sev"] for lid, s in last[1].items() if s["mode"] in TUBE}
    lam_w, lam_r = pricing.fit_line_hazards(line_history(store, now))
    minutes = math.ceil((settles_at - now) / MIN_MS)
    sim = pricing.LineAnySim(base, lam_w, lam_r, now, minutes, random.Random(seed_of(mid)))
    hhmm, opened = local_time(settles_at), local_time(now)
    rule = (f"YES if any of the {len(base)} Tube lines has a worse TfL status in the last reading at or before "
            f"{local_time(settles_at, '%H:%M:%S')} than at {opened} (e.g. Good Service → Minor Delays). Same or "
            f"better everywhere → NO. No reading by 3 min after → NO.")
    return Market(mid, "lines_any", "yesno", f"Will any Tube line get worse by {hhmm}?", [h["id"] for h in spots],
                  now, settles_at, [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}], sim,
                  f"TfL Line Status API · all Tube lines · {settle_stamp(settles_at)}", rule,
                  {"baseline": base, "baselineDesc": {lid: last[1][lid]["desc"] for lid in base},
                   "names": {lid: last[1][lid]["name"] for lid in base}, "openedHHMM": opened})


def build_line(mid: str, store: Store, spots: list[dict], now: int, settles_at: int, recent: set) -> Market | None:
    last = store.latest("line_status", now)
    if not last or now - last[0] > STALE_S["line_status"] * 1000:
        return None
    delayed = [lid for lid, s in last[1].items() if s["mode"] in TUBE and s["sev"] != GOOD_SERVICE
               and any(lid in h["lines"] for h in spots)]
    if not delayed:
        return None
    lid = next((x for x in delayed if x not in recent), delayed[0])
    info = last[1][lid]
    lam_w, lam_r = pricing.fit_line_hazards(line_history(store, now))
    minutes = math.ceil((settles_at - now) / MIN_MS)
    sim = pricing.LineOneSim(lid, lam_r, lam_w, now, minutes, random.Random(seed_of(mid)))
    hhmm = local_time(settles_at)
    rule = (f"YES if the {info['name']} line shows Good Service in the last TfL reading at or before "
            f"{local_time(settles_at, '%H:%M:%S')}; any delay, closure or suspension → NO. No reading by 3 min "
            f"after → NO.")
    return Market(mid, "line", "yesno", f"Will the {info['name']} line be back to Good Service by {hhmm}?",
                  [h["id"] for h in spots if lid in h["lines"]], now, settles_at,
                  [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}], sim,
                  f"TfL Line Status API · {info['name']} line · {settle_stamp(settles_at)}", rule,
                  {"line": lid, "name": info["name"], "desc": info["desc"]})


def build_rain(mid: str, store: Store, spots: list[dict], now: int, settles_at: int) -> Market | None:
    last = store.latest("rain_now", now)
    if not last or now - last[0] > STALE_S["rain_now"] * 1000:
        return None
    snap = last[1]
    fc = dict(snap["fc"])
    first, slot_end = now - now % pricing.SLOT_MS, settles_at
    probs, slot = {}, first
    while slot <= slot_end:
        probs[slot] = pricing.slot_rain_prob(fc.get(slot), snap["mm"], slot == first)
        slot += pricing.SLOT_MS
    minutes = math.ceil((settles_at - now) / MIN_MS)
    sim = pricing.RainSim(probs, now, minutes, random.Random(seed_of(mid)))
    hhmm = local_time(settles_at)
    rule = (f"YES if any Open-Meteo reading for central London (Oxford Circus) shows more than {RAIN_MM} mm in the "
            f"current 15 min between {local_time(now)} and {local_time(settles_at, '%H:%M:%S')}; otherwise (or with "
            f"no readings) NO.")
    return Market(mid, "rain", "yesno", f"Rain in central London before {hhmm}?", [h["id"] for h in spots], now,
                  settles_at, [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}], sim,
                  f"Open-Meteo precipitation · central London (Oxford Circus) · {settle_stamp(settles_at)}", rule,
                  {"nowMm": snap["mm"], "fc": snap["fc"]})


# ---------------------------------------------------------------- settlement


def reading_at(store: Store, source: str, t: int, now: int, before_ms: int = WINDOW_BEFORE_MS):
    """('ok', ts, snap) for the last reading in [T-before, T], else the first in (T, T+3min];
    ('wait', ms) while that could still arrive; ('missing',) after."""
    hit = store.latest(source, t)
    if hit and hit[0] >= t - before_ms:
        return ("ok", *hit)
    late = store.between(source, t + 1, min(now, t + WAIT_AFTER_MS))
    if late:
        return ("ok", *late[0])
    if now < t + WAIT_AFTER_MS:
        return ("wait", t + WAIT_AFTER_MS - now)
    return ("missing",)


def settle_busiest(m: Market, store: Store, now: int):
    got = reading_at(store, "crowding", m.settles_at, now)
    if got[0] != "ok":
        return got
    ts, snap = got[1], got[2]
    vals = {oid: snap.get(nap) for oid, nap in m.spec["stations"].items()}
    if any(v is None for v in vals.values()):
        return ("missing",) if now >= m.settles_at + WAIT_AFTER_MS else ("wait", m.settles_at + WAIT_AFTER_MS - now)
    winner = max(m.option_ids(), key=lambda o: (vals[o], -m.option_ids().index(o)))
    ranked = sorted(vals, key=lambda o: -vals[o])
    text = " · ".join(f"{m.spec['names'][o]} {vals[o] * 100:.0f}% of usual" for o in ranked)
    return ("done", winner, text, {"value": {o: round(v, 4) for o, v in vals.items()},
                                   "source": "TfL Crowding API", "fetchedAt": ts})


def settle_cam(m: Market, store: Store, now: int):
    t = m.settles_at
    if now < t:
        return ("wait", t - now)
    window = store.between("jamcams", t - CAM_WINDOW_MS, t)
    for cam in m.spec["cams"]:
        counts = [(ts, s[cam]) for ts, s in window if cam in s]
        if counts:
            return cam_result(m, store, cam, statistics.median(c for _, c in counts), counts[-1][0])
    late = store.between("jamcams", t + 1, min(now, t + WAIT_AFTER_MS))
    for ts, s in late:
        for cam in m.spec["cams"]:
            if cam in s:
                return cam_result(m, store, cam, s[cam], ts)
    return ("wait", t + WAIT_AFTER_MS - now) if now < t + WAIT_AFTER_MS else ("missing",)


def cam_result(m: Market, store: Store, cam: str, value: float, ts: int):
    winner = "over" if value > m.spec["line"] else "under"
    name = store.cam_names.get(cam, cam)
    fallback = "" if cam == m.spec["cam"] else " (fallback camera)"
    shown = f"{value:g}"
    return ("done", winner, f"{shown} people on {name}{fallback} · line {m.spec['line']}",
            {"value": value, "source": f"JamCam {cam.removeprefix('JamCams_')} ({name})", "fetchedAt": ts})


def settle_lines_any(m: Market, store: Store, now: int):
    got = reading_at(store, "line_status", m.settles_at, now)
    if got[0] == "wait":
        return got
    if got[0] == "missing":
        return ("done", "no", "No line-status reading — resolves No", {"value": None, "source": "TfL Line Status API",
                                                                        "fetchedAt": None})
    ts, snap = got[1], got[2]
    worse = [lid for lid, sev in m.spec["baseline"].items() if lid in snap and snap[lid]["sev"] < sev]
    if worse:
        names, was = m.spec["names"], m.spec["baselineDesc"]
        text = " · ".join(f"{names[lid]}: {was[lid]} → {snap[lid]['desc']}" for lid in worse)
    else:
        text = f"No Tube line worse than at {m.spec['openedHHMM']} ({len(m.spec['baseline'])} checked)"
    return ("done", "yes" if worse else "no", text,
            {"value": {lid: snap[lid]["desc"] for lid in worse}, "source": "TfL Line Status API", "fetchedAt": ts})


def settle_line(m: Market, store: Store, now: int):
    got = reading_at(store, "line_status", m.settles_at, now)
    if got[0] == "wait":
        return got
    if got[0] == "missing" or m.spec["line"] not in got[2]:
        if got[0] != "missing" and now < m.settles_at + WAIT_AFTER_MS:
            return ("wait", m.settles_at + WAIT_AFTER_MS - now)
        return ("done", "no", "No line-status reading — resolves No",
                {"value": None, "source": "TfL Line Status API", "fetchedAt": None})
    ts, s = got[1], got[2][m.spec["line"]]
    return ("done", "yes" if s["sev"] == GOOD_SERVICE else "no", f"{m.spec['name']}: {s['desc']}",
            {"value": s["desc"], "source": "TfL Line Status API", "fetchedAt": ts})


def settle_rain(m: Market, store: Store, now: int):
    if now < m.settles_at:
        return ("wait", m.settles_at - now)
    recs = store.between("rain_now", m.opened_at, m.settles_at)
    wet = [(ts, s["mm"]) for ts, s in recs if s["mm"] > RAIN_MM]
    if wet:
        ts, mm = wet[0]
        return ("done", "yes", f"{mm:g} mm at {local_time(ts)} (Open-Meteo)",
                {"value": mm, "source": "Open-Meteo precipitation", "fetchedAt": ts})
    if not recs:
        return ("done", "no", "No rain reading — resolves No",
                {"value": None, "source": "Open-Meteo precipitation", "fetchedAt": None})
    peak = max(s["mm"] for _, s in recs)
    return ("done", "no", f"Dry: max {peak:g} mm across {len(recs)} readings {local_time(recs[0][0])}–"
                          f"{local_time(recs[-1][0])}", {"value": peak, "source": "Open-Meteo precipitation",
                                                         "fetchedAt": recs[-1][0]})


def settle_level(m: Market, store: Store, now: int):
    got = reading_at(store, "crowding", m.settles_at, now)
    if got[0] != "ok":
        return got
    ts, snap = got[1], got[2]
    v = snap.get(m.spec["naptan"])
    if v is None:
        return ("missing",) if now >= m.settles_at + WAIT_AFTER_MS else ("wait", m.settles_at + WAIT_AFTER_MS - now)
    pct = v * 100
    return ("done", "over" if pct > m.spec["line"] else "under",
            f"{m.spec['name']} {pct:.1f}% of usual · line {m.spec['line']:g}%",
            {"value": round(pct, 2), "source": "TfL Crowding API", "fetchedAt": ts})


def settle_bus(m: Market, store: Store, now: int):
    got = reading_at(store, "bus", m.settles_at, now)
    if got[0] != "ok":
        return got
    ts, snap = got[1], got[2]
    n = snap.get(m.spec["spot"])
    if n is None:
        return ("missing",) if now >= m.settles_at + WAIT_AFTER_MS else ("wait", m.settles_at + WAIT_AFTER_MS - now)
    return ("done", "over" if n > m.spec["line"] else "under",
            f"{n:g} buses due at {m.spec['name']} stops · line {m.spec['line']}",
            {"value": n, "source": "TfL Bus Arrivals API", "fetchedAt": ts})


SETTLERS = {"busiest": settle_busiest, "level": settle_level, "bus": settle_bus, "cam": settle_cam,
            "lines_any": settle_lines_any, "line": settle_line, "rain": settle_rain}


def try_settle(m: Market, store: Store, now: int):
    """('wait', ms) | ('done', winner, reading, settlement) | ('void', reading, settlement)."""
    if now < m.settles_at:
        return ("wait", m.settles_at - now)
    got = SETTLERS[m.sub](m, store, now)
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
    prev, m.prev_obs = m.prev_obs, {"ts": ts, "snap": snap}
    if prev is None:
        return None
    gap = max(1.0, (ts - prev["ts"]) / MIN_MS)
    if m.sub in ("busiest", "level"):
        for oid, nap in m.spec["stations"].items():
            a, b = prev["snap"].get(nap), snap.get(nap)
            sd = m.spec["sigmas"].get(nap)
            if a and b and sd:
                z = (math.log(b) - math.log(a)) / (sd * math.sqrt(gap))
                if abs(z) > JUMP_SIGMA:
                    return f"jump: {m.spec['names'][oid]} {z:+.1f}σ"
    elif m.sub == "cam":
        cam = m.spec["cam"]
        a, b, sd = prev["snap"].get(cam), snap.get(cam), m.spec["sigma"]
        if a is not None and b is not None and sd:
            z = (math.log1p(b) - math.log1p(a)) / sd
            if abs(z) > JUMP_SIGMA:
                return f"jump: {m.spec['camName']} {z:+.1f}σ"
    return None
