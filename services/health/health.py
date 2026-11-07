"""Data health: how fresh each recorded London feed is, plus TfL key validity and YOLO reachability.

    python3 services/health/health.py          # compact table
    python3 services/health/health.py --json   # the same as JSON (what GET /health on :8052 serves)
    HEALTH_RAW=/path/to/data/raw python3 services/health/health.py   # another checkout's data

Per source (from data/raw/<source>/<day>/<hour>.ndjson.gz): last ts, age, records in the last 10 min,
expected cadence (the recorder's poll table) and a status:
    ok     age <= 2 x cadence
    stale  age >  2 x cadence            (one missed write on a 60 s feed is flagged within a minute)
    dead   age >  max(3 x cadence, 180 s), or no data at all
Top-level `status` is the worst of CORE (the feeds the markets read) and YOLO; `degraded` lists every
component that is not ok. Halt markets when `status` is not "ok".
"""

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "recorder"))
import record  # noqa: E402
from common import load_env  # noqa: E402
from scrape.run import REFRESH as SCRAPE_EVERY  # noqa: E402

RAW = Path(os.environ.get("HEALTH_RAW", ROOT / "data" / "raw"))
WINDOW = 600
YOLO_FRESH = 600
DEAD_FLOOR = 180
CORE = ("jamcams", "bus", "tube", "line_status")
# Sources whose write cadence differs from their poll interval.
WRITE_CADENCE = {
    "news": 900,             # polled every 5 min, but GDELT publishes one file per 15 min
    "disruptions": 900,      # written only when the content changes
    "crowding_profile": 86400,
    "scrape_fixtures": SCRAPE_EVERY,
}
RANK = {"ok": 0, "event": 0, "stale": 1, "dead": 2}
TFL_CHECK = "https://api.tfl.gov.uk/Line/Meta/Modes"


def cadences() -> dict[str, float | None]:
    """Seconds between writes per source; None = event stream (no expected cadence)."""
    table = {name: interval for name, _, interval in record.Recorder(yolo=False).sources()}
    table.update(WRITE_CADENCE)
    return table


def classify(age: float | None, cadence: float | None) -> str:
    if cadence is None:
        return "event"
    if age is None or age > max(3 * cadence, DEAD_FLOOR):
        return "dead"
    return "stale" if age > 2 * cadence else "ok"


