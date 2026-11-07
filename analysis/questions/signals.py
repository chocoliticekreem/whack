"""Which visible signals actually predict city crowding moves? Pooled over all 20 TfL Crowding stations.

    python3 analysis/questions/signals.py

For each station and each open (every 5 min), compare the sign of the live change over the next h minutes with:
- typical: the sign of the typical-Saturday change over the same h (the 'today vs typical Saturday' chart);
- momentum: the sign of the live change over the previous h;
- gap: live above typical now -> expect a fall back toward typical (reversion to the usual Saturday).
Also: the Arsenal egress check (King's Cross vs the other hubs after the 16:55 estimated egress), and the VIX after
the multiverse warm-up.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import series as S
from evaluate import value_at, vix_series, reversion, momentum
from load import MIN, hhmm, corr

HERE = Path(__file__).resolve().parent


def station_series():
    out: dict[str, list[tuple[int, float]]] = {}
    for ts, snap in S.crowding_snaps():
        for nap, v in snap.items():
            out.setdefault(nap, []).append((ts, v))
    return out


def sign(x, eps=1e-9):
    return 0 if abs(x) < eps else (1 if x > 0 else -1)


def pooled(h):
    ss = station_series()
    tally = {"typical": [0, 0], "momentum": [0, 0], "gap": [0, 0]}
    xs, ys = [], []
    for nap, ser in ss.items():
        t = ser[0][0] - ser[0][0] % (5 * MIN) + 5 * MIN + h * MIN
        while t + h * MIN <= ser[-1][0]:
            a, b = value_at(ser, t, 6 * MIN), value_at(ser, t + h * MIN, 6 * MIN)
            p = value_at(ser, t - h * MIN, 6 * MIN)
            ta, tb = S.typical(nap, t), S.typical(nap, t + h * MIN)
            if None not in (a, b, p) and ta and tb:
                real = sign(b - a, 1e-4)
                if real:
                    for name, s in (("typical", sign(tb - ta)), ("momentum", sign(a - p, 1e-4)),
                                    ("gap", -sign(a / ta - 1, 0.02))):
                        if s:
                            tally[name][0] += s == real
                            tally[name][1] += 1
                    xs.append(tb / ta - 1)
                    ys.append(b / a - 1)
            t += 5 * MIN
    return {k: {"hit": round(h_ / n, 3) if n else None, "n": n} for k, (h_, n) in tally.items()} | {
        "corr_typical_vs_real_pct_change": round(corr(xs, ys), 3)}


def arsenal():
    """King's Cross live/typical before and after the estimated Emirates egress (16:55)."""
    hr = S.hub_ratio()
    rows = []
    for ts, r in hr:
        if 16 * 60 + 30 <= int(hhmm(ts)[:2]) * 60 + int(hhmm(ts)[3:]) <= 18 * 60 + 20:
            rows.append((hhmm(ts), {k: round(v, 2) for k, v in r.items()}))
    return rows[::2]


def vix_after(warm_hhmm="14:30"):
    ser = [(ts, v) for ts, v in vix_series() if hhmm(ts) >= warm_hhmm]
    if not ser:
        return None
    vs = [v for _, v in ser]
    d5 = []
    for i, (ts, v) in enumerate(ser):
        w = value_at(ser, ts + 5 * MIN, 3 * MIN)
        if w is not None:
            d5.append(w - v)
    rev, mom = reversion(15, 3 * MIN), momentum(5, 3 * MIN)
    hits = {"reversion_15": [0, 0], "momentum_5": [0, 0]}
    for ts, v in ser[::5]:
        w = value_at(ser, ts + 5 * MIN, 3 * MIN)
        if w is None or w == v:
            continue
        for name, fn in (("reversion_15", rev), ("momentum_5", mom)):
            c = fn(ts, ser)
            if c:
                hits[name][0] += (c > 0) == (w > v)
                hits[name][1] += 1
    return {"from": warm_hhmm, "frames": len(ser), "min": min(vs), "p50": statistics.median(vs), "max": max(vs),
            "abs_5min_change_p50": round(statistics.median(abs(x) for x in d5), 3),
            "share_5min_change_lt_0.2": round(sum(1 for x in d5 if abs(x) < 0.2) / len(d5), 3),
            "signals_vs_no_line": {k: {"hit": round(a / b, 3) if b else None, "n": b} for k, (a, b) in hits.items()}}


def vix_drivers():
    p = HERE / "out" / "vix.json"
    if not p.exists():
        return None
    rows = json.loads(p.read_text())["rows"]
    out: dict[str, int] = {}
    for r in rows:
        if hhmm(r["ts"]) >= "14:30":
            out[r["driver"]] = out.get(r["driver"], 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1])[:8])


def main():
    res = {f"pooled_{h}min": pooled(h) for h in (5, 15, 30, 60)}
    res["arsenal_hub_ratio"] = arsenal()
    res["vix_after_warmup"] = vix_after()
    res["vix_drivers_after_warmup"] = vix_drivers()
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "signals.json").write_text(json.dumps(res, indent=1))
    for k, v in res.items():
        print(f"\n## {k}")
        if isinstance(v, list):
            for row in v:
                print("  ", row)
        else:
            print("  ", v)


if __name__ == "__main__":
    main()
