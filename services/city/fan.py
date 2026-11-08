"""The futures fan: every market's 50 simulated futures as small paths of the number it settles on, next to the
real readings so far, so the UI can show the futures behind each price.

`futures` on each market in GET /markets:
    {n, alive, ruledOut, byOption {option id: alive futures landing there (sums to alive)},
     paths [[[ts, value], ...] per future, ≤ 12 points], alivePaths [1 | 0 per path],
     real [[ts, value], ...] (readings since open, ≤ 12 points), lines [strike or bracket edges],
     label (what the paths measure), unit ("%", "pts" or ""), about (one line, or ""),
     realised {value, closest} once settled (closest = the index of the future that landed nearest)}

The value per series: City Pulse; London VIX; planes holding; Good lines broken (clean sheet: 0 until a break);
the leading hub's rise in vs usual, in points (hub_surge); the top hub's vs usual, in % (hub_hour); a station's
% of usual (bracket ladders).
"""
from __future__ import annotations

import math

import markets as mk
import pricing
from pricing import MIN_MS

MAX_POINTS = 12
VIX_AREA = "Oxford Circus"


def pick(n: int, k: int = MAX_POINTS) -> list[int]:
    """Up to k indices spread evenly over range(n), always the first and the last."""
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def rnd(v: float) -> float:
    return round(v, 3 if abs(v) < 10 else 1)


def thin(points: list[tuple[int, float]]) -> list[list]:
    return [[int(points[i][0]), rnd(points[i][1])] for i in pick(len(points))]


# ---------------------------------------------------------------- the simulated paths, per simulator


def walk_paths(sim, value) -> list[list[tuple[int, float]]]:
    """Walk simulators (ScalarSim, CrowdSim and its subclasses): one value per simulated minute, value(i, k)."""
    ks = pick(sim.m + 1)
    return [[(sim.t0 + k * MIN_MS, value(i, k)) for k in ks] for i in range(pricing.N_FUTURES)]


def race_value(sim: pricing.RaceSim, i: int, k: int) -> float:
    """The leading hub's score at minute k: vs usual (typical value interpolated from open to settle), or its rise
    since open in points for a 'gain' race."""
    frac = k / max(1, sim.m)
    best = -math.inf
    for j in range(len(sim.stations)):
        typ = sim.typ0[j] + (sim.typ_t[j] - sim.typ0[j]) * frac
        vs = math.exp(sim.paths[i][j][k]) / typ
        score = (vs - math.exp(sim.stations[j][2]) / sim.typ0[j]) * 100 if sim.mode == "gain" else vs * 100
        best = max(best, score)
    return best


def sim_paths(sim) -> list[list[tuple[int, float]]]:
    if hasattr(sim, "fan_paths"):
        return sim.fan_paths()
    end = sim.t0 + sim.m * MIN_MS
    if isinstance(sim, pricing.DeltaSim):
        return [[(sim.t0, sim.x0), (end, f)] for f in sim.finals]
    if isinstance(sim, pricing.BreakSim):
        out = []
        for b in sim.break_at:
            if b > sim.m:
                out.append([(sim.t0, 0.0), (end, 0.0)])
            else:
                tb = sim.t0 + int(b * MIN_MS)
                out.append([(sim.t0, 0.0), (tb, 0.0), (tb, 1.0), (end, 1.0)])
        return out
    if isinstance(sim, pricing.RaceSim):
        return walk_paths(sim, lambda i, k: race_value(sim, i, k))
    if isinstance(sim, pricing.ScalarSim):
        return walk_paths(sim, lambda i, k: math.exp(sim.paths[i][k]))
    if isinstance(sim, pricing.CrowdSim):
        return walk_paths(sim, lambda i, k: math.exp(sim.paths[i][0][k]) * 100)
    return []


# ---------------------------------------------------------------- the real readings, per series


def hub_reader(m, store, gain: bool):
    spots = [{"id": hid, "naptan": nap} for hid, nap in m.spec["stations"].items()]

    def read(ts: int, snap: dict) -> float | None:
        vs = mk.vs_usual(store, spots, ts, snap)
        if vs is None:
            return None
        if gain:
            return max((v - m.spec["open"][h]) * 100 for h, v in vs.items())
        return max(vs.values()) * 100
    return read


