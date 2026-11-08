"""City Pulse: why the old futures were biased by time of day, and which model replaces them, out of sample.

    python3 analysis/judge/pulse_model.py <raw_dir> [start ISO] [split ISO] [end ISO] [seeds]

Default window 2026-11-07T12:00Z / 18:00Z / 2026-11-08T00:00Z, 40 seeds. Every 5-min round is built with the live
builder (markets.build_city_pulse: 50 futures, the line on the step their spread allows, the [3%, 97%] clamp) at its
real open (settle - 6 min) and settled with markets.settle_city_pulse. A market's price depends on its 50 random
futures (seeded by the market id) and the line rounding they feed, and one draw per market moved a model's
out-of-sample Brier by ±0.02, as much as the models differ. So each round is priced under `seeds` ids (the live id
first) and every score is the expected score over those draws; standard errors and bootstrap CIs are over markets.

Candidates differ only in the walk's drift (pricing.fit_pulse, swapped in here) and, for the "2 h sigma" ones, its
sigma; each falls back to the old walk exactly where the live code does (no usual profile at the open reading or 10 min
before it). A candidate's one fitted coefficient is fitted on markets opened in [start, split); every candidate is
scored on markets opened in [split, end), so no score uses a settle the fit saw. Rounds opened after `end` are reported
apart: TfL's typical day switches weekday at midnight (docs/markets.md, City Pulse).

Prints: (1) the old model by hour over the whole recording: mean signed error of the realised pulse against the
futures' median, Brier, log loss, Above's hit rate, mean opening P(Above); (2) the fits; (3) out-of-sample Brier and log
loss of each candidate, paired against the old model (mean ± 1 SE), next to 50/50 and climatology (Above's in-sample
hit rate under that model's own lines), plus a Gaussian log score of the futures' spread and the share of outcomes
outside the futures' 90% band; (4) the pulse leak: 10 points on the Tube Crowd trader's pick at the opening quote on an
empty book (priced as analysis/judge/tops_replay.py does), ROI with a 2000-resample bootstrap 95% CI; (5) a 10-bin
reliability table of the opening P(Above), old vs new, over every market x seed; (6) the new model by hour.
Read-only: never writes the recorder data.
"""
from __future__ import annotations

import math
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import markets as mk  # noqa: E402
import pricing  # noqa: E402
import traders  # noqa: E402
from data import Store, load_hotspots  # noqa: E402

