"""On-demand local markets: click anywhere in central London and trade the nearest real sensor, this round.

A market needs a real sensor to settle on. A click at (lat, lon) looks for the nearest settleable sensor within
400 m: a TfL Crowding station (live busyness and a typical-day profile) or a JamCam whose YOLO people count is
fresh (a count in the last 10 min). With one, it lists an over/under market on that sensor in the current 5-minute
round, lined at the futures' median. Without one, the click gets the central simulation's forecast for the nearest
street segments and nothing to trade: no settlement source is ever invented.

One market per sensor per round (a second click near the same sensor gets the same market), at most 12 per round,
and at most 4 new ones a minute per player. The markets are ordinary exchange markets in series local_cam and
local_station: same 50 futures, particle filter, LMSR, odds floor, position and house caps, decided-outcome lock,
settlement snapshot, void and refund. AI traders opt in by feed (Camadel on cameras, Tube Sigma and the
Simulation on stations).

Settlement:
- local_cam: the YOLO people count of the first camera image TfL publishes after bets close (images refresh about
  every 5 min, the recorder counts each new one). An image nobody could see before the close, so nobody can count
  it in advance. No new image counted within 8 min of the close → void, stakes refunded.
- local_station: the station's vs usual (live TfL busyness ÷ its typical value for that weekday and 15-min band) in
  the first TfL Crowding reading published after bets close, as the hub markets do.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from email.utils import parsedate_to_datetime

import data
import integrity
import markets as mk
import pricing
import traders
from data import Store, local_time
from markets import Market, Series
from pricing import MIN_MS, N_FUTURES

RADIUS_M = 400
CAM_FRESH_MS = 10 * MIN_MS
CAM_WAIT_MS = 8 * MIN_MS
IMAGE_MS = 5 * MIN_MS
COUNT_LAG_MS = MIN_MS
MAX_PER_ROUND = 12
CREATE_N, CREATE_WINDOW_S = 4, 60.0
MIN_OPEN_MS = 60_000  # room for the 45 s bet delay of a locally observable market
MAX_FAVOURITE = 0.85
LONDON = (51.42, -0.32, 51.61, 0.08)
# Camera futures, as the multiverse cameras: the level moves on the sqrt scale, at most 0.1 per minute
# (engine.ANCHOR_STEP_SQRT, here 2.5 sigma of a 0.04 step), and each frame adds counting noise of at least the
# Poisson 0.5 (engine.POISSON_SD), widened to what this camera's own frame-to-frame changes show today.
WALK_SD = 0.04
NOISE_FLOOR = 0.5
ANCHOR_FRAMES = 3
SEG_NEAR_M = 120
SEG_MAX = 5
SEG_STALE_MS = 3 * MIN_MS
SEG_POLL_S = 20
FORECAST_LABEL = "Forecast only: no live sensor here to settle a bet"
CAM_FEED = "TfL JamCam + YOLO people count"
CROWD_FEED = "TfL Crowding API"

# The 20 TfL Crowding stations (WGS84, TfL StopPoint). A station is settleable while the feed reports it and its
# typical-day profile is known.
STATIONS = {
    "940GZZLUOXC": ("Oxford Circus", 51.515224, -0.141903),
    "940GZZLUBND": ("Bond Street", 51.514304, -0.149723),
    "940GZZLUTCR": ("Tottenham Court Road", 51.516426, -0.130410),
    "940GZZLUPCC": ("Piccadilly Circus", 51.510077, -0.134470),
    "940GZZLULSQ": ("Leicester Square", 51.511386, -0.128426),
    "940GZZLUBNK": ("Bank", 51.513347, -0.089000),
    "940GZZLULVT": ("Liverpool Street", 51.517991, -0.081426),
    "940GZZLUKSX": ("King's Cross St Pancras", 51.530312, -0.123853),
    "940GZZLUWLO": ("Waterloo", 51.503299, -0.114780),
    "940GZZLUVIC": ("Victoria", 51.496359, -0.143102),
    "940GZZLULNB": ("London Bridge", 51.505721, -0.088873),
    "940GZZLUWSM": ("Westminster", 51.501402, -0.125002),
    "940GZZLUGPK": ("Green Park", 51.506947, -0.142787),
    "940GZZLUHBN": ("Holborn", 51.517580, -0.120475),
    "940GZZLUEUS": ("Euston", 51.528055, -0.132182),
    "940GZZLUPAC": ("Paddington", 51.516581, -0.175689),
    "940GZZLUCYF": ("Canary Wharf", 51.503049, -0.018689),
    "940GZZLUSTD": ("Stratford", 51.541806, -0.003458),
    "940GZZLUTWH": ("Tower Hill", 51.509971, -0.076546),
    "940GZZLUMBA": ("Marble Arch", 51.513424, -0.158953),
}


class LocalLimit(PermissionError):
    def __init__(self, msg: str, code: str):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- camera positions and per-image counts

_reduce_jamcams = data.REDUCERS["jamcams"]


def image_ms(s) -> int | None:
    try:
        return int(parsedate_to_datetime(s).timestamp() * 1000) if s else None
    except (TypeError, ValueError):
        return None


def reduce_jamcams(payload, ctx: dict):
    """The store's JamCam reducer, plus what a local market needs and its {cam: people} snapshot drops: each
    camera's position, and each YOLO count paired with the image it counted (the newest image published at or
    before the count). Counts are keyed by their own time, so a count repeated over many polls is kept once."""
    pos, imgs, counts = (ctx.setdefault(k, {}) for k in ("cam_pos", "cam_imgs", "cam_counts"))
    for cam in payload if isinstance(payload, list) else []:
        cid = cam.get("id")
        lat, lon = data.num(cam.get("lat")), data.num(cam.get("lon"))
        if not cid or lat is None or lon is None:
            continue
        pos[cid] = (lat, lon)
        seen = imgs.setdefault(cid, [])
        img = image_ms(cam.get("lastModified"))
        if img is not None and img not in seen:
            seen.append(img)
            seen.sort()
        yolo = cam.get("yolo") or {}
        n, at = data.num(yolo.get("people")), data.num(yolo.get("at"))
        if n is None or at is None or int(at) in counts.setdefault(cid, {}):
            continue
        counted = max((i for i in seen if i <= at), default=None)
        counts[cid][int(at)] = (counted, int(n))
    return _reduce_jamcams(payload, ctx)


data.REDUCERS["jamcams"] = reduce_jamcams


def cam_counts(store: Store, cid: str, start: int, now: int) -> list[tuple[int, int | None, int]]:
    """[(count time, image time, people)] for one camera, counted in [start, now], oldest first."""
    rows = store.ctx.get("cam_counts", {}).get(cid, {})
    return sorted((at, img, n) for at, (img, n) in rows.items() if start <= at <= now)


def cam_name(store: Store, cid: str) -> str:
    return store.cam_names.get(cid, cid)


# ---------------------------------------------------------------- sensors


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    k = math.pi / 180
    a = math.sin((lat2 - lat1) * k / 2) ** 2 + math.cos(lat1 * k) * math.cos(lat2 * k) * \
        math.sin((lon2 - lon1) * k / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(min(1.0, a)))


def sensors(store: Store, now: int) -> list[dict]:
    """Every sensor a local market could settle on right now: {kind, id, name, lat, lon}."""
    out = []
    last = store.latest("crowding", now)
    if last and store.fresh("crowding", now):
        for nap, (name, lat, lon) in STATIONS.items():
            if nap in last[1] and store.typical(nap, now):
                out.append({"kind": "station", "id": nap, "name": name, "lat": lat, "lon": lon})
    for cid, (lat, lon) in store.ctx.get("cam_pos", {}).items():
        recent = cam_counts(store, cid, now - CAM_FRESH_MS, now)
        if recent:
            out.append({"kind": "cam", "id": cid, "name": cam_name(store, cid), "lat": lat, "lon": lon})
    return out


def nearest(store: Store, lat: float, lon: float, now: int, radius: float = RADIUS_M) -> list[dict]:
    """Settleable sensors within `radius` metres, nearest first, each with distanceM."""
    rows = [s | {"distanceM": round(metres(lat, lon, s["lat"], s["lon"]))} for s in sensors(store, now)]
    return sorted((s for s in rows if s["distanceM"] <= radius), key=lambda s: (s["distanceM"], s["id"]))


def market_id(sensor: dict, settles_at: int) -> str:
    key = sensor["id"].removeprefix("JamCams_") if sensor["kind"] == "cam" else sensor["id"][-3:]
    return f"local_{sensor['kind']}-{key}-{local_time(settles_at, '%H%M')}"


# ---------------------------------------------------------------- simulators


class CamSim:
    """Over/under on one camera's next people count: 50 anchored walks on the sqrt scale.

    Each future starts at the mean sqrt count of the last ANCHOR_FRAMES frames (plus that mean's own uncertainty),
    walks WALK_SD per minute (steps capped at 2.5 sigma) to the expected settle frame, then adds one frame of
    counting noise. A new count mid-round rules out the futures whose level is far from it."""

    tol = 1.5

    def __init__(self, cid: str, anchor: float, level_sd: float, noise: float, last: int, t0: int, minutes: int,
                 rng: random.Random):
        self.cid, self.noise, self.t0, self.m = cid, noise, t0, minutes
        self.line = 0.5
        self.paths, self.eps = [], []
        for _ in range(N_FUTURES):
            x = max(0.0, anchor + level_sd * rng.gauss(0.0, 1.0))
            path = [x]
            for _ in range(minutes):
                x = max(0.0, x + WALK_SD * pricing.capped_gauss(rng))
                path.append(x)
            self.paths.append(path)
            self.eps.append(noise * rng.gauss(0.0, 1.0))
        self.prev, self.seen_ts, self.skip = last, None, True

    def count(self, i: int) -> float:
        return max(0.0, self.paths[i][-1] + self.eps[i]) ** 2

    def finals(self) -> list[float]:
        return [self.count(i) for i in range(N_FUTURES)]

    def outcome(self, i: int) -> str:
        return "over" if self.count(i) > self.line else "under"

    def error(self, i: int, ts: int, snap: dict) -> float | None:
        v = snap.get(self.cid)
        if ts != self.seen_ts:
            # A camera poll repeats the last count until a new image is counted; score each count once.
            self.seen_ts, self.skip = ts, v is None or v == self.prev
            if v is not None:
                self.prev = v
        if self.skip:
            return None
        k = max(0, min(self.m, round((ts - self.t0) / MIN_MS)))
        return abs(math.sqrt(max(v, 0.0)) - self.paths[i][k]) / self.noise

    def fan_paths(self) -> list[list[tuple[int, float]]]:
        from fan import pick
        ks = pick(self.m + 1)
        return [[(self.t0 + k * MIN_MS, self.count(i) if k == self.m else self.paths[i][k] ** 2) for k in ks]
                for i in range(N_FUTURES)]


class StationSim(pricing.ScalarSim):
    """Over/under on one station's vs usual: hub_surge's station walk (a capped log walk of live busyness from the
    last reading), read at settle against the typical value for the expected settle band."""

    def __init__(self, x0: float, mu: float, sd: float, t0: int, minutes: int, rng: random.Random, naptan: str,
                 typ_t: float):
        super().__init__(x0, mu, sd, t0, minutes, rng, lambda s: s.get(naptan), 0.0)
        self.typ_t = typ_t

    def vs_finals(self) -> list[float]:
        return [math.exp(p[-1]) / self.typ_t * 100 for p in self.paths]

    def set_line(self, pct: float) -> None:
        self.strike = pct / 100 * self.typ_t

    def fan_paths(self) -> list[list[tuple[int, float]]]:
        from fan import pick
        ks = pick(self.m + 1)
        return [[(self.t0 + k * MIN_MS, math.exp(p[k]) / self.typ_t * 100) for k in ks] for p in self.paths]


# ---------------------------------------------------------------- build


def build_cam(sensor: dict, store: Store, now: int, settles_at: int) -> Market | None:
    cid = sensor["id"]
    hist = cam_counts(store, cid, now - mk.HISTORY_MS, now)
    if not hist or now - hist[-1][0] > CAM_FRESH_MS:
        return None
    roots = [math.sqrt(n) for _, _, n in hist]
    recent = roots[-ANCHOR_FRAMES:]
    diffs = [b - a for (ta, _, _), (tb, _, _), a, b in zip(hist, hist[1:], roots, roots[1:])
             if tb - ta <= 3 * IMAGE_MS]
    spread = statistics.pvariance(diffs) if len(diffs) >= 4 else 2 * NOISE_FLOOR ** 2
    noise = max(NOISE_FLOOR, math.sqrt(max(0.0, (spread - 5 * WALK_SD ** 2) / 2)))
    closes = settles_at - mk.CLOSE_MS
    last_img = hist[-1][1] or hist[-1][0]
    expected = last_img + max(1, math.ceil((closes - last_img) / IMAGE_MS)) * IMAGE_MS + COUNT_LAG_MS
    minutes = max(1, math.ceil((expected - now) / MIN_MS))
    mid = market_id(sensor, settles_at)
    sim = CamSim(cid, statistics.fmean(recent), noise / math.sqrt(len(recent)), noise, hist[-1][2], now, minutes,
                 random.Random(mk.seed_of(mid)))
    sim.line = math.floor(statistics.median(sim.finals())) + 0.5
    line, name, hhmm = sim.line, sensor["name"], local_time(settles_at)
    rule = (f"The YOLO people count on TfL JamCam {name} ({cid}) for the first camera image TfL publishes after "
            f"bets close at {local_time(closes, '%H:%M:%S')} (images refresh about every 5 min). Over = "
            f"{math.ceil(line)} or more people; Under = {math.floor(line)} or fewer. No new image counted by "
            f"{local_time(closes + CAM_WAIT_MS)} → void, stakes refunded.")
    return Market(mid, "local_cam", "overunder", f"People on the {name} camera at {hhmm}: over or under {line:g}?",
                  [], now, settles_at, mk.over_under_options(f"{line:g}"), sim,
                  f"{CAM_FEED} · {name} · {mk.settle_stamp(settles_at)}", rule,
                  {"line": line, "sensor": sensor_ref(sensor), "open": hist[-1][2], "openTs": hist[-1][0],
                   "anchor": round(statistics.fmean(recent) ** 2, 2), "noise": round(noise, 3),
                   "frames": len(hist)})


def build_station(sensor: dict, store: Store, now: int, settles_at: int) -> Market | None:
    nap = sensor["id"]
    last = store.latest("crowding", now)
    if not last or nap not in last[1] or not store.fresh("crowding", now):
        return None
    ts0, snap = last
    typ0 = store.typical(nap, ts0)
    if not typ0:
        return None
    _, _, _, mu, sd = mk.crowd_station(store, {"id": "station", "naptan": nap}, snap, now)
    minutes = mk.crowd_horizon(ts0, settles_at, now)
    t_end = now + minutes * MIN_MS
    typ_t = store.typical(nap, t_end) or typ0
    mid = market_id(sensor, settles_at)
    rng = random.Random(mk.seed_of(mid))
    sim = StationSim(snap[nap], mu, sd, now, minutes, rng, nap, typ_t)
    blend_station(sim, store, nap, now, t_end, mid)
    line = float(round(statistics.median(sim.vs_finals())))
    sim.set_line(line)
    name, hhmm, day = sensor["name"], local_time(settles_at), data.day_name(ts0)
    rule = (f"{name}'s vs usual = its live TfL busyness ÷ its typical {day} for that 15-min band (TfL Crowding "
            f"profile), in {mk.crowd_settle_rule(settles_at)}. Over = above {line:g}% of usual; Under = "
            f"{line:g}% or below.")
    return Market(mid, "local_station", "overunder", f"{name} busyness at {hhmm}: over or under {line:g}% of "
                  f"usual?", [], now, settles_at, mk.over_under_options(f"{line:g}%"), sim,
                  f"{CROWD_FEED} · {name} vs a usual {day} · {mk.settle_stamp(settles_at)}", rule,
                  {"line": line, "sensor": sensor_ref(sensor), "naptan": nap, "name": name, "day": day,
                   "openTs": ts0, "open": round(snap[nap] / typ0 * 100, 2), "stations": {"station": nap},
                   "names": {"station": name}, "sigmas": {nap: sd}})


def blend_station(sim: StationSim, store: Store, nap: str, now: int, t_end: int, mid: str) -> None:
    """The last SIM_WEIGHT of the futures from the central simulation's forecast for this station, when it covers
    it (13 of the 20 stations), as hub_surge blends its hubs."""
    fc, n = store.sim(now), pricing.sim_count()
    if not fc or not n:
        return
    rng = random.Random(mk.seed_of(mid) ^ mk.SIM_SALT)
    draws = pricing.sim_draws(fc, [nap], t_end, rng, n)
    if draws:
        pricing.blend_scalar(sim, [d[nap] * sim.typ_t for d in draws], rng)


def sensor_ref(sensor: dict) -> dict:
    return {k: sensor[k] for k in ("kind", "id", "name", "lat", "lon")}


BUILDERS = {"cam": build_cam, "station": build_station}


# ---------------------------------------------------------------- settle


def settle_cam(m, store: Store, now: int):
    cid = m.spec["sensor"]["id"]
    deadline = m.closes_at + CAM_WAIT_MS
    after = [(img, at, n) for at, img, n in cam_counts(store, cid, m.closes_at + 1, min(now, deadline))
             if img is not None and img > m.closes_at]
    if not after:
        return ("wait", max(1, min(deadline - now, 5_000))) if now < deadline else ("missing",)
    img, at, n = min(after)
    winner = "over" if n > m.spec["line"] else "under"
    name = m.spec["sensor"]["name"]
    return ("done", winner, f"{n} people on {name} in the {local_time(img, '%H:%M:%S')} image vs "
            f"{m.spec['line']:g} → {m.label(winner).split()[0]}",
            {"value": n, "source": CAM_FEED, "sensorId": cid, "sensor": name, "imageAt": img, "measuredAt": img,
             "fetchedAt": at})


def settle_station(m, store: Store, now: int):
    nap = m.spec["sensor"]["id"]
    got = mk.crowd_values(m, store, now)
    if got[0] != "ok":
        return got
    ts, vals, at = got[1], got[2], got[3]["station"]
    typ = store.typical(nap, ts)
    if not typ:
        return ("void", f"No typical-day value for {m.spec['name']} at {local_time(ts)} — void",
                {"value": None, "source": CROWD_FEED, "sensorId": nap, "measuredAt": at, "fetchedAt": ts})
    pct = round(vals["station"] / typ * 100, 4)
    winner = "over" if pct > m.spec["line"] else "under"
    return ("done", winner, f"{m.spec['name']} {pct:.1f}% of a usual {m.spec['day']} vs {m.spec['line']:g}% → "
            f"{m.label(winner).split()[0]}",
            {"value": round(pct, 2), "source": CROWD_FEED, "sensorId": nap, "sensor": m.spec["name"],
             "measuredAt": at, "fetchedAt": ts})


def no_build(*_):
    """Local series are built on demand by a click (BUILDERS), never by the round's lineup."""
    return None


mk.register(Series("local_cam", "jamcams", no_build, settle_cam, enabled=False))
mk.register(Series("local_station", "crowding", no_build, settle_station, enabled=False))


# ---------------------------------------------------------------- futures fan


def series_view(m, store: Store):
    """fan.series_view for a local market: (label, unit, lines, read(ts, snap) -> value | None)."""
    sensor = m.spec["sensor"]
    if m.sub == "local_cam":
        cid = sensor["id"]
        return f"People on {sensor['name']}", "", [m.spec["line"]], lambda ts, s: s.get(cid)
    nap = sensor["id"]

    def vs(ts: int, snap: dict) -> float | None:
        typ = store.typical(nap, ts)
        return snap[nap] / typ * 100 if nap in snap and typ else None
    return f"{sensor['name']} vs usual", "%", [m.spec["line"]], vs


# ---------------------------------------------------------------- AI traders (opt in by feed)


def cams_local(m, store: Store, now: int, ctx: dict):
    """Camadel: back the side of the line the camera's last 30 min of counts sit on."""
    rows = cam_counts(store, m.spec["sensor"]["id"], now - 30 * MIN_MS, now)
    if len(rows) < 3:
        return None
    mean = statistics.fmean(n for _, _, n in rows)
    side = "over" if mean > m.spec["line"] else "under"
    return side, (f"{m.spec['sensor']['name']} averaged {mean:.1f} people over its last {len(rows)} frames; "
                  f"{'above' if side == 'over' else 'below'} a {m.spec['line']:g} line")


def tube_local(m, store: Store, now: int, ctx: dict):
    """Tube Sigma: carry half the last 15 min's change in vs usual forward to the settle reading."""
    nap = m.spec["naptan"]
    hist = [(ts, s[nap] / t * 100) for ts, s in store.between("crowding", now - 16 * MIN_MS, now)
            if nap in s and (t := store.typical(nap, ts))]
    if len(hist) < 2 or hist[-1][1] == hist[0][1]:
        return None
    (t0, a), (t1, b) = hist[0], hist[-1]
    proj = b + 0.5 * (b - a) / max(1.0, (t1 - t0) / MIN_MS) * (m.settles_at - t1) / MIN_MS
    side = "over" if proj > m.spec["line"] else "under"
    return side, (f"{m.spec['name']} went {a:.0f}% → {b:.0f}% of usual between {local_time(t0)} and "
                  f"{local_time(t1)}; that trend puts it near {proj:.0f}% against a {m.spec['line']:g}% line")


def sim_local(m, store: Store, now: int, ctx: dict):
    """Simaissance: the side the central simulation's station forecast rates 5+ points above its price."""
    fc = store.sim(now)
    dist = pricing.sim_dist(fc, m.spec["naptan"], m.settles_at) if fc else None
    if not dist:
        return None
    mu, sd = dist
    over = 1 - 0.5 * (1 + math.erf((m.spec["line"] / 100 - mu) / (sd * math.sqrt(2))))
    probs, prices = {"over": over, "under": 1 - over}, m.prices()
    best = max(prices, key=lambda o: probs[o] - prices[o])
    if probs[best] - prices[best] < traders.SIM_EDGE:
        return None
    return best, (f"Simaissance: {m.spec['name']} {mu * 100:.0f}% of usual at {local_time(m.settles_at)}; "
                  f"{m.label(best)} in {probs[best] * 100:.0f}% of its draws vs the market's {prices[best] * 100:.0f}%")


# ---------------------------------------------------------------- integrity (bet delay, suspend on change, caps)


def cam_moved(m, prev: dict, snap: dict) -> str | None:
    cid = m.spec["sensor"]["id"]
    a, b = prev.get(cid), snap.get(cid)
    if a is None or b is None or abs(math.sqrt(b) - math.sqrt(a)) <= CAM_MOVE_SQRT:
        return None
    return f"{m.spec['sensor']['name']} count {a:g} → {b:g}"


CAM_MOVE_SQRT = 1.5  # 3 frames of the 0.5 counting-noise floor on the sqrt scale
CAM_JUMP = integrity.Trigger("jamcams", f"the camera's people count jumps by more than {CAM_MOVE_SQRT:g} on the "
                                        f"square-root scale between two images", cam_moved)
# One camera or one station is something a person standing there can see before the feed publishes it, so both
# get the locally observable delay and cap (clean_sheet's), and pause on a big move of their own sensor.
integrity.TABLE["local_cam"] = integrity.replace(integrity.LOCAL, suspends_on=(CAM_JUMP,))
integrity.TABLE["local_station"] = integrity.replace(integrity.LOCAL, suspends_on=(integrity.HUBS,), waits_for="crowding")


traders.PLAYS["cams"]["local_cam"] = cams_local
traders.PLAYS["tube"]["local_station"] = tube_local
traders.PLAYS["sim"]["local_station"] = sim_local


# ---------------------------------------------------------------- the simulation's street forecast (forecast only)


def to_local(lat: float, lon: float, origin: dict) -> tuple[float, float]:
    """services/multiverse/central.to_local: metres east (x) and south (z) of the seed's origin."""
    k = 111_320.0 * math.cos(math.radians(origin["lat"]))
    return (lon - origin["lon"]) * k, -(lat - origin["lat"]) * 111_320.0


def seg_dist(p: tuple[float, float], line: list) -> float:
    best = math.inf
    for a, b in zip(line, line[1:]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        t = 0.0 if dx == dz == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) /
                                                       (dx * dx + dz * dz)))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz))
    return best