def clean_reader(m):
    good = m.spec["good"]
    return lambda ts, snap: float(sum(1 for l in good if l in snap and snap[l]["sev"] != pricing.GOOD_SEVERITY))


def series_view(m, store) -> tuple[str, str, list[float], object]:
    """(label, unit, lines, read(ts, snap) -> value | None) for the market's series."""
    sub, spec = m.sub, m.spec
    if spec.get("urban"):
        def read(ts, snap):
            got = mk.urban.features(store, spec, ts)
            return mk.urban.score(spec, spec["base"], got[0]) if got else None
        return "Change score (positive = Yes)", "", [0.0], read
    if sub.startswith("local_"):
        import local
        return local.series_view(m, store)
    if sub == "city_pulse":
        return "City Pulse", "", [spec["line"]], lambda ts, s: mk.pulse_of(s)
    if sub == "london_vix":
        return "London VIX", "", [spec["line"]], lambda ts, s: s.get("vix")
    if sub == "heathrow_stack":
        return "Planes holding", "", [spec["line"]], lambda ts, s: float(s["total"])
    if sub == "clean_sheet":
        return "Good lines broken", "", [0.5], clean_reader(m)
    if sub == "hub_surge":
        return "Leading hub's rise in vs usual", "pts", [0.0], hub_reader(m, store, True)
    if sub == "hub_hour":
        return "Top hub vs usual", "%", [100.0], hub_reader(m, store, False)
    nap = spec.get("naptan")
    return (f"{spec.get('name', 'Station')} busyness", "%", list(spec.get("edges", [])),
            lambda ts, s: s[nap] * 100 if nap in s else None)


def about(m, store) -> str:
    if m.sub != "london_vix":
        return ""
    last = store.latest("vix", m.settles_at)
    area = (last[1].get("area") if last else None) or VIX_AREA
    return f"The London VIX is the spread across 50 simulated futures of {area}"


def real_path(m, store, read, now: int) -> list[tuple[int, float]]:
    start = m.spec.get("openTs", m.opened_at)
    end = min(now, m.settles_at)
    if m.result and (m.result.get("settlement") or {}).get("fetchedAt"):
        end = max(end, m.result["settlement"]["fetchedAt"])
    out = []
    for ts, snap in store.between(m.feed, start, end):
        v = read(ts, snap)
        if v is not None:
            out.append((ts, v))
    return out


def realised_value(m, real: list[tuple[int, float]]) -> float | None:
    """The settled number in the fan's units (vs usual is a fraction in the settlement); None when void."""
    if m.result.get("winner") is None:
        return None
    v = (m.result.get("settlement") or {}).get("value")
    if v is None:
        return real[-1][1] if real else None
    return v * 100 if m.sub in ("hub_surge", "hub_hour") else float(v)


def futures_json(m, store, now: int) -> dict:
    fut = m.futures
    by_option = {o: 0 for o in m.option_ids()}
    for i in range(pricing.N_FUTURES):
        if fut.alive[i]:
            by_option[fut.sim.outcome(i)] += 1
    label, unit, lines, read = series_view(m, store)
    paths = sim_paths(fut.sim)
    real = real_path(m, store, read, now)
    out = {"n": pricing.N_FUTURES, "alive": sum(fut.alive), "ruledOut": fut.ruled_out,
           "byOption": by_option, "paths": [thin(p) for p in paths], "alivePaths": [int(a) for a in fut.alive],
           "real": thin(real), "lines": [rnd(x) for x in lines], "label": label, "unit": unit,
           "about": about(m, store)}
    if m.result:
        value = realised_value(m, real)
        closest = None
        if value is not None and paths:
            closest = min(range(len(paths)), key=lambda i: (not fut.alive[i], abs(paths[i][-1][1] - value)))
        out["realised"] = {"value": None if value is None else rnd(value), "closest": closest}
    return out
