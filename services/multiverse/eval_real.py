"""Does the multiverse market forecast real Oxford Circus better than naive baselines?

    python3 services/multiverse/eval_real.py --raw data/raw --windows 6 [--seed 7]

Runs independent five-minute markets (n=50 worlds, brokers off, no network) over the recorder's real
`jamcams` + `bus` data for the newest recorded day. Each market is reseeded at its window's first frame and
then fed the next frames exactly as the server's replay feeds them. Before each frame is observed, every
method forecasts what that frame will show:

- per camera: a NEW YOLO people count (one whose `at` was not seen before; repeats of a count across
  snapshots are observed by the market but never scored);
- bus: the frame's lateness proxy (`busDelayMin`).

Methods: the market (price-weighted mean over live worlds), an equal-weight market (the same spawned worlds,
uniform weights, never pruned or resampled), persistence (the last observed value of that variable, any time
earlier today; a camera with no earlier count falls back to the seed typical), and the seed typical (the
seed's recorded count for that camera, else the mean of its counted cameras; bus: mean of today's bus reads
before the window).

80% interval: every method is read through the engine's own noise model (counts Gaussian on the sqrt scale,
sd sqrt(POISSON_SD^2 + MODEL_SD^2); bus Gaussian, sd BUS_SD_MIN; both floored at 0), so a market's interval is
the 10th-90th percentile of the price-weighted mixture of its worlds and a baseline's is that of one centre.

No lookahead: windows start at or after the seed's own timestamp, because the seed pins its streets to camera
counts recorded before it was built and an earlier window would be scored on those very counts.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import brokers

os.environ.pop("ANTHROPIC_API_KEY", None)
brokers.load_env = lambda: None

import engine  # noqa: E402
import feeds  # noqa: E402

HERE = Path(__file__).resolve().parent
SEED_PATH = HERE / "seeds" / "oxford-circus.json"
N = 50
HORIZON = engine.DEFAULT_HORIZON
CRN_SEED = 47
Q_LO, Q_HI = 0.1, 0.9
COUNT_SD = math.sqrt(engine.POISSON_SD ** 2 + engine.MODEL_SD ** 2)
BUS_SD = engine.BUS_SD_MIN
METHODS = ("market", "equal", "persist", "typical")
LABELS = {"market": "market (priced, pruned)", "equal": "equal-weight worlds (no pruning)",
          "persist": "persistence (last value)", "typical": "seed typical"}


# ---------------------------------------------------------------- metrics


def mae(forecasts: list[float], observed: list[float]) -> float | None:
    if not observed:
        return None
    return sum(abs(f - o) for f, o in zip(forecasts, observed)) / len(observed)


def coverage(intervals: list[tuple[float, float]], observed: list[float]) -> float | None:
    """Share of observations inside their interval (ends inclusive)."""
    if not observed:
        return None
    return sum(lo <= o <= hi for (lo, hi), o in zip(intervals, observed)) / len(observed)


def persistence_forecasts(values: list[float | None], fallback: float) -> list[float]:
    """Forecast for each index = the last non-None value before it; fallback until there is one."""
    out, last = [], None
    for v in values:
        out.append(fallback if last is None else last)
        if v is not None:
            last = v
    return out


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def mixture_cdf(y: float, centres: list[float], weights: list[float], kind: str) -> float:
    """P(Y <= y) for the weighted mixture of the engine's noise model around each centre, floored at 0."""
    if y < 0:
        return 0.0
    total = sum(weights) or 1.0
    if kind == "count":
        zs = ((math.sqrt(y) - math.sqrt(max(c, 0.0))) / COUNT_SD for c in centres)
    else:
        zs = ((y - c) / BUS_SD for c in centres)
    return sum(w * phi(z) for w, z in zip(weights, zs)) / total


def mixture_quantile(q: float, centres: list[float], weights: list[float], kind: str) -> float:
    if mixture_cdf(0.0, centres, weights, kind) >= q:
        return 0.0
    lo, hi = 0.0, max(1.0, max(centres))
    while mixture_cdf(hi, centres, weights, kind) < q:
        hi *= 2
    for _ in range(50):
        mid = (lo + hi) / 2
        if mixture_cdf(mid, centres, weights, kind) < q:
            lo = mid
        else:
            hi = mid
    return hi


