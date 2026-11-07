"""Build seeds/oxford-circus.json: the real West End street graph + the recorder's real JamCams.

    python3 services/multiverse/real_seed.py            # needs a twin (TWIN_URL, default :3060) once
    python3 services/multiverse/real_seed.py --offline  # rebuild from seeds/oxford-circus-field.json

Streets come from the twin's /api/twin/field (OSM roads with inferred pedestrian loads) for Oxford Circus;
the raw field, weather, bus stops and disruptions are cached in seeds/ so the demo never calls the twin or
Overpass again. Cameras are the recorder's own `jamcams` rows (ids exactly as recorded) inside the field's
street box; a camera with a recorded YOLO count pins its street to that count. Zones are curated real places.
The twin projects around its snapped origin (250 m grid), so every local x/z here uses that same origin.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / "predict"))

import feeds  # noqa: E402
import seed as seedmod  # noqa: E402

SEEDS = HERE / "seeds"
SEED_PATH = SEEDS / "oxford-circus.json"
FIELD_CACHE = SEEDS / "oxford-circus-field.json"
TWIN_URL = os.environ.get("TWIN_URL", "http://127.0.0.1:3060")
OXFORD_CIRCUS = (51.5152, -0.1419)
TOWER_HILL = seedmod.TOWER_HILL
ORIGIN_GRID_M = 250.0
# Predict's cost grows fast with the street count (94 s a world on the raw 11k-piece field), so the seed keeps
# roads only (footpaths dropped), within reach of the nearest YOLO-counted cams, merged into street edges.
STREET_RADIUS_M = 1150.0
STREET_KINDS = {"major", "minor"}
MERGE_MAX_M = 250.0
SIMPLIFY_M = 4.0
CAM_HISTORY_MIN = 120

ANCHORS: list[tuple[str, float, float, float, str]] = [
    ("Oxford Circus station", 51.5152, -0.1419, 1.0, "station"),
    ("Regent St / Conduit St", 51.5118, -0.1414, 0.8, "poi"),
    ("Carnaby St", 51.5132, -0.1388, 0.8, "poi"),
    ("John Lewis / Cavendish Sq", 51.5160, -0.1450, 0.6, "poi"),
    ("Bond Street station", 51.5142, -0.1494, 0.9, "station"),
    ("Selfridges / Orchard St", 51.5146, -0.1530, 0.8, "poi"),
    ("Marble Arch", 51.5131, -0.1589, 0.6, "station"),
    ("Oxford St east (Tottenham Ct Rd side)", 51.5163, -0.1352, 0.7, "poi"),
    ("Soho / Golden Sq", 51.5117, -0.1366, 0.6, "poi"),
    ("Piccadilly Circus", 51.5099, -0.1346, 0.9, "station"),
    ("Mayfair / Berkeley Sq", 51.5098, -0.1459, 0.4, "poi"),
    ("Piccadilly / St James's St", 51.5073, -0.1393, 0.5, "poi"),
    ("Green Park station", 51.5067, -0.1428, 0.6, "station"),
    ("Park Lane / Mount St", 51.5095, -0.1540, 0.3, "poi"),
    ("Fitzrovia / Mortimer St", 51.5180, -0.1408, 0.4, "poi"),
    ("Marylebone / Wigmore St", 51.5167, -0.1500, 0.4, "poi"),
]


def snap_origin(lat: float, lon: float) -> tuple[float, float]:
    """The twin's snapOrigin (apps/stream/src/twin/config.ts): a 250 m grid anchored on Tower Hill."""
    d_lat = ORIGIN_GRID_M / seedmod.M_PER_DEG_LAT
    d_lon = ORIGIN_GRID_M / (seedmod.M_PER_DEG_LAT * math.cos(math.radians(TOWER_HILL[0])))
    return (round(TOWER_HILL[0] + round((lat - TOWER_HILL[0]) / d_lat) * d_lat, 6),
            round(TOWER_HILL[1] + round((lon - TOWER_HILL[1]) / d_lon) * d_lon, 6))


def fetch_twin() -> dict:
    seedmod.TWIN_URL = TWIN_URL
    lat, lon = OXFORD_CIRCUS
    field = seedmod.fetch_json(f"/api/twin/field?lat={lat}&lon={lon}", 60.0)
    q = f"lat={lat}&lon={lon}"
    roads = [s for s in field["segments"] if s.get("kind") in STREET_KINDS]
    return {"field": {"at": field["at"], "segments": roads},
            "weather": seedmod.try_fetch(f"/api/around/weather?{q}") or {},
            "buses": seedmod.try_fetch(f"/api/around/buses?{q}") or {},
            "disruptions": seedmod.try_fetch(f"/api/around/disruptions?{q}&radius=1000") or {}}