BOOT = 2000
STAKE = 10
OPEN_BEFORE_MS = 6 * pricing.MIN_MS
SETTLE_WAIT_MS = 40 * pricing.MIN_MS
SPREAD_MS = 2 * 3_600_000
MIN_CHANGES = 6
LIVE_FIT = pricing.fit_pulse
LIVE_SIM = pricing.ScalarSim
OLD, NEW = "old: walk drift", "new: usual + 10-min trend"


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def hhmm(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%H:%M")


def opening(store: Store, now: int, at: int) -> dict | None:
    """What the builder sees at the open: log pulse x0, the usual curve's log change to the expected settle stamp du,
    walk minutes, log pulse vs usual at the open stamp d0, the last 10 min's change in it (None when the live fit
    falls back), and the 2 h sigma: rms of real `minutes`-long changes in vs usual over the 2 h before, per sqrt min."""
    last = store.latest("crowding", now)
    p0 = mk.pulse_of(last[1]) if last else None
    if not p0:
        return None
    m0 = store.measured_at(last[0])
    t_end, minutes = mk.crowd_walk(m0, at, now)
    along = mk.pulse_along_usual(store, (m0, last[1]), t_end)
    devs = mk.pulse_devs(store, now - SPREAD_MS, now)
    drift = LIVE_FIT(mk.pulse_devs(store, now - mk.PULSE_TREND_MS, now), m0, minutes)
    grid = {round(t / mk.PUBLISH_MS): d for t, d in devs}
    k = round(minutes * pricing.MIN_MS / mk.PUBLISH_MS)
    changes = [grid[j + k] - d for j, d in grid.items() if j + k in grid]
    sigma = math.sqrt(statistics.fmean(c * c for c in changes) / minutes) if len(changes) >= MIN_CHANGES else None
    return {"x0": math.log(p0), "du": math.log(along / p0) if along else 0.0, "minutes": minutes,
            "d0": dict(devs).get(m0), "mom": drift * minutes / pricing.PULSE_MOMENTUM if drift is not None else None,
            "sigma": sigma}


def build(store: Store, spots: list[dict], now: int, at: int, seeds: int, fit, sigma: float | None) -> dict | None:
    """One round priced under `seeds` market ids with `fit` as the drift (and the walk's sigma raised to `sigma`):
    the expected opening P(Above), Brier, log loss and pick return over the draws, plus every draw's (P, Above won)."""
    pricing.fit_pulse = fit
    if sigma:
        pricing.ScalarSim = lambda x0, mu, sd, *rest: LIVE_SIM(x0, mu, max(sd, sigma), *rest)
    draws = []
    try:
        for k in range(seeds):
            mid = f"city_pulse-{hhmm(at).replace(':', '')}" + (f"/{k}" if k else "")
            m = mk.build_city_pulse(mid, store, spots, now, at)
            got = mk.settle_city_pulse(m, store, at + SETTLE_WAIT_MS) if m else ("missing",)
            if got[0] != "done":
                return None
            finals = sorted(math.exp(p[-1]) for p in m.futures.sim.paths)
            pick = traders.tube_pulse(m, store, now, {})
            odds = pricing.locked_odds(pricing.Book(m.option_ids()).quote(m.open_model, pick[0], STAKE), STAKE) \
                if pick else None
            draws.append({"p": m.open_model["over"], "won": got[1] == "over", "value": got[3]["value"],
                          "median": statistics.median(finals), "out90": not finals[2] <= got[3]["value"] <= finals[47],
                          "sd": m.futures.sim.sd * math.sqrt(m.futures.sim.m),
                          "ret": ((odds if pick[0] == got[1] else 0) - 1) if odds else None,
                          "pick_won": bool(pick) and pick[0] == got[1]})
    finally:
        pricing.fit_pulse, pricing.ScalarSim = LIVE_FIT, LIVE_SIM

    def mean(f) -> float:
        return statistics.fmean(f(d) for d in draws)

    rets = [d["ret"] for d in draws if d["ret"] is not None]
    return {"at": at, "open": now, "draws": draws, "value": draws[0]["value"], "p": mean(lambda d: d["p"]),
            "brier": mean(lambda d: (d["p"] - d["won"]) ** 2),
            "ll": mean(lambda d: -math.log(d["p"] if d["won"] else 1 - d["p"])),
            "won": mean(lambda d: d["won"]), "err": mean(lambda d: math.log(d["value"] / d["median"])),
            "out90": mean(lambda d: d["out90"]), "pick_won": mean(lambda d: d["pick_won"]),
            "gauss": mean(lambda d: 0.5 * (math.log(d["value"] / d["median"]) / d["sd"]) ** 2 + math.log(d["sd"])
                          + 0.5 * math.log(2 * math.pi)),
            "ret": statistics.fmean(rets) if rets else None}


def flat(rows: list[dict], p: float) -> tuple[float, float]:
    """Brier and log loss of a constant P(Above) against each market's expected outcome over the draws."""
    return (statistics.fmean(r["won"] * (1 - p) ** 2 + (1 - r["won"]) * p ** 2 for r in rows),
            statistics.fmean(-r["won"] * math.log(p) - (1 - r["won"]) * math.log(1 - p) for r in rows))


def mean_se(xs: list[float]) -> tuple[float, float]:
    return statistics.fmean(xs), statistics.stdev(xs) / math.sqrt(len(xs))


def ols1(xs: list[float], ys: list[float]) -> float:
    return sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)


def roi_ci(rows: list[dict]) -> str:
    rets = [r["ret"] for r in rows if r["ret"] is not None]
    if not rets:
        return "n=  0"
    rng = random.Random(0)
    boot = sorted(statistics.fmean(rng.choice(rets) for _ in rets) for _ in range(BOOT))
    won = sum(r["pick_won"] for r in rows if r["ret"] is not None)
    return (f"n={len(rets):3d} pick won {won:5.1f}  ROI {statistics.fmean(rets):+6.1%}  "
            f"[{boot[int(.025 * BOOT)]:+.0%}, {boot[int(.975 * BOOT)]:+.0%}]")


def by_hour(rows: list[dict], label: str) -> None:
    print(f"\n{label}, by hour of open (UTC = London in November); seed-averaged")
    print(f"  {'hour':>4} {'n':>3} {'err vs median':>13} {'Brier':>6} {'LL':>6} {'Above won':>9} {'mean P(Above)':>13}")
    groups = defaultdict(list)
    for r in rows:
        groups[hhmm(r["open"])[:2]].append(r)
    for h, g in groups.items():
        print(f"  {h:>4} {len(g):3d} {statistics.fmean(r['err'] for r in g):+12.2%} "
              f"{statistics.fmean(r['brier'] for r in g):6.3f} {statistics.fmean(r['ll'] for r in g):6.3f} "
              f"{sum(r['won'] for r in g):5.1f}/{len(g):<3d} {statistics.fmean(r['p'] for r in g):13.2f}")