def interval80(centres: list[float], weights: list[float], kind: str) -> tuple[float, float]:
    return (mixture_quantile(Q_LO, centres, weights, kind), mixture_quantile(Q_HI, centres, weights, kind))


def weighted_mean(centres: list[float], weights: list[float]) -> float:
    total = sum(weights) or 1.0
    return sum(c * w for c, w in zip(centres, weights)) / total


# ---------------------------------------------------------------- data


def newest_day(raw: Path) -> tuple[int, int, str]:
    days = sorted(p.name for p in (raw / "jamcams").glob("????-??-??") if p.is_dir())
    if not days:
        raise SystemExit(f"no recorded jamcams under {raw}")
    start = datetime.strptime(days[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000) - 1, days[-1]


def load_day(raw: Path, seed: dict, start: int, end: int) -> list[dict]:
    """One frame per recorded jamcams minute (as feeds.load_real_frames), plus `new`: the counts that first
    appear in that frame."""
    cams = {c["id"] for c in seed["cams"]}
    naptans = {b["naptanId"] for b in seed.get("buses", []) if b.get("naptanId")}
    buses = feeds.bus_snapshots(raw, naptans, start, end)
    seen: set[tuple[str, int | None]] = set()
    frames: dict[int, dict] = {}
    for ts, payload in feeds.records_between(raw, "jamcams", start, end):
        frame = feeds.real_frame(payload, ts, cams, buses)
        frame["new"] = dict(frames.get(ts // 60_000, {}).get("new", {}))
        for cid, r in feeds.jamcam_readings(payload).items():
            key = (cid, r["at"])
            if cid not in cams or key in seen:
                continue
            seen.add(key)
            if cid in frame["readings"]:
                frame["new"][cid] = r["people"]
        frames[ts // 60_000] = frame
    return [frames[k] for k in sorted(frames)]


def seed_typical(seed: dict) -> dict[str, float]:
    counted = {c["id"]: float(c["people"]) for c in seed["cams"] if c.get("counted")}
    mean = sum(counted.values()) / len(counted) if counted else 0.0
    return {c["id"]: counted.get(c["id"], mean) for c in seed["cams"]}


def window_frames(frames: list[dict], i: int) -> list[dict]:
    t0 = frames[i]["ts"]
    return [f for f in frames[i + 1:] if f["ts"] <= t0 + HORIZON * 60_000]


def pick_windows(frames: list[dict], k: int, floor_ms: int) -> list[int]:
    """Up to k non-overlapping windows with >= 1 new camera count and >= 1 bus read, most counts first."""
    cands = []
    for i, f in enumerate(frames):
        if f["ts"] < floor_ms:
            continue
        win = window_frames(frames, i)
        cams = sum(len(g["new"]) for g in win)
        if cams and any(g["busDelayMin"] is not None for g in win):
            cands.append((cams, f["ts"], i))
    chosen: list[tuple[int, int]] = []
    for _, ts, i in sorted(cands, key=lambda c: (-c[0], -c[1])):
        if len(chosen) < k and all(abs(ts - t) >= HORIZON * 60_000 for t, _ in chosen):
            chosen.append((ts, i))
    return [i for _, i in sorted(chosen)]


def baselines(frames: list[dict], typical: dict[str, float]) -> tuple[dict, dict]:
    """Persistence forecasts keyed by (camId, frame ts) for cameras and by frame ts for bus."""
    series: dict[str, list[tuple[int, float]]] = {}
    for f in frames:
        for cid, people in f["new"].items():
            series.setdefault(cid, []).append((f["ts"], people))
    cam = {}
    for cid, pts in series.items():
        for (ts, _), fc in zip(pts, persistence_forecasts([v for _, v in pts], typical[cid])):
            cam[(cid, ts)] = fc
    bus = dict(zip((f["ts"] for f in frames), persistence_forecasts([f["busDelayMin"] for f in frames], 0.0)))
    return cam, bus


# ---------------------------------------------------------------- run


def series_at(series: list[float], minute: int) -> float:
    return series[min(minute, len(series) - 1)]


def run_window(base: dict, frames: list[dict], i: int, rng_seed: int, persist_cam: dict,
               persist_bus: dict, typical: dict[str, float]) -> dict:
    f0 = frames[i]
    seed = engine.reseed(base, f0["ts"], f0["readings"])
    mv = engine.Multiverse(seed, n=N, horizon=HORIZON, crn_seed=CRN_SEED, rng_seed=rng_seed)
    mv.spawn()
    flat = [(w["run"]["cams"], float(w["assumptions"]["busDelayMin"])) for w in mv.live()]
    flat_w = [1.0] * len(flat)
    prior_bus = [f["busDelayMin"] for f in frames[:i] if f["busDelayMin"] is not None]
    bus_typical = sum(prior_bus) / len(prior_bus) if prior_bus else 0.0
    rows = []
    for g in window_frames(frames, i):
        minute = mv.minute_of(g["ts"])
        live = mv.live()
        prices = [w["price"] for w in live]
        for cid, obs in g["new"].items():
            mk = [series_at(w["run"]["cams"][cid], minute) for w in live]
            eq = [series_at(cams[cid], minute) for cams, _ in flat]
            centres = {"market": (mk, prices), "equal": (eq, flat_w),
                       "persist": ([persist_cam[(cid, g["ts"])]], [1.0]), "typical": ([typical[cid]], [1.0])}
            rows.append({"kind": "count", "cam": cid, "ts": g["ts"], "obs": obs,
                         **{m: (weighted_mean(c, w), interval80(c, w, "count")) for m, (c, w) in centres.items()}})
        if g["busDelayMin"] is not None:
            mk = [float(w["assumptions"]["busDelayMin"]) for w in live]
            centres = {"market": (mk, prices), "equal": ([b for _, b in flat], flat_w),
                       "persist": ([persist_bus[g["ts"]]], [1.0]), "typical": ([bus_typical], [1.0])}
            rows.append({"kind": "bus", "ts": g["ts"], "obs": g["busDelayMin"],
                         **{m: (weighted_mean(c, w), interval80(c, w, "bus")) for m, (c, w) in centres.items()}})
        mv.advance(g)
    return {"start": f0["ts"], "frames": len(window_frames(frames, i)), "rows": rows}


def score(rows: list[dict], method: str) -> tuple[float | None, float | None]:
    obs = [r["obs"] for r in rows]
    return mae([r[method][0] for r in rows], obs), coverage([r[method][1] for r in rows], obs)


def hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%M")


def fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "   -  "
    return f"{v * 100:5.0f}%" if pct else f"{v:6.2f}"


def gain(market: float | None, base: float | None) -> float | None:
    if market is None or not base:
        return None
    return (base - market) / base


def headline(minutes: int, cams: list[dict], bus: list[dict]) -> str:
    m_mae, m_cov = score(cams, "market")
    p_mae, _ = score(cams, "persist")
    g = gain(m_mae, p_mae)
    if g is None:
        return "No scoreable camera counts: no headline."
    verb = (f"{g * 100:.0f}% closer than" if g >= 0 else
            f"off by {m_mae:.1f} people per camera, {m_mae / p_mae:.1f}x the {p_mae:.1f} of")
    width = (" (too wide: under-confident)" if m_cov > 0.9 else " (too narrow: over-confident)" if m_cov < 0.7
             else "")
    bm, bp = score(bus, "market")[0], score(bus, "persist")[0]
    bus_part = ""
    if bm is not None:
        bus_part = (f" Bus delay: the market was off by {bm:.2f} min on average, persistence by {bp:.2f}"
                    f" ({'market wins' if bm < bp else 'persistence wins' if bm > bp else 'tie'}).")
    return (f"On {minutes} real minutes of Oxford Circus ({len(cams)} fresh camera counts), the market's "
            f"people forecast was {verb} assuming nothing changes; {m_cov * 100:.0f}% of readings fell inside "
            f"its 80% range{width}.{bus_part}")


def report(results: list[dict], day: str, rng_seed: int, elapsed: float, cam_names: dict[str, str]) -> None:
    allrows = [r for res in results for r in res["rows"]]
    cams = [r for r in allrows if r["kind"] == "count"]
    bus = [r for r in allrows if r["kind"] == "bus"]
    minutes = sum(res["frames"] for res in results)
    print(f"Oxford Circus, recorder data {day} (UTC). {len(results)} five-minute markets, n={N} worlds, "
          f"brokers off, rng seed {rng_seed}.")
    print("\nwindow       frames  cams  bus | people MAE: market  equal persist typical | bus MAE: market persist")
    for res in results:
        c = [r for r in res["rows"] if r["kind"] == "count"]
        b = [r for r in res["rows"] if r["kind"] == "bus"]
        print(f"{hhmm(res['start'])}-{hhmm(res['start'] + HORIZON * 60_000)}  {res['frames']:6d} {len(c):5d} {len(b):4d} |"
              f"             {fmt(score(c, 'market')[0])} {fmt(score(c, 'equal')[0])} {fmt(score(c, 'persist')[0])}"
              f" {fmt(score(c, 'typical')[0])} |        {fmt(score(b, 'market')[0])} {fmt(score(b, 'persist')[0])}")
    print(f"\n{'':34}people per camera ({len(cams)} counts)   bus delay, min ({len(bus)} reads)")
    print(f"{'method':34}{'MAE':>8} {'in 80%':>8} {'vs mkt':>8}   {'MAE':>8} {'in 80%':>8} {'vs mkt':>8}")
    cm, bm = score(cams, "market")[0], score(bus, "market")[0]
    for m in METHODS:
        (ca, cc), (ba, bc) = score(cams, m), score(bus, m)
        cg = "" if m == "market" else fmt(gain(cm, ca), True)
        bg = "" if m == "market" else fmt(gain(bm, ba), True)
        print(f"{LABELS[m]:34}{fmt(ca):>8} {fmt(cc, True):>8} {cg:>8}   {fmt(ba):>8} {fmt(bc, True):>8} {bg:>8}")
    print("('vs mkt' = how much closer the market was than that method; negative = the market lost)")
    print("\nper camera (people MAE, market vs persistence):")
    for cid in sorted({r["cam"] for r in cams}):
        rs = [r for r in cams if r["cam"] == cid]
        mk, ps = score(rs, "market")[0], score(rs, "persist")[0]
        tag = "market wins" if mk < ps else "persistence wins" if mk > ps else "tie"
        print(f"   {cam_names.get(cid, cid):34} n={len(rs):2d}  market {mk:5.2f}  persist {ps:5.2f}  {tag}")
    print(f"\nHEADLINE: {headline(minutes, cams, bus)}")
    print(f"({elapsed:.1f} s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw", type=Path, default=feeds.alt_root())
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    t0 = time.perf_counter()
    base = json.loads(SEED_PATH.read_text())
    start, end, day = newest_day(args.raw)
    frames = load_day(args.raw, base, start, end)
    typical = seed_typical(base)
    persist_cam, persist_bus = baselines(frames, typical)
    picks = pick_windows(frames, args.windows, base["at"])
    if not picks:
        print(f"no five-minute window at or after the seed ({hhmm(base['at'])} UTC) has a new camera count and bus data")
        sys.exit(1)
    if len(picks) < args.windows:
        print(f"only {len(picks)} of {args.windows} requested windows have a new camera count and bus data "
              f"at or after the seed ({hhmm(base['at'])} UTC)")
    results = [run_window(base, frames, i, args.seed + k, persist_cam, persist_bus, typical)
               for k, i in enumerate(picks)]
    report(results, day, args.seed, time.perf_counter() - t0, {c["id"]: c.get("name") or c["id"] for c in base["cams"]})


if __name__ == "__main__":
    main()
