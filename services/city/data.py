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
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / "multiverse"))
import feeds  # noqa: E402  (read-only reuse: parse_ts, alt_root)

LONDON = ZoneInfo("Europe/London")
SOURCES = ("crowding", "line_status", "jamcams", "rain_now", "bus", "aircraft", "scrape_fixtures", "vix")
# TfL Crowding and Open-Meteo publish every 5 min, so one missed poll (not every poll gap) halts them. Fixtures are
# scraped every 30 min. `vix` is not a recorder source: the server polls the multiverse engine for it (live only).
STALE_S = {"crowding": 360, "rain_now": 360, "line_status": 180, "jamcams": 180, "bus": 180, "aircraft": 180,
           "scrape_fixtures": 3600, "vix": 180}
BUS_DUE_S = 300
MULTIVERSE_URL = os.environ.get("MULTIVERSE_URL", "http://127.0.0.1:8349").rstrip("/")
SIM_URL = os.environ.get("SIM_URL", "http://127.0.0.1:8449").rstrip("/")
SIM_POLL_S = 30
SIM_MAX_AGE_MS = 3 * 60_000
SIM_HORIZONS = (5, 15, 60)
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


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
                           "name": line.get("name") or line["id"], "mode": line.get("mode") or "",
                           "reason": " ".join((worst.get("reason") or "").split())}
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


def reduce_aircraft(payload: Any, ctx: dict) -> dict | None:
    summary = payload.get("summary") if isinstance(payload, dict) else None
    total = num((summary or {}).get("holding_total"))
    if total is None:
        return None
    stacks = {k: int(v) for k, v in (summary.get("holding") or {}).items() if num(v) is not None}
    return {"total": int(total), "stacks": stacks}


def reduce_fixtures(payload: Any, ctx: dict) -> dict | None:
    keys = ("home", "away", "status", "home_score", "away_score", "kickoff_utc", "egress_est_utc", "venue",
            "stations")
    rows = [{k: e.get(k) for k in keys} for e in payload if isinstance(e, dict)] if isinstance(payload, list) else []
    return {"fixtures": rows}


REDUCERS: dict[str, Callable[[Any, dict], dict | None]] = {
    "crowding": reduce_crowding, "line_status": reduce_lines, "jamcams": reduce_jamcams,
    "rain_now": reduce_rain, "bus": reduce_bus, "aircraft": reduce_aircraft, "scrape_fixtures": reduce_fixtures,
}


def parse_profile(payload: Any) -> tuple[str, dict[str, dict[str, float]]] | None:
    """TfL crowding_profile -> (naptan, {weekday: {"HH:MM" band start: typical fraction of baseline}})."""
    if not isinstance(payload, dict) or not payload.get("naptan"):
        return None
    days = {}
    for day in payload.get("daysOfWeek") or []:
        bands = {b["timeBand"][:5]: v for b in day.get("timeBands") or []
                 if b.get("timeBand") and (v := num(b.get("percentageOfBaseLine"))) is not None}
        if day.get("dayOfWeek") and bands:
            days[day["dayOfWeek"]] = bands
    return payload["naptan"], days


def band_of(ms: int) -> tuple[str, str]:
    """(weekday code, "HH:MM" start of the 15-min TfL band) for a timestamp, Europe/London."""
    d = datetime.fromtimestamp(ms / 1000, LONDON)
    return WEEKDAYS[d.weekday()], f"{d.hour:02d}:{d.minute - d.minute % 15:02d}"


def day_name(ms: int) -> str:
    return DAY_NAMES[datetime.fromtimestamp(ms / 1000, LONDON).weekday()]


def fetch_vix(url: str = MULTIVERSE_URL, timeout: float = 2.0) -> dict | None:
    """GET <multiverse>/vix: {vix, vix_history [{ts, vix, delta, driver, driverDelta, spike}], vix_driver}.
    None when the engine is down or has not spawned yet (it answers 404 before the first /spawn)."""
    try:
        with urllib.request.urlopen(f"{url}/vix", timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("vix_history"), list) else None


def reduce_sim(state: Any) -> dict | None:
    """Central-London agent simulation GET /state -> {ts, base, area, vix, stations {naptan: {name, hub, h: {mean,
    p10, p90}}}} in % of usual (TfL typical day). `base` is the sim's own now; horizon h means base + h min. None
    when the state has no usable station forecast."""
    if not isinstance(state, dict) or not isinstance(state.get("forecast"), dict):
        return None
    seed_at, minute = num(state.get("seedAt")), num(state.get("minute"))
    if seed_at is None or minute is None:
        return None
    base = int(seed_at + minute * 60_000)
    stations = {}
    for row in state["forecast"].get("stations") or []:
        hz = {}
        for h in SIM_HORIZONS:
            f = row.get(f"+{h}") or {}
            m, lo, hi = num(f.get("mean")), num(f.get("p10")), num(f.get("p90"))
            if m is not None and lo is not None and hi is not None and m > 0 and hi >= lo:
                hz[str(h)] = {"mean": m, "p10": lo, "p90": hi}
        if row.get("naptan") and hz:
            stations[row["naptan"]] = {"name": row.get("name") or row["naptan"], "hub": row.get("hub"), "h": hz}
    if not stations:
        return None
    return {"ts": int(num(state.get("liveTs")) or base), "base": base, "area": state.get("area") or "",
            "vix": num(state.get("vix")), "stations": stations}


