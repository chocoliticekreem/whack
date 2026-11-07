"""City-scale settle series built from the recorder, one function per candidate. Stdlib only.

Every function returns a sorted list of (ts_ms, value) at the feed's own cadence. `typical()` gives the
TfL Crowding typical-Saturday value for a station at a time (15-min band).
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from load import LONDON, MIN, load

HUBS = {  # the five city hotspots (services/city/hotspots.json)
    "Oxford Circus": "940GZZLUOXC", "King's Cross": "940GZZLUKSX", "Piccadilly Circus": "940GZZLUPCC",
    "Waterloo": "940GZZLUWLO", "Bank": "940GZZLUBNK",
}
GOOD = 10
CAM_MAX_AGE_MS = 10 * MIN


@lru_cache(maxsize=None)
def raw(source: str):
    return load(source)


@lru_cache(maxsize=None)
def profile() -> dict[str, dict[str, float]]:
    """naptan -> {"HH:MM": typical % of baseline} for Saturday."""
    out: dict[str, dict[str, float]] = {}
    for _, p in raw("crowding_profile"):
        for day in p.get("daysOfWeek") or []:
            if day.get("dayOfWeek") == "SAT":
                out[p["naptan"]] = {b["timeBand"][:5]: b["percentageOfBaseLine"] for b in day["timeBands"]}
    return out


def band(ts: int) -> str:
    d = datetime.fromtimestamp(ts / 1000, LONDON)
    return f"{d.hour:02d}:{d.minute - d.minute % 15:02d}"


def typical(naptan: str, ts: int) -> float | None:
    return profile().get(naptan, {}).get(band(ts))


def crowding_snaps() -> list[tuple[int, dict[str, float]]]:
    out = []
    for ts, p in raw("crowding"):
        snap = {r["naptan"]: r["percentageOfBaseline"] for r in p
                if r.get("dataAvailable") and isinstance(r.get("percentageOfBaseline"), (int, float))
                and r["percentageOfBaseline"] > 0}
        if snap:
            out.append((ts, snap))
    return out


def crowd_index() -> list[tuple[int, float]]:
    """London Crowd Index: sum of live % over the 20 stations / sum of their typical-Saturday %, x100.
    100 = a normal Saturday at this time."""
    out = []
    for ts, snap in crowding_snaps():
        live = typ = 0.0
        for nap, v in snap.items():
            t = typical(nap, ts)
            if t:
                live += v
                typ += t
        if typ:
            out.append((ts, round(100 * live / typ, 1)))
    return out


def crowd_raw() -> list[tuple[int, float]]:
    """Mean live % of baseline over the stations reporting, x100 (no typical adjustment)."""
    return [(ts, round(100 * sum(s.values()) / len(s), 2)) for ts, s in crowding_snaps() if len(s) >= 18]


def crowd_index_typical_slope(ts: int, ahead_min: int) -> float:
    """How the typical Saturday moves between now and now+ahead (the 'today vs typical Saturday' chart)."""
    a = sum(v for nap in profile() if (v := typical(nap, ts)))
    b = sum(v for nap in profile() if (v := typical(nap, ts + ahead_min * MIN)))
    return b - a


def hub_ratio() -> list[tuple[int, dict[str, float]]]:
    """Per hub: live % / typical-Saturday %, i.e. how far above its usual Saturday."""
    out = []
    for ts, snap in crowding_snaps():
        row = {}
        for name, nap in HUBS.items():
            t = typical(nap, ts)
            if nap in snap and t:
                row[name] = snap[nap] / t
        if len(row) == len(HUBS):
            out.append((ts, row))
    return out


def hub_raw() -> list[tuple[int, dict[str, float]]]:
    return [(ts, {n: s[nap] for n, nap in HUBS.items() if nap in s}) for ts, s in crowding_snaps()]


def line_snaps(modes: tuple[str, ...] = ("tube",)) -> list[tuple[int, dict[str, tuple[int, str]]]]:
    out = []
    for ts, p in raw("line_status"):
        snap = {}
        for line in p:
            if line.get("mode") not in modes:
                continue
            sts = [s for s in line.get("statuses") or [] if isinstance(s.get("statusSeverity"), int)]
            if sts:
                w = min(sts, key=lambda s: s["statusSeverity"])
                snap[line["id"]] = (w["statusSeverity"], w.get("reason") or "")
        if snap:
            out.append((ts, snap))
    return out


def lines_disrupted(modes: tuple[str, ...] = ("tube",)) -> list[tuple[int, int]]:
    return [(ts, sum(1 for sev, _ in s.values() if sev != GOOD)) for ts, s in line_snaps(modes)]


def cams_total(min_cams: int = 12) -> list[tuple[int, int]]:
    """Sum of fresh (<=10 min old) YOLO people counts over every camera with one; needs >= min_cams."""
    out = []
    for ts, p in raw("jamcams"):
        fresh = [c["yolo"]["people"] for c in p if c.get("yolo") and isinstance(c["yolo"].get("people"), (int, float))
                 and ts - c["yolo"].get("at", 0) <= CAM_MAX_AGE_MS]
        if len(fresh) >= min_cams:
            out.append((ts, int(sum(fresh))))
    return out


def bikes_docked() -> list[tuple[int, int]]:
    out = []
    for ts, p in raw("bikepoint"):
        try:
            out.append((ts, sum(int(r["NbBikes"]) for r in p)))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def holding() -> list[tuple[int, int]]:
    return [(ts, p["summary"]["holding_total"]) for ts, p in raw("aircraft")
            if isinstance(p, dict) and isinstance((p.get("summary") or {}).get("holding_total"), int)]


def airborne_low() -> list[tuple[int, int]]:
    return [(ts, p["summary"]["below_8000ft"]) for ts, p in raw("aircraft")
            if isinstance(p, dict) and isinstance((p.get("summary") or {}).get("below_8000ft"), int)]


def grid_demand() -> list[tuple[int, int]]:
    return [(ts, p["demand"]["demand"]) for ts, p in raw("grid") if isinstance(p, dict) and p.get("demand")]


def rain_mm() -> list[tuple[int, float]]:
    return [(ts, p["current"]["precipitation"]) for ts, p in raw("rain_now") if isinstance(p, dict)]


def road_serious() -> list[tuple[int, int]]:
    return [(ts, sum(1 for r in p if r.get("statusSeverity") not in ("Good",))) for ts, p in raw("road_status")]
