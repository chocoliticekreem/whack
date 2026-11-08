"""Fit hub_surge's relative dynamics (pricing.fit_relative_ou) on a window of the recorded crowding.

    python3 analysis/judge/fit_hub_ou.py <raw_dir> <start ISO> <end ISO>

The live builder refits this every round on the readings up to its open (markets.hub_fits); this prints the same
fit at the window's end for k = 1-4 TfL steps (readings binned by their TfL stamp into 5-min slots): the reversion b,
each hub's level against the others, residual sd and correlation. Read-only.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import markets as mk  # noqa: E402
import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    spots = load_hotspots()
    store = Store(raw, spots)
    store.refresh(start, end)
    ids = [h["id"] for h in spots]
    grid = mk.hub_grid(store, spots, start, end)
    for k in range(1, 5):
        fit = pricing.fit_relative_ou(grid, ids, k, (end - mk.HISTORY_MS) // pricing.HUB_STEP_MS, mk.HUB_LEVEL_PRIOR,
                                      mk.HUB_SD_FLOOR * math.sqrt(5 * k))
        if fit is None:
            print(f"k={k}: too few joint steps")
            continue
        print(f"k={k} ({5 * k} min, {fit.pairs} joint steps): b = {fit.b:.3f}, compounded kappa "
              f"{-math.log(max(1e-9, 1 - fit.b)) / (5 * k):.4f} per min")
        for h, lv, sd in zip(ids, fit.levels, fit.sds):
            print(f"  {h:14} level {lv:+.3f}  residual sd {sd:.4f}")
        print("  correlation (order " + ", ".join(ids) + "):")
        for row in fit.corr:
            print("  [" + ", ".join(f"{c:+.2f}" for c in row) + "],")

if __name__ == "__main__":
    main()
