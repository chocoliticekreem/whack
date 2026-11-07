"""Seed the parallel world from the live twin state at any origin (Tower Hill by default).

Prefers GET /api/twin/field (one contract, see README). Falls back to assembling the same shape
from the live routes the twin already exposes. Nothing here is invented: inferred loads are
labelled `source: "inferred"` with a low confidence and listed under `assumptions`.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TWIN_URL = os.environ.get("TWIN_URL", "http://localhost:3047")
TOWER_HILL = (51.5098, -0.0766)
ORIGIN = TOWER_HILL
M_PER_DEG_LAT = 111_320.0
LONDON = ZoneInfo("Europe/London")

# Real places around the origin (lat, lon, weight, kind). Weight = relative pull as a destination.
ANCHORS: list[tuple[str, float, float, float, str]] = [
    ("Tower Hill station", 51.5098, -0.0766, 1.0, "station"),
    ("Tower Gateway DLR", 51.5106, -0.0743, 0.6, "station"),
    ("Fenchurch Street station", 51.5115, -0.0789, 0.9, "station"),
    ("Aldgate station", 51.5143, -0.0755, 0.6, "station"),
    ("Monument station", 51.5105, -0.0860, 0.5, "station"),
    ("Tower of London", 51.5081, -0.0759, 1.0, "poi"),
    ("Tower Bridge", 51.5055, -0.0754, 0.8, "poi"),
    ("St Katharine Docks", 51.5074, -0.0712, 0.5, "poi"),
    ("Trinity Square Gardens", 51.5097, -0.0781, 0.3, "poi"),
    ("All Hallows by the Tower", 51.5094, -0.0794, 0.2, "poi"),
    ("Minories offices", 51.5118, -0.0745, 0.6, "poi"),
    ("Fenchurch Place offices", 51.5112, -0.0803, 0.6, "poi"),
    ("St Dunstan's Hill offices", 51.5102, -0.0830, 0.4, "poi"),
    ("Royal Mint Court", 51.5097, -0.0719, 0.4, "poi"),
    ("Tower Hill Memorial", 51.5100, -0.0773, 0.2, "poi"),
    ("Mansell Street / Goodmans Yard", 51.5112, -0.0730, 0.3, "poi"),
]
ZONE_RADIUS_M = 220.0
ANCHOR_PICK_M = 550.0
ANCHOR_MIN_GAP_M = 120.0
ANCHOR_MIN_WEIGHT = 0.25
CAM_SNAP_M = 80.0
INFER_RADIUS_M = 250.0
# A JamCam frame covers roughly 80 m of street; people-in-frame -> people per 100 m.
CAM_VIEW_M = 80.0


def to_local(lat: float, lon: float, origin: tuple[float, float] = ORIGIN) -> tuple[float, float]:
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(origin[0]))
    return ((lon - origin[1]) * m_per_deg_lon, -(lat - origin[0]) * M_PER_DEG_LAT)


def is_tower_hill(origin: tuple[float, float]) -> bool:
    return dist(to_local(origin[0], origin[1], TOWER_HILL), (0.0, 0.0)) < 150.0


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def fetch_json(path: str, timeout: float = 25.0) -> Any:
    req = urllib.request.Request(TWIN_URL + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def try_fetch(path: str, timeout: float = 25.0) -> Any | None:
    try:
        return fetch_json(path, timeout)
    except Exception:
        return None


def around(path: str, radius: int | None = None, origin: tuple[float, float] = ORIGIN) -> str:
    q = {"lat": origin[0], "lon": origin[1]}
    if radius:
        q["radius"] = radius
    return f"/api/around/{path}?{urllib.parse.urlencode(q)}"


def anchors_local(field: dict | None = None, origin: tuple[float, float] = ORIGIN) -> list[dict]:
    """Tower Hill keeps its curated anchors; any other origin builds zones from the field's own POIs/stations."""
    if field is not None and not is_tower_hill(origin):
        seen: set[str] = set()
        out = []
        near = [a for a in field.get("anchors", []) if a.get("name") and dist((a["x"], a["z"]), (0.0, 0.0)) <= ANCHOR_PICK_M
                and (a.get("kind") == "station" or float(a.get("weight", 0)) >= ANCHOR_MIN_WEIGHT)]
        for a in sorted(near, key=lambda a: (a.get("kind") != "station", -float(a.get("weight", 0)), dist((a["x"], a["z"]), (0.0, 0.0)))):
            if a["name"] in seen or any(dist((a["x"], a["z"]), (o["x"], o["z"])) < ANCHOR_MIN_GAP_M for o in out):
                continue
            seen.add(a["name"])
            out.append({"name": a["name"], "x": round(float(a["x"]), 1), "z": round(float(a["z"]), 1),
                        "weight": max(0.2, float(a.get("weight", 0.3))), "kind": a.get("kind", "poi")})
            if len(out) >= 16:
                break
        if len(out) >= 3:
            return out
    out = []
    for name, lat, lon, weight, kind in ANCHORS:
        x, z = to_local(lat, lon, origin)
        out.append({"name": name, "x": round(x, 1), "z": round(z, 1), "weight": weight, "kind": kind})
    return out


