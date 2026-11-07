"""Build a seed for ANY bounding box: merged street graph, station zones, hub catchments, cameras, buses.

    python3 services/multiverse/central_seed.py                       # central London (CENTRAL_BBOX)
    python3 services/multiverse/central_seed.py --bbox 51.509,-0.152,51.521,-0.131 --out seeds/oxford-bbox.json
    python3 services/multiverse/central_seed.py --offline             # streets from seeds/central-osm.json.gz

One code path for every place. Inputs, all read-only:
- streets, stations, POIs: the twin's cached OpenFreeMap tiles (`apps/stream/.cache/twin-osm-v3-*.json.gz`, repo
  or main checkout, or `--tiles DIR`), cut to the bbox and saved compact in seeds/central-osm.json.gz so the
  seed rebuilds offline;
- stations: the recorder's newest `crowding` record (TfL Crowding list: live busyness) and `crowding_profile`
  (typical day), placed by name on the OSM stations, else by NaPTAN from services/city/hotspots.json;
- hubs: services/city/hotspots.json (hub id, NaPTAN, bus stops), the ones inside the bbox;
- cameras: the recorder's `jamcams` rows inside the bbox, each pinned to its nearest street, plus its newest
  YOLO count of the last CAM_HISTORY_MIN;
- buses: the newest `bus` snapshot at stops with a known position (a cached twin bus-stop list, or a hub's
  listed stop placed at the hub).

Graph: major + minor roads (footpaths dropped), largest connected piece, cul-de-sacs pruned, chains through
degree-2 nodes merged into street edges, junction clusters joined by edges under CONTRACT_M collapsed (dual
carriageways), then chains merged again up to MERGE_MAX_M. Zones = one per station (every street goes to its
nearest station); hub catchments = zones grouped by nearest hub (the partition the sim reports flows across).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feeds

HERE = Path(__file__).resolve().parent
SEEDS = HERE / "seeds"
OSM_CACHE = SEEDS / "central-osm.json.gz"
CENTRAL_SEED = SEEDS / "central-london.json"
HOTSPOTS = HERE.parent / "city" / "hotspots.json"
# King's Cross down to Waterloo, Marble Arch east to Bank (south, west, north, east).
CENTRAL_BBOX = (51.4995, -0.1600, 51.5345, -0.0840)
TOWER_HILL = (51.5098, -0.0766)
M_PER_DEG_LAT = 111_320.0
ORIGIN_GRID_M = 250.0
LONDON = ZoneInfo("Europe/London")
STREET_KINDS = ("major", "minor")
SNAP_M = 2.0
CONTRACT_M = 15.0
MERGE_MAX_M = 300.0
SIMPLIFY_M = 4.0
CAM_SNAP_M = 60.0
CAM_HISTORY_MIN = 120
POI_CELL_M = 150.0
POI_MAX = 120
POI_MIN_GAP_M = 100.0
POI_WEIGHTS = {"attraction": 3.0, "shop": 1.0, "food": 1.0}
BUS_HORIZON_S = 3600


# ---------------------------------------------------------------- geometry


def snap_origin(lat: float, lon: float) -> tuple[float, float]:
    """The twin's snapOrigin (250 m grid anchored on Tower Hill), so local x/z line up with the twin's map."""
    d_lat = ORIGIN_GRID_M / M_PER_DEG_LAT
    d_lon = ORIGIN_GRID_M / (M_PER_DEG_LAT * math.cos(math.radians(TOWER_HILL[0])))
    return (round(TOWER_HILL[0] + round((lat - TOWER_HILL[0]) / d_lat) * d_lat, 6),
            round(TOWER_HILL[1] + round((lon - TOWER_HILL[1]) / d_lon) * d_lon, 6))


def to_local(lat: float, lon: float, origin: tuple[float, float]) -> tuple[float, float]:
    return ((lon - origin[1]) * M_PER_DEG_LAT * math.cos(math.radians(origin[0])), -(lat - origin[0]) * M_PER_DEG_LAT)


def to_latlon(x: float, z: float, origin: tuple[float, float]) -> tuple[float, float]:
    return (origin[0] - z / M_PER_DEG_LAT, origin[1] + x / (M_PER_DEG_LAT * math.cos(math.radians(origin[0]))))


def inside(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    return bbox[0] <= lat <= bbox[2] and bbox[1] <= lon <= bbox[3]


def polyline_len(line: list) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:]))


def point_seg_dist(p: tuple[float, float], a: list, b: list) -> float:
    dx, dz = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dz * dz
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


def line_dist(p: tuple[float, float], line: list) -> float:
    return min(point_seg_dist(p, a, b) for a, b in zip(line, line[1:]))


def simplify(line: list, tol: float = SIMPLIFY_M) -> list:
    """Douglas-Peucker; straight runs keep only their ends."""
    if len(line) < 3:
        return line
    (ax, az), (bx, bz) = line[0], line[-1]
    seg = math.hypot(bx - ax, bz - az) or 1e-9
    far, idx = max(((abs((bx - ax) * (az - p[1]) - (ax - p[0]) * (bz - az)) / seg, i)
                    for i, p in enumerate(line[1:-1], 1)), default=(0.0, 0))
    if far <= tol:
        return [line[0], line[-1]]
    return simplify(line[:idx + 1], tol)[:-1] + simplify(line[idx:], tol)


# ---------------------------------------------------------------- OSM input


def tile_dirs() -> list[Path]:
    """The twin's disk cache in this checkout and, in a git worktree, the main checkout."""
    dirs = [feeds.REPO / "apps" / "stream" / ".cache"]
    main = feeds.alt_root().parent.parent / "apps" / "stream" / ".cache"
    if main not in dirs:
        dirs.append(main)
    return [d for d in dirs if d.is_dir()]


