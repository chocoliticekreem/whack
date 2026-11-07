"""Observations: recorded NDJSON (replay) or the twin's live routes (live).

Recorder format: data/raw/<source>/YYYY-MM-DD/HH.ndjson(.gz), one {"ts","source","payload"} per line.
Camera counts are read from any payload carrying `readings: [{camId|id, people}]` (the shape of the
YOLO /counts reply); the bus signal from `meanDelayMin`. Unknown sources are skipped, not guessed.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parent.parent.parent
CAM_SOURCES = tuple(s for s in os.environ.get("MULTIVERSE_CAM_SOURCES", "jamcam_counts,jamcam_yolo,counts").split(",") if s)
BUS_SOURCES = tuple(s for s in os.environ.get("MULTIVERSE_BUS_SOURCES", "bus_delay").split(",") if s)

# One alternative-data stream per broker: feed name -> the recorder sources it reads.
ALT_FEEDS = {
    "aviation": ("aircraft",),
    "weather": ("rain_now",),
    "grid": ("grid",),
    "news": ("news",),
    "bikes": ("bikepoint",),
    "tube": ("crowding", "crowding_profile"),
    "attention": ("wiki_views", "wiki_edits"),
}
ALT_SOURCES = tuple(dict.fromkeys(src for srcs in ALT_FEEDS.values() for src in srcs))
OXFORD_CIRCUS = (51.5152, -0.1418)
OXFORD_CIRCUS_NAPTAN = "940GZZLUOXC"
BIKE_RADIUS_KM = 0.8
RAIN_MM = 0.1
RAIN_LOOKAHEAD_MIN = 60
# Sources written as many records per poll (one per station / per edit): read this many back.
MANY = {"wiki_edits": 60, "crowding_profile": 100}
# How far back a record still counts as "latest" (crowding_profile is daily, wiki_views every 6 h).
STALE_MIN = {"crowding_profile": 36 * 60, "wiki_views": 12 * 60, "news": 60, "crowding": 30, "grid": 30,
             "rain_now": 30, "wiki_edits": 60}


def parse_ts(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


def read_lines(path: Path) -> Iterator[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (OSError, EOFError):
        return


def readings_of(payload: Any) -> dict[str, float]:
    rows = payload.get("readings") if isinstance(payload, dict) else payload if isinstance(payload, list) else None
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cid = r.get("camId") or r.get("id")
        people = r.get("people")
        if cid and isinstance(people, (int, float)):
            out[str(cid)] = float(people)
    return out


def load_frames(raw_root: Path, start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
    """Merge cam and bus records into frames keyed by timestamp, oldest first."""
    frames: dict[int, dict] = {}
    for source in CAM_SOURCES + BUS_SOURCES:
        for path in sorted((raw_root / source).glob("*/*.ndjson*")):
            for rec in read_lines(path):
                try:
                    ts = parse_ts(rec["ts"])
                except (KeyError, ValueError, TypeError):
                    continue
                if (start_ms is not None and ts < start_ms) or (end_ms is not None and ts > end_ms):
                    continue
                frame = frames.setdefault(ts, {"ts": ts, "readings": {}, "busDelayMin": None})
                if source in CAM_SOURCES:
                    frame["readings"].update(readings_of(rec.get("payload")))
                else:
                    delay = (rec.get("payload") or {}).get("meanDelayMin")
                    if isinstance(delay, (int, float)):
                        frame["busDelayMin"] = float(delay)
    out = [frames[k] for k in sorted(frames)]
    # debt: re-reads the hour files per frame. ceiling: fine for the fixture and a live tick; slow for a
    # day of real bikepoint replay. upgrade: when replaying recorded days, index records per source once.
    for frame in out:
        frame["feeds"] = feeds_at(raw_root, frame["ts"])
    return out


def live_frame(twin_url: str, cam_ids: list[str]) -> dict:
    """One fresh frame from the twin's /api/around/counts (YOLO over TfL JamCams)."""
    url = f"{twin_url}/api/around/counts?ids={urllib.parse.quote(','.join(cam_ids))}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=60) as res:
        data = json.loads(res.read().decode("utf-8"))
    readings = data.get("readings") or []
    ts = max((int(r.get("at") or 0) for r in readings), default=0)
    return {"ts": ts or None, "readings": readings_of(readings), "busDelayMin": None}