def fetch_field(origin: tuple[float, float] = ORIGIN) -> dict | None:
    data = try_fetch(f"/api/twin/field?lat={origin[0]}&lon={origin[1]}", 30.0)
    if isinstance(data, dict) and isinstance(data.get("segments"), list) and data["segments"]:
        return data
    return None


def fetch_cams_with_counts(origin: tuple[float, float] = ORIGIN) -> list[dict]:
    cams = (try_fetch(around("cams", 700, origin)) or {}).get("cams", [])
    if not cams:
        return []
    ids = ",".join(c["id"] for c in cams)
    readings = (try_fetch(f"/api/around/counts?ids={urllib.parse.quote(ids)}", 60.0) or {}).get("readings", [])
    by_id = {r["camId"]: r for r in readings}
    out = []
    for c in cams:
        r = by_id.get(c["id"])
        if not r:
            continue
        x, z = to_local(c["lat"], c["lon"], origin)
        out.append({"id": c["id"], "name": c.get("name"), "x": round(x, 1), "z": round(z, 1),
                    "people": r.get("people", 0), "vehicles": r.get("vehicles", 0), "at": r.get("at")})
    return out


def midpoint(line: list[list[float]]) -> tuple[float, float]:
    xs = [p[0] for p in line]
    zs = [p[1] for p in line]
    return (sum(xs) / len(xs), sum(zs) / len(zs))


def polyline_len(line: list[list[float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:]))


def fallback_field(cams: list[dict], origin: tuple[float, float] = ORIGIN) -> dict:
    """Build the field contract from OSM roads + camera counts when /api/twin/field is absent."""
    osm = fetch_json(f"/api/twin/osm?lat={origin[0]}&lon={origin[1]}", 60.0)
    segments = []
    for i, road in enumerate(osm.get("roads", [])):
        line = road["line"]
        if len(line) < 2:
            continue
        seg = {"id": f"osm-{i}", "a": line[0], "b": line[-1], "line": line, "kind": road["kind"],
               "lengthM": round(polyline_len(line), 1), "peds": 0.0, "vehicles": 0.0,
               "source": "inferred", "confidence": 0.1}
        segments.append(seg)
    attach_cam_loads(segments, cams)
    return {"at": int(time.time() * 1000), "segments": segments, "anchors": anchors_local(None, origin), "cams": cams}


def attach_cam_loads(segments: list[dict], cams: list[dict]) -> None:
    """Observed cams pin their nearest street; nearby streets inherit a distance-decayed estimate."""
    for cam in cams:
        p = (cam["x"], cam["z"])
        drivable = [s for s in segments if s["kind"] != "rail"]
        best = min(drivable, key=lambda s: dist(midpoint(s["line"]), p), default=None)
        if best and dist(midpoint(best["line"]), p) <= CAM_SNAP_M:
            best["peds"] = cam["people"] * 100.0 / CAM_VIEW_M
            best["vehicles"] = float(cam["vehicles"])
            best["source"], best["confidence"], best["camId"] = "observed", 0.8, cam["id"]
    for seg in segments:
        if seg["source"] == "observed" or seg["kind"] == "rail":
            continue
        m = midpoint(seg["line"])
        near = [(dist((c["x"], c["z"]), m), c) for c in cams]
        near = [(d, c) for d, c in near if d <= INFER_RADIUS_M]
        if not near:
            continue
        w = [(1 - d / INFER_RADIUS_M) for d, _ in near]
        seg["peds"] = round(sum(wi * c["people"] * 100 / CAM_VIEW_M for wi, (_, c) in zip(w, near)) / len(near), 2)
        seg["vehicles"] = round(sum(wi * c["vehicles"] for wi, (_, c) in zip(w, near)) / len(near), 2)
        seg["confidence"] = round(0.3 * max(w), 2)