def read_tiles(dirs: list[Path], bbox: tuple[float, float, float, float]) -> dict:
    """Roads (major/minor), stations and POIs from every cached twin OSM tile, in lat/lon, cut to the bbox.
    Tiles overlap; duplicates are dropped later when vertices snap together."""
    seen: set[str] = set()
    roads, stations, pois = [], {}, []
    for d in dirs:
        for path in sorted(d.glob("twin-osm-v*-z*-*_*-b-*.json.gz")):
            if path.name in seen:
                continue
            seen.add(path.name)
            m = re.search(r"-z\d+-(-?[\d.]+)_(-?[\d.]+)-b-", path.name)
            if not m:
                continue
            origin = (float(m.group(1)), float(m.group(2)))
            try:
                body = json.loads(gzip.open(path, "rt").read())["body"]
            except (OSError, ValueError, KeyError):
                continue
            for r in body.get("roads", []):
                if r.get("kind") not in STREET_KINDS:
                    continue
                pts = [to_latlon(x, z, origin) for x, z in r["line"]]
                run: list = []
                for la, lo in pts:
                    if inside(la, lo, bbox):
                        run.append([round(la, 6), round(lo, 6)])
                        continue
                    if len(run) >= 2:
                        roads.append({"kind": r["kind"], "line": run})
                    run = []
                if len(run) >= 2:
                    roads.append({"kind": r["kind"], "line": run})
            for s in body.get("stations", []):
                la, lo = to_latlon(s["pos"][0], s["pos"][1], origin)
                stations.setdefault((s["name"], s.get("kind")), [round(la, 6), round(lo, 6)])
            for p in body.get("pois", []):
                if p.get("kind") in POI_WEIGHTS:
                    la, lo = to_latlon(p["x"], p["z"], origin)
                    if inside(la, lo, bbox):
                        pois.append({"name": p.get("name") or p["kind"], "kind": p["kind"],
                                     "lat": round(la, 6), "lon": round(lo, 6)})
    uniq = {(p["name"], round(p["lat"], 4), round(p["lon"], 4)): p for p in pois}
    return {"bbox": list(bbox), "tiles": sorted(seen), "roads": roads,
            "stations": [{"name": n, "kind": k, "lat": ll[0], "lon": ll[1]} for (n, k), ll in sorted(stations.items())],
            "pois": list(uniq.values())}


# ---------------------------------------------------------------- street graph


class Graph:
    """Undirected multigraph keyed by node index; edges carry a polyline from node u to node v."""

    def __init__(self) -> None:
        self.pos: list[list[float]] = []
        self.edges: dict[int, dict] = {}
        self.adj: dict[int, set[int]] = {}
        self.next_edge = 0

    def add_edge(self, u: int, v: int, line: list, kind: str) -> None:
        eid = self.next_edge
        self.next_edge += 1
        self.edges[eid] = {"u": u, "v": v, "line": line, "kind": kind}
        self.adj.setdefault(u, set()).add(eid)
        self.adj.setdefault(v, set()).add(eid)

    def remove_edge(self, eid: int) -> None:
        e = self.edges.pop(eid)
        for n in (e["u"], e["v"]):
            self.adj[n].discard(eid)
            if not self.adj[n]:
                del self.adj[n]

    def other(self, eid: int, n: int) -> int:
        e = self.edges[eid]
        return e["v"] if e["u"] == n else e["u"]

    def degree(self, n: int) -> int:
        return len(self.adj.get(n, ()))


