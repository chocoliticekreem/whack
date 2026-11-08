"""Monte Carlo error of the opening price: rebuild the same open with K different seeds and measure the spread.

    python3 analysis/judge/mc_error.py <raw_dir> [K] [N]

For every series in the lineup, at 16 opens spread over the recorded day, each market is built K times from the same
readings with seed_of(id) salted by k. Reports the standard deviation of the opening model price across seeds (the
Monte Carlo error a single build carries; the largest option's, in points), the spread of the line where the series
places one on the futures, the spread with every build's line held at the first build's (sampling error alone), and
the CPU time of one build. N overrides pricing.N_FUTURES (to size it). Read-only: never writes the recorder data.
"""
from __future__ import annotations

import statistics
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import markets as mk  # noqa: E402
import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso + "+00:00").timestamp() * 1000)


def main() -> None:
    raw, k_seeds = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 20
    if len(sys.argv) > 3:
        pricing.N_FUTURES = int(sys.argv[3])
    spots = load_hotspots()
    store = Store(raw, spots)
    salt = {"k": 0}
    mk.seed_of = lambda mid: zlib.crc32(f"{mid}#{salt['k']}".encode())
    sds: dict[str, list[float]] = {}
    lines: dict[str, list[float]] = {}
    fixed: dict[str, list[float]] = {}
    cpu: dict[str, list[float]] = {}
    for now in (ms("2026-11-07T13:01:00") + k * 37 * 60_000 for k in range(16)):
        store.refresh(now - mk.HISTORY_MS, now)
        at = (now + mk.CLOSE_MS) // 300_000 * 300_000 + 300_000
        for sid, series in mk.SERIES.items():
            if series.due and not series.due(at):
                continue
            built = []
            for k in range(k_seeds):
                salt["k"] = k
                t = time.process_time()
                m = series.build(f"{sid}-mc", store, spots, now, at)
                cpu.setdefault(sid, []).append(time.process_time() - t)
                if m is None:
                    break
                built.append(m)
            if len(built) < k_seeds:
                continue
            probs = [m.futures.probs() for m in built]
            sds.setdefault(sid, []).append(max(statistics.pstdev([p[o] for p in probs]) for o in probs[0]))
            line = [m.spec.get("line") for m in built]
            if all(isinstance(x, (int, float)) for x in line):
                lines.setdefault(sid, []).append(statistics.pstdev(line))
            if hasattr(built[0].futures.sim, "strike"):
                for m in built:
                    m.futures.sim.strike = built[0].futures.sim.strike
                probs = [m.futures.probs() for m in built]
                fixed.setdefault(sid, []).append(max(statistics.pstdev([p[o] for p in probs]) for o in probs[0]))
    print(f"N_FUTURES={pricing.N_FUTURES}, K={k_seeds} seeds per open")
    print(f"{'series':15} {'opens':>5} {'MC SD mean':>10} {'MC SD max':>9} {'line SD':>8} {'same line':>9} "
          f"{'build ms':>8}")
    for sid, v in sds.items():
        line = f"{statistics.fmean(lines[sid]):8.3f}" if sid in lines else f"{'-':>8}"
        same = f"{100 * statistics.fmean(fixed[sid]):9.2f}" if sid in fixed else f"{'-':>9}"
        print(f"{sid:15} {len(v):5d} {100 * statistics.fmean(v):10.2f} {100 * max(v):9.2f} {line} {same} "
              f"{1000 * statistics.fmean(cpu[sid]):8.1f}")


if __name__ == "__main__":
    main()