def attach_cam_ids(segments: list[dict], cams: list[dict]) -> None:
    """Scoring needs each camera pinned to one segment, whichever source built the field."""
    for cam in cams:
        if any(s.get("camId") == cam["id"] for s in segments):
            continue
        p = (cam["x"], cam["z"])
        best = min((s for s in segments if s["kind"] != "rail"), key=lambda s: dist(midpoint(s["line"]), p), default=None)
        if best and dist(midpoint(best["line"]), p) <= CAM_SNAP_M:
            best["camId"] = cam["id"]


def segment_line(seg: dict) -> list[list[float]]:
    return seg.get("line") or [seg["a"], seg["b"]]


def build_zones(field: dict, anchors: list[dict]) -> list[dict]:
    zones = {a["name"]: {"id": f"z{i}", "name": a["name"], "x": a["x"], "z": a["z"], "kind": a["kind"],
                         "weight": a["weight"], "people": 0.0, "vehicles": 0.0, "observedPeople": 0,
                         "camIds": [], "segmentIds": [], "observedShare": 0.0}
             for i, a in enumerate(anchors)}
    for seg in field["segments"]:
        line = segment_line(seg)
        m = midpoint(line)
        a = min(anchors, key=lambda a: dist((a["x"], a["z"]), m))
        if dist((a["x"], a["z"]), m) > ZONE_RADIUS_M:
            continue
        zone = zones[a["name"]]
        length = seg.get("lengthM") or polyline_len(line)
        seg["zoneId"] = zone["id"]
        zone["segmentIds"].append(seg["id"])
        zone["people"] += seg.get("peds", 0) * length / 100.0
        zone["vehicles"] += seg.get("vehicles", 0) * length / 100.0
        if seg.get("source") == "observed":
            zone["observedShare"] += 1
    for cam in field.get("cams", []):
        a = min(anchors, key=lambda a: dist((a["x"], a["z"]), (cam["x"], cam["z"])))
        zones[a["name"]]["camIds"].append(cam["id"])
        zones[a["name"]]["observedPeople"] += cam.get("people", 0)
    out = []
    for z in zones.values():
        n = len(z["segmentIds"])
        z["observedShare"] = round(z["observedShare"] / n, 2) if n else 0.0
        z["people"], z["vehicles"] = round(z["people"], 1), round(z["vehicles"], 1)
        out.append(z)
    return out


def london_time(now: float | None = None) -> dict:
    dt = datetime.fromtimestamp(now or time.time(), LONDON)
    hour = dt.hour + dt.minute / 60
    if 7 <= hour < 10:
        daypart = "morning_rush"
    elif 12 <= hour < 14:
        daypart = "lunch"
    elif 16.5 <= hour < 19:
        daypart = "evening_rush"
    elif 10 <= hour < 16.5:
        daypart = "daytime"
    else:
        daypart = "night"
    return {"iso": dt.isoformat(timespec="seconds"), "weekday": dt.strftime("%A"), "hour": round(hour, 2),
            "isWeekend": dt.weekday() >= 5, "daypart": daypart}


def compact_disruptions(raw: dict | None, origin: tuple[float, float] = ORIGIN) -> list[dict]:
    out = []
    seen = set()
    for d in (raw or {}).get("items", []):
        key = (d.get("street"), d.get("comments"))
        if key in seen:
            continue
        seen.add(key)
        line = d.get("line") or []
        pts = [to_local(lat, lon, origin) for lon, lat in line]
        where = midpoint([list(p) for p in pts]) if pts else None
        out.append({"id": d.get("id"), "street": d.get("street"), "severity": d.get("severity"),
                    "closure": d.get("closure"), "comments": d.get("comments"),
                    "where": {"x": round(where[0], 1), "z": round(where[1], 1)} if where else None,
                    "distanceM": d.get("distanceM")})
    return out