def micro_graph(roads: list[dict], origin: tuple[float, float]) -> Graph:
    """Every consecutive vertex pair is an edge; vertices within SNAP_M share a node; duplicate pairs from
    overlapping tiles collapse (major wins)."""
    g = Graph()
    grid: dict[tuple[int, int], list[int]] = {}

    def node(x: float, z: float) -> int:
        cx, cz = int(math.floor(x / SNAP_M)), int(math.floor(z / SNAP_M))
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for n in grid.get((cx + dx, cz + dz), ()):
                    if math.hypot(g.pos[n][0] - x, g.pos[n][1] - z) <= SNAP_M:
                        return n
        g.pos.append([x, z])
        grid.setdefault((cx, cz), []).append(len(g.pos) - 1)
        return len(g.pos) - 1

    pairs: dict[tuple[int, int], str] = {}
    for r in roads:
        ids = [node(*to_local(la, lo, origin)) for la, lo in r["line"]]
        for a, b in zip(ids, ids[1:]):
            if a == b:
                continue
            key = (min(a, b), max(a, b))
            if pairs.get(key) != "major":
                pairs[key] = r["kind"]
    for (a, b), kind in pairs.items():
        g.add_edge(a, b, [list(g.pos[a]), list(g.pos[b])], kind)
    return g


def largest_component(g: Graph) -> None:
    comp: dict[int, int] = {}
    for start in list(g.adj):
        if start in comp:
            continue
        stack, comp[start] = [start], start
        while stack:
            n = stack.pop()
            for eid in g.adj.get(n, ()):
                m = g.other(eid, n)
                if m not in comp:
                    comp[m] = start
                    stack.append(m)
    counts: dict[int, int] = {}
    for root in comp.values():
        counts[root] = counts.get(root, 0) + 1
    main = max(counts, key=counts.get)
    for eid in [e for e, d in g.edges.items() if comp[d["u"]] != main]:
        g.remove_edge(eid)


def prune_dead_ends(g: Graph) -> None:
    """Drop cul-de-sacs repeatedly: nobody routes through them."""
    stack = [n for n in g.adj if g.degree(n) == 1]
    while stack:
        n = stack.pop()
        if g.degree(n) != 1:
            continue
        eid = next(iter(g.adj[n]))
        m = g.other(eid, n)
        g.remove_edge(eid)
        if g.degree(m) == 1:
            stack.append(m)


def merge_chains(g: Graph, max_len: float | None) -> None:
    """Join edges through nodes with exactly two edges of the same kind into one street edge."""
    for n in list(g.adj):
        if g.degree(n) != 2:
            continue
        e1, e2 = list(g.adj[n])
        if e1 == e2 or g.edges[e1]["kind"] != g.edges[e2]["kind"]:
            continue
        a, b = g.other(e1, n), g.other(e2, n)
        if a == n or b == n or a == b:
            continue
        l1 = g.edges[e1]["line"] if g.edges[e1]["v"] == n else g.edges[e1]["line"][::-1]
        l2 = g.edges[e2]["line"] if g.edges[e2]["u"] == n else g.edges[e2]["line"][::-1]
        line = l1 + l2[1:]
        if max_len is not None and polyline_len(line) > max_len:
            continue
        kind = g.edges[e1]["kind"]
        g.remove_edge(e1)
        g.remove_edge(e2)
        g.add_edge(a, b, line, kind)