def get_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None


class StreetForecast:
    """The central simulation's people per street segment at the current round's settle time, polled outside the
    exchange lock (live only). `near(lat, lon, now)` reads the cached copy."""

    def __init__(self, url: str = data.SIM_URL, fetch=get_json):
        self.url, self.fetch = url, fetch
        self.geometry: dict | None = None
        self.last: dict | None = None
        self.lock = threading.Lock()

    def poll(self, target_ms: int, now: int) -> bool:
        if self.geometry is None:
            geo = self.fetch(f"{self.url}/geometry")
            if not (isinstance(geo, dict) and geo.get("origin") and geo.get("segments")):
                return False
            self.geometry = geo
        base = self.fetch(f"{self.url}/segments?horizon=0")
        if not (isinstance(base, dict) and isinstance(base.get("people"), list)):
            return False
        h = max(0, min(60, round((target_ms - base["at"]) / MIN_MS)))
        got = base if h == 0 else self.fetch(f"{self.url}/segments?horizon={h}")
        if not (isinstance(got, dict) and len(got.get("people") or []) == len(self.geometry["segments"])):
            return False
        with self.lock:
            self.last = {"at": got["at"], "horizonMin": h, "people": got["people"], "fetched": now}
        return True

    def run(self, target, clock) -> None:
        while True:
            now = clock()
            self.poll(target(now), now)
            time.sleep(SEG_POLL_S)

    def near(self, lat: float, lon: float, now: int) -> dict | None:
        with self.lock:
            last = self.last
        if not last or not self.geometry or now - last["fetched"] > SEG_STALE_MS:
            return None
        p = to_local(lat, lon, self.geometry["origin"])
        rows = sorted((seg_dist(p, s["line"]), i) for i, s in enumerate(self.geometry["segments"]))
        close = [r for r in rows if r[0] <= SEG_NEAR_M][:SEG_MAX] or [r for r in rows[:3] if r[0] <= RADIUS_M]
        if not close:
            return None
        segs = [{"id": self.geometry["segments"][i]["id"], "people": last["people"][i], "distanceM": round(d)}
                for d, i in close]
        return {"at": last["at"], "horizonMin": last["horizonMin"], "segments": segs,
                "people": round(sum(s["people"] for s in segs), 1)}


