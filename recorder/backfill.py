"""One-shot history pull into data/backfill/<source>.ndjson.gz (one row per line, overwritten each run).

    python3 recorder/backfill.py
"""

import gzip
import json
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, iso, utc_now  # noqa: E402
from common import get_json as _get_json  # noqa: E402

OUT = DATA / "backfill"
LAT, LON = 51.515, -0.14
EA = "https://environment.data.gov.uk/flood-monitoring"


def get_json(url: str, timeout: float = 30, tries: int = 3):
    for attempt in range(tries):
        try:
            return _get_json(url, timeout)
        except Exception:  # noqa: BLE001 - public APIs 503 now and then; retry, then surface
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def save(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / f"{name}.ndjson.gz", "wt") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"{name:14} {len(rows):6} rows -> {OUT / (name + '.ndjson.gz')}", flush=True)


def chunks(days: int, step: int = 7):
    """(from, to) dates covering the last `days` days, `step` days at a time (Elexon caps ranges)."""
    today = utc_now().date()
    start = today - timedelta(days=days)
    while start <= today:
        end = min(start + timedelta(days=step - 1), today)
        yield start, end
        start = end + timedelta(days=1)


def open_meteo() -> list[dict]:
    d = get_json("https://api.open-meteo.com/v1/forecast?"
                 f"latitude={LAT}&longitude={LON}&minutely_15=precipitation,temperature_2m&past_days=7&timezone=UTC")
    m = d["minutely_15"]
    now = utc_now().strftime("%Y-%m-%dT%H:%M")
    return [{"time": t, "precipitation": p, "temperature_2m": c}
            for t, p, c in zip(m["time"], m["precipitation"], m["temperature_2m"]) if t <= now]


def ea_thames() -> list[dict]:
    since = iso(utc_now() - timedelta(days=7))
    stations = get_json(f"{EA}/id/stations?lat=51.5&long=-0.12&dist=5")["items"]
    rows = []
    for s in stations:
        ref = s["stationReference"]
        readings = get_json(f"{EA}/id/stations/{ref}/readings?since={since}&_limit=10000&_sorted", timeout=60)
        for r in readings["items"]:
            rows.append({"station": ref, "label": s.get("label"), "lat": s.get("lat"), "lon": s.get("long"),
                         "measure": r["measure"].rsplit("/", 1)[-1], "dateTime": r["dateTime"], "value": r.get("value")})
    return rows


def elexon_demand() -> list[dict]:
    rows = []
    for a, b in chunks(14):
        d = get_json("https://data.elexon.co.uk/bmrs/api/v1/demand/outturn?"
                     f"settlementDateFrom={a}&settlementDateTo={b}&format=json", timeout=60)
        rows += d["data"]
    return rows


def carbon_london() -> list[dict]:
    rows = []
    for a, b in chunks(14):
        d = get_json(f"https://api.carbonintensity.org.uk/regional/intensity/{a}T00:00Z/{b + timedelta(days=1)}T00:00Z"
                     "/regionid/13", timeout=60)
        for r in d["data"]["data"]:
            rows.append({"from": r["from"], "to": r["to"], "forecast": r["intensity"]["forecast"],
                         "index": r["intensity"]["index"], "mix": {g["fuel"]: g["perc"] for g in r["generationmix"]}})
    now = utc_now().strftime("%Y-%m-%dT%H:%MZ")
    seen, unique = set(), []
    for r in rows:
        if r["from"] not in seen and r["from"] <= now:
            seen.add(r["from"])
            unique.append(r)
    return unique


def main() -> None:
    failed = 0
    for name, fn in [("open_meteo", open_meteo), ("ea_thames", ea_thames),
                     ("elexon_demand", elexon_demand), ("carbon_london", carbon_london)]:
        try:
            save(name, fn())
        except Exception as e:  # noqa: BLE001 - one dead API must not block the others
            failed += 1
            print(f"{name:14} FAILED: {type(e).__name__}: {e}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