def compact_buses(raw: dict | None, origin: tuple[float, float] = ORIGIN) -> list[dict]:
    out = []
    for s in (raw or {}).get("stops", []):
        x, z = to_local(s["lat"], s["lon"], origin)
        arrivals = sorted(a.get("seconds", 0) for a in s.get("arrivals", []))
        out.append({"stop": s["name"], "indicator": s.get("indicator"), "x": round(x, 1), "z": round(z, 1),
                    "arrivalsSec": arrivals, "lines": sorted({a["line"] for a in s.get("arrivals", [])})})
    return out


def build_seed(interventions: list[dict] | None = None, origin: tuple[float, float] = ORIGIN,
               area: str | None = None) -> dict:
    field = fetch_field(origin)
    source = "field"
    if field is None:
        cams = fetch_cams_with_counts(origin)
        field = fallback_field(cams, origin)
        source = "fallback(osm+jamcams)"
    for seg in field["segments"]:
        seg.setdefault("line", [seg["a"], seg["b"]])
        seg.setdefault("lengthM", round(polyline_len(seg["line"]), 1))
    attach_cam_ids(field["segments"], field.get("cams", []))
    anchors = anchors_local(field, origin)
    zones = build_zones(field, anchors)
    weather = try_fetch(around("weather", None, origin)) or {}
    seed = {
        "at": int(time.time() * 1000), "source": source, "time": london_time(), "weather": weather,
        "origin": {"lat": origin[0], "lon": origin[1]},
        "area": area or ("Tower Hill" if is_tower_hill(origin) else f"{origin[0]:.4f}, {origin[1]:.4f}"),
        "disruptions": compact_disruptions(try_fetch(around("disruptions", 1000, origin)), origin),
        "buses": compact_buses(try_fetch(around("buses", None, origin)), origin),
        "zones": zones, "segments": field["segments"], "anchors": anchors,
        "fieldPoiCount": len(field.get("anchors", [])),
        "cams": field.get("cams", []), "interventions": interventions or [],
        "assumptions": seed_assumptions(field, source, weather),
    }
    return seed


def seed_assumptions(field: dict, source: str, weather: dict) -> list[str]:
    observed = sum(1 for s in field["segments"] if s.get("source") == "observed")
    inferred = sum(1 for s in field["segments"] if s.get("peds", 0) > 0 and s.get("source") != "observed")
    out = [f"Only {observed} street segments carry a camera reading; {inferred} nearby segments inherit a "
           f"distance-decayed estimate (confidence <= 0.3); the rest start at 0 (no observation, not 'empty').",
           f"A JamCam frame is taken to cover ~{CAM_VIEW_M:.0f} m of street when converting people-in-frame "
           "to people per 100 m."]
    if source != "field":
        out.append("Field endpoint unavailable; seed built from OSM roads + TfL JamCam YOLO counts directly.")
    if not weather:
        out.append("Weather feed unavailable; weather reactions disabled.")
    return out


def seed_summary(seed: dict) -> dict:
    zones = [z for z in seed["zones"] if z["people"] > 0 or z["camIds"]]
    return {
        "at": seed["at"], "source": seed["source"], "time": seed["time"], "weather": seed["weather"],
        "area": seed.get("area"), "origin": seed.get("origin"),
        "zones": [{k: z[k] for k in ("id", "name", "kind", "people", "vehicles", "observedPeople", "camIds")}
                  for z in sorted(zones, key=lambda z: -z["people"])],
        "zoneCount": len(seed["zones"]), "segmentCount": len(seed["segments"]),
        "observedSegments": sum(1 for s in seed["segments"] if s.get("source") == "observed"),
        "cams": [{k: c.get(k) for k in ("id", "name", "people", "vehicles", "at")} for c in seed["cams"]],
        "disruptions": seed["disruptions"][:8], "busStops": len(seed["buses"]),
        "busArrivalsNext30": sum(1 for s in seed["buses"] for a in s["arrivalsSec"] if a <= 1800),
        "interventions": seed["interventions"], "assumptions": seed["assumptions"],
    }