# ---------------------------------------------------------------- the exchange's local book


class LocalMarkets:
    def __init__(self, ex, clock=time.monotonic):
        self.ex, self.clock = ex, clock
        self.hits: dict[str, deque] = {}
        self.streets = StreetForecast()

    def round_at(self, now: int) -> int:
        from exchange import round_for
        return round_for(now)

    def in_round(self, at: int) -> list[Market]:
        rnd = self.ex.rounds.get(at, {"markets": []})
        return [self.ex.markets[i] for i in rnd["markets"] if self.ex.markets[i].sub.startswith("local_")]

    def wait_s(self, player: str) -> int:
        """Seconds until `player` may open another local market; 0 when they may now."""
        q, t = self.hits.setdefault(player, deque()), self.clock()
        while q and q[0] <= t - CREATE_WINDOW_S:
            q.popleft()
        return 0 if len(q) < CREATE_N else max(1, math.ceil(q[0] + CREATE_WINDOW_S - t))

    def view(self, m: Market, now: int, lat: float | None = None, lon: float | None = None) -> dict:
        s = m.spec["sensor"]
        local = {"sensor": s} | ({"distanceM": round(metres(lat, lon, s["lat"], s["lon"]))} if lat is not None else {})
        return self.ex.market_json(m, now) | {"local": local}

    def create(self, body: dict, player: str, now: int | None = None) -> dict:
        """POST /local {lat, lon}: {market} for the nearest settleable sensor within 400 m (the existing one if this
        round already has it), else {forecastOnly}."""
        now = self.ex.clock.now() if now is None else now
        lat, lon = point(body)
        at = self.round_at(now)
        near = nearest(self.ex.store, lat, lon, now)
        if near and (m := self.ex.markets.get(market_id(near[0], at))) is not None:
            return {"market": self.view(m, now, lat, lon)}
        if not near:
            far = nearest(self.ex.store, lat, lon, now, math.inf)
            why = (f"No TfL Crowding station or counted JamCam within {RADIUS_M} m" +
                   (f"; the nearest is {far[0]['name']}, {far[0]['distanceM']:,} m away" if far else ""))
            out = self.forecast_only(lat, lon, now, why)
            if far:
                out["nearest"] = {k: far[0][k] for k in ("name", "kind", "lat", "lon", "distanceM")}
            return {"forecastOnly": out}
        left = at - mk.CLOSE_MS - now
        if left < MIN_OPEN_MS:
            raise LocalLimit(f"This round closes in {max(0, left) // 1000} s; local markets open again with the next "
                             f"round", "round_closing")
        if len(self.in_round(at)) >= MAX_PER_ROUND:
            raise LocalLimit(f"{MAX_PER_ROUND} local markets are already open this round; trade one of their pins",
                             "local_cap")
        if wait := self.wait_s(player):
            raise LocalLimit(f"At most {CREATE_N} new local markets a minute; you can open another in {wait} s",
                             "rate_limited")
        reasons = []
        for s in near:
            if (m := self.ex.markets.get(market_id(s, at))) is not None:
                return {"market": self.view(m, now, lat, lon)}
            m = BUILDERS[s["kind"]](s, self.ex.store, now, at)
            if m is None:
                reasons.append(f"{s['name']} has no fresh reading")
                continue
            fav = max(m.open_model.values())
            if fav > MAX_FAVOURITE:
                reasons.append(f"{s['name']} is too predictable to price ({fav:.0%} on one side)")
                continue
            self.ex.add_market(m, now)
            self.hits[player].append(self.clock())
            return {"market": self.view(m, now, lat, lon)}
        return {"forecastOnly": self.forecast_only(lat, lon, now, "; ".join(reasons))}

    def forecast_only(self, lat: float, lon: float, now: int, reason: str) -> dict:
        fc = self.streets.near(lat, lon, now)
        note = None if fc else ("The central simulation covers the streets inside its area only" if
                                self.streets.last else "The central simulation's street forecast is not available")
        return {"lat": lat, "lon": lon, "label": FORECAST_LABEL, "reason": reason, "forecast": fc,
                "note": note, "tradeable": False}

    def list_json(self, round_q: str | None = None, now: int | None = None) -> dict:
        """GET /local?round=: the local markets of a round (settlesAt ms or HH:MM; default the current one)."""
        now = self.ex.clock.now() if now is None else now
        at = self.round_at(now)
        if round_q:
            at = int(round_q) if round_q.isdigit() else next(
                (r for r in sorted(self.ex.rounds, reverse=True) if local_time(r) == round_q), None)
            if at is None:
                raise LookupError(f"no round {round_q}")
        found = sensors(self.ex.store, now)
        return {"round": local_time(at), "settlesAt": at, "cap": MAX_PER_ROUND, "radiusM": RADIUS_M,
                "markets": [self.view(m, now) for m in self.in_round(at)],
                "sensors": {"stations": sum(s["kind"] == "station" for s in found),
                            "cams": sum(s["kind"] == "cam" for s in found)}}


def point(body: dict) -> tuple[float, float]:
    lat, lon = data.num(body.get("lat")), data.num(body.get("lon"))
    if lat is None or lon is None:
        raise ValueError("lat and lon must be numbers")
    if not (LONDON[0] <= lat <= LONDON[2] and LONDON[1] <= lon <= LONDON[3]):
        raise ValueError("that point is outside London")
    return lat, lon