def fetch_sim(url: str = SIM_URL, timeout: float = 3.0) -> dict | None:
    """GET <sim>/state, reduced by reduce_sim. None on any failure (down, timeout, bad JSON, no forecast)."""
    try:
        with urllib.request.urlopen(f"{url}/state", timeout=timeout) as resp:
            return reduce_sim(json.loads(resp.read()))
    except (OSError, ValueError, urllib.error.URLError):
        return None


class SimFeed:
    """The last good simulation forecast: `now(ms)` returns it while it is at most 3 min old, else None."""

    def __init__(self, fetch: Callable[[], dict | None] = fetch_sim):
        self.fetch = fetch
        self.last: dict | None = None

    def poll(self) -> bool:
        got = self.fetch()
        if got is not None:
            self.last = got
        return got is not None

    def now(self, now_ms: int) -> dict | None:
        if self.last is None or not 0 <= now_ms - self.last["ts"] <= SIM_MAX_AGE_MS:
            return None
        return self.last

    def why_none(self, now_ms: int) -> str:
        if self.last is None:
            return f"the city simulation at {SIM_URL} has not answered"
        return (f"the city simulation's last forecast is {(now_ms - self.last['ts']) / 60_000:.0f} min old "
                f"(over 3 min is stale)")


class Store:
    """Per source: a sorted list of (ts_ms, snapshot). Hour files are parsed once; the open hour is re-read
    from where it left off when its mtime changes."""

    def __init__(self, root: Path, hotspots: list[dict]):
        self.root = root
        self.ctx: dict = {"stop_to_spot": {s: h["id"] for h in hotspots for s in h["busStops"]}}
        self.ts: dict[str, list[int]] = {s: [] for s in SOURCES}
        self.snap: dict[str, list[dict]] = {s: [] for s in SOURCES}
        self.files: dict[Path, tuple[float, int]] = {}
        self.profile: dict[str, dict[str, dict[str, float]]] = {}
        self.profile_file: Path | None = None
        self.sim_feed: SimFeed | None = None

    def sim(self, now: int) -> dict | None:
        """The fresh central-London simulation forecast (live mode only), else None."""
        return self.sim_feed.now(now) if self.sim_feed else None

    def sim_why_none(self, now: int) -> str:
        return self.sim_feed.why_none(now) if self.sim_feed else "the city simulation is only read in live mode"

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
        for source in REDUCERS:
            for path in self.hour_paths(source, start_ms, end_ms):
                self.read_file(source, path)
        self.load_profile()

    def load_profile(self) -> None:
        """The newest recorded crowding_profile file (TfL's typical day per station; the recorder takes it once)."""
        files = sorted(self.root.glob("crowding_profile/*/*.ndjson*"))
        if not files or files[-1] == self.profile_file:
            return
        out = {}
        opener = gzip.open if files[-1].suffix == ".gz" else open
        try:
            with opener(files[-1], "rt") as f:
                for line in f:
                    try:
                        got = parse_profile(json.loads(line).get("payload"))
                    except (ValueError, AttributeError):
                        continue
                    if got:
                        out[got[0]] = got[1]
        except (OSError, EOFError):
            return
        if out:
            self.profile, self.profile_file = out, files[-1]

    def typical(self, naptan: str, ms: int) -> float | None:
        """TfL's typical fraction of baseline for this station, weekday and 15-min band (None if unknown or 0)."""
        day, band = band_of(ms)
        return self.profile.get(naptan, {}).get(day, {}).get(band) or None

    def ingest_vix(self, payload: dict) -> int:
        """Add the multiverse's VIX history (one point per frame); each point carries the engine's spawn time."""
        hist = [e for e in payload.get("vix_history") or []
                if isinstance(e, dict) and num(e.get("ts")) and num(e.get("vix")) is not None]
        spawns = [int(e["ts"]) for e in hist if e.get("driver") == "spawn"]
        spawn = max(spawns) if spawns else None
        for e in hist:
            self.add("vix", int(e["ts"]), {"vix": float(e["vix"]), "driver": e.get("driver") or "",
                                           "driverDelta": num(e.get("driverDelta")) or 0.0, "spawnTs": spawn,
                                           "area": payload.get("area")})
        return len(hist)

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
