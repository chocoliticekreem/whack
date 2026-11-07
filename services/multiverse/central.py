"""The central-London world model: persona groups walking the merged street graph, demand from station flows.

A drop-in for `worlds` (the Predict model) in `engine.Multiverse(model=central)`: the same sample / jitter /
shift / describe / run_world contract, plus `run_many` (worlds in parallel) and `reseed`.

Demand, one general rule for every station in the seed (no hand-placed crowds):
- busyness: TfL's typical day for that station, weekday and quarter-hour, times the live/typical ratio at the
  seed, times the world's `demandTrend` drift over the hour, times any placed intervention;
- flows: exits per minute = BASE_EXITS_PER_MIN x `flowScale` x busyness, and as many entries. Exits walk to
  destinations drawn by gravity (weight x exp(-distance / GRAVITY_M)) over stations and POI clusters; entries
  walk from such an origin to the station. Bus arrivals put `alightPerBus` riders on the street at the stop.
- the swarm skeleton (who goes where, when, and a uniform per group) is drawn from `crn_seed` once and shared by
  every world; a world's knobs only scale sizes, speeds, lingering and thresholds (common random numbers).

Walk: shortest paths (Dijkstra trees toward each destination, cached per closed-edge set), slowed by pavement
load. A group that arrives at a station enters it; elsewhere it lingers. Output per minute: people per street
edge, per camera frame and per zone, and each station's busyness; flows crossing hub catchments are tallied.
"""
from __future__ import annotations

import array
import atexit
import copy
import heapq
import math
import os
import random
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
FORECAST_MIN = 60
WARMUP_MIN = 20
SLOT_MIN = 5
K_DEST = 6
# Someone who has lingered at a destination walks on to another one (gravity) up to this many times.
MAX_HOPS = 2
# debt: absolute station flow is not published by TfL Crowding (only % of a station baseline), so one constant
# sets the scale and the `flowScale` knob lets the cameras price it. ceiling: every station shares one baseline
# size. upgrade: when station entry/exit counts (TfL NUMBAT) are recorded, scale each station by its own.
BASE_EXITS_PER_MIN = 120.0
GRAVITY_M = 600.0
MIN_TRIP_M = 150.0
SPEED_MPS = {"commuter": 1.4, "tourist": 1.0}
LINGER_MIN = {"commuter": 6.0, "tourist": 20.0}
CAPACITY_PER_100M = {"major": 110.0, "minor": 70.0}
MIN_SEG_M = 50.0
CAM_VIEW_M = 80.0
SEG_OFFSETS = (0, 5, 15, 60)
RATIO_CLAMP = (0.2, 5.0)
INCIDENT_RADIUS_M = 120.0
INCIDENT_CAPACITY = 0.4
SURGE_DECAY_M = 400.0
SURGE_REACH_M = 1200.0
STATION_NEAR_M = 200.0
TOURIST_SHARE = {"weekend": 0.5, "morning_rush": 0.15, "evening_rush": 0.2, "lunch": 0.35, "daytime": 0.4,
                 "night": 0.25}

RANGES: dict[str, tuple[float, float]] = {
    "flowScale": (0.4, 2.5),
    "demandTrend": (-0.4, 0.4),
    "touristFactor": (0.5, 1.8),
    "capacityScale": (0.6, 1.4),
    "alightPerBus": (3.0, 15.0),
    "busDelayMin": (0.0, 8.0),
    "walkPace": (0.8, 1.2),
    "lingerScale": (0.5, 2.0),
}
RAIN_PROB = 0.25
INCIDENT_PROB = 0.1
JITTER_SD = 0.08
FLIP_PROB = 0.1
LABELS = {
    "flowScale": lambda v: f"station flows x{v:.2f}",
    "demandTrend": lambda v: f"busyness {v * 100:+.0f}%/h vs usual",
    "touristFactor": lambda v: f"tourists x{v:.2f}",
    "capacityScale": lambda v: f"pavement capacity x{v:.2f}",
    "alightPerBus": lambda v: f"{v:.0f} riders per bus",
    "busDelayMin": lambda v: f"buses {v:.1f} min late",
    "walkPace": lambda v: f"walking pace x{v:.2f}",
    "lingerScale": lambda v: f"lingering x{v:.2f}",
}

KINDS = ("closure", "capacity", "demand_surge", "demand_drop", "weather")
DEFAULT_MAGNITUDE = {"closure": 0.0, "capacity": 0.5, "demand_surge": 0.5, "demand_drop": 0.5, "weather": 2.0}
# Rain at this rate (mm/h) or more sends every tourist to shelter; lighter rain sends a proportional share.
RAIN_FULL_MM_H = 2.0
SHAPES = ("ramp_up", "linear_decay")
CARRY = ("source", "rule", "reason", "planned", "shape")
DEFAULT_RADIUS_M = {"closure": 60.0, "capacity": 100.0, "demand_surge": 300.0, "demand_drop": 300.0, "weather": 0.0}
STATION_CLOSURE_M = 80.0
ABS_MS = 10 ** 11


# ---------------------------------------------------------------- assumptions (the engine's model contract)


def sample_assumptions(rng: random.Random, horizon: int, zone_ids: list[str]) -> dict[str, Any]:
    a: dict[str, Any] = {k: round(rng.uniform(lo, hi), 3) for k, (lo, hi) in RANGES.items()}
    a["alightPerBus"] = round(a["alightPerBus"])
    a["rainAtMin"] = round(rng.uniform(0, horizon * 0.8), 1) if rng.random() < RAIN_PROB else None
    a["incident"] = ({"zoneId": rng.choice(zone_ids), "atMin": round(rng.uniform(0, horizon * 0.6), 1)}
                     if zone_ids and rng.random() < INCIDENT_PROB else None)
    a["events"] = []
    a["interventions"] = []
    return a


def jitter_assumptions(parent: dict[str, Any], rng: random.Random, horizon: int,
                       zone_ids: list[str], sds: dict[str, float] | None = None) -> dict[str, Any]:
    child = copy.deepcopy(parent)
    sds = sds or {}
    for k, (lo, hi) in RANGES.items():
        child[k] = round(min(hi, max(lo, parent[k] + rng.gauss(0, sds.get(k, JITTER_SD * (hi - lo))))), 3)
    child["alightPerBus"] = round(child["alightPerBus"])
    if rng.random() < FLIP_PROB:
        child["rainAtMin"] = None if parent["rainAtMin"] is not None else round(rng.uniform(0, horizon * 0.8), 1)
    elif child["rainAtMin"] is not None:
        child["rainAtMin"] = round(min(horizon, max(0.0, child["rainAtMin"] + rng.gauss(0, sds.get("rainAtMin", 2.0)))), 1)
    if rng.random() < FLIP_PROB / 2 and zone_ids:
        child["incident"] = None if parent["incident"] else {"zoneId": rng.choice(zone_ids),
                                                             "atMin": round(rng.uniform(0, horizon * 0.6), 1)}
    return child


def shift_assumptions(a: dict[str, Any], minutes: float) -> dict[str, Any]:
    """Timed knobs move earlier for a new epoch; placed interventions carry absolute times and stay put."""
    out = copy.deepcopy(a)
    if out.get("rainAtMin") is not None:
        out["rainAtMin"] = round(max(0.0, out["rainAtMin"] - minutes), 1)
    if out.get("incident"):
        out["incident"]["atMin"] = round(max(0.0, out["incident"]["atMin"] - minutes), 1)
    return out


