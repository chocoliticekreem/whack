"""Live recorder: polls TfL feeds, JamCam clip metadata and alternative data (aircraft, grid, rain,
GDELT news, Wikipedia) into data/raw/<source>/<day>/<hour>.ndjson.gz.

    python3 recorder/record.py                       # run forever
    python3 recorder/record.py --once                # poll every source once and exit
    python3 recorder/record.py --only aircraft,grid  # just these sources
    python3 recorder/record.py --skip bus,wiki_edits # everything except these
    python3 recorder/record.py --yolo                # also ask a local YOLO server for JamCam counts
"""

import argparse
import hashlib
import json
import logging
import os
import random
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alt  # noqa: E402
from common import (DATA, RAW, ROOT, USER_AGENT, HttpError, get_json, http, iso, load_env, sleep_until,  # noqa: E402
                    utc_now, write_record)

TFL = "https://api.tfl.gov.uk"
JAMCAM_S3 = "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk"
JAMCAM_BOX = (51.495, 51.530, -0.175, -0.100)  # lat_min, lat_max, lon_min, lon_max
CROWDING_STATIONS = [
    "Oxford Circus", "Bond Street", "Tottenham Court Road", "Piccadilly Circus", "Leicester Square",
    "Bank", "Liverpool Street", "King's Cross St. Pancras", "Waterloo", "Victoria", "London Bridge",
    "Westminster", "Green Park", "Holborn", "Euston", "Paddington", "Canary Wharf", "Stratford",
    "Tower Hill", "Marble Arch",
]
NAPTAN_CACHE = DATA / "naptan_cache.json"

# Calls per minute per non-TfL host, shared by every thread.
HOST_CAPS = {"api.adsb.lol": 6, "data.elexon.co.uk": 20, "api.carbonintensity.org.uk": 6,
             "api.open-meteo.com": 6, "data.gdeltproject.org": 10, "wikimedia.org": 60,
             "stream.wikimedia.org": 6}
ELEXON = "https://data.elexon.co.uk/bmrs/api/v1"
CARBON_LONDON = "https://api.carbonintensity.org.uk/regional/regionid/13"
CARBON_EVERY = 1800
RAIN_URL = ("https://api.open-meteo.com/v1/forecast?latitude=51.515&longitude=-0.14"
            "&minutely_15=precipitation,rain&past_minutely_15=4&forecast_minutely_15=8"
            "&current=precipitation,rain,temperature_2m,weather_code,cloud_cover,wind_speed_10m&timezone=UTC")
GDELT_LAST = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_RETRY_FOR = 7200  # the CDN can serve a cached 404 for a just-announced file for up to an hour
WIKI_STREAM = "https://stream.wikimedia.org/v2/stream/recentchange"
WIKI_ONCE_SECONDS = 60
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"
YOLO_BACKOFF_MIN, YOLO_BACKOFF_MAX = 30, 120  # seconds

log = logging.getLogger("recorder")


def redact(msg: str) -> str:
    key = os.environ.get("TFL_APP_KEY")
    return msg.replace(key, "***") if key else msg