def recorded_cams(raw_root: Path, now_ms: int) -> tuple[list[dict], dict[str, dict]]:
    """The newest `jamcams` row list, plus each camera's newest YOLO count over the last CAM_HISTORY_MIN."""
    rows: list[dict] = []
    counts: dict[str, dict] = {}
    for rec in feeds.latest_records(raw_root, "jamcams", now_ms, k=CAM_HISTORY_MIN, back_min=CAM_HISTORY_MIN):
        rows = rec["payload"] if isinstance(rec.get("payload"), list) else rows
        for cid, r in feeds.jamcam_readings(rec.get("payload")).items():
            counts[cid] = r
    return rows, counts


def node(p: list[float]) -> tuple[int, int]:
    return (int(round(p[0])), int(round(p[1])))


def merge_chains(segments: list[dict]) -> list[dict]:
    """Join the field's ~22 m pieces into street edges (through nodes with exactly two pieces of the same kind,
    up to MERGE_MAX_M), so Predict routes over ~5x fewer segments. Loads become length-weighted means."""
    ends: dict[tuple[int, int], list[int]] = {}
    for i, s in enumerate(segments):
        ends.setdefault(node(s["line"][0]), []).append(i)
        ends.setdefault(node(s["line"][-1]), []).append(i)
    used: set[int] = set()
    out = []

    def extend(line: list, parts: list[int], forward: bool) -> None:
        while seedmod.polyline_len(line) < MERGE_MAX_M:
            tip = node(line[-1] if forward else line[0])
            nxt = [j for j in ends.get(tip, []) if j not in used and j not in parts]
            if len(ends.get(tip, [])) != 2 or len(nxt) != 1 or segments[nxt[0]]["kind"] != segments[parts[0]]["kind"]:
                return
            j = nxt[0]
            pts = segments[j]["line"] if node(segments[j]["line"][0]) == tip else segments[j]["line"][::-1]
            if forward:
                line.extend(pts[1:])
            else:
                line[:0] = pts[::-1][:-1]
            parts.append(j)
            used.add(j)

    for i, s in enumerate(segments):
        if i in used:
            continue
        used.add(i)
        line, parts = list(s["line"]), [i]
        extend(line, parts, True)
        extend(line, parts, False)
        lengths = [max(segments[j]["lengthM"], 0.1) for j in parts]
        total = sum(lengths)
        mean = lambda key: round(sum(segments[j].get(key, 0.0) * w for j, w in zip(parts, lengths)) / total, 2)
        out.append({"id": f"s{len(out)}", "a": line[0], "b": line[-1], "line": line, "kind": s["kind"],
                    "lengthM": round(seedmod.polyline_len(line), 1), "peds": mean("peds"), "vehicles": mean("vehicles"),
                    "source": "inferred", "confidence": mean("confidence")})
    return out


def simplify(line: list, tol: float = SIMPLIFY_M) -> list:
    """Douglas-Peucker: every polyline vertex is a graph node in Predict, so straight runs keep only their ends."""
    if len(line) < 3:
        return line
    (ax, az), (bx, bz) = line[0], line[-1]
    seg = math.hypot(bx - ax, bz - az) or 1e-9
    far, idx = max(((abs((bx - ax) * (az - p[1]) - (ax - p[0]) * (bz - az)) / seg, i)
                    for i, p in enumerate(line[1:-1], 1)), default=(0.0, 0))
    if far <= tol:
        return [line[0], line[-1]]
    return simplify(line[:idx + 1], tol)[:-1] + simplify(line[idx:], tol)


def largest_component(segments: list[dict]) -> list[dict]:
    """Dropping footpaths leaves islands; a walker routed to an island explores the whole graph and fails."""
    parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(n: tuple[int, int]) -> tuple[int, int]:
        while parent.setdefault(n, n) != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for s in segments:
        pts = [node(p) for p in s["line"]]
        for p in pts[1:]:
            parent[find(p)] = find(pts[0])
    sizes: dict[tuple[int, int], int] = {}
    for s in segments:
        r = find(node(s["line"][0]))
        sizes[r] = sizes.get(r, 0) + 1
    main = max(sizes, key=sizes.get)
    return [s for s in segments if find(node(s["line"][0])) == main]


def prune_dead_ends(segments: list[dict]) -> list[dict]:
    """Drop cul-de-sac pieces (repeatedly): nobody routes through them, and every node costs Dijkstra time."""
    while True:
        deg: dict[tuple[int, int], int] = {}
        for s in segments:
            for p in (s["line"][0], s["line"][-1]):
                deg[node(p)] = deg.get(node(p), 0) + 1
        kept = [s for s in segments if deg[node(s["line"][0])] > 1 and deg[node(s["line"][-1])] > 1]
        if len(kept) == len(segments):
            return kept
        segments = kept