def _parse_ts(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def read_lines(path: Path) -> list[bytes]:
    """Every complete line of a gzip NDJSON file. The recorder appends one gzip member per write, so a
    member being written (or cut by a kill) is truncated: keep everything before it."""
    lines = []
    try:
        with gzip.open(path, "rb") as f:
            for line in f:
                if line.endswith(b"\n"):
                    lines.append(line)
    except (EOFError, OSError, zlib.error):
        pass
    return lines


def line_ts(line: bytes) -> float | None:
    """Fetch time of one raw line; reads the `{"ts":"...Z"` prefix without parsing the payload."""
    if line.startswith(b'{"ts":"') and line[26:27] == b"Z":
        try:
            return _parse_ts(line[7:27].decode())
        except ValueError:
            pass
    try:
        return _parse_ts(json.loads(line)["ts"])
    except (ValueError, KeyError, TypeError):
        return None


def _hour_files(src_dir: Path) -> list[Path]:
    return sorted(src_dir.glob("*/*.ndjson.gz"), key=lambda p: (p.parent.name, p.name), reverse=True)


def source_health(src_dir: Path, cadence: float | None, now: float) -> dict:
    """Newest hour files first: stop once a file's hour ends before the 10-min window and a last line is known."""
    last_ts, last_line, recent = None, None, 0
    for path in _hour_files(src_dir):
        try:
            hour = datetime.strptime(f"{path.parent.name} {path.name[:2]}", "%Y-%m-%d %H")
        except ValueError:
            continue
        hour_end = hour.replace(tzinfo=timezone.utc).timestamp() + 3600
        if last_ts is not None and hour_end < now - WINDOW:
            break
        for line in read_lines(path):
            ts = line_ts(line)
            if ts is None:
                continue
            if now - WINDOW <= ts <= now + 60:
                recent += 1
            if last_ts is None or ts >= last_ts:
                last_ts, last_line = ts, line
        if last_ts is not None and hour_end - 3600 < now - WINDOW:
            break
    age = None if last_ts is None else max(0.0, now - last_ts)
    out = {
        "last_ts": None if last_ts is None else datetime.fromtimestamp(last_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_s": None if age is None else round(age),
        "records_10min": recent,
        "cadence_s": cadence,
        "status": classify(age, cadence),
    }
    if src_dir.name == "jamcams" and last_line is not None:
        out.update(yolo_freshness(last_line, now))
    return out


def yolo_freshness(line: bytes, now: float) -> dict:
    """From the newest jamcams record: cams carrying YOLO counts and how many are younger than 10 min."""
    try:
        rows = json.loads(line)["payload"]
    except (ValueError, KeyError):
        return {}
    ats = [r["yolo"].get("at") for r in rows if isinstance(r, dict) and isinstance(r.get("yolo"), dict)]
    fresh = sum(1 for at in ats if isinstance(at, (int, float)) and now - at / 1000 <= YOLO_FRESH)
    return {"cams": len(rows), "yolo_cams": len(ats), "yolo_fresh_10min": fresh}


def tfl_key_check(timeout: float = 5) -> dict:
    key = os.environ.get("TFL_APP_KEY")
    if not key:
        return {"status": "unset"}
    req = urllib.request.Request(f"{TFL_CHECK}?app_key={key}", headers={"User-Agent": record.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return {"status": "valid", "http": res.status}
    except urllib.error.HTTPError as e:
        body = e.read(200)
        if b"Invalid app_key" in body:
            return {"status": "invalid", "http": e.code}
        return {"status": "error", "http": e.code}
    except Exception as e:  # noqa: BLE001 - network failure: report, never raise
        return {"status": "error", "detail": type(e).__name__}


def yolo_check(url: str, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as res:
            return res.status == 200
    except Exception:  # noqa: BLE001
        return False


def yolo_status(reachable: bool, jamcams: dict) -> str:
    """Dead when unreachable or, while jamcams itself is fresh, no counted cam is younger than 10 min."""
    if not reachable:
        return "dead"
    if jamcams.get("status") != "ok" or not jamcams.get("yolo_cams"):
        return "ok"
    fresh = jamcams.get("yolo_fresh_10min", 0)
    if fresh == 0:
        return "dead"
    return "ok" if 2 * fresh >= jamcams["yolo_cams"] else "stale"


def snapshot(raw: Path = RAW, now: float | None = None, tfl: dict | None = None,
             yolo_reachable: bool | None = None) -> dict:
    """The whole health report. `tfl` / `yolo_reachable` let a server pass cached network checks."""
    now = time.time() if now is None else now
    table = cadences()
    names = set(table) | ({p.name for p in raw.iterdir() if p.is_dir()} if raw.is_dir() else set())
    sources = {n: source_health(raw / n, table.get(n, 300), now) for n in sorted(names)}
    yolo_url = os.environ.get("JAMCAM_YOLO_URL", "http://127.0.0.1:8047").rstrip("/")
    reachable = yolo_check(yolo_url) if yolo_reachable is None else yolo_reachable
    jam = sources.get("jamcams", {})
    yolo = {"url": yolo_url, "reachable": reachable, "status": yolo_status(reachable, jam),
            "yolo_fresh_10min": jam.get("yolo_fresh_10min"), "yolo_cams": jam.get("yolo_cams")}
    tfl = tfl_key_check() if tfl is None else tfl
    core = [sources[n]["status"] for n in CORE if n in sources] + [yolo["status"]]
    degraded = [n for n, s in sources.items() if RANK[s["status"]] > 0]
    degraded += ["yolo"] if yolo["status"] != "ok" else []
    degraded += ["tfl_key"] if tfl["status"] not in ("valid", "unset") else []
    return {
        "now": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": max(core, key=RANK.__getitem__),
        "core": list(CORE) + ["yolo"],
        "degraded": degraded,
        "sources": sources,
        "yolo": yolo,
        "tfl_key": tfl,
    }


def _fmt(v) -> str:
    return "-" if v is None else str(v)


def table(snap: dict) -> str:
    lines = [f"{snap['now']}  status={snap['status']}  degraded={','.join(snap['degraded']) or '-'}",
             f"{'source':18} {'status':6} {'age_s':>7} {'10min':>5} {'cadence':>7}  last_ts"]
    for name, s in snap["sources"].items():
        extra = ""
        if "yolo_cams" in s:
            extra = f"  yolo fresh {s['yolo_fresh_10min']}/{s['yolo_cams']} of {s['cams']} cams"
        lines.append(f"{name:18} {s['status']:6} {_fmt(s['age_s']):>7} {s['records_10min']:>5} "
                     f"{_fmt(s['cadence_s']):>7}  {_fmt(s['last_ts'])}{extra}")
    y, t = snap["yolo"], snap["tfl_key"]
    lines.append(f"yolo   {y['status']:6} reachable={y['reachable']} {y['url']}")
    lines.append(f"tfl    {t['status']}" + (f" http={t['http']}" if "http" in t else "")
                 + (f" {t['detail']}" if "detail" in t else ""))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print JSON instead of the table")
    args = ap.parse_args()
    load_env()
    snap = snapshot()
    print(json.dumps(snap, indent=1) if args.json else table(snap))


if __name__ == "__main__":
    main()