def reliability(old: list[dict], new: list[dict]) -> None:
    draws = [[d for r in rows for d in r["draws"]] for rows in (old, new)]
    print(f"\nReliability of the opening P(Above), out of sample, 10 bins over every market x seed "
          f"({len(draws[0])} draws): n, mean P(Above), Above's hit rate")
    print(f"  {'bin':9} {'old n':>5} {'mean P':>6} {'hit':>5}   {'new n':>5} {'mean P':>6} {'hit':>5}")
    for k in range(10):
        cells = []
        for ds in draws:
            b = [d for d in ds if k / 10 <= d["p"] < (k + 1) / 10]
            cells.append(f"{len(b):5d} {statistics.fmean(d['p'] for d in b):6.2f} "
                         f"{statistics.fmean(d['won'] for d in b):5.2f}" if b else f"{0:5d} {'-':>6} {'-':>5}")
        print(f"  {k / 10:.1f}-{(k + 1) / 10:.1f}   {cells[0]}   {cells[1]}")


def main() -> None:
    raw = Path(sys.argv[1])
    start = ms(sys.argv[2] if len(sys.argv) > 2 else "2026-11-07T12:00:00Z")
    split = ms(sys.argv[3] if len(sys.argv) > 3 else "2026-11-07T18:00:00Z")
    end = ms(sys.argv[4] if len(sys.argv) > 4 else "2026-11-08T00:00:00Z")
    seeds = int(sys.argv[5]) if len(sys.argv) > 5 else 40
    spots = load_hotspots()
    store = Store(raw, spots)
    store.refresh(start - mk.HISTORY_MS, int(time.time() * 1000))
    first = start // mk.ROUND_MS * mk.ROUND_MS + mk.ROUND_MS
    rounds = [at for at in range(first, store.ts["crowding"][-1] - SETTLE_WAIT_MS, mk.ROUND_MS)
              if at - OPEN_BEFORE_MS >= start]
    seen = {at: f for at in rounds if (f := opening(store, at - OPEN_BEFORE_MS, at))}

    def run(fit, wide: bool, window: int = mk.PULSE_TREND_MS) -> list[dict]:
        live_window, mk.PULSE_TREND_MS = mk.PULSE_TREND_MS, window
        try:
            return [r for at in rounds if (r := build(store, spots, at - OPEN_BEFORE_MS, at, seeds, fit,
                                                      seen.get(at, {}).get("sigma") if wide else None))]
        finally:
            mk.PULSE_TREND_MS = live_window

    old_all = run(lambda *a: None, False)
    by_hour(old_all, f"Old model, every round opened {hhmm(start)} to the end of the recording ({seeds} seeds)")

    fit = [r for r in old_all if r["open"] < split and seen[r["at"]]["mom"] is not None]
    resid = [math.log(r["value"]) - seen[r["at"]]["x0"] - seen[r["at"]]["du"] for r in fit]
    beta = ols1([seen[r["at"]]["mom"] for r in fit], resid)
    kappa = -ols1([seen[r["at"]]["d0"] * seen[r["at"]]["minutes"] for r in fit], resid)
    print(f"\nFits on {len(fit)} markets opened {hhmm(start)}-{hhmm(split)}; residual = log settle pulse - log open "
          f"pulse - the usual curve's log change:")
    print(f"  trend: residual = beta x 10-min change in log pulse vs usual       beta  = {beta:+.3f} "
          f"(live pricing.PULSE_MOMENTUM = {pricing.PULSE_MOMENTUM})")
    print(f"  OU:    residual = -kappa x minutes x log pulse vs usual at open    kappa = {kappa:+.4f} per minute")
    print("  diagnosis only, never used to price: the same trend beta, and the usual curve's amplitude a in "
          "log settle - log open = a x the usual curve's log change, per window")
    for lo_, hi_ in ((start, split), (split, end - 30 * pricing.MIN_MS), (split, end)):
        rows = [r for r in old_all if lo_ <= r["open"] < hi_ and seen[r["at"]]["mom"] is not None]
        moves = [math.log(r["value"]) - seen[r["at"]]["x0"] for r in rows]
        b = ols1([seen[r["at"]]["mom"] for r in rows], [y - seen[r["at"]]["du"] for r, y in zip(rows, moves)])
        a = ols1([seen[r["at"]]["du"] for r in rows], moves)
        print(f"    opened {hhmm(lo_)}-{hhmm(hi_)}: n={len(rows):3d} trend beta {b:+.3f}  usual amplitude a {a:+.2f}")

    def drift(fn):
        def fit_(devs, t0, minutes):
            got = LIVE_FIT(devs, t0, minutes)
            return None if got is None else fn(got, dict(devs)[t0])
        return fit_

    def rolling(devs, t0, minutes):
        """The trend coefficient refitted at each open on the last 2 h of TfL stamps (each `minutes`-long change in vs
        usual that ended by t0 against the 10 min before its start), shrunk toward the fixed coefficient with a prior
        worth 5 typical pairs."""
        got = LIVE_FIT(devs, t0, minutes)
        if got is None:
            return None
        grid = {round(t / mk.PUBLISH_MS): d for t, d in devs}
        k = round(minutes * pricing.MIN_MS / mk.PUBLISH_MS)
        pairs = [(d - grid[j - 2], grid[j + k] - d) for j, d in grid.items() if j - 2 in grid and j + k in grid]
        if not pairs:
            return got
        w = 5 * statistics.fmean(x * x for x, _ in pairs)
        b = (sum(x * y for x, y in pairs) + pricing.PULSE_MOMENTUM * w) / (sum(x * x for x, _ in pairs) + w)
        return got * b / pricing.PULSE_MOMENTUM

    candidates = {  # name: (drift, use the 2 h sigma, window of readings the drift sees)
        OLD: (lambda *a: None, False, mk.PULSE_TREND_MS),
        "usual curve only": (drift(lambda g, d0: 0.0), False, mk.PULSE_TREND_MS),
        "usual + OU on vs usual": (drift(lambda g, d0: -kappa * d0), False, mk.PULSE_TREND_MS),
        "usual + 2 h sigma": (drift(lambda g, d0: 0.0), True, mk.PULSE_TREND_MS),
        NEW: (LIVE_FIT, False, mk.PULSE_TREND_MS),
        "usual + 10-min trend + 2 h sigma": (LIVE_FIT, True, mk.PULSE_TREND_MS),
        "usual + trend refit on the last 2 h": (rolling, False, SPREAD_MS),
    }
    test, ins, late = {}, {}, {}
    for name, (fn, wide, window) in candidates.items():
        rows = old_all if name == OLD else run(fn, wide, window)
        ins[name] = [r for r in rows if r["open"] < split]
        test[name] = [r for r in rows if split <= r["open"] < end]
        late[name] = [r for r in rows if r["open"] >= end]

    base = test[OLD]
    n = len(base)
    print(f"\nOut of sample: {n} markets opened {hhmm(split)}-{hhmm(end)} UTC, expected scores over {seeds} seeds. "
          f"diff = paired vs old, mean ± 1 SE over markets (negative = better). Gauss = log score of the realised log "
          f"pulse under N(log median, futures' sd).")
    print(f"  {'model':34} {'Brier':>6} {'diff':>16}  {'LL':>6} {'diff':>16}  {'Gauss':>6} {'out 90%':>7} "
          f"{'Above won':>9}")
    for name in candidates:
        rows = test[name]
        assert [r["at"] for r in rows] == [r["at"] for r in base], name
        db, sb = mean_se([a["brier"] - b["brier"] for a, b in zip(rows, base)])
        dl, sl = mean_se([a["ll"] - b["ll"] for a, b in zip(rows, base)])
        print(f"  {name:34} {statistics.fmean(r['brier'] for r in rows):6.4f} {db:+.4f} ± {sb:.4f}  "
              f"{statistics.fmean(r['ll'] for r in rows):6.4f} {dl:+.4f} ± {sl:.4f}  "
              f"{statistics.fmean(r['gauss'] for r in rows):+6.3f} {statistics.fmean(r['out90'] for r in rows):7.0%} "
              f"{sum(r['won'] for r in rows):5.1f}/{n}")
    for name in (OLD, NEW):
        clim = statistics.fmean(r["won"] for r in ins[name])
        for label, p in ((f"50/50 on {name.split(':')[0]} lines", .5),
                         (f"climatology {clim:.2f} on {name.split(':')[0]} lines", clim)):
            b, ll = flat(test[name], p)
            print(f"  {label:34} {b:6.4f} {'':16}  {ll:6.4f}")

    print(f"\nPulse leak: {STAKE} points on Tube Crowd's city_pulse pick at the opening quote, empty book; ROI over "
          f"every draw, {BOOT}-resample bootstrap 95% CI over markets")
    for name in (OLD, NEW):
        print(f"  {hhmm(start)}-{hhmm(split)} {name:34} {roi_ci(ins[name])}  (fit window)")
    for name in candidates:
        print(f"  {hhmm(split)}-{hhmm(end)} {name:34} {roi_ci(test[name])}")
    for name in (OLD, NEW):
        print(f"  after {hhmm(end)}   {name:34} {roi_ci(late[name])}")
    reliability(test[OLD], test[NEW])
    by_hour(test[NEW], "New model, out of sample")
    lo, ln = late[OLD], late[NEW]
    if lo:
        print(f"\nOpened after {hhmm(end)} (n={len(lo)}): Brier old {statistics.fmean(r['brier'] for r in lo):.4f} new "
              f"{statistics.fmean(r['brier'] for r in ln):.4f}; LL old {statistics.fmean(r['ll'] for r in lo):.4f} new "
              f"{statistics.fmean(r['ll'] for r in ln):.4f}")


if __name__ == "__main__":
    main()