def describe(a: dict[str, Any], zone_names: dict[str, str]) -> list[str]:
    out = [LABELS[k](a[k]) for k in RANGES]
    if a.get("rainAtMin") is not None:
        out.append(f"rain from +{a['rainAtMin']:.0f} min")
    if a.get("incident"):
        out.append(f"incident at {zone_names.get(a['incident']['zoneId'], a['incident']['zoneId'])} "
                   f"+{a['incident']['atMin']:.0f} min")
    for iv in a.get("interventions", []):
        out.append(iv.get("note") or iv["kind"])
    return out


# ---------------------------------------------------------------- interventions


def _time_ms(v: Any, now_ms: int, default: int | None) -> int | None:
    if v is None:
        return default
    f = float(v)
    return int(f) if abs(f) >= ABS_MS else int(now_ms + f * 60_000)


def to_local(lat: float, lon: float, origin: dict) -> tuple[float, float]:
    k = 111_320.0 * math.cos(math.radians(origin["lat"]))
    return ((lon - origin["lon"]) * k, -(lat - origin["lat"]) * 111_320.0)


def seg_dist(p: tuple[float, float], line: list) -> float:
    best = math.inf
    for a, b in zip(line, line[1:]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dz * dz
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz))
    return best


def normalise_intervention(raw: dict, seed: dict, now_ms: int, iv_id: str | None = None,
                           strict: bool = True) -> dict:
    """{kind, where, start?, end?, magnitude?, note?, shape?} -> the resolved intervention every world applies.

    where: {"segments": [ids]} | {"lat", "lon", "radiusM" (or radius_m)?} | {"naptan"} (also a bare list of
    segment ids or a NaPTAN string); weather may omit it (the whole area). start / end: minutes from now, or
    epoch ms; end None = open-ended. magnitude: closure / capacity = remaining capacity (closure 0; at a NaPTAN
    it scales the station's busyness, on streets their pavement capacity); demand_surge / demand_drop = relative
    change (0.5 = +/-50 %); weather = rain in mm/h. shape: ramp_up or linear_decay scales the effect over
    [start, end]. The event layer's fields (source, rule, reason, planned) are carried through. strict=False
    (derived events): a NaPTAN or place outside this seed resolves to nothing, flagged `resolved: false`."""
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    where = raw.get("where")
    if isinstance(where, list):
        where = {"segments": where}
    elif isinstance(where, str):
        where = {"naptan": where}
    if where is None and kind != "weather":
        raise ValueError("where is required: {segments: [ids]} | {lat, lon, radiusM} | {naptan}")
    where = dict(where or {})
    mag = float(raw["magnitude"]) if raw.get("magnitude") is not None else DEFAULT_MAGNITUDE[kind]
    if not math.isfinite(mag) or mag < 0:
        raise ValueError("magnitude must be a non-negative number")
    start = _time_ms(raw.get("start"), now_ms, now_ms)
    end = _time_ms(raw.get("end"), now_ms, None)
    if end is not None and end <= start:
        raise ValueError("end must be after start")
    segs_by_id = {s["id"]: s for s in seed["segments"]}
    stations = {s["naptan"]: s for s in seed.get("stations", [])}
    seg_ids: list[str] = []
    st_w: dict[str, float] = {}
    center: list[float] | None = None
    closes_station = False
    if "segments" in where:
        bad = [s for s in where["segments"] if s not in segs_by_id]
        if bad or not where["segments"]:
            raise ValueError(f"unknown segment ids: {bad[:5]}" if bad else "segments is empty")
        seg_ids = list(dict.fromkeys(where["segments"]))
        pts = [p for sid in seg_ids for p in segs_by_id[sid]["line"]]
        center = [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]
        st_w = {n: 1.0 for n, s in stations.items()
                if min(seg_dist((s["x"], s["z"]), segs_by_id[sid]["line"]) for sid in seg_ids) <= STATION_NEAR_M}
    elif "naptan" in where:
        st = stations.get(where["naptan"])
        if st is None and strict:
            raise ValueError(f"unknown station {where['naptan']}")
        if st is not None:
            st_w = {st["naptan"]: 1.0}
            closes_station = kind == "closure"
    elif "lat" in where and "lon" in where:
        x, z = to_local(float(where["lat"]), float(where["lon"]), seed["origin"])
        radius = float(where.pop("radius_m", None) or where.get("radiusM") or DEFAULT_RADIUS_M[kind])
        where["radiusM"] = radius
        center = [round(x, 1), round(z, 1)]
        seg_ids = [s["id"] for s in seed["segments"] if kind != "weather" and seg_dist((x, z), s["line"]) <= radius]
        for n, s in stations.items() if kind != "weather" else ():
            d = math.hypot(s["x"] - x, s["z"] - z)
            if d <= radius + SURGE_REACH_M:
                st_w[n] = round(math.exp(-max(0.0, d - radius) / SURGE_DECAY_M), 3)
        if kind in ("closure", "capacity") and not seg_ids and strict:
            raise ValueError(f"no street within {radius:.0f} m of ({where['lat']}, {where['lon']})")
    elif kind != "weather":
        raise ValueError("where must carry segments, naptan, or lat + lon")
    if raw.get("shape") is not None and raw["shape"] not in SHAPES:
        raise ValueError(f"shape must be one of {', '.join(SHAPES)}")
    note = raw.get("note") or f"{kind.replace('_', ' ')}" + (f" at {where['naptan']}" if "naptan" in where else "")
    return {"id": iv_id or raw.get("id") or f"iv-{now_ms}", "kind": kind, "where": where, "startMs": start,
            "endMs": end, "magnitude": round(mag, 4), "note": str(note)[:120], "segments": seg_ids,
            "stations": st_w, "center": center, "closesStation": closes_station,
            "redirect": not raw.get("source"), "resolved": bool(seg_ids or st_w or kind == "weather"),
            **{k: raw[k] for k in CARRY if raw.get(k) is not None}}


# ---------------------------------------------------------------- static graph (cached per process)