class RateLimiter:
    """Sliding-window cap on TfL calls per minute, shared by every thread."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.time()
                while self.calls and now - self.calls[0] >= 60:
                    self.calls.popleft()
                if len(self.calls) < self.per_minute:
                    self.calls.append(now)
                    return
                wait = 60 - (now - self.calls[0]) + 0.05
            time.sleep(wait)


class Tfl:
    def __init__(self):
        self.key = os.environ.get("TFL_APP_KEY") or None
        self.limiter = RateLimiter(300 if self.key else 40)

    def get(self, path: str, timeout: float = 30, tries: int = 4):
        sep = "&" if "?" in path else "?"
        for attempt in range(tries):
            url = f"{TFL}{path}{sep}app_key={self.key}" if self.key else f"{TFL}{path}"
            self.limiter.acquire()
            try:
                return get_json(url, timeout)
            except HttpError as e:
                # TfL answers a bad key with a 429, so without this every call would back off as if rate limited.
                if e.status == 429 and self.key and b"Invalid app_key" in e.body:
                    log.error("TfL rejected TFL_APP_KEY (invalid app_key); continuing without a key at 40 calls/min")
                    self.key, self.limiter.per_minute = None, 40
                    continue
                if e.status != 429 or attempt == tries - 1:
                    raise HttpError(e.status, e.retry_after, f"TfL {path.split('?')[0]}: HTTP {e.status}") from None
                base = e.retry_after if e.retry_after is not None else 5 * 2**attempt
                time.sleep(base + random.uniform(0, base / 2 + 1))


def strip_type(obj):
    if isinstance(obj, dict):
        return {k: strip_type(v) for k, v in obj.items() if k != "$type"}
    if isinstance(obj, list):
        return [strip_type(v) for v in obj]
    return obj


def pick(d: dict, keys: tuple) -> dict:
    return {k: d.get(k) for k in keys}


def props(place: dict) -> dict:
    return {p["key"]: p.get("value") for p in place.get("additionalProperties", [])}


SEED = ROOT / "services" / "multiverse" / "seeds" / "oxford-circus.json"
HOTSPOTS = ROOT / "services" / "city" / "hotspots.json"


def seed_cam_ids(path: Path = SEED, hotspots: Path = HOTSPOTS) -> set[str]:
    """The 2-worker YOLO server can't keep 83 cameras fresh, so only count the cameras the markets read:
    the Oxford Circus multiverse seed plus the city hotspots."""
    ids: set[str] = set()
    try:
        ids |= {c["id"] for c in json.loads(path.read_text())["cams"]}
    except (OSError, KeyError, ValueError):
        pass
    try:
        ids |= {c for h in json.loads(hotspots.read_text())["hotspots"] for c in h["cams"]}
    except (OSError, KeyError, ValueError):
        pass
    return ids


class Recorder:
    def __init__(self, yolo: bool):
        self.tfl = Tfl()
        self.stop = threading.Event()
        self.stats: dict[str, dict] = {}
        self.stats_lock = threading.Lock()
        self.disruption_hash: str | None = None
        self.naptans: dict[str, str] | None = None
        self.profile_day: str | None = None
        self.cams: list[dict] = []
        self.cams_at = 0.0
        self.yolo_url = os.environ.get("JAMCAM_YOLO_URL", "http://127.0.0.1:8047").rstrip("/") if yolo else None
        self.yolo_down = False
        self.yolo_retry_at = 0.0
        self.yolo_backoff = YOLO_BACKOFF_MIN
        self.yolo_ids = seed_cam_ids()
        self.hosts: dict[str, RateLimiter] = {}
        self.hosts_lock = threading.Lock()
        self.carbon_at = 0.0
        self.gdelt_done: deque[str] = deque(maxlen=200)
        self.gdelt_pending: dict[str, float] = {}
        self.wiki_scanned = 0

    # ---- bookkeeping -------------------------------------------------------------------------

    def bump(self, source: str, records: int = 0, nbytes: int = 0, error: bool = False, skipped: bool = False):
        with self.stats_lock:
            s = self.stats.setdefault(source, {"writes": 0, "records": 0, "bytes": 0, "errors": 0, "skipped": 0})
            s["writes"] += 0 if (error or skipped) else 1
            s["records"] += records
            s["bytes"] += nbytes
            s["errors"] += int(error)
            s["skipped"] += int(skipped)

    def emit(self, source: str, ts, payload) -> None:
        n = write_record(source, ts, payload)
        self.bump(source, len(payload) if isinstance(payload, list) else 1, n)

    def status_line(self) -> str:
        with self.stats_lock:
            parts = [f"{k}={v['writes']}w/{v['records']}r" + (f"/{v['errors']}err" if v["errors"] else "")
                     for k, v in sorted(self.stats.items())]
        if self.wiki_scanned:
            parts.append(f"wiki_stream_scanned={self.wiki_scanned}")
        return f"[{iso(utc_now())}] " + " ".join(parts)

    def host_limiter(self, host: str) -> RateLimiter:
        with self.hosts_lock:
            return self.hosts.setdefault(host, RateLimiter(HOST_CAPS.get(host, 10)))

    def polite(self, url: str, timeout: float = 30, tries: int = 3) -> bytes:
        """GET a non-TfL URL under its host's per-minute cap; back off on 429/503."""
        host = urllib.parse.urlsplit(url).hostname or ""
        limiter = self.host_limiter(host)
        for attempt in range(tries):
            limiter.acquire()
            try:
                return http(url, timeout)[2]
            except HttpError as e:
                if e.status not in (429, 503) or attempt == tries - 1:
                    raise HttpError(e.status, e.retry_after, f"{host}: HTTP {e.status}") from None
                base = e.retry_after if e.retry_after is not None else 5 * 2**attempt
                time.sleep(base + random.uniform(0, base / 2 + 1))
        raise AssertionError("unreachable")

    def polite_json(self, url: str, timeout: float = 30):
        return json.loads(self.polite(url, timeout))

    # ---- sources -----------------------------------------------------------------------------

    def poll_bus(self):
        ts = utc_now()
        rows = self.tfl.get("/Mode/bus/Arrivals?count=1", timeout=90)
        keys = ("naptanId", "vehicleId", "lineName", "timeToStation", "expectedArrival")
        self.emit("bus", ts, [pick(r, keys) for r in rows])

    def poll_tube(self):
        ts = utc_now()
        rows = self.tfl.get("/Mode/tube/Arrivals?count=1", timeout=60)
        keys = ("naptanId", "stationName", "vehicleId", "lineId", "platformName", "direction",
                "destinationNaptanId", "timeToStation", "expectedArrival", "currentLocation")
        self.emit("tube", ts, [pick(r, keys) for r in rows])

    def poll_line_status(self):
        ts = utc_now()
        lines = self.tfl.get("/Line/Mode/tube,dlr,overground,elizabeth-line/Status")
        out = [{
            "id": ln.get("id"), "name": ln.get("name"), "mode": ln.get("modeName"),
            "statuses": [pick(s, ("statusSeverity", "statusSeverityDescription", "reason"))
                         for s in ln.get("lineStatuses", [])],
        } for ln in lines]
        self.emit("line_status", ts, out)

    def poll_road_status(self):
        ts = utc_now()
        roads = self.tfl.get("/Road/all/Status")
        keys = ("id", "displayName", "statusSeverity", "statusSeverityDescription", "bounds")
        self.emit("road_status", ts, [pick(r, keys) for r in roads])

    def poll_disruptions(self):
        ts = utc_now()
        start, end = ts.strftime("%Y-%m-%d"), (ts + timedelta(days=1)).strftime("%Y-%m-%d")
        rows = strip_type(self.tfl.get(f"/Road/all/Street/Disruption?startDate={start}&endDate={end}", timeout=60))
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        if digest == self.disruption_hash:
            self.bump("disruptions", skipped=True)
            return
        self.disruption_hash = digest
        self.emit("disruptions", ts, rows)

    def poll_bikepoint(self):
        ts = utc_now()
        out = []
        for bp in self.tfl.get("/BikePoint", timeout=60):
            p = {a["key"]: a for a in bp.get("additionalProperties", [])}
            val = lambda k: p.get(k, {}).get("value")  # noqa: E731
            out.append({
                "id": bp.get("id"), "lat": bp.get("lat"), "lon": bp.get("lon"),
                "NbBikes": val("NbBikes"), "NbEBikes": val("NbEBikes"), "NbEmptyDocks": val("NbEmptyDocks"),
                "modified": p.get("NbBikes", {}).get("modified"),
            })
        self.emit("bikepoint", ts, out)

    def resolve_naptans(self) -> dict[str, str]:
        if self.naptans is not None:
            return self.naptans
        if NAPTAN_CACHE.is_file():
            self.naptans = json.loads(NAPTAN_CACHE.read_text())
            if set(self.naptans) >= set(CROWDING_STATIONS):
                return self.naptans
        found = {}
        for name in CROWDING_STATIONS:
            try:
                found[name] = self._lookup_naptan(name)
            except Exception as e:  # noqa: BLE001 - one bad station must not block the rest
                log.error(redact(f"naptan lookup {name}: {e}"))
        NAPTAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        if len(found) == len(CROWDING_STATIONS):
            NAPTAN_CACHE.write_text(json.dumps(found, indent=1))
        self.naptans = found
        return found

    def _lookup_naptan(self, name: str) -> str:
        res = self.tfl.get(f"/StopPoint/Search/{urllib.parse.quote(name)}?modes=tube")
        matches = res.get("matches", [])
        if not matches:
            raise LookupError("no match")
        mid = matches[0]["id"]
        if mid.startswith("940GZZLU"):
            return mid
        hub = self.tfl.get(f"/StopPoint/{mid}")
        for child in hub.get("children", []):
            if child.get("naptanId", "").startswith("940GZZLU"):
                return child["naptanId"]
        raise LookupError(f"no tube station under {mid}")

    def poll_crowding(self):
        ts = utc_now()
        out = []
        for name, naptan in self.resolve_naptans().items():
            try:
                live = self.tfl.get(f"/crowding/{naptan}/Live")
                out.append({"naptan": naptan, "name": name, **pick(live, ("dataAvailable", "percentageOfBaseline", "timeUtc"))})
            except Exception as e:  # noqa: BLE001
                log.error(redact(f"crowding {naptan}: {e}"))
        if out:
            self.emit("crowding", ts, out)
        self.poll_crowding_profile()

    def poll_crowding_profile(self):
        ts = utc_now()
        day = ts.strftime("%Y-%m-%d")
        if self.profile_day == day or (RAW / "crowding_profile" / day).is_dir():
            self.profile_day = day
            return
        for name, naptan in self.resolve_naptans().items():
            try:
                prof = strip_type(self.tfl.get(f"/crowding/{naptan}"))
                self.emit("crowding_profile", utc_now(), {"name": name, **prof})
            except Exception as e:  # noqa: BLE001
                log.error(redact(f"crowding_profile {naptan}: {e}"))
        self.profile_day = day

    def jamcam_list(self) -> list[dict]:
        if self.cams and time.time() - self.cams_at < 3600:
            return self.cams
        lat0, lat1, lon0, lon1 = JAMCAM_BOX
        cams = []
        for p in self.tfl.get("/Place/Type/JamCam", timeout=60):
            pr = props(p)
            if pr.get("available") == "false" or not (lat0 <= p["lat"] <= lat1 and lon0 <= p["lon"] <= lon1):
                continue
            cams.append({"id": p["id"], "name": p.get("commonName"), "lat": p["lat"], "lon": p["lon"],
                         "view": pr.get("view"), "imageUrl": pr.get("imageUrl"), "videoUrl": pr.get("videoUrl")})
        self.cams, self.cams_at = cams, time.time()
        return cams

    def _head_clip(self, cam: dict) -> dict:
        url = cam.get("videoUrl") or f"{JAMCAM_S3}/{cam['id'].removeprefix('JamCams_')}.mp4"
        row = {"id": cam["id"], "name": cam["name"], "lat": cam["lat"], "lon": cam["lon"], "view": cam["view"],
               "imageUrl": cam["imageUrl"], "videoUrl": url}
        try:
            _, headers, _ = http(url, timeout=15, method="HEAD")
            h = {k.lower(): v for k, v in headers.items()}
            row.update(lastModified=h.get("last-modified"), etag=h.get("etag", "").strip('"'),
                       contentLength=int(h["content-length"]) if h.get("content-length") else None)
        except Exception as e:  # noqa: BLE001
            row["error"] = str(e)
        return row

    def _yolo_counts(self, ids: list[str]) -> dict[str, dict]:
        """A down YOLO server is retried with backoff, so a restarted server is picked up again."""
        if not self.yolo_url or time.time() < self.yolo_retry_at:
            return {}
        counts = {}
        try:
            for i in range(0, len(ids), 10):
                res = get_json(f"{self.yolo_url}/counts?ids={','.join(ids[i:i + 10])}", timeout=10)
                counts.update({r["camId"]: r for r in res.get("readings", [])})
        except Exception as e:  # noqa: BLE001
            if not self.yolo_down:
                print(f"warning: YOLO server at {self.yolo_url} unreachable ({e}); retrying with backoff", flush=True)
            log.error(f"yolo unreachable: {e}; retrying in {self.yolo_backoff:.0f}s")
            self.yolo_down = True
            self.yolo_retry_at = time.time() + self.yolo_backoff
            self.yolo_backoff = min(self.yolo_backoff * 2, YOLO_BACKOFF_MAX)
            return counts
        if self.yolo_down:
            log.info(f"yolo reachable again at {self.yolo_url}")
            print(f"YOLO server at {self.yolo_url} reachable again", flush=True)
        self.yolo_down, self.yolo_backoff = False, YOLO_BACKOFF_MIN
        return counts

    def poll_jamcams(self):
        ts = utc_now()
        cams = self.jamcam_list()
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(self._head_clip, cams))
        ids = [c["id"] for c in cams]
        counts = self._yolo_counts([i for i in ids if i in self.yolo_ids] if self.yolo_ids else ids)
        for row in rows:
            if row["id"] in counts:
                c = counts[row["id"]]
                row["yolo"] = {"people": c.get("people"), "vehicles": c.get("vehicles"), "at": c.get("at")}
        self.emit("jamcams", ts, rows)

    # ---- alternative data --------------------------------------------------------------------

    def poll_aircraft(self):
        ts = utc_now()
        self.emit("aircraft", ts, alt.parse_aircraft(self.polite_json(alt.AIRCRAFT_URL)))

    def poll_grid(self):
        ts = utc_now()
        since = iso(ts - timedelta(minutes=30))
        fuel = self.polite_json(f"{ELEXON}/datasets/FUELINST?publishDateTimeFrom={since}"
                                f"&publishDateTimeTo={iso(ts)}&format=json")
        demand = self.polite_json(f"{ELEXON}/demand/outturn/summary?format=json")
        out = {"fuelinst": alt.latest_fuelinst(fuel.get("data", [])), "demand": alt.latest_demand(demand)}
        if time.time() - self.carbon_at >= CARBON_EVERY:
            try:
                out["carbon_london"] = alt.parse_carbon(self.polite_json(CARBON_LONDON))
                self.carbon_at = time.time()
            except Exception as e:  # noqa: BLE001 - carbon is a side field; keep the Elexon reading
                log.error(f"grid carbon: {e}")
        self.emit("grid", ts, out)

    def poll_rain_now(self):
        ts = utc_now()
        self.emit("rain_now", ts, alt.parse_rain(self.polite_json(RAIN_URL)))

    def poll_news(self):
        """One record per GDELT 15-min export: {file, scanned, events[]}; events may be empty."""
        url = alt.gdelt_export_url(self.polite(GDELT_LAST).decode())
        if url and not self.gdelt_done and not self.gdelt_pending:
            # First tick: the newest file is often still a CDN 404, so start one slot back too.
            self.gdelt_pending[alt.gdelt_previous_url(url)] = time.time()
        if url and url not in self.gdelt_done:
            self.gdelt_pending.setdefault(url, time.time())
        if not self.gdelt_pending:
            self.bump("news", skipped=True)
            return
        for url, first in sorted(self.gdelt_pending.items()):
            try:
                scanned, events = alt.parse_gdelt_export(self.polite(url, timeout=60))
            except HttpError as e:
                if time.time() - first > GDELT_RETRY_FOR:
                    del self.gdelt_pending[url]
                    log.error(f"news: gave up on {url}: {e}")
                else:
                    log.info(f"news: {url} not available yet ({e}); retrying next tick")
                continue
            payload = {"file": url.rsplit("/", 1)[-1], "scanned": scanned, "events": events}
            self.bump("news", len(events), write_record("news", utc_now(), payload))
            del self.gdelt_pending[url]
            self.gdelt_done.append(url)

    def poll_wiki_views(self):
        ts = utc_now()
        start, end = (ts - timedelta(days=14)).strftime("%Y%m%d"), ts.strftime("%Y%m%d")
        out = []
        for title in alt.WIKI_WATCHLIST:
            article = urllib.parse.quote(title.replace(" ", "_"), safe="")
            try:
                views = alt.parse_pageviews(self.polite_json(f"{PAGEVIEWS}/{article}/daily/{start}/{end}"))
                out.append({"title": title, "views": views})
            except Exception as e:  # noqa: BLE001 - one missing article must not drop the rest
                log.error(f"wiki_views {title}: {e}")
        if out:
            self.emit("wiki_views", ts, out)

    def stream_wiki_edits(self, until: float | None = None) -> None:
        """Hold the EventStreams SSE connection open and write watchlist edits; reconnect with `since`."""
        since, backoff = None, 5
        limiter = self.host_limiter("stream.wikimedia.org")
        while not self.stop.is_set() and (until is None or time.time() < until):
            url = WIKI_STREAM + (f"?since={urllib.parse.quote(since)}" if since else "")
            limiter.acquire()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=60) as res:
                    backoff = 5
                    for _, data in alt.sse_events(res):
                        self.wiki_scanned += 1
                        row = alt.match_wiki_edit(data)
                        if row:
                            self.emit("wiki_edits", utc_now(), row)
                        if row or self.wiki_scanned % 500 == 0:
                            since = alt.event_dt(data) or since
                        if self.stop.is_set() or (until is not None and time.time() >= until):
                            return
            except Exception as e:  # noqa: BLE001 - drops are normal for SSE; reconnect
                self.bump("wiki_edits", error=True)
                log.error(f"wiki_edits stream: {type(e).__name__}: {e}; reconnecting in {backoff}s")
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 120)

    # ---- loop --------------------------------------------------------------------------------

    def sources(self):
        """(name, poll fn, interval s); interval None = a long-lived stream in its own thread."""
        return [
            ("bus", self.poll_bus, 60), ("tube", self.poll_tube, 60),
            ("line_status", self.poll_line_status, 60), ("road_status", self.poll_road_status, 60),
            ("disruptions", self.poll_disruptions, 300), ("bikepoint", self.poll_bikepoint, 120),
            ("crowding", self.poll_crowding, 30), ("jamcams", self.poll_jamcams, 60),
            ("aircraft", self.poll_aircraft, 60), ("grid", self.poll_grid, 300),
            ("rain_now", self.poll_rain_now, 300), ("news", self.poll_news, 300),
            ("wiki_views", self.poll_wiki_views, 6 * 3600), ("wiki_edits", self.stream_wiki_edits, None),
        ]

    def run_one(self, name: str, fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - a failed poll is logged and retried next tick
            self.bump(name, error=True)
            log.error(redact(f"{name}: {type(e).__name__}: {e}"))

    def loop(self, name: str, fn, interval: float) -> None:
        next_at = time.time()
        while not self.stop.is_set():
            self.run_one(name, fn)
            next_at += interval
            if next_at < time.time():
                next_at = time.time()
            sleep_until(next_at, self.stop)

    def run(self, once: bool, names: list[str]) -> None:
        threads = []
        for name, fn, interval in self.sources():
            if name not in names:
                continue
            if interval is None:
                until = time.time() + WIKI_ONCE_SECONDS if once else None
                target = lambda n=name, f=fn, u=until: self.run_one(n, lambda: f(u))  # noqa: E731
            elif once:
                target = lambda n=name, f=fn: self.run_one(n, f)  # noqa: E731
            else:
                target = lambda n=name, f=fn, i=interval: self.loop(n, f, i)  # noqa: E731
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            threads.append(t)
        if once:
            for t in threads:
                t.join()
            print(self.status_line(), flush=True)
            return
        try:
            while not self.stop.wait(60):
                print(self.status_line(), flush=True)
        except KeyboardInterrupt:
            self.stop.set()


SOURCE_NAMES = ("bus", "tube", "line_status", "road_status", "disruptions", "bikepoint", "crowding", "jamcams",
                "aircraft", "grid", "rain_now", "news", "wiki_views", "wiki_edits")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="poll every source once and exit")
    ap.add_argument("--yolo", action="store_true", help="add JamCam people/vehicle counts from JAMCAM_YOLO_URL")
    ap.add_argument("--env-file", help="file with TFL_APP_KEY= (default: repo-root .env, else the main checkout's)")
    ap.add_argument("--only", help=f"comma-separated sources to run (default: all of {', '.join(SOURCE_NAMES)})")
    ap.add_argument("--skip", help="comma-separated sources to leave out")
    args = ap.parse_args()
    names = [n.strip() for n in args.only.split(",")] if args.only else list(SOURCE_NAMES)
    skip = {n.strip() for n in args.skip.split(",")} if args.skip else set()
    unknown = (set(names) | skip) - set(SOURCE_NAMES)
    if unknown:
        ap.error(f"unknown source(s): {', '.join(sorted(unknown))}")
    names = [n for n in names if n not in skip]

    load_env(args.env_file)
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA / "recorder.log", level=logging.INFO,
                        format="%(asctime)s %(threadName)s %(message)s")
    rec = Recorder(yolo=args.yolo)
    budget = rec.tfl.limiter.per_minute
    print(f"recorder: writing to {RAW} | TfL key {'set' if rec.tfl.key else 'not set'} | cap {budget}/min"
          f"{' | yolo ' + rec.yolo_url if rec.yolo_url else ''}", flush=True)
    print(f"sources: {', '.join(names)}", flush=True)
    log.info(f"start once={args.once} yolo={args.yolo} cap={budget}/min sources={','.join(names)}")
    rec.run(args.once, names)


if __name__ == "__main__":
    main()