# ---------------------------------------------------------------- alternative data


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def alt_root() -> Path:
    """Recorder root for live alternative data: MULTIVERSE_ALT_RAW, the repo's data/raw, or (in a git
    worktree) the main checkout's data/raw. Read-only."""
    env = os.environ.get("MULTIVERSE_ALT_RAW")
    if env:
        return Path(env)
    local = REPO / "data" / "raw"
    if local.is_dir():
        return local
    dotgit = REPO / ".git"
    if dotgit.is_file():
        gitdir = dotgit.read_text().strip().removeprefix("gitdir:").strip()
        if "/.git/worktrees/" in gitdir:
            return Path(gitdir.split("/.git/worktrees/")[0]) / "data" / "raw"
    return local


def hour_files(raw_root: Path, source: str, at_ms: int, back_min: int) -> list[Path]:
    """Recorder files for `source` covering (at - back_min, at], oldest first."""
    at = datetime.fromtimestamp(at_ms / 1000, timezone.utc)
    hours, t = [], at - timedelta(minutes=back_min)
    t = t.replace(minute=0, second=0, microsecond=0)
    while t <= at:
        hours.append(t)
        t += timedelta(hours=1)
    out = []
    for h in hours:
        for ext in (".ndjson.gz", ".ndjson"):
            path = raw_root / source / h.strftime("%Y-%m-%d") / f"{h.strftime('%H')}{ext}"
            if path.is_file():
                out.append(path)
    return out


def latest_records(raw_root: Path, source: str, at_ms: int, k: int = 2) -> list[dict]:
    """The newest k records of `source` at or before at_ms (oldest first), within its staleness window."""
    back = STALE_MIN.get(source, 20)
    recs = []
    for path in hour_files(raw_root, source, at_ms, back):
        for rec in read_lines(path):
            try:
                ts = parse_ts(rec["ts"])
            except (KeyError, ValueError, TypeError):
                continue
            if at_ms - back * 60_000 <= ts <= at_ms:
                recs.append(dict(rec, tsMs=ts))
    recs.sort(key=lambda r: r["tsMs"])
    return recs[-k:]


def num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def dist_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def summarize_aviation(recs: list[dict], at_ms: int) -> dict | None:
    s = (recs[-1]["payload"] or {}).get("summary") if recs else None
    if not s:
        return None
    return {"holdingTotal": s.get("holding_total"), "holding": s.get("holding"), "below8000ft": s.get("below_8000ft"),
            "airborne": s.get("airborne")}


def summarize_weather(recs: list[dict], at_ms: int) -> dict | None:
    """Rain now, and minutes until the first 15-min step ahead with rain (None = dry for the next hour)."""
    if not recs:
        return None
    p = recs[-1]["payload"] or {}
    cur = p.get("current") or {}
    ahead, ahead_mm = None, 0.0
    for step in p.get("minutely_15") or []:
        try:
            t = parse_ts(step["time"] + ("" if step["time"].endswith("Z") else "Z"))
        except (KeyError, ValueError, AttributeError):
            continue
        mins = (t - at_ms) / 60_000
        mm = num(step.get("precipitation")) or 0.0
        if -15 < mins <= RAIN_LOOKAHEAD_MIN:
            ahead_mm = max(ahead_mm, mm)
            if mm >= RAIN_MM and ahead is None and mins >= 0:
                ahead = round(mins, 1)
    return {"precipNowMm": num(cur.get("precipitation")), "cloudPct": num(cur.get("cloud_cover")),
            "weatherCode": cur.get("weather_code"), "rainInMin": ahead, "maxMmNextHour": round(ahead_mm, 2)}


def summarize_grid(recs: list[dict], at_ms: int) -> dict | None:
    if not recs:
        return None
    def demand(r: dict) -> float | None:
        p = r["payload"] or {}
        return num((p.get("demand") or {}).get("demand")) or num((p.get("fuelinst") or {}).get("total_mw"))
    now = demand(recs[-1])
    prev = demand(recs[0]) if len(recs) > 1 else None
    carbon = next(((r["payload"] or {}).get("carbon_london") for r in reversed(recs)
                   if (r["payload"] or {}).get("carbon_london")), None) or {}
    return {"demandMw": now, "demandChangeMw": round(now - prev) if now is not None and prev is not None else None,
            "carbonIndex": carbon.get("index"), "carbonForecast": carbon.get("forecast")}


def summarize_news(recs: list[dict], at_ms: int) -> dict | None:
    if not recs:
        return None
    events = (recs[-1]["payload"] or {}).get("events") or []
    tones = [num(e.get("AvgTone")) for e in events if num(e.get("AvgTone")) is not None]
    codes: dict[str, int] = {}
    for e in events:
        code = str(e.get("EventCode") or "")[:2]
        codes[code] = codes.get(code, 0) + 1
    return {"eventsNearOxfordCircus": len(events), "mentions": int(sum(num(e.get("NumMentions")) or 0 for e in events)),
            "avgTone": round(sum(tones) / len(tones), 2) if tones else None,
            "topCodes": sorted(codes, key=lambda c: -codes[c])[:3]}


