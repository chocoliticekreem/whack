"""Live recorder: polls TfL feeds (and JamCam clip metadata) into data/raw/<source>/<day>/<hour>.ndjson.gz.

    python3 recorder/record.py            # run forever
    python3 recorder/record.py --once     # poll every source once and exit
    python3 recorder/record.py --yolo     # also ask a local YOLO server for JamCam counts
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
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, RAW, HttpError, get_json, http, iso, load_env, sleep_until, utc_now, write_record  # noqa: E402

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
        url = f"{TFL}{path}{sep}app_key={self.key}" if self.key else f"{TFL}{path}"
        for attempt in range(tries):
            self.limiter.acquire()
            try:
                return get_json(url, timeout)
            except HttpError as e:
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
        self.yolo_warned = False

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
        return f"[{iso(utc_now())}] " + " ".join(parts)

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
        if not self.yolo_url:
            return {}
        counts = {}
        try:
            for i in range(0, len(ids), 10):
                res = get_json(f"{self.yolo_url}/counts?ids={','.join(ids[i:i + 10])}", timeout=10)
                counts.update({r["camId"]: r for r in res.get("readings", [])})
        except Exception as e:  # noqa: BLE001
            if not self.yolo_warned:
                print(f"warning: YOLO server at {self.yolo_url} unreachable ({e}); continuing without counts", flush=True)
                log.error(f"yolo unreachable: {e}")
                self.yolo_warned = True
            self.yolo_url = None
        return counts

    def poll_jamcams(self):
        ts = utc_now()
        cams = self.jamcam_list()
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(self._head_clip, cams))
        counts = self._yolo_counts([c["id"] for c in cams])
        for row in rows:
            if row["id"] in counts:
                c = counts[row["id"]]
                row["yolo"] = {"people": c.get("people"), "vehicles": c.get("vehicles"), "at": c.get("at")}
        self.emit("jamcams", ts, rows)

    # ---- loop --------------------------------------------------------------------------------

    def sources(self):
        return [
            ("bus", self.poll_bus, 60), ("tube", self.poll_tube, 60),
            ("line_status", self.poll_line_status, 60), ("road_status", self.poll_road_status, 60),
            ("disruptions", self.poll_disruptions, 300), ("bikepoint", self.poll_bikepoint, 120),
            ("crowding", self.poll_crowding, 300), ("jamcams", self.poll_jamcams, 60),
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

    def run(self, once: bool) -> None:
        threads = []
        for name, fn, interval in self.sources():
            target = (lambda n=name, f=fn: self.run_one(n, f)) if once else (lambda n=name, f=fn, i=interval: self.loop(n, f, i))
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="poll every source once and exit")
    ap.add_argument("--yolo", action="store_true", help="add JamCam people/vehicle counts from JAMCAM_YOLO_URL")
    ap.add_argument("--env-file", help="file with TFL_APP_KEY= (default: repo-root .env, else the main checkout's)")
    args = ap.parse_args()

    load_env(args.env_file)
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA / "recorder.log", level=logging.INFO,
                        format="%(asctime)s %(threadName)s %(message)s")
    rec = Recorder(yolo=args.yolo)
    budget = rec.tfl.limiter.per_minute
    print(f"recorder: writing to {RAW} | TfL key {'set' if rec.tfl.key else 'not set'} | cap {budget}/min"
          f"{' | yolo ' + rec.yolo_url if rec.yolo_url else ''}", flush=True)
    log.info(f"start once={args.once} yolo={args.yolo} cap={budget}/min")
    rec.run(args.once)


if __name__ == "__main__":
    main()