class Static:
    def __init__(self, seed: dict) -> None:
        self.key = seed["graphKey"]
        self.nodes = [tuple(p) for p in seed["nodes"]]
        self.segs = seed["segments"]
        self.E = len(self.segs)
        self.seg_index = {s["id"]: i for i, s in enumerate(self.segs)}
        self.length = [max(float(s["lengthM"]), 1.0) for s in self.segs]
        self.cap = [CAPACITY_PER_100M.get(s["kind"], 70.0) for s in self.segs]
        self.adj: list[list[tuple[int, int]]] = [[] for _ in self.nodes]
        for i, s in enumerate(self.segs):
            self.adj[s["u"]].append((s["v"], i))
            self.adj[s["v"]].append((s["u"], i))
        zone_ids = [z["id"] for z in seed["zones"]]
        self.zone_ids = zone_ids
        zidx = {z: i for i, z in enumerate(zone_ids)}
        self.zone_of = [zidx.get(s.get("zoneId"), 0) for s in self.segs]
        self.hub_of = [s.get("hub") for s in self.segs]
        self.zone_center = {z["id"]: (z["x"], z["z"]) for z in seed["zones"]}
        self.anchors = seed["anchors"]
        self.anchor_node = [self.nearest_node((a["x"], a["z"])) for a in self.anchors]
        self.station_naptans = [s["naptan"] for s in seed["stations"]]
        self.station_anchor = {a["naptan"]: i for i, a in enumerate(self.anchors) if a.get("naptan")}
        self.station_node = {n: self.anchor_node[i] for n, i in self.station_anchor.items()}
        self.station_xy = {s["naptan"]: (s["x"], s["z"]) for s in seed["stations"]}
        self.cams = [(c["id"], self.seg_index[c["segment"]]) for c in seed.get("cams", [])
                     if c.get("segment") in self.seg_index]
        self.gravity = {}
        for i, a in enumerate(self.anchors):
            w = []
            for j, b in enumerate(self.anchors):
                d = math.hypot(a["x"] - b["x"], a["z"] - b["z"])
                w.append(0.0 if i == j or d < MIN_TRIP_M else b["weight"] * math.exp(-d / GRAVITY_M))
            self.gravity[i] = cumulative(w)
        self.node_edges = [[e for _, e in self.adj[n]] for n in range(len(self.nodes))]
        self.trees: dict[tuple, list] = {}
        self.paths: dict[tuple, tuple] = {}
        self.skeletons: dict[tuple, list] = {}

    def nearest_node(self, p: tuple[float, float]) -> int:
        return min(range(len(self.nodes)), key=lambda n: (self.nodes[n][0] - p[0]) ** 2 + (self.nodes[n][1] - p[1]) ** 2)

    def nearest_station_anchor(self, node: int, exclude: set[str] = frozenset()) -> int | None:
        p = self.nodes[node]
        best = min(((math.hypot(self.anchors[i]["x"] - p[0], self.anchors[i]["z"] - p[1]), i)
                    for n, i in self.station_anchor.items() if n not in exclude), default=None)
        return best[1] if best else None

    def tree(self, dest: int, closed: frozenset) -> list:
        """Dijkstra from `dest`: for every node, (next node, edge) one step closer to dest (None = cut off)."""
        key = (dest, closed)
        t = self.trees.get(key)
        if t is not None:
            return t
        best = [math.inf] * len(self.nodes)
        nxt: list = [None] * len(self.nodes)
        best[dest] = 0.0
        heap = [(0.0, dest)]
        while heap:
            d, n = heapq.heappop(heap)
            if d > best[n]:
                continue
            for m, e in self.adj[n]:
                if e in closed:
                    continue
                c = d + self.length[e]
                if c < best[m]:
                    best[m] = c
                    nxt[m] = (n, e)
                    heapq.heappush(heap, (c, m))
        self.trees[key] = nxt
        return nxt

    def path(self, src: int, dest: int, closed: frozenset) -> tuple[list[int], list[float]] | None:
        key = (src, dest, closed)
        hit = self.paths.get(key)
        if hit is not None or key in self.paths:
            return hit
        t = self.tree(dest, closed)
        edges, cum, n, total = [], [], src, 0.0
        while n != dest:
            step = t[n]
            if step is None:
                self.paths[key] = None
                return None
            n, e = step
            edges.append(e)
            total += self.length[e]
            cum.append(total)
        out = (edges, cum)
        self.paths[key] = out
        return out


def cumulative(w: list[float]) -> list[float]:
    out, acc = [], 0.0
    for v in w:
        acc += v
        out.append(acc)
    return out


def draw(cum: list[float], u: float) -> int:
    target = u * cum[-1]
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


_STATIC: dict[str, Static] = {}


def static_of(seed: dict) -> Static:
    st = _STATIC.get(seed["graphKey"])
    if st is None:
        _STATIC.clear()
        st = _STATIC[seed["graphKey"]] = Static(seed)
    return st


def skeleton(st: Static, crn_seed: int, horizon: int) -> list[tuple]:
    """The shared swarm: (station, slot start, 'exit'|'entry', other anchor, depart offset, u) per group,
    drawn from crn_seed alone so every world walks the same people, only sized differently."""
    key = (crn_seed, horizon)
    sk = st.skeletons.get(key)
    if sk is not None:
        return sk
    rng = random.Random(crn_seed)
    sk = []
    for nap in st.station_naptans:
        ai = st.station_anchor[nap]
        if st.gravity[ai][-1] <= 0:
            continue
        for t0 in range(-WARMUP_MIN, horizon + 1, SLOT_MIN):
            for _ in range(K_DEST):
                sk.append((nap, t0, "exit", draw(st.gravity[ai], rng.random()), rng.random() * SLOT_MIN, rng.random()))
                sk.append((nap, t0, "entry", draw(st.gravity[ai], rng.random()), rng.random() * SLOT_MIN, rng.random()))
    st.skeletons[key] = sk
    return sk


# ---------------------------------------------------------------- dynamic seed parts (time, live, buses)