def bikes_near(payload: Any) -> dict[str, tuple[float, float]]:
    out = {}
    for st in payload if isinstance(payload, list) else []:
        lat, lon = num(st.get("lat")), num(st.get("lon"))
        if lat is None or lon is None or dist_km(OXFORD_CIRCUS, (lat, lon)) > BIKE_RADIUS_KM:
            continue
        out[str(st.get("id"))] = (num(st.get("NbBikes")) or 0.0, num(st.get("NbEmptyDocks")) or 0.0)
    return out


def summarize_bikes(recs: list[dict], at_ms: int) -> dict | None:
    """Docks near Oxford Circus; bikesTaken = bikes gone since the previous snapshot (positive = emptying)."""
    if not recs:
        return None
    now = bikes_near(recs[-1]["payload"])
    if not now:
        return None
    out = {"stations": len(now), "bikes": int(sum(b for b, _ in now.values())),
           "emptyDocks": int(sum(d for _, d in now.values())), "bikesTaken": None, "minutes": None}
    if len(recs) > 1:
        prev = bikes_near(recs[0]["payload"])
        common = set(now) & set(prev)
        out["bikesTaken"] = int(sum(prev[i][0] - now[i][0] for i in common))
        out["minutes"] = round((recs[-1]["tsMs"] - recs[0]["tsMs"]) / 60_000, 1)
    return out


def typical_crowding(profile: dict, at_ms: int) -> float | None:
    at = datetime.fromtimestamp(at_ms / 1000, timezone.utc)
    day = at.strftime("%a").upper()
    band = f"{at.hour:02d}:{at.minute // 15 * 15:02d}"
    for d in profile.get("daysOfWeek") or []:
        if d.get("dayOfWeek") == day:
            for tb in d.get("timeBands") or []:
                if str(tb.get("timeBand", "")).startswith(band):
                    return num(tb.get("percentageOfBaseLine"))
    return None


def summarize_tube(live: list[dict], profiles: list[dict], at_ms: int) -> dict | None:
    """Oxford Circus crowding now vs the typical level for this weekday and 15-min band."""
    row = next((r for r in ((live[-1]["payload"] if live else None) or []) if r.get("naptan") == OXFORD_CIRCUS_NAPTAN), None)
    if not row or not row.get("dataAvailable"):
        return None
    now = num(row.get("percentageOfBaseline"))
    prof = next((r["payload"] for r in reversed(profiles) if (r["payload"] or {}).get("naptan") == OXFORD_CIRCUS_NAPTAN), None)
    typical = typical_crowding(prof, at_ms) if prof else None
    return {"station": row.get("name"), "nowPctOfBaseline": round(now, 3) if now is not None else None,
            "typicalPctOfBaseline": typical,
            "vsTypical": round(now / typical, 2) if now is not None and typical else None}


def summarize_attention(views: list[dict], edits: list[dict], at_ms: int) -> dict | None:
    """Watchlist pageviews on the latest day vs the 14-day mean, plus edits in the last hour."""
    rows = (views[-1]["payload"] if views else None) or []
    if not rows and not edits:
        return None
    days: dict[str, int] = {}
    for r in rows if isinstance(rows, list) else []:
        for d, v in (r.get("views") or {}).items():
            days[d] = days.get(d, 0) + int(num(v) or 0)
    out = {"viewsLatestDay": None, "viewsVsMean": None, "editsLastHour": len(edits)}
    if days:
        latest = max(days)
        mean = sum(days.values()) / len(days)
        out |= {"viewsLatestDay": days[latest], "viewsVsMean": round(days[latest] / mean, 2) if mean else None}
    return out


def feeds_at(raw_root: Path | None, at_ms: int) -> dict[str, dict | None]:
    """The latest summary of every alternative-data feed as of at_ms (None where nothing recent is recorded)."""
    if raw_root is None or not raw_root.is_dir():
        return {name: None for name in ALT_FEEDS}
    rec = {src: latest_records(raw_root, src, at_ms, k=MANY.get(src, 2)) for src in ALT_SOURCES}
    return {
        "aviation": summarize_aviation(rec["aircraft"], at_ms),
        "weather": summarize_weather(rec["rain_now"], at_ms),
        "grid": summarize_grid(rec["grid"], at_ms),
        "news": summarize_news(rec["news"], at_ms),
        "bikes": summarize_bikes(rec["bikepoint"], at_ms),
        "tube": summarize_tube(rec["crowding"], rec["crowding_profile"], at_ms),
        "attention": summarize_attention(rec["wiki_views"], rec["wiki_edits"], at_ms),
    }
