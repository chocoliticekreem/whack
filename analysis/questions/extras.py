"""Extra checks used in docs/questions.md.

    python3 analysis/questions/extras.py

- ties: how often the 5-min 'which hub gains most on its usual Saturday' race has a tied top (no TfL update);
- feed cadence: median and max gap per settle feed (void risk);
- peak: each station's typical-Saturday pm peak band, and today's city busyness peak so far;
- holding: hourly range of the Heathrow holding total;
- cams: fresh-camera coverage over the day.
"""
from __future__ import annotations

import statistics

import series as S
from evaluate import value_at
from load import MIN, hhmm


def ties():
    hr = S.hub_ratio()
    n = tied = zero = 0
    for ts, r0 in hr:
        r1 = value_at(hr, ts + 5 * MIN, 6 * MIN)
        if not r1 or ts + 5 * MIN > hr[-1][0]:
            continue
        g = sorted((r1[o] - r0[o] for o in r0), reverse=True)
        n += 1
        tied += abs(g[0] - g[1]) < 1e-6
        zero += all(abs(x) < 1e-6 for x in g)
    return {"n": n, "top_tied": tied, "all_flat": zero}


def cadence():
    out = {}
    for name, ser in (("crowding", S.crowding_snaps()), ("line_status", S.line_snaps()),
                      ("jamcams", S.raw("jamcams")), ("aircraft", S.raw("aircraft")),
                      ("bikepoint", S.raw("bikepoint"))):
        gaps = [(b[0] - a[0]) / MIN for a, b in zip(ser, ser[1:])]
        out[name] = {"n": len(ser), "gap_p50_min": round(statistics.median(gaps), 2), "gap_max_min": round(max(gaps), 1),
                     "first": hhmm(ser[0][0]), "last": hhmm(ser[-1][0])}
    # crowding: how often the TfL value for a hub actually changes between consecutive readings
    snaps = S.crowding_snaps()
    same = sum(1 for (_, a), (_, b) in zip(snaps, snaps[1:]) if a.get(S.HUBS["Oxford Circus"]) == b.get(
        S.HUBS["Oxford Circus"]))
    out["crowding"]["oxford_unchanged_between_readings"] = round(same / (len(snaps) - 1), 3)
    return out


def peaks():
    prof = S.profile()
    names = {r["naptan"]: r["name"] for _, p in S.raw("crowding_profile")[:1] for r in [p]}
    bands = {}
    for _, p in S.raw("crowding_profile"):
        sat = next(d for d in p["daysOfWeek"] if d["dayOfWeek"] == "SAT")
        bands[p["name"]] = sat.get("pmPeakTimeBand")
    city_typ = {}
    for nap, b in prof.items():
        for k, v in b.items():
            city_typ[k] = city_typ.get(k, 0) + v
    typ_peak = max((k for k in city_typ if "15:00" <= k <= "21:00"), key=lambda k: city_typ[k])
    raw = S.crowd_raw()
    live_peak = max(raw, key=lambda x: x[1])
    del names
    return {"pm_peak_bands": bands, "city_typical_peak_band": typ_peak,
            "live_city_peak_so_far": (hhmm(live_peak[0]), live_peak[1]), "live_last": (hhmm(raw[-1][0]), raw[-1][1])}


def holding_by_hour():
    out = {}
    for ts, v in S.holding():
        out.setdefault(hhmm(ts)[:2], []).append(v)
    return {h: (min(v), round(statistics.mean(v), 1), max(v)) for h, v in out.items()}


def cams_cov():
    out = {}
    for ts, p in S.raw("jamcams"):
        fresh = sum(1 for c in p if c.get("yolo") and ts - c["yolo"].get("at", 0) <= S.CAM_MAX_AGE_MS)
        out.setdefault(hhmm(ts)[:4] + "0", []).append(fresh)
    return {k: max(v) for k, v in out.items() if k >= "16:30"}


def main():
    for name, fn in (("ties", ties), ("cadence", cadence), ("peaks", peaks), ("holding_by_hour", holding_by_hour),
                     ("cams_fresh_by_10min", cams_cov)):
        print(f"\n## {name}\n  ", fn())


if __name__ == "__main__":
    main()