def build(cache: dict, raw_root: Path, now_ms: int) -> dict:
    origin = snap_origin(*OXFORD_CIRCUS)
    oc = seedmod.to_local(*OXFORD_CIRCUS, origin)
    pieces = []
    for seg in cache["field"]["segments"]:
        seg = dict(seg, line=seg.get("line") or [seg["a"], seg["b"]])
        seg["lengthM"] = round(seedmod.polyline_len(seg["line"]), 1)
        if seg["kind"] in STREET_KINDS and seedmod.dist(seedmod.midpoint(seg["line"]), oc) <= STREET_RADIUS_M:
            pieces.append(seg)
    segments = merge_chains(prune_dead_ends(largest_component(pieces)))
    for i, s in enumerate(segments):
        s["id"], s["line"] = f"s{i}", simplify(s["line"])
    xs = [p[0] for s in segments for p in s["line"]]
    zs = [p[1] for s in segments for p in s["line"]]
    rows, counts = recorded_cams(raw_root, now_ms)
    cams = []
    for row in rows:
        x, z = seedmod.to_local(float(row["lat"]), float(row["lon"]), origin)
        if not (min(xs) <= x <= max(xs) and min(zs) <= z <= max(zs)):
            continue
        r = counts.get(row["id"])
        cams.append({"id": row["id"], "name": row.get("name"), "x": round(x, 1), "z": round(z, 1),
                     "lat": row["lat"], "lon": row["lon"], "people": r["people"] if r else 0,
                     "vehicles": r["vehicles"] if r else 0, "at": r["at"] if r else None, "counted": bool(r)})
    seedmod.attach_cam_loads(segments, [c for c in cams if c["counted"]])
    seedmod.attach_cam_ids(segments, cams)
    pinned = {s["camId"] for s in segments if s.get("camId")}
    cams = [c for c in cams if c["id"] in pinned]
    anchors = [{"name": n, "x": round(seedmod.to_local(la, lo, origin)[0], 1),
                "z": round(seedmod.to_local(la, lo, origin)[1], 1), "weight": w, "kind": k}
               for n, la, lo, w, k in ANCHORS]
    zones = seedmod.build_zones({"segments": segments, "cams": cams}, anchors)
    buses = []
    for s in (cache.get("buses") or {}).get("stops", []):
        x, z = seedmod.to_local(s["lat"], s["lon"], origin)
        arrivals = sorted(a.get("seconds", 0) for a in s.get("arrivals", []))
        buses.append({"stop": s["name"], "naptanId": s.get("id"), "indicator": s.get("indicator"),
                      "x": round(x, 1), "z": round(z, 1), "arrivalsSec": arrivals,
                      "lines": sorted({a["line"] for a in s.get("arrivals", [])})})
    counted = sum(c["counted"] for c in cams)
    return {
        "at": now_ms, "source": "real(twin field + recorder jamcams)", "time": seedmod.london_time(now_ms / 1000),
        "weather": cache.get("weather") or {}, "origin": {"lat": origin[0], "lon": origin[1]},
        "area": "Oxford Circus", "disruptions": seedmod.compact_disruptions(cache.get("disruptions"), origin),
        "buses": buses, "zones": zones, "segments": segments, "anchors": anchors, "fieldPoiCount": 0,
        "cams": cams, "interventions": [],
        "assumptions": [
            f"Real OSM streets within {STREET_RADIUS_M:.0f} m of Oxford Circus ({len(segments)} segments, "
            "pedestrian loads inferred by the twin).",
            f"{len(cams)} real TfL JamCams in the street box; {counted} carry a recorded YOLO count, the rest "
            "start uncounted and are scored only once the recorder sees a count.",
            f"A JamCam frame is taken to cover ~{seedmod.CAM_VIEW_M:.0f} m of street.",
        ],
    }


def main() -> None:
    SEEDS.mkdir(exist_ok=True)
    if "--offline" in sys.argv:
        cache = json.loads(FIELD_CACHE.read_text())
    else:
        cache = fetch_twin()
        FIELD_CACHE.write_text(json.dumps(cache, separators=(",", ":")))
    raw_root = feeds.alt_root()
    now_ms = int(time.time() * 1000)
    seed = build(cache, raw_root, now_ms)
    SEED_PATH.write_text(json.dumps(seed, separators=(",", ":")))
    ids = {c["id"] for c in seed["cams"]}
    counted, fresh = set(), set()
    for ts, payload in feeds.records_between(raw_root, "jamcams", now_ms - 3_600_000, now_ms):
        counted |= ids & set(feeds.jamcam_readings(payload))
        fresh |= set(feeds.fresh_people(payload, ts, ids))
    print(f"seed saved: {SEED_PATH.relative_to(feeds.REPO)}")
    print(f"zones {len(seed['zones'])} | segments {len(seed['segments'])} | cams {len(seed['cams'])} | "
          f"bus stops {len(seed['buses'])}")
    print(f"seed cams with a YOLO count in the last hour: {len(counted)}/{len(ids)} "
          f"({', '.join(sorted(c['name'] for c in seed['cams'] if c['id'] in counted))}); "
          f"fresh (<= {feeds.READING_MAX_AGE_MIN} min old) at some minute: {len(fresh)}")


if __name__ == "__main__":
    main()