def contract_short(g: Graph, limit: float) -> int:
    """Collapse junction-to-junction edges shorter than `limit` (dual-carriageway junction clusters) into one
    node at their mean position; parallel edges left behind keep the shorter one, self-loops go."""
    parent: dict[int, int] = {}

    def find(n: int) -> int:
        while parent.setdefault(n, n) != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for eid, e in g.edges.items():
        if polyline_len(e["line"]) < limit and g.degree(e["u"]) != 2 and g.degree(e["v"]) != 2:
            parent[find(e["u"])] = find(e["v"])
    groups: dict[int, list[int]] = {}
    for n in list(g.adj):
        groups.setdefault(find(n), []).append(n)
    moved = 0
    for root, members in groups.items():
        if len(members) < 2:
            continue
        moved += len(members) - 1
        x = sum(g.pos[m][0] for m in members) / len(members)
        z = sum(g.pos[m][1] for m in members) / len(members)
        g.pos[root] = [x, z]
    old = list(g.edges.items())
    g.edges, g.adj = {}, {}
    best: dict[tuple[int, int], tuple[float, list, str]] = {}
    for _, e in old:
        u, v = find(e["u"]), find(e["v"])
        if u == v:
            continue
        line = [list(g.pos[u])] + e["line"][1:-1] + [list(g.pos[v])]
        key = (min(u, v), max(u, v))
        if key[0] != u:
            line = line[::-1]
        length = polyline_len(line)
        if key not in best or length < best[key][0]:
            kind = "major" if e["kind"] == "major" or (key in best and best[key][2] == "major") else e["kind"]
            best[key] = (length, line, kind)
    for (u, v), (_, line, kind) in best.items():
        g.add_edge(u, v, line, kind)
    return moved


def street_graph(roads: list[dict], origin: tuple[float, float]) -> tuple[list[list[float]], list[dict], dict]:
    t0 = time.perf_counter()
    g = micro_graph(roads, origin)
    stats = {"rawPieces": len(roads), "microEdges": len(g.edges)}
    largest_component(g)
    prune_dead_ends(g)
    merge_chains(g, MERGE_MAX_M)
    stats["junctionEdges"] = len(g.edges)
    stats["contractedNodes"] = contract_short(g, CONTRACT_M)
    prune_dead_ends(g)
    merge_chains(g, MERGE_MAX_M)
    largest_component(g)
    used = sorted(g.adj)
    index = {n: i for i, n in enumerate(used)}
    nodes = [[round(g.pos[n][0], 1), round(g.pos[n][1], 1)] for n in used]
    segments = []
    for e in sorted(g.edges.values(), key=lambda e: (index[e["u"]], index[e["v"]])):
        line = simplify([[round(p[0], 1), round(p[1], 1)] for p in e["line"]])
        line[0], line[-1] = nodes[index[e["u"]]], nodes[index[e["v"]]]
        segments.append({"id": f"s{len(segments)}", "u": index[e["u"]], "v": index[e["v"]], "kind": e["kind"],
                         "lengthM": round(max(polyline_len(e["line"]), 1.0), 1), "line": line})
    stats |= {"nodes": len(nodes), "edges": len(segments), "buildS": round(time.perf_counter() - t0, 2)}
    return nodes, segments, stats


# ---------------------------------------------------------------- stations, hubs, cameras, buses


def norm_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("&", "and").replace("st.", "st"))
    s = re.sub(r"\b(underground|station|london|international)\b", "", s)
    return " ".join(s.split())


def load_hotspots(path: Path = HOTSPOTS) -> list[dict]:
    try:
        return json.loads(path.read_text())["hotspots"]
    except (OSError, ValueError, KeyError):
        return []


def recorded_stations(raw: Path, now_ms: int) -> tuple[list[dict], dict[str, dict]]:
    """The newest TfL Crowding rows (live busyness) and each station's newest typical-day profile."""
    live = feeds.latest_records(raw, "crowding", now_ms, k=1, back_min=60)
    rows = live[-1]["payload"] if live and isinstance(live[-1]["payload"], list) else []
    profiles: dict[str, dict] = {}
    for rec in feeds.latest_records(raw, "crowding_profile", now_ms, k=400, back_min=7 * 24 * 60):
        p = rec.get("payload")
        if isinstance(p, dict) and p.get("naptan"):
            profiles[p["naptan"]] = p
    return rows, profiles


