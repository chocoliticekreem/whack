"""Recorded TfL / Open-Meteo snapshots, reduced to the few numbers the city markets read.

Recorder layout: <raw>/<source>/YYYY-MM-DD/HH.ndjson(.gz), one {"ts","source","payload"} per line.
Every query takes `now` and never returns a record stamped after it, so replay cannot peek ahead.
"""
from __future__ import annotations

import bisect
import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / "multiverse"))
import feeds  # noqa: E402  (read-only reuse: parse_ts, alt_root)

LONDON = ZoneInfo("Europe/London")
SOURCES = ("crowding", "line_status", "jamcams", "rain_now", "bus")
# TfL Crowding and Open-Meteo publish every 5 min, so one missed poll (not every poll gap) halts them.
STALE_S = {"crowding": 360, "rain_now": 360, "line_status": 180, "jamcams": 180, "bus": 180}
BUS_DUE_S = 300


def load_hotspots(path: Path = HERE / "hotspots.json") -> list[dict]:
    return json.loads(path.read_text())["hotspots"]


def raw_root() -> Path:
    env = os.environ.get("CITY_RAW")
    return Path(env) if env else feeds.alt_root()


def local_time(ms: int, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ms / 1000, LONDON).strftime(fmt)


def num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ---------------------------------------------------------------- reducers: payload -> compact snapshot


def reduce_crowding(payload: Any, ctx: dict) -> dict | None:
    out = {}
    for row in payload if isinstance(payload, list) else []:
        pct = num(row.get("percentageOfBaseline"))
        if row.get("dataAvailable") and pct is not None and pct > 0:
            out[row["naptan"]] = pct
    return out or None


def reduce_lines(payload: Any, ctx: dict) -> dict | None:
    out = {}
    for line in payload if isinstance(payload, list) else []:
        statuses = [s for s in line.get("statuses") or [] if num(s.get("statusSeverity")) is not None]
        if not statuses:
            continue
        worst = min(statuses, key=lambda s: s["statusSeverity"])
        out[line["id"]] = {"sev": int(worst["statusSeverity"]), "desc": worst.get("statusSeverityDescription") or "",
                           "name": line.get("name") or line["id"], "mode": line.get("mode") or ""}
    return out or None


def reduce_jamcams(payload: Any, ctx: dict) -> dict | None:
    people, names = {}, ctx.setdefault("cam_names", {})
    for cam in payload if isinstance(payload, list) else []:
        names[cam["id"]] = cam.get("name") or cam["id"]
        n = num((cam.get("yolo") or {}).get("people"))
        if n is not None:
            people[cam["id"]] = n
    return people


def reduce_rain(payload: Any, ctx: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    cur = payload.get("current") or {}
    mm = num(cur.get("precipitation"))
    if mm is None:
        return None
    fc = []
    for step in payload.get("minutely_15") or []:
        v = num(step.get("precipitation"))
        if v is not None and step.get("time"):
            fc.append((open_meteo_ms(step["time"]), v))
    return {"mm": mm, "slot": open_meteo_ms(cur["time"]) if cur.get("time") else None, "fc": fc}


def open_meteo_ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=LONDON).timestamp() * 1000)


def reduce_bus(payload: Any, ctx: dict) -> dict | None:
    stop_to_spot = ctx["stop_to_spot"]
    out = {h: 0 for h in set(stop_to_spot.values())}
    for row in payload if isinstance(payload, list) else []:
        spot = stop_to_spot.get(row.get("naptanId"))
        tts = num(row.get("timeToStation"))
        if spot and tts is not None and tts <= BUS_DUE_S:
            out[spot] += 1
    return out


REDUCERS: dict[str, Callable[[Any, dict], dict | None]] = {
    "crowding": reduce_crowding, "line_status": reduce_lines, "jamcams": reduce_jamcams,
    "rain_now": reduce_rain, "bus": reduce_bus,
}


class Store:
    """Per source: a sorted list of (ts_ms, snapshot). Hour files are parsed once; the open hour is re-read
    from where it left off when its mtime changes."""

    def __init__(self, root: Path, hotspots: list[dict]):
        self.root = root
        self.ctx: dict = {"stop_to_spot": {s: h["id"] for h in hotspots for s in h["busStops"]}}
        self.ts: dict[str, list[int]] = {s: [] for s in SOURCES}
        self.snap: dict[str, list[dict]] = {s: [] for s in SOURCES}
        self.files: dict[Path, tuple[float, int]] = {}

    @property
    def cam_names(self) -> dict[str, str]:
        return self.ctx.get("cam_names", {})

    def add(self, source: str, ts: int, snap: dict) -> None:
        tss = self.ts[source]
        i = bisect.bisect_left(tss, ts)
        if i < len(tss) and tss[i] == ts:
            self.snap[source][i] = snap
            return
        tss.insert(i, ts)
        self.snap[source].insert(i, snap)

    def refresh(self, start_ms: int, end_ms: int) -> None:
        for source in SOURCES:
            for path in self.hour_paths(source, start_ms, end_ms):
                self.read_file(source, path)

    def hour_paths(self, source: str, start_ms: int, end_ms: int) -> list[Path]:
        t = datetime.fromtimestamp(start_ms / 1000, timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
        out = []
        while t <= end:
            for ext in (".ndjson.gz", ".ndjson"):
                p = self.root / source / t.strftime("%Y-%m-%d") / f"{t.strftime('%H')}{ext}"
                if p.is_file():
                    out.append(p)
            t += timedelta(hours=1)
        return out

    def read_file(self, source: str, path: Path) -> None:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        seen_mtime, done = self.files.get(path, (None, 0))
        if seen_mtime == mtime:
            return
        n = 0
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt") as f:
                for line in f:
                    if not line.endswith("\n"):
                        break  # half-written last line: read it whole next refresh
                    if not line.strip():
                        continue
                    n += 1
                    if n <= done:
                        continue
                    self.ingest(source, line)
        except (OSError, EOFError):
            pass  # the recorder is mid-write on the open hour; the rest is read next refresh
        self.files[path] = (mtime, n)

    def ingest(self, source: str, line: str) -> None:
        try:
            rec = json.loads(line)
            ts = feeds.parse_ts(rec["ts"])
        except (ValueError, KeyError, TypeError):
            return
        snap = REDUCERS[source](rec.get("payload"), self.ctx)
        if snap is not None:
            self.add(source, ts, snap)

    # ------------------------------------------------------------ queries (all capped at `now`)

    def latest(self, source: str, now: int) -> tuple[int, dict] | None:
        i = bisect.bisect_right(self.ts[source], now)
        return (self.ts[source][i - 1], self.snap[source][i - 1]) if i else None

    def between(self, source: str, start: int, end: int) -> list[tuple[int, dict]]:
        tss = self.ts[source]
        lo, hi = bisect.bisect_left(tss, start), bisect.bisect_right(tss, end)
        return list(zip(tss[lo:hi], self.snap[source][lo:hi]))

    def age_s(self, source: str, now: int) -> float | None:
        last = self.latest(source, now)
        return (now - last[0]) / 1000 if last else None

    def fresh(self, source: str, now: int) -> bool:
        age = self.age_s(source, now)
        return age is not None and age <= STALE_S[source]

    def newest_ts(self, now: int) -> int | None:
        stamps = [x[0] for x in (self.latest(s, now) for s in SOURCES) if x]
        return max(stamps) if stamps else None
