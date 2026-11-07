"""Samples every market's prices, status and live clue every 5 s to scratch/prices.jsonl, so price-vs-reading lag
can be shown after the fact.

    python3 analysis/live_trading/prices.py --url http://127.0.0.1:8161 --minutes 80
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

from client import SCRATCH


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8161")
    ap.add_argument("--minutes", type=float, default=80)
    a = ap.parse_args()
    end = time.time() + a.minutes * 60
    with open(SCRATCH / "prices.jsonl", "a", buffering=1) as f:
        while time.time() < end:
            try:
                with urllib.request.urlopen(a.url + "/markets", timeout=10) as r:
                    d = json.loads(r.read())
            except (OSError, ValueError):
                time.sleep(5)
                continue
            for m in d["markets"]:
                clue = next((s.get("data") for s in m.get("signals") or []), None) or {}
                f.write(json.dumps({"t": d["now"], "m": m["id"], "status": m["status"],
                                    "p": {o["id"]: o["prob"] for o in m["options"]},
                                    "alive": m["futures"].get("alive"), "ruledOut": m["futures"].get("ruledOut"),
                                    "clue": {k: clue.get(k) for k in ("vix", "change5", "pulse", "total", "laggard",
                                                                       "leader") if k in clue},
                                    "halt": m.get("haltReason")}) + "\n")
            time.sleep(5)


if __name__ == "__main__":
    main()