def profile_bands(profile: dict | None) -> dict[str, list[float]]:
    """{MON..SUN: 96 quarter-hour values of percentageOfBaseLine}; missing bands are None."""
    out: dict[str, list] = {}
    for d in (profile or {}).get("daysOfWeek") or []:
        vals: list = [None] * 96
        for tb in d.get("timeBands") or []:
            m = re.match(r"(\d\d):(\d\d)", str(tb.get("timeBand", "")))
            v = feeds.num(tb.get("percentageOfBaseLine"))
            if m and v is not None:
                vals[(int(m.group(1)) * 60 + int(m.group(2))) // 15 % 96] = round(v, 3)
        out[str(d.get("dayOfWeek"))] = vals
    return out


def place_stations(rows: list[dict], profiles: dict[str, dict], osm_stations: list[dict], hubs: list[dict],
                   bbox: tuple, origin: tuple[float, float], now_ms: int) -> tuple[list[dict], list[str]]:
    by_name: dict[str, dict] = {}
    for s in sorted(osm_stations, key=lambda s: s.get("kind") != "subway"):
        by_name.setdefault(norm_name(s["name"]), s)
    hub_at = {h["naptan"]: (h["lat"], h["lon"]) for h in hubs if h.get("naptan")}
    out, notes = [], []
    for r in rows:
        nap, name = r.get("naptan"), r.get("name") or ""
        osm = by_name.get(norm_name(name))
        ll = (osm["lat"], osm["lon"]) if osm else hub_at.get(nap)
        if ll is None:
            notes.append(f"{name} ({nap}): no position in the cached OSM tiles or hotspots; left out")
            continue
        if not inside(ll[0], ll[1], bbox):
            continue
        x, z = to_local(ll[0], ll[1], origin)
        live = feeds.num(r.get("percentageOfBaseline")) if r.get("dataAvailable") else None
        try:
            live_at = feeds.parse_ts(r["timeUtc"]) if r.get("timeUtc") else now_ms
        except ValueError:
            live_at = now_ms
        out.append({"naptan": nap, "name": name, "lat": ll[0], "lon": ll[1], "x": round(x, 1), "z": round(z, 1),
                    "live": round(live, 4) if live is not None else None, "liveAt": live_at,
                    "profile": profile_bands(profiles.get(nap))})
    return out, notes


def recorded_cams(raw: Path, now_ms: int) -> tuple[list[dict], dict[str, dict]]:
    rows: list[dict] = []
    counts: dict[str, dict] = {}
    for rec in feeds.latest_records(raw, "jamcams", now_ms, k=CAM_HISTORY_MIN, back_min=CAM_HISTORY_MIN):
        rows = rec["payload"] if isinstance(rec.get("payload"), list) else rows
        for cid, r in feeds.jamcam_readings(rec.get("payload")).items():
            counts[cid] = r
    return rows, counts


def pin_cams(rows: list[dict], counts: dict[str, dict], segments: list[dict], bbox: tuple,
             origin: tuple[float, float]) -> tuple[list[dict], int]:
    grid = segment_grid(segments)
    cams, unpinned = [], 0
    for row in rows:
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not inside(lat, lon, bbox):
            continue
        x, z = to_local(lat, lon, origin)
        best = nearest_segment(grid, segments, (x, z), CAM_SNAP_M)
        if best is None:
            unpinned += 1
            continue
        r = counts.get(row["id"])
        cams.append({"id": row["id"], "name": row.get("name"), "lat": lat, "lon": lon, "x": round(x, 1),
                     "z": round(z, 1), "segment": best, "people": r["people"] if r else 0,
                     "vehicles": r["vehicles"] if r else 0, "at": r["at"] if r else None, "counted": bool(r)})
    return cams, unpinned


def segment_grid(segments: list[dict], cell: float = 100.0) -> dict:
    grid: dict[tuple[int, int], list[int]] = {}
    for i, s in enumerate(segments):
        xs = [p[0] for p in s["line"]]
        zs = [p[1] for p in s["line"]]
        for cx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
            for cz in range(int(min(zs) // cell), int(max(zs) // cell) + 1):
                grid.setdefault((cx, cz), []).append(i)
    return {"cell": cell, "cells": grid}


def nearest_segment(grid: dict, segments: list[dict], p: tuple[float, float], limit: float) -> str | None:
    cell = grid["cell"]
    reach = int(limit // cell) + 1
    cx, cz = int(p[0] // cell), int(p[1] // cell)
    cands = {i for dx in range(-reach, reach + 1) for dz in range(-reach, reach + 1)
             for i in grid["cells"].get((cx + dx, cz + dz), ())}
    best = min(((line_dist(p, segments[i]["line"]), i) for i in cands), default=None)
    return segments[best[1]]["id"] if best and best[0] <= limit else None


def bus_stops(hubs: list[dict], origin: tuple[float, float], bbox: tuple) -> dict[str, dict]:
    """Stops with a position: a cached twin bus-stop list (seeds/*-field.json) first, then each hub's listed
    stops at the hub itself."""
    out: dict[str, dict] = {}
    for path in sorted(SEEDS.glob("*-field.json")):
        try:
            stops = (json.loads(path.read_text()).get("buses") or {}).get("stops", [])
        except (OSError, ValueError):
            continue
        for s in stops:
            if s.get("id") and inside(s["lat"], s["lon"], bbox):
                out[s["id"]] = {"naptanId": s["id"], "stop": s.get("name"), "indicator": s.get("indicator"),
                                "lat": s["lat"], "lon": s["lon"], "placed": "stop"}
    for h in hubs:
        for nap in h.get("busStops", []):
            out.setdefault(nap, {"naptanId": nap, "stop": f"{h['name']} stop", "indicator": None,
                                 "lat": h["lat"], "lon": h["lon"], "placed": f"hub {h['id']}"})
    for s in out.values():
        x, z = to_local(s["lat"], s["lon"], origin)
        s["x"], s["z"] = round(x, 1), round(z, 1)
    return out


def bus_arrivals(raw: Path, stops: dict[str, dict], now_ms: int) -> list[dict]:
    recs = feeds.latest_records(raw, "bus", now_ms, k=1, back_min=10)
    rows = recs[-1]["payload"] if recs and isinstance(recs[-1]["payload"], list) else []
    ts = recs[-1]["tsMs"] if recs else now_ms
    arrivals: dict[str, list] = {}
    lines: dict[str, set] = {}
    for r in rows:
        nap = r.get("naptanId") if isinstance(r, dict) else None
        if nap not in stops:
            continue
        try:
            sec = round((feeds.parse_ts(r["expectedArrival"]) - now_ms) / 1000)
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= sec <= BUS_HORIZON_S:
            arrivals.setdefault(nap, []).append(sec)
            lines.setdefault(nap, set()).add(str(r.get("lineName")))
    return [dict(stops[n], arrivalsSec=sorted(arrivals.get(n, [])), lines=sorted(lines.get(n, set())), snapshotAt=ts)
            for n in sorted(stops)]


# ---------------------------------------------------------------- zones, hubs, destinations


def assign_zones(segments: list[dict], stations: list[dict], cams: list[dict]) -> list[dict]:
    """One zone per station; every street belongs to the zone of its nearest station."""
    zones = [{"id": f"z{i}", "name": s["name"], "naptan": s["naptan"], "x": s["x"], "z": s["z"], "kind": "station",
              "weight": 1.0, "people": 0.0, "segmentIds": [], "camIds": [], "hub": None}
             for i, s in enumerate(stations)]
    for s in segments:
        mx = sum(p[0] for p in s["line"]) / len(s["line"])
        mz = sum(p[1] for p in s["line"]) / len(s["line"])
        z = min(zones, key=lambda z: (z["x"] - mx) ** 2 + (z["z"] - mz) ** 2)
        s["zoneId"] = z["id"]
        z["segmentIds"].append(s["id"])
    seg_zone = {s["id"]: s["zoneId"] for s in segments}
    by_id = {z["id"]: z for z in zones}
    for c in cams:
        by_id[seg_zone[c["segment"]]]["camIds"].append(c["id"])
    return zones


def assign_hubs(hubs_raw: list[dict], zones: list[dict], segments: list[dict], bbox: tuple,
                origin: tuple[float, float]) -> tuple[list[dict], int]:
    hubs = []
    for h in hubs_raw:
        if not inside(h["lat"], h["lon"], bbox):
            continue
        x, z = to_local(h["lat"], h["lon"], origin)
        hubs.append({"id": h["id"], "name": h["name"], "naptan": h.get("naptan"), "lat": h["lat"], "lon": h["lon"],
                     "x": round(x, 1), "z": round(z, 1), "cams": h.get("cams", []), "busStops": h.get("busStops", []),
                     "zoneIds": []})
    if not hubs:
        return [], 0
    for z in zones:
        own = next((h for h in hubs if h["naptan"] == z["naptan"]), None)
        h = own or min(hubs, key=lambda h: (h["x"] - z["x"]) ** 2 + (h["z"] - z["z"]) ** 2)
        z["hub"] = h["id"]
        h["zoneIds"].append(z["id"])
    zone_hub = {z["id"]: z["hub"] for z in zones}
    node_hubs: dict[int, set] = {}
    for s in segments:
        s["hub"] = zone_hub[s["zoneId"]]
        for n in (s["u"], s["v"]):
            node_hubs.setdefault(n, set()).add(s["hub"])
    boundary = 0
    for s in segments:
        s["boundary"] = len(node_hubs[s["u"]] | node_hubs[s["v"]]) > 1
        boundary += s["boundary"]
    return hubs, boundary


def poi_anchors(pois: list[dict], stations: list[dict], origin: tuple[float, float]) -> list[dict]:
    """Destinations beyond the stations: POIs pooled on a POI_CELL_M grid, weighted by count (attractions x3),
    the top POI_MAX cells at least POI_MIN_GAP_M from a station or a better cell."""
    cells: dict[tuple[int, int], dict] = {}
    for p in pois:
        x, z = to_local(p["lat"], p["lon"], origin)
        c = cells.setdefault((int(x // POI_CELL_M), int(z // POI_CELL_M)), {"w": 0.0, "xs": [], "zs": [], "names": {}})
        w = POI_WEIGHTS[p["kind"]]
        c["w"] += w
        c["xs"].append(x)
        c["zs"].append(z)
        c["names"][p["name"]] = c["names"].get(p["name"], 0) + w
    out: list[dict] = []
    top = max((c["w"] for c in cells.values()), default=1.0)
    for c in sorted(cells.values(), key=lambda c: -c["w"]):
        x, z = sum(c["xs"]) / len(c["xs"]), sum(c["zs"]) / len(c["zs"])
        if any(math.hypot(x - o["x"], z - o["z"]) < POI_MIN_GAP_M for o in stations + out):
            continue
        name = max(c["names"], key=c["names"].get)
        out.append({"name": f"{name} area", "x": round(x, 1), "z": round(z, 1),
                    "weight": round(0.3 + 0.7 * c["w"] / top, 3), "kind": "poi"})
        if len(out) >= POI_MAX:
            break
    return out


# ---------------------------------------------------------------- build


def london_time(ms: int) -> dict:
    dt = datetime.fromtimestamp(ms / 1000, LONDON)
    hour = dt.hour + dt.minute / 60
    daypart = ("morning_rush" if 7 <= hour < 10 else "lunch" if 12 <= hour < 14 else "evening_rush"
               if 16.5 <= hour < 19 else "daytime" if 10 <= hour < 16.5 else "night")
    return {"iso": dt.isoformat(timespec="seconds"), "weekday": dt.strftime("%A"), "hour": round(hour, 2),
            "isWeekend": dt.weekday() >= 5, "daypart": daypart}


def build(osm: dict, raw: Path, now_ms: int, bbox: tuple[float, float, float, float], area: str,
          hubs_raw: list[dict] | None = None) -> dict:
    hubs_raw = load_hotspots() if hubs_raw is None else hubs_raw
    origin = snap_origin((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    roads = [r for r in osm["roads"] if any(inside(la, lo, bbox) for la, lo in r["line"])]
    nodes, segments, stats = street_graph(roads, origin)
    if not segments:
        raise ValueError(f"no streets inside bbox {bbox}")
    rows, profiles = recorded_stations(raw, now_ms)
    stations, notes = place_stations(rows, profiles, osm["stations"], hubs_raw, bbox, origin, now_ms)
    if not stations:
        raise ValueError(f"no TfL Crowding station with a position inside bbox {bbox}")
    cam_rows, counts = recorded_cams(raw, now_ms)
    cams, unpinned = pin_cams(cam_rows, counts, segments, bbox, origin)
    zones = assign_zones(segments, stations, cams)
    hubs, boundary = assign_hubs(hubs_raw, zones, segments, bbox, origin)
    for s, z in zip(stations, zones):
        s["zoneId"], s["hub"] = z["id"], z["hub"]
    stops = bus_stops(hubs, origin, bbox)
    buses = bus_arrivals(raw, stops, now_ms)
    anchors = [{"name": s["name"], "x": s["x"], "z": s["z"], "weight": 1.0, "kind": "station", "naptan": s["naptan"]}
               for s in stations] + poi_anchors(osm.get("pois", []), stations, origin)
    rain = feeds.latest_records(raw, "rain_now", now_ms, k=1)
    rain_mm = feeds.rain_mm_h(rain[-1]["payload"]) if rain else None
    key = hashlib.sha1(json.dumps([nodes, [(s["u"], s["v"], s["lengthM"], s["kind"]) for s in segments]])
                       .encode()).hexdigest()[:12]
    counted = sum(c["counted"] for c in cams)
    return {
        "model": "central", "at": now_ms, "source": "real(central: cached OSM tiles + recorder)", "area": area,
        "time": london_time(now_ms), "bbox": list(bbox), "origin": {"lat": origin[0], "lon": origin[1]},
        "graphKey": key, "graph": stats, "weather": {"rainMmH": rain_mm}, "nodes": nodes, "segments": segments,
        "zones": zones, "hubs": hubs, "stations": stations, "cams": cams, "anchors": anchors, "buses": buses,
        "disruptions": [], "interventions": [], "boundarySegments": boundary, "notes": notes,
        "assumptions": [
            f"Streets: OSM major + minor roads inside {bbox} from {len(osm.get('tiles', []))} cached twin tiles, "
            f"merged to {len(segments)} street edges on {len(nodes)} junctions (footpaths dropped).",
            f"{len(stations)} TfL Crowding stations inside the box; each is a zone, {len(hubs)} hub catchments.",
            f"{len(cams)} JamCams pinned to a street within {CAM_SNAP_M:.0f} m ({counted} with a recorded YOLO count, "
            f"{unpinned} too far from any kept street).",
            f"{sum(len(b['arrivalsSec']) for b in buses)} bus arrivals in the next hour at {len(buses)} stops with a "
            "known position.",
        ],
    }


def parse_bbox(s: str) -> tuple[float, float, float, float]:
    parts = [float(v) for v in s.split(",")]
    if len(parts) != 4 or parts[0] >= parts[2] or parts[1] >= parts[3]:
        raise argparse.ArgumentTypeError("bbox is south,west,north,east")
    return parts[0], parts[1], parts[2], parts[3]


def load_osm(offline: bool, bbox: tuple, extra_dirs: list[Path]) -> dict:
    if offline:
        osm = json.loads(gzip.open(OSM_CACHE, "rt").read())
        cb = osm["bbox"]
        if not (cb[0] <= bbox[0] and cb[1] <= bbox[1] and cb[2] >= bbox[2] and cb[3] >= bbox[3]):
            print(f"warning: bbox {bbox} reaches outside the cached streets {cb}", file=sys.stderr)
        return osm
    dirs = tile_dirs() + extra_dirs
    osm = read_tiles(dirs, bbox)
    if not osm["roads"]:
        raise SystemExit(f"no cached twin OSM tiles with streets in {bbox} under {[str(d) for d in dirs]}")
    return osm


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bbox", type=parse_bbox, default=CENTRAL_BBOX, help="south,west,north,east")
    ap.add_argument("--area", default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--offline", action="store_true", help="streets from seeds/central-osm.json.gz")
    ap.add_argument("--tiles", type=Path, action="append", default=[], help="another twin .cache dir")
    ap.add_argument("--at", type=int, default=None, help="seed time, ms (default: now)")
    args = ap.parse_args()
    central = args.bbox == CENTRAL_BBOX
    osm = load_osm(args.offline, args.bbox, args.tiles)
    if central and not args.offline:
        OSM_CACHE.write_bytes(gzip.compress(json.dumps(osm, separators=(",", ":")).encode()))
    raw = feeds.alt_root()
    now = args.at or int(time.time() * 1000)
    seed = build(osm, raw, now, args.bbox, args.area or ("Central London" if central else "bbox"))
    out = args.out or (CENTRAL_SEED if central else SEEDS / "bbox.json")
    out = out if out.is_absolute() else HERE / out
    out.write_text(json.dumps(seed, separators=(",", ":")))
    g = seed["graph"]
    print(f"seed saved: {out.relative_to(feeds.REPO) if out.is_relative_to(feeds.REPO) else out}")
    print(f"bbox {seed['bbox']} | streets: {g['rawPieces']} OSM pieces -> {g['microEdges']} vertex pairs -> "
          f"{g['edges']} edges on {g['nodes']} junctions ({g['buildS']} s)")
    print(f"zones {len(seed['zones'])} | hubs {len(seed['hubs'])} ({seed['boundarySegments']} boundary edges) | "
          f"cams {len(seed['cams'])} ({sum(c['counted'] for c in seed['cams'])} counted) | "
          f"stations {len(seed['stations'])} | bus stops {len(seed['buses'])} | destinations {len(seed['anchors'])}")
    for n in seed["notes"]:
        print(f"  note: {n}")


if __name__ == "__main__":
    main()