def typical_at(profile: dict, at_ms: int) -> float | None:
    dt = datetime.fromtimestamp(at_ms / 1000, LONDON)
    vals = (profile or {}).get(dt.strftime("%a").upper())
    return vals[(dt.hour * 60 + dt.minute) // 15] if vals else None


def dynamic(seed: dict, horizon: int) -> dict:
    """What changes between epochs, small enough to ship to every worker per run."""
    at = seed["at"]
    stations = {}
    for s in seed.get("stations", []):
        typ = [typical_at(s.get("profile"), at + m * 60_000) for m in range(-WARMUP_MIN, horizon + 1)]
        known = [v for v in typ if v is not None]
        fill = sum(known) / len(known) if known else (s.get("live") or 0.3)
        typ = [fill if v is None else max(v, 0.01) for v in typ]
        ratio = 1.0
        if s.get("live") is not None:
            t_live = typical_at(s.get("profile"), int(s.get("liveAt") or at))
            ratio = s["live"] / max(t_live if t_live is not None else fill, 0.01)
        stations[s["naptan"]] = {"typical": typ, "ratio": min(RATIO_CLAMP[1], max(RATIO_CLAMP[0], ratio))}
    return {"at": at, "time": seed.get("time") or {}, "stations": stations, "buses": seed.get("buses", []),
            "interventions": seed.get("interventions", []), "horizon": horizon}


def static_part(seed: dict) -> dict:
    keys = ("graphKey", "nodes", "segments", "zones", "anchors", "stations", "cams", "origin")
    out = {k: seed.get(k) for k in keys}
    out["stations"] = [{k: s[k] for k in ("naptan", "name", "x", "z")} for s in seed.get("stations", [])]
    return out


# ---------------------------------------------------------------- one world


def run_world(seed: dict, a: dict[str, Any], horizon: int, crn_seed: int, dyn: dict | None = None) -> dict[str, Any]:
    st = static_of(seed)
    dyn = dyn or dynamic(seed, horizon)
    return Run(st, dyn, a, horizon, crn_seed).go()


class Run:
    def __init__(self, st: Static, dyn: dict, a: dict, horizon: int, crn_seed: int) -> None:
        self.st, self.dyn, self.a, self.H, self.crn = st, dyn, a, horizon, crn_seed
        self.at = dyn["at"]
        self.events: list[str] = []
        self.ivs = [iv for iv in list(dyn.get("interventions") or []) + list(a.get("interventions") or [])
                    if iv.get("endMs") is None or iv["endMs"] > self.at - WARMUP_MIN * 60_000]
        if a.get("incident") and a["incident"]["zoneId"] in st.zone_center:
            c = st.zone_center[a["incident"]["zoneId"]]
            segs = [s["id"] for s in st.segs if seg_dist(c, s["line"]) <= INCIDENT_RADIUS_M]
            self.ivs.append({"kind": "capacity", "segments": segs, "stations": {}, "center": None,
                             "startMs": self.at + int(a["incident"]["atMin"] * 60_000), "endMs": None,
                             "magnitude": INCIDENT_CAPACITY, "note": "assumed incident", "closesStation": False})
        if a.get("rainAtMin") is not None:
            self.ivs.append({"kind": "weather", "segments": [], "stations": {}, "center": None,
                             "startMs": self.at + int(a["rainAtMin"] * 60_000), "endMs": None, "magnitude": RAIN_FULL_MM_H,
                             "note": "rain", "closesStation": False})
        daypart = "weekend" if dyn["time"].get("isWeekend") else dyn["time"].get("daypart", "daytime")
        self.tourist_share = min(0.95, TOURIST_SHARE.get(daypart, 0.35) * float(a["touristFactor"]))
        self.minutes = range(-WARMUP_MIN, horizon + 1)

    def minute_of(self, ms: int | None) -> float:
        return math.inf if ms is None else (ms - self.at) / 60_000

    def active(self, iv: dict, m: float) -> bool:
        return self.minute_of(iv["startMs"]) <= m < self.minute_of(iv["endMs"])

    def strength(self, iv: dict, m: float) -> float:
        """0 outside [start, end); inside, 1, or the share of the way through for ramp_up / linear_decay."""
        if not self.active(iv, m):
            return 0.0
        shape = iv.get("shape")
        if shape not in SHAPES or iv.get("endMs") is None:
            return 1.0
        s, e = self.minute_of(iv["startMs"]), self.minute_of(iv["endMs"])
        frac = (m - s) / max(e - s, 1e-9)
        return frac if shape == "ramp_up" else 1.0 - frac

    def station_series(self) -> tuple[dict[str, list[float]], dict[str, list[float]], dict[str, str]]:
        """Busyness per station and minute (with interventions) and the share of it that flows as usual
        (a surge aimed at a place, not a station, walks to that place instead)."""
        trend = float(self.a.get("demandTrend", 0.0))
        pct: dict[str, list[float]] = {}
        flow: dict[str, list[float]] = {}
        redirect: dict[str, str] = {}
        closed_st = {n for iv in self.ivs if iv["kind"] == "closure" and iv.get("closesStation") and iv.get("redirect")
                     for n in iv["stations"]}
        for nap, d in self.dyn["stations"].items():
            row, frow = [], []
            for i, m in enumerate(self.minutes):
                base = d["typical"][i] * d["ratio"] * max(0.0, 1.0 + trend * max(m, 0) / 60.0)
                mult, fmult = 1.0, 1.0
                for iv in self.ivs:
                    w = iv["stations"].get(nap)
                    k = self.strength(iv, m) if w else 0.0
                    if not k:
                        continue
                    if iv["kind"] == "demand_surge":
                        mult *= 1.0 + iv["magnitude"] * w * k
                        fmult *= 1.0 if iv.get("center") else 1.0 + iv["magnitude"] * w * k
                    elif iv["kind"] == "demand_drop":
                        mult *= max(0.0, 1.0 - iv["magnitude"] * w * k)
                        fmult *= max(0.0, 1.0 - iv["magnitude"] * w * k)
                    elif iv["kind"] in ("closure", "capacity") and iv.get("where", {}).get("naptan"):
                        keep = 1.0 - (1.0 - min(1.0, iv["magnitude"])) * k
                        mult *= keep
                        fmult *= keep
                row.append(base * mult)
                frow.append(base * fmult)
            pct[nap], flow[nap] = row, frow
        # debt: a closed station's demand moves whole to the nearest open one, as if both had the same baseline.
        # ceiling: TfL baselines differ per station. upgrade: with per-station flow sizes, convert by baseline.
        for nap in closed_st:
            if nap not in self.st.station_anchor:
                continue
            alt = self.st.nearest_station_anchor(self.st.station_node[nap], closed_st)
            if alt is None:
                continue
            alt_nap = self.st.anchors[alt]["naptan"]
            redirect[nap] = alt_nap
            d = self.dyn["stations"][nap]
            for i, m in enumerate(self.minutes):
                if any(iv["kind"] == "closure" and nap in iv["stations"] and self.active(iv, m) for iv in self.ivs):
                    moved = d["typical"][i] * d["ratio"]
                    pct[alt_nap][i] += moved
            self.events.append(f"{nap} closed: its demand moves to {self.st.anchors[alt]['name']}")
        return pct, flow, redirect

    def groups(self, flow: dict[str, list[float]], redirect: dict[str, str]) -> list[list]:
        """[depart, origin node, dest anchor (or -1 - node), size, speed m/min, linger min, u, archetype, spread min]:
        a group's people leave over `spread` minutes, so on the street it is a stream that long, not a blob."""
        st, a = self.st, self.a
        scale = BASE_EXITS_PER_MIN * float(a["flowScale"]) * SLOT_MIN / K_DEST
        pace = float(a["walkPace"]) * 60.0
        linger = float(a["lingerScale"])
        out = []
        for nap, t0, kind, other, off, u in skeleton(st, self.crn, self.H):
            i = min(len(flow[nap]) - 1, t0 + SLOT_MIN // 2 + WARMUP_MIN)
            size = scale * flow[nap][i]
            if size < 0.05:
                continue
            arch = "tourist" if u < self.tourist_share else "commuter"
            if kind == "exit":
                src, dest = st.station_node[nap], other
            else:
                target = redirect.get(nap, nap) if self.closed_station(nap, t0 + off) else nap
                src, dest = st.anchor_node[other], st.station_anchor[target]
            out.append([t0 + off, src, dest, size, SPEED_MPS[arch] * pace, LINGER_MIN[arch] * linger, u, arch, SLOT_MIN])
        rng = random.Random(self.crn * 7919 + 1)
        delay = float(a["busDelayMin"])
        for b in self.dyn["buses"]:
            if not b.get("arrivalsSec"):
                continue
            src = st.nearest_node((b["x"], b["z"]))
            near = st.nearest_station_anchor(src)
            for sec in b["arrivalsSec"]:
                u, pick = rng.random(), rng.random()
                dep = sec / 60.0 + delay
                if dep > self.H or near is None:
                    continue
                arch = "tourist" if u < self.tourist_share else "commuter"
                out.append([dep, src, draw(st.gravity[near], pick), float(a["alightPerBus"]),
                            SPEED_MPS[arch] * pace, LINGER_MIN[arch] * linger, u, arch, 1.0])
        for k, iv in enumerate(self.ivs):
            if iv["kind"] != "demand_surge" or not iv.get("center"):
                continue
            node = st.nearest_node(tuple(iv["center"]))
            for nap, w in iv["stations"].items():
                if nap not in st.station_node or nap not in self.dyn["stations"]:
                    continue
                d = self.dyn["stations"][nap]
                for t0 in range(-WARMUP_MIN, self.H + 1, SLOT_MIN):
                    k = self.strength(iv, t0 + SLOT_MIN / 2)
                    if not k:
                        continue
                    i = t0 + SLOT_MIN // 2 + WARMUP_MIN
                    typ = d["typical"][min(i, len(d["typical"]) - 1)]
                    size = BASE_EXITS_PER_MIN * float(a["flowScale"]) * SLOT_MIN * typ * d["ratio"] * iv["magnitude"] * w * k
                    if size >= 0.05:
                        out.append([t0 + SLOT_MIN / 2, st.station_node[nap], -1 - node, size,
                                    SPEED_MPS["commuter"] * pace, LINGER_MIN["tourist"] * linger, 1.0, "commuter",
                                    SLOT_MIN])
        out.sort(key=lambda g: g[0])
        return out

    def closed_station(self, nap: str, m: float) -> bool:
        return any(iv["kind"] == "closure" and iv.get("closesStation") and nap in iv["stations"] and self.active(iv, m)
                   for iv in self.ivs)

    def closed_at(self, m: float) -> frozenset:
        return frozenset(self.st.seg_index[s] for iv in self.ivs if iv["kind"] == "closure" and self.active(iv, m)
                         for s in iv["segments"] if s in self.st.seg_index)

    def capacity_at(self, m: float) -> list[float]:
        cap = [c * float(self.a["capacityScale"]) for c in self.st.cap]
        for iv in self.ivs:
            if iv["kind"] == "capacity" and self.active(iv, m):
                for s in iv["segments"]:
                    if s in self.st.seg_index:
                        cap[self.st.seg_index[s]] *= 1.0 - (1.0 - min(1.0, iv["magnitude"])) * self.strength(iv, m)
        return cap

    def shelter_at(self, m: float) -> float:
        return max((min(1.0, iv["magnitude"] / RAIN_FULL_MM_H) * self.strength(iv, m) for iv in self.ivs
                    if iv["kind"] == "weather"), default=0.0)

    def go(self) -> dict[str, Any]:
        st, E = self.st, self.st.E
        pct, flow, redirect = self.station_series()
        groups = self.groups(flow, redirect)
        seg_minutes = sorted({n + o for n in range(0, self.H - FORECAST_MIN + 1) for o in SEG_OFFSETS if n + o <= self.H})
        seg_want = set(seg_minutes)
        seg_data = array.array("f")
        cams = {cid: [] for cid, _ in st.cams}
        zones = {z: [] for z in st.zone_ids}
        exchange: dict[str, float] = {}
        walking: list[list] = []
        lingering: list[tuple[float, int, float]] = []
        nxt = 0
        load = [0.0] * E
        prev_ratio = [0.0] * E
        closed_now: frozenset = frozenset()
        cap = self.capacity_at(-WARMUP_MIN)
        reroutes = 0
        station_dests = set(st.station_anchor.values())
        for m in self.minutes:
            closed = self.closed_at(m)
            if closed != closed_now:
                closed_now = closed
                for w in walking:
                    rest = w[8][w[9]:]
                    if closed and any(e in closed for e in rest):
                        if self.replan(w, closed):
                            reroutes += 1
            cap = self.capacity_at(m)
            shelter = self.shelter_at(m)
            while nxt < len(groups) and groups[nxt][0] <= m:
                dep, src, dest, size, speed, linger, u, arch, spread = groups[nxt]
                nxt += 1
                if shelter and arch == "tourist" and u < self.tourist_share * shelter:
                    alt = st.nearest_station_anchor(src)
                    dest = alt if alt is not None else dest
                dnode = st.anchor_node[dest] if dest >= 0 else -1 - dest
                p = st.path(src, dnode, closed_now)
                if p is None or not p[0]:
                    continue
                # [size, speed, linger, dest anchor, dest node, head metres, stream metres, -, edges, head idx, cum]
                walking.append([size, speed, linger, dest, dnode, 0.0, speed * spread, (u, 0), p[0], 0, p[1]])
            for i in range(E):
                load[i] = 0.0
            still = []
            for w in walking:
                edges, idx, cum = w[8], w[9], w[10]
                n = len(edges)
                e = edges[min(idx, n - 1)]
                r = prev_ratio[e]
                w[5] += w[1] * (1.0 if r <= 1.0 else max(0.25, 1.0 / r))
                head, stream, total = w[5], w[6], cum[-1]
                while idx < n and head >= cum[idx]:
                    idx += 1
                if m >= 0 and idx < n and idx != w[9]:
                    h0, h1 = st.hub_of[e], st.hub_of[edges[idx]]
                    if h0 and h1 and h0 != h1:
                        key = f"{h0}>{h1}"
                        exchange[key] = exchange.get(key, 0.0) + w[0]
                w[9] = idx
                if head - stream >= total:
                    if w[3] < 0 or w[3] not in station_dests:
                        lingering.append((m + w[2], w[4], w[0], w))
                    continue
                lo, hi = max(0.0, head - stream), min(head, total)
                per_m = w[0] / stream
                j = idx - 1 if idx >= n else idx
                while j >= 0:
                    start = cum[j - 1] if j else 0.0
                    if cum[j] <= lo:
                        break
                    overlap = min(cum[j], hi) - max(start, lo)
                    if overlap > 0:
                        load[edges[j]] += per_m * overlap
                    j -= 1
                still.append(w)
            walking = still
            if lingering:
                walking += self.walk_on([g for g in lingering if g[0] <= m], closed_now)
                lingering = [g for g in lingering if g[0] > m]
                for _, node, size, _ in lingering:
                    es = st.node_edges[node]
                    for e in es:
                        load[e] += size / len(es)
            for i in range(E):
                prev_ratio[i] = load[i] * 100.0 / max(st.length[i], MIN_SEG_M) / max(cap[i], 1e-6)
            if m < 0:
                continue
            for cid, e in st.cams:
                cams[cid].append(round(load[e] * min(1.0, CAM_VIEW_M / st.length[e]), 2))
            ztot = [0.0] * len(st.zone_ids)
            for i in range(E):
                ztot[st.zone_of[i]] += load[i]
            for zi, zid in enumerate(st.zone_ids):
                zones[zid].append(round(ztot[zi], 1))
            if m in seg_want:
                seg_data.extend(load)
        if reroutes:
            self.events.append(f"closure: {reroutes} walking groups rerouted")
        stations = {nap: [round(v, 4) for v in row[WARMUP_MIN:]] for nap, row in pct.items()}
        pre = {nap: [round(v, 4) for v in row[:WARMUP_MIN]] for nap, row in pct.items()}
        return {"cams": cams, "zones": zones, "stations": stations, "stationsPre": pre,
                "segments": {"minutes": seg_minutes, "data": seg_data.tobytes()},
                "exchange": {k: round(v, 1) for k, v in sorted(exchange.items())},
                "events": self.events[:12], "groups": len(groups)}

    def walk_on(self, done: list, closed: frozenset) -> list[list]:
        """Groups whose lingering ended walk to another destination (gravity, drawn from their own uniform)."""
        out = []
        for _, node, size, w in done:
            u, hops = w[7]
            if w[3] < 0 or hops >= MAX_HOPS:
                continue
            u = (u * 7.13 + 0.37) % 1.0
            dest = draw(self.st.gravity[w[3]], u)
            dnode = self.st.anchor_node[dest]
            p = self.st.path(node, dnode, closed)
            if p is None or not p[0]:
                continue
            out.append([size, w[1], w[2], dest, dnode, 0.0, w[6], (u, hops + 1), p[0], 0, p[1]])
        return out

    def entry_node(self, edges: list[int], idx: int, dest: int) -> int:
        """The node a path enters edges[idx] from: shared with the previous edge, or (first edge) the end not
        shared with the next one / not the destination."""
        seg = self.st.segs[edges[idx]]
        ends = (seg["u"], seg["v"])
        if idx > 0:
            prev = self.st.segs[edges[idx - 1]]
            return next(n for n in ends if n in (prev["u"], prev["v"]))
        if len(edges) > 1:
            nxt = self.st.segs[edges[1]]
            return next((n for n in ends if n not in (nxt["u"], nxt["v"])), ends[0])
        return ends[1] if ends[0] == dest else ends[0]

    def replan(self, w: list, closed: frozenset) -> bool:
        edges, idx = w[8], min(w[9], len(w[8]) - 1)
        here = self.entry_node(edges, idx, w[4])
        p = self.st.path(here, w[4], closed)
        if p is None or not p[0]:
            return False
        w[8], w[9], w[10], w[5] = p[0], 0, p[1], 0.0
        return True


# ---------------------------------------------------------------- epochs


def reseed(seed: dict, ts_ms: int, readings: dict[str, float], stations: dict[str, float] | None = None) -> dict:
    """A new epoch on the same graph: latest camera counts and station busyness, bus arrivals moved on."""
    s = dict(seed)
    s["at"] = ts_ms
    s["time"] = london_time(ts_ms)
    s["cams"] = [dict(c, people=readings[c["id"]], at=ts_ms) if c["id"] in readings else c for c in seed.get("cams", [])]
    s["stations"] = [dict(x, live=stations[x["naptan"]], liveAt=ts_ms) if stations and x["naptan"] in stations else x
                     for x in seed.get("stations", [])]
    shift = (ts_ms - seed["at"]) / 1000
    s["buses"] = [dict(b, arrivalsSec=[round(v - shift) for v in b.get("arrivalsSec", []) if v - shift >= 0])
                  for b in seed.get("buses", [])]
    return s


def london_time(ms: int) -> dict:
    dt = datetime.fromtimestamp(ms / 1000, LONDON)
    hour = dt.hour + dt.minute / 60
    daypart = ("morning_rush" if 7 <= hour < 10 else "lunch" if 12 <= hour < 14 else "evening_rush"
               if 16.5 <= hour < 19 else "daytime" if 10 <= hour < 16.5 else "night")
    return {"iso": dt.isoformat(timespec="seconds"), "weekday": dt.strftime("%A"), "hour": round(hour, 2),
            "isWeekend": dt.weekday() >= 5, "daypart": daypart}


# ---------------------------------------------------------------- worlds in parallel

WORKERS = max(1, min(8, (os.cpu_count() or 2) - 4))
_POOL: dict[str, Any] = {"pool": None, "key": None}


def _init_worker(static: dict) -> None:
    static_of(static)
    _STATIC["seed"] = static  # type: ignore[assignment]


def _work(job: tuple) -> dict:
    dyn, a, horizon, crn = job
    seed = _STATIC["seed"]  # type: ignore[index]
    return run_world(seed, a, horizon, crn, dyn)  # type: ignore[arg-type]


def _pool(seed: dict) -> Any:
    if _POOL["pool"] is not None and _POOL["key"] == seed["graphKey"]:
        return _POOL["pool"]
    shutdown()
    import multiprocessing as mp
    _POOL["pool"] = mp.get_context("spawn").Pool(WORKERS, initializer=_init_worker, initargs=(static_part(seed),))
    _POOL["key"] = seed["graphKey"]
    return _POOL["pool"]


def shutdown() -> None:
    if _POOL["pool"] is not None:
        _POOL["pool"].terminate()
        _POOL["pool"], _POOL["key"] = None, None


atexit.register(shutdown)


def run_many(seed: dict, assumptions: list[dict], horizon: int, crn_seed: int) -> list[dict]:
    """Every world of a batch, in parallel when there are enough of them to pay for the pipe."""
    dyn = dynamic(seed, horizon)
    if WORKERS <= 1 or len(assumptions) < 4:
        return [run_world(seed, a, horizon, crn_seed, dyn) for a in assumptions]
    pool = _pool(seed)
    chunk = max(1, math.ceil(len(assumptions) / (WORKERS * 2)))
    return pool.map(_work, [(dyn, a, horizon, crn_seed) for a in assumptions], chunksize=chunk)


# ---------------------------------------------------------------- recorder frames (replay and live)


def seed_ids(seed: dict) -> tuple[set[str], set[str]]:
    return {c["id"] for c in seed.get("cams", [])}, {b["naptanId"] for b in seed.get("buses", []) if b.get("naptanId")}


def station_readings(payload: Any, naptans: set[str], seen: dict[str, str]) -> dict[str, float]:
    """Live busyness from one `crowding` record, only where the TfL reading time is new (a station repeats its
    value between updates; scoring a repeat would count the same evidence twice). Updates `seen`."""
    out = {}
    for row in payload if isinstance(payload, list) else []:
        nap = row.get("naptan") if isinstance(row, dict) else None
        if nap not in naptans or not row.get("dataAvailable"):
            continue
        v = row.get("percentageOfBaseline")
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        t = str(row.get("timeUtc") or "")
        if seen.get(nap) == t:
            continue
        seen[nap] = t
        out[nap] = float(v)
    return out


def load_frames(raw: Any, seed: dict, start_ms: int, end_ms: int) -> list[dict]:
    """One frame per recorded `jamcams` minute in [start, end]: fresh YOLO counts at the seed's cameras, the
    bus lateness proxy at its stops, rain, and new station busyness readings."""
    import feeds
    cams, naptans = seed_ids(seed)
    st_naps = {s["naptan"] for s in seed.get("stations", [])}
    buses = feeds.bus_snapshots(raw, naptans, start_ms, end_ms)
    frames = {ts // 60_000: feeds.real_frame(payload, ts, cams, buses)
              for ts, payload in feeds.records_between(raw, "jamcams", start_ms, end_ms)}
    crowd = sorted(feeds.records_between(raw, "crowding", start_ms - 30 * 60_000, end_ms), key=lambda r: r[0])
    rains = sorted(feeds.records_between(raw, "rain_now", start_ms - 30 * 60_000, end_ms), key=lambda r: r[0])
    seen: dict[str, str] = {}
    out, ci, ri, rain = [], 0, 0, None
    while ci < len(crowd) and crowd[ci][0] <= seed["at"]:
        station_readings(crowd[ci][1], st_naps, seen)
        ci += 1
    for k in sorted(frames):
        f = frames[k]
        st: dict[str, float] = {}
        while ci < len(crowd) and crowd[ci][0] <= f["ts"]:
            st |= station_readings(crowd[ci][1], st_naps, seen)
            ci += 1
        while ri < len(rains) and rains[ri][0] <= f["ts"]:
            rain = feeds.rain_mm_h(rains[ri][1])
            ri += 1
        f["rainMmH"] = rain
        f["stations"] = st
        out.append(f)
    return out


def latest_frame(raw: Any, seed: dict, now_ms: int, seen: dict[str, str]) -> dict | None:
    import feeds
    cams, naptans = seed_ids(seed)
    recs = feeds.latest_records(raw, "jamcams", now_ms, k=1, back_min=5)
    if not recs:
        return None
    ts = recs[-1]["tsMs"]
    frame = feeds.real_frame(recs[-1].get("payload"), ts, cams, feeds.bus_snapshots(raw, naptans, ts, ts))
    rain = feeds.latest_records(raw, "rain_now", ts, k=1)
    crowd = feeds.latest_records(raw, "crowding", ts, k=1)
    frame["rainMmH"] = feeds.rain_mm_h(rain[-1]["payload"]) if rain else None
    frame["stations"] = (station_readings(crowd[-1]["payload"], {s["naptan"] for s in seed["stations"]}, seen)
                         if crowd else {})
    return frame


def seed_at(seed: dict, raw: Any, ts_ms: int) -> tuple[dict, dict[str, str]]:
    """The seed re-timed to ts: cameras, station busyness and bus arrivals as recorded then (no lookahead).
    Also returns the station reading times already used, so frames only score newer readings."""
    import feeds
    cams, naptans = seed_ids(seed)
    recs = feeds.latest_records(raw, "jamcams", ts_ms, k=1, back_min=5)
    readings = feeds.fresh_people(recs[-1]["payload"], recs[-1]["tsMs"], cams) if recs else {}
    crowd = feeds.latest_records(raw, "crowding", ts_ms, k=1, back_min=30)
    live: dict[str, tuple[float, int]] = {}
    seen: dict[str, str] = {}
    for row in (crowd[-1]["payload"] if crowd else None) or []:
        v = row.get("percentageOfBaseline") if isinstance(row, dict) else None
        if row.get("dataAvailable") and isinstance(v, (int, float)) and v > 0:
            live[row["naptan"]] = (float(v), feeds.parse_ts(row["timeUtc"]) if row.get("timeUtc") else ts_ms)
            seen[row["naptan"]] = str(row.get("timeUtc") or "")
    s = dict(seed, at=ts_ms, time=london_time(ts_ms))
    s["cams"] = [dict(c, people=readings[c["id"]], at=ts_ms, counted=True) if c["id"] in readings else c
                 for c in seed.get("cams", [])]
    s["stations"] = [dict(x, live=live[x["naptan"]][0], liveAt=live[x["naptan"]][1]) if x["naptan"] in live
                     else dict(x, live=None) for x in seed.get("stations", [])]
    bus = feeds.latest_records(raw, "bus", ts_ms, k=1, back_min=10)
    rows = bus[-1]["payload"] if bus and isinstance(bus[-1]["payload"], list) else []
    arr: dict[str, list[int]] = {}
    for r in rows:
        if isinstance(r, dict) and r.get("naptanId") in naptans:
            try:
                sec = round((feeds.parse_ts(r["expectedArrival"]) - ts_ms) / 1000)
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= sec <= 3600:
                arr.setdefault(r["naptanId"], []).append(sec)
    s["buses"] = [dict(b, arrivalsSec=sorted(arr.get(b["naptanId"], []))) for b in seed.get("buses", [])]
    return s, seen


# ---------------------------------------------------------------- derived events (events.py)


class EventFeed:
    """The event layer's interventions at any minute. Replay: events.replay over the window once (it calls
    events.derive at every update of a rule's feed). Live: events.derive on the newest record of each source,
    the tube graph being the union of the last TUBE_UNION_MIN of arrivals snapshots."""

    TUBE_UNION_MIN = 60

    def __init__(self, raw: Any, start_ms: int | None = None, end_ms: int | None = None) -> None:
        import events
        self.events, self.raw = events, raw
        self.replaying = start_ms is not None and end_ms is not None and raw is not None
        self.ticks: list[tuple[int, list[dict]]] = list(events.replay(raw, start_ms - 30 * 60_000, end_ms)) \
            if self.replaying else []

    def at(self, ts_ms: int) -> list[dict]:
        if not self.replaying:
            return self.live(ts_ms)
        cur: list[dict] = []
        for t, ivs in self.ticks:
            if t > ts_ms:
                break
            cur = ivs
        return [iv for iv in cur if iv["end"] > ts_ms]

    def live(self, now_ms: int) -> list[dict]:
        import feeds
        ev = self.events
        if self.raw is None:
            return []
        snap: dict[str, dict] = {}
        for src in ev.SOURCES:
            if src in ("tube", "crowding_profile"):
                continue
            recs = feeds.latest_records(self.raw, src, now_ms, k=1,
                                        back_min=24 * 60 if src == "scrape_fixtures" else 60)
            if recs:
                snap[src] = {"ts": recs[-1]["ts"], "payload": recs[-1]["payload"]}
        profiles = {}
        for rec in feeds.latest_records(self.raw, "crowding_profile", now_ms, k=400, back_min=7 * 24 * 60):
            p = rec.get("payload")
            if isinstance(p, dict) and p.get("naptan"):
                profiles[p["naptan"]] = p
        if profiles:
            snap["crowding_profile"] = {"ts": feeds.iso(now_ms), "payload": [profiles[k] for k in sorted(profiles)]}
        rows: dict = {}
        for rec in feeds.latest_records(self.raw, "tube", now_ms, k=60, back_min=self.TUBE_UNION_MIN):
            ev.add_tube_rows(rows, rec.get("payload"))
        if rows:
            snap["tube"] = {"ts": feeds.iso(now_ms), "payload": [rows[k] for k in sorted(rows, key=str)]}
        return ev.derive(snap, now_ms)


# A derived event enters this share of the worlds; the readings then price it in or out (a wrong "closed" call
# costs every world that carries it as soon as TfL publishes the station busy).
EVENT_PRIOR = 0.5


def resolve_events(derived: list[dict], seed: dict, now_ms: int) -> tuple[list[dict], list[dict]]:
    """Derived events -> (resolved interventions for the worlds, the compact list for /state)."""
    out, shown = [], []
    for ev in derived:
        try:
            iv = normalise_intervention(ev, seed, now_ms, ev.get("id"), strict=False)
        except (ValueError, TypeError, KeyError):
            continue
        shown.append({"id": ev.get("id"), "kind": ev["kind"], "reason": ev.get("reason"), "planned": ev.get("planned"),
                      "start": ev.get("start"), "end": ev.get("end"), "inSim": iv["resolved"]})
        if iv["resolved"]:
            out.append(iv | {"prior": EVENT_PRIOR})
    return out, shown


# ---------------------------------------------------------------- forecasts (/state, /forecast, /segments)

FORECAST_HORIZONS = (5, 15, 60)
Q_LO, Q_HI = 0.1, 0.9


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def mixture_quantile(q: float, centres: list[float], weights: list[float], sd: float, kind: str) -> float:
    """Quantile of the weighted mixture of the engine's noise model around each world's value: log-normal
    (station busyness, sd on the log scale) or Gaussian on the sqrt scale (camera counts), floored at 0."""
    total = sum(weights) or 1.0
    if kind == "log":
        lc = [math.log(max(c, 1e-6)) for c in centres]
        f = lambda y: sum(w * _phi((math.log(max(y, 1e-6)) - c) / sd) for c, w in zip(lc, weights)) / total  # noqa: E731
    else:
        sc = [math.sqrt(max(c, 0.0)) for c in centres]
        f = lambda y: sum(w * _phi((math.sqrt(max(y, 0.0)) - c) / sd) for c, w in zip(sc, weights)) / total  # noqa: E731
        if f(0.0) >= q:
            return 0.0
    lo, hi = 0.0, max(1e-3, max(centres, default=1.0)) * 2 + 1
    while f(hi) < q:
        hi *= 2
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < q else (lo, mid)
    return hi


def _typical(seed: dict, nap: str, at_ms: int) -> float | None:
    st = next((s for s in seed.get("stations", []) if s["naptan"] == nap), None)
    return typical_at(st.get("profile"), at_ms) if st else None


def _at(series: list[float], m: int) -> float:
    return series[min(m, len(series) - 1)]


def station_forecast(mv: Any, nap: str, horizon: int) -> dict:
    """Busyness at now + horizon: TfL percentageOfBaseline (`busyness`) and as % of the typical day (mean, p10,
    p90; the interval includes the engine's station noise, so it is a range for the reading TfL will publish)."""
    import engine
    live = mv.live()
    pairs = [(_at(w["run"]["stations"][nap], mv.now_minute() + horizon), w["price"]) for w in live
             if nap in w["run"].get("stations", {})]
    if not pairs:
        raise LookupError(f"no forecast for station {nap}")
    vals, prices = [v for v, _ in pairs], [p for _, p in pairs]
    target = mv.seed["at"] + (mv.now_minute() + horizon) * 60_000
    typ = _typical(mv.seed, nap, target)
    mean = sum(v * p for v, p in pairs) / (sum(prices) or 1.0)
    lo = mixture_quantile(Q_LO, vals, prices, engine.STATION_SD, "log")
    hi = mixture_quantile(Q_HI, vals, prices, engine.STATION_SD, "log")
    pct = (lambda v: round(100.0 * v / typ, 1)) if typ else (lambda v: None)
    return {"naptan": nap, "name": mv.station_names.get(nap, nap), "horizonMin": horizon, "at": target,
            "busyness": round(mean, 4), "typical": typ, "mean": pct(mean), "p10": pct(lo), "p90": pct(hi)}


def hub_forecast(mv: Any, hub: dict, horizon: int) -> dict:
    """A hub's busyness = its catchment stations' summed busyness over their summed typical, per world;
    people = everyone on the catchment's streets (price-weighted quantiles across worlds)."""
    import engine
    live = mv.live()
    m = mv.now_minute() + horizon
    target = mv.seed["at"] + m * 60_000
    naps = [s["naptan"] for s in mv.seed["stations"] if s.get("hub") == hub["id"]]
    typ = sum(t for n in naps if (t := _typical(mv.seed, n, target)))
    prices = [w["price"] for w in live]
    ratios, people = [], []
    for w in live:
        st = w["run"].get("stations", {})
        ratios.append(sum(_at(st[n], m) for n in naps if n in st) / typ if typ else 0.0)
        people.append(sum(_at(w["run"]["zones"][z], m) for z in hub["zoneIds"] if z in w["run"]["zones"]))
    total = sum(prices) or 1.0
    order = sorted(zip(people, prices))

    def wq(q: float) -> float:
        acc = 0.0
        for v, p in order:
            acc += p / total
            if acc >= q:
                return v
        return order[-1][0] if order else 0.0

    mean = sum(r * p for r, p in zip(ratios, prices)) / total
    return {"hub": hub["id"], "name": hub["name"], "horizonMin": horizon, "at": target, "stations": naps,
            "mean": round(100 * mean, 1) if typ else None,
            "p10": round(100 * mixture_quantile(Q_LO, ratios, prices, engine.STATION_SD, "log"), 1) if typ else None,
            "p90": round(100 * mixture_quantile(Q_HI, ratios, prices, engine.STATION_SD, "log"), 1) if typ else None,
            "people": {"mean": round(sum(v * p for v, p in zip(people, prices)) / total), "p10": round(wq(Q_LO)),
                       "p90": round(wq(Q_HI))}}


def forecasts(mv: Any, horizons: tuple[int, ...] = FORECAST_HORIZONS) -> dict:
    """/state block: per station and per hub, % of usual (mean, p10, p90) at each horizon."""
    def compact(f: dict) -> dict:
        return {k: f[k] for k in ("mean", "p10", "p90")} | ({"people": f["people"]} if "people" in f else {})
    stations = [{"naptan": s["naptan"], "name": s["name"], "hub": s.get("hub"),
                 **{f"+{h}": compact(station_forecast(mv, s["naptan"], h)) for h in horizons}}
                for s in mv.seed.get("stations", [])]
    hubs = [{"hub": h["id"], "name": h["name"], "naptan": h.get("naptan"),
             **{f"+{k}": compact(hub_forecast(mv, h, k)) for k in horizons}} for h in mv.seed.get("hubs", [])]
    return {"unit": "% of usual (TfL typical day, same weekday and quarter-hour)", "stations": stations, "hubs": hubs}


def cam_forecast(mv: Any, horizon: int) -> list[dict]:
    import engine
    live = mv.live()
    m = mv.now_minute() + horizon
    sd = math.sqrt(engine.POISSON_SD ** 2 + engine.MODEL_SD ** 2)
    out = []
    for cid, name in mv.cam_names.items():
        pairs = [(_at(w["run"]["cams"][cid], m), w["price"]) for w in live if cid in w["run"]["cams"]]
        if not pairs:
            continue
        vals, prices = [v for v, _ in pairs], [p for _, p in pairs]
        out.append({"camId": cid, "name": name, "mean": round(sum(v * p for v, p in pairs) / (sum(prices) or 1.0), 1),
                    "p10": round(mixture_quantile(Q_LO, vals, prices, sd, "sqrt"), 1),
                    "p90": round(mixture_quantile(Q_HI, vals, prices, sd, "sqrt"), 1)})
    return out


def segment_forecast(mv: Any, horizon: int) -> dict:
    """Price-weighted people per street edge at now + horizon, in seed order (as /geometry lists them)."""
    m = mv.now_minute() + horizon
    E = len(mv.seed["segments"])
    acc = [0.0] * E
    total = 0.0
    for w in mv.live():
        seg = w["run"].get("segments")
        if not seg or m not in seg["minutes"]:
            continue
        vals = array.array("f")
        vals.frombytes(seg["data"])
        k = seg["minutes"].index(m) * E
        p = w["price"]
        for i in range(E):
            acc[i] += p * vals[k + i]
        total += p
    if total <= 0:
        raise LookupError(f"no segment forecast at +{horizon} min")
    return {"horizonMin": horizon, "at": mv.seed["at"] + m * 60_000, "people": [round(v / total, 1) for v in acc]}


def scenario(mv: Any, ivs: list[dict], horizons: tuple[int, ...]) -> dict:
    """What-if without touching the market: every live world is copied, given `ivs` on top of what it carries,
    re-run and priced as its original. Returns the station and hub forecast blocks before and after."""
    before = forecasts(mv, horizons)
    live = mv.live()
    batch = [dict(w["assumptions"], interventions=list(w["assumptions"].get("interventions", [])) + ivs) for w in live]
    runs = mv.run_batch(batch)
    clone = copy.copy(mv)
    clone.worlds = [dict(w, assumptions=a, run=r) for w, a, r in zip(live, batch, runs)]
    after = forecasts(clone, horizons)
    return {"before": {k: before[k] for k in ("stations", "hubs")}, "after": {k: after[k] for k in ("stations", "hubs")}}
