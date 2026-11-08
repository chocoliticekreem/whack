"""When does a TfL crowding reading become public? Polls /crowding/{naptan}/Live and logs each poll.

    python3 analysis/judge/crowd_availability.py probe <minutes> <out.ndjson>   # 3 stations every 15 s
    python3 analysis/judge/crowd_availability.py report <out.ndjson>

TfL stamps each reading with timeUtc, but the stamp is not when it is served. The probe records the first poll
that returns each new stamp; availability lag = first seen - stamp, bounded below by the previous poll. 12 calls a
minute with the recorder's key, well inside TfL's 500/min. Never prints the key.
"""
from __future__ import annotations

import itertools
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "recorder"))
from common import USER_AGENT, load_env  # noqa: E402

STATIONS = {"Oxford Circus": "940GZZLUOXC", "Bank": "940GZZLUBNK", "King's Cross St. Pancras": "940GZZLUKSX"}
EVERY = 15


def probe(minutes: float, out: Path) -> None:
    load_env()
    key = os.environ.get("TFL_APP_KEY")
    end = time.time() + minutes * 60
    with out.open("a") as fh:
        while time.time() < end:
            tick = time.time()
            for name, naptan in STATIONS.items():
                url = f"https://api.tfl.gov.uk/crowding/{naptan}/Live" + (f"?app_key={key}" if key else "")
                t0 = time.time()
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=20) as res:
                        body, age = json.loads(res.read()), res.headers.get("Age")
                    rec = {"t": t0, "name": name, "timeUtc": body.get("timeUtc"),
                           "pct": body.get("percentageOfBaseline"), "age": age}
                except Exception as e:  # noqa: BLE001 - one failed poll is logged, not fatal
                    rec = {"t": t0, "name": name, "err": f"{type(e).__name__} {getattr(e, 'code', '')}"}
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
            time.sleep(max(0.0, EVERY - (time.time() - tick)))


def stamp_s(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def report(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    lags, bounds = [], []
    for name in STATIONS:
        polls = [r for r in rows if r["name"] == name and r.get("timeUtc")]
        seen, prev_t = None, None
        for r in polls:
            if seen is not None and r["timeUtc"] != seen:
                lag = r["t"] - stamp_s(r["timeUtc"])
                lags.append(lag)
                bounds.append((lag - (r["t"] - prev_t), lag))
                print(f"{name:26} stamp {r['timeUtc']}  first seen +{lag:5.0f}s  (prev poll +{lag - (r['t'] - prev_t):4.0f}s)")
            seen, prev_t = r["timeUtc"], r["t"]
    errs = sum(1 for r in rows if r.get("err"))
    print(f"polls={len(rows)} errors={errs} new stamps={len(lags)}")
    if lags:
        lo = [b[0] for b in bounds]
        print(f"availability lag (first seen - stamp): min {min(lags):.0f}s median {statistics.median(lags):.0f}s "
              f"max {max(lags):.0f}s; lower bound (previous poll) median {statistics.median(lo):.0f}s")
        gaps = sorted({stamp_s(r["timeUtc"]) for r in rows if r.get("timeUtc")})
        steps = [b - a for a, b in itertools.pairwise(gaps)]
        if steps:
            print(f"stamp cadence: median {statistics.median(steps):.0f}s")


if __name__ == "__main__":
    if sys.argv[1] == "probe":
        probe(float(sys.argv[2]), Path(sys.argv[3]))
    else:
        report(Path(sys.argv[2]))
