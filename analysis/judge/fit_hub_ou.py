"""Fit hub_surge's relative dynamics (pricing.RelativeRaceSim) on a window of the recorded crowding.

    python3 analysis/judge/fit_hub_ou.py <raw_dir> <start ISO> <end ISO>

TfL stamps every 5 min, so 1-minute changes are mostly zero: readings are binned by their TfL stamp into 5-min slots
and fitted publish to publish (pricing.fit_relative_ou). Prints kappa per minute (markets.HUB_KAPPA), each hub's
innovation sd and the innovations' correlation (markets.HUB_CORR). Read-only.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402

BIN_MS = 5 * 60_000


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    spots = load_hotspots()
    store = Store(raw, spots)
    store.refresh(start, end)
    grid: dict[int, dict[str, tuple[float, float]]] = {}
    for ts, snap in store.between("crowding", start, end):
        for h in spots:
            nap = h["naptan"]
            stamp = store.tfl_stamp(ts, nap) or ts
            typ = store.typical(nap, stamp)
            if nap in snap and typ:
                grid.setdefault(round(stamp / BIN_MS), {})[h["id"]] = (math.log(snap[nap]), math.log(snap[nap] / typ))
    ids = [h["id"] for h in spots]
    kappa, sds, corr, n = pricing.fit_relative_ou(grid, ids)
    print(f"kappa = {kappa:.4f} per min ({n} joint 5-min steps)")
    for h in ids:
        print(f"  {h:14} innovation sd {sds[h]:.4f} per min")
    print("innovation correlation (order " + ", ".join(ids) + "):")
    for h, row in zip(ids, corr):
        print("  [" + ", ".join(f"{c:+.2f}" for c in row) + "],")


if __name__ == "__main__":
    main()
