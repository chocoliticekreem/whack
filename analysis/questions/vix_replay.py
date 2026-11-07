"""Replay the Oxford Circus multiverse in-process over recorded frames and dump the London VIX per minute.

    ANTHROPIC_API_KEY= python3 analysis/questions/vix_replay.py [from=17:00] [to=18:20] [n=40]

No server, no network (brokers off). Reads services/multiverse read-only. Writes the series as JSON to stdout's
last line and to analysis/questions/out/vix.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "services" / "multiverse"))
os.environ["ANTHROPIC_API_KEY"] = ""

from load import LONDON, raw_root  # noqa: E402

os.environ.setdefault("MULTIVERSE_RAW", str(raw_root()))
from server import App  # noqa: E402


def ms(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return int(datetime(2026, 11, 7, h, m, tzinfo=LONDON).timestamp() * 1000)


def main() -> None:
    a = sys.argv[1:]
    t0, t1 = ms(a[0] if a else "17:00"), ms(a[1] if len(a) > 1 else "18:20")
    n = int(a[2]) if len(a) > 2 else 40
    app = App()
    started = time.perf_counter()
    state = app.spawn({"n": n, "mode": "replay", "brokers": "off", "from": t0, "to": t1})
    rows = []
    while True:
        out = app.observe({"next": True})
        obs = out.get("observation")
        if obs is None:
            break
        rows.append({"ts": obs.get("ts"), "minute": obs.get("minute"), "vix": out.get("vix"),
                     "driver": out.get("vix_driver"), "cams": obs.get("compared")})
        print(f"{len(rows):3d} vix={out.get('vix'):6.2f} driver={out.get('vix_driver')}", flush=True)
    hist = out.get("vix_history") or state.get("vix_history") or []
    res = {"rows": rows, "history": hist, "seconds": round(time.perf_counter() - started, 1)}
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "vix.json").write_text(json.dumps(res))
    print(f"frames={len(rows)} history={len(hist)} seconds={res['seconds']}")


if __name__ == "__main__":
    main()
