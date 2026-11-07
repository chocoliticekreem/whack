"""Scraper runner: each scraper writes one record per run to data/raw/scrape_<name>/<day>/<hour>.ndjson.gz.

    python3 recorder/scrape/run.py          # refresh every 30 min, forever
    python3 recorder/scrape/run.py --once   # run every scraper once and print what it found
"""

import argparse
import json
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import DATA, iso, sleep_until, utc_now, write_record  # noqa: E402
from scrape import fixtures  # noqa: E402

SCRAPERS = {"fixtures": fixtures.scrape}
REFRESH = 1800

log = logging.getLogger("scrape")


def run_all(verbose: bool) -> None:
    for name, fn in SCRAPERS.items():
        ts = utc_now()
        try:
            rows = fn(ts, log)
        except Exception as e:  # noqa: BLE001 - a failed scrape is logged and retried next round
            log.error(f"{name}: {type(e).__name__}: {e}")
            print(f"[{iso(ts)}] {name}: error {e}", flush=True)
            continue
        write_record(f"scrape_{name}", ts, rows)
        print(f"[{iso(ts)}] {name}: {len(rows)} rows", flush=True)
        if verbose:
            for row in rows[:3]:
                print("  " + json.dumps(row), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="run every scraper once and exit")
    args = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA / "scrape.log", level=logging.INFO, format="%(asctime)s %(message)s")
    if args.once:
        run_all(verbose=True)
        return
    stop = threading.Event()
    next_at = utc_now().timestamp()
    try:
        while not stop.is_set():
            run_all(verbose=False)
            next_at += REFRESH
            sleep_until(next_at, stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
