"""Particle-filter audit on the recorded day: measurement noise of the TfL readings, and weight degeneracy in-round.

    python3 analysis/judge/pf_audit.py <raw_dir> <start ISO> <end ISO>

1. Noise. A random walk read with white noise, y_t = x_t + n_t, has first differences with lag-1 autocovariance
   -σ_n² and variance σ_w²·gap + 2σ_n². Per hub and for City Pulse (log vs usual, one value per TfL stamp, pairs
   4-6 min apart) this prints σ_n, σ_w per minute, and the likelihood width the filter uses (tol × the walk's
   per-minute sigma, as Futures.reweight does), all in log points (0.01 = 1%).
2. Degeneracy. The real Exchange replays the window (in-memory ledger); every Futures.reweight call logs, per
   series, the survivors, the ESS of the raw likelihood weights over them, and whether tempering stepped in.
Read-only: never writes the recorder data.
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import markets as mk  # noqa: E402
import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402
from exchange import Clock, Exchange  # noqa: E402
from ledger import Ledger  # noqa: E402


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def stamped(store: Store, start: int, end: int, value_of) -> list[tuple[int, float]]:
    """(TfL measured-at ms, log value) once per TfL stamp."""
    out = []
    for ts, snap in store.between("crowding", start, end):
        v = value_of(ts, snap)
        at = store.measured_at(ts)
        if v and (not out or at > out[-1][0]):
            out.append((at, math.log(v)))
    return out


def noise(series: list[tuple[int, float]]) -> tuple[float, float, int] | None:
    """(σ_n, σ_w per minute, pairs) from consecutive differences 4-6 min apart."""
    d = []
    for (t0, x0), (t1, x1) in zip(series, series[1:]):
        gap = (t1 - t0) / pricing.MIN_MS
        d.append((x1 - x0, gap) if 4 <= gap <= 6 else None)
    pairs = [(a[0], b[0]) for a, b in zip(d, d[1:]) if a and b]
    flat = [x for x in d if x]
    if len(pairs) < 10:
        return None
    mean = statistics.fmean(x for x, _ in flat)
    g1 = statistics.fmean((a - mean) * (b - mean) for a, b in pairs)
    g0 = statistics.fmean((x - mean) ** 2 for x, _ in flat)
    sn2 = max(0.0, -g1)
    sw2 = max(0.0, g0 - 2 * sn2) / statistics.fmean(g for _, g in flat)
    return math.sqrt(sn2), math.sqrt(sw2), len(pairs)


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    spots = load_hotspots()
    store = Store(raw, spots)
    store.refresh(start - mk.HISTORY_MS, end)

    print("1. Measurement noise (log points): σ_n from the lag-1 autocovariance; the filter's likelihood sd is "
          "tol × the walk's per-minute sigma")
    print(f"   {'series':15} {'pairs':>5} {'σ_n':>7} {'σ_w/min':>8} {'walk σ/min':>10} {'likelihood sd':>13}")
    rows = [("City Pulse", lambda ts, s: mk.pulse_of(s), mk.pulse_vs_usual(store, start - mk.HISTORY_MS, end))]
    for h in spots:
        nap = h["naptan"]
        rows.append((h["id"], lambda ts, s, nap=nap: s.get(nap) and s[nap] / (store.usual(nap, ts) or 1.0),
                     mk.crowd_series(store, nap, start - mk.HISTORY_MS, end)))
    for name, value_of, fit_series in rows:
        got = noise(stamped(store, start, end, value_of))
        if got is None:
            continue
        _, walk_sd = pricing.fit_log_walk(fit_series, 0.01, 0.002, 0.05)
        print(f"   {name:15} {got[2]:5d} {got[0]:7.4f} {got[1]:8.4f} {walk_sd:10.4f} {1.5 * walk_sd:13.4f}")

    log: dict[str, list[tuple[int, float, bool]]] = defaultdict(list)
    plain = pricing.Futures.reweight

    def logged(self, errs):
        if errs:
            w = [math.exp(-0.5 * min(e / self.sim.tol, 30.0) ** 2) * self.weights[i] for e, i in errs]
            raw_ess = pricing.ess(w) / len(w)
            log[self.series].append((len(errs), raw_ess, raw_ess < pricing.MIN_ESS))
        plain(self, errs)

    pricing.Futures.reweight = logged
    ex = Exchange(Store(raw, spots), Clock(start), spots, Ledger(":memory:"))
    t = start
    while t <= end:
        for m in ex.active():
            m.futures.series = m.sub
        ex.tick(t)
        t += 30_000
    print("\n2. Reweighting in-round (generic filter path; hub_surge restarts and heathrow_stack reweights in closed "
          "form instead)")
    print(f"   {'series':15} {'updates':>7} {'survivors':>9} {'raw ESS/N':>9} {'tempered':>8}")
    for s, rows_ in sorted(log.items()):
        print(f"   {s:15} {len(rows_):7d} {statistics.fmean(r[0] for r in rows_):9.1f} "
              f"{statistics.fmean(r[1] for r in rows_):9.2f} {sum(r[2] for r in rows_) / len(rows_):8.0%}")
    ruled = defaultdict(list)
    for m in ex.markets.values():
        ruled[m.sub].append(m.futures.ruled_out)
    print("   futures ruled out by the end of the round (mean of 50): " +
          ", ".join(f"{s} {statistics.fmean(v):.1f}" for s, v in sorted(ruled.items())))


if __name__ == "__main__":
    main()
