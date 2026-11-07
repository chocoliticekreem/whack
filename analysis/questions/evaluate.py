"""Measure every candidate series on today's recorded data.

    python3 analysis/questions/evaluate.py            # prints the tables, writes out/results.json

Method, the same for every series (no look-ahead):
- Opens every `step` minutes; the market settles at open + h on the feed's last reading (stale -> void).
- The house prices from the past only: the empirical distribution of h-minute changes seen before the open
  (all 1-minute-grid pairs that ended by the open). Over/under lines sit at now + median past change, on a
  half-step for integer series so there are no ties. Favourite price = the house's probability of the likelier side.
- "moved" = the settle value differs from the value at open. "same side" = the settle lands on the same side of the
  line as the value at open (what someone who only looks at the current number would bet).
- Each signal makes a call (over/under, yes/no, a pick) or abstains; hit rate is over its calls.
"""
from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import series as S
from load import MIN, dist, hhmm, pctl

HERE = Path(__file__).resolve().parent
RNG = random.Random(7)
MIN_PAST = 10
VIX_WARM_HHMM = "14:30"


def value_at(ser, t, max_age):
    lo, hi = 0, len(ser)
    while lo < hi:
        mid = (lo + hi) // 2
        if ser[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    ts, v = ser[lo - 1]
    return v if t - ts <= max_age else None


def grid(ser, step=MIN):
    t0 = ser[0][0] - ser[0][0] % step + step
    return list(range(t0, ser[-1][0] + 1, step))


def past_deltas(ser, t, h, max_age, key=None):
    out = []
    for s in grid(ser):
        if s + h * MIN > t:
            break
        a, b = value_at(ser, s, max_age), value_at(ser, s + h * MIN, max_age)
        if a is not None and b is not None:
            out.append((b[key] - a[key]) if key else (b - a))
    return out


def opens(ser, h, step, warm_min=30):
    t = ser[0][0] - ser[0][0] % (step * MIN) + step * MIN + warm_min * MIN
    end = ser[-1][0] - h * MIN
    while t <= end:
        yield t
        t += step * MIN


def overunder(name, ser, h, step, max_age, signals=None, integer=False, warm=30, strike_mode="drift"):
    """strike_mode drift: now + median past change. open: the value at open (+0.5 for integers, so ties = No)."""
    signals = signals or {}
    rows, voids, total = [], 0, 0
    for t in opens(ser, h, step, warm):
        x0 = value_at(ser, t, max_age)
        if x0 is None:
            continue
        total += 1
        past = past_deltas(ser, t, h, max_age)
        if len(past) < MIN_PAST:
            continue
        x1 = value_at(ser, t + h * MIN, max_age)
        if x1 is None:
            voids += 1
            continue
        drift = statistics.median(past) if strike_mode == "drift" else 0
        strike = (int((x0 + drift) // 1) + 0.5) if integer else round(x0 + drift, 3)
        p_over = sum(1 for d in past if x0 + d > strike) / len(past)
        r = {"t": t, "x0": x0, "x1": x1, "strike": strike, "p_over": p_over, "fav": max(p_over, 1 - p_over),
             "over": x1 > strike, "moved": x1 != x0, "same": (x1 > strike) == (x0 > strike), "calls": {}}
        for sname, fn in signals.items():
            c = fn(t, ser)
            if c:
                r["calls"][sname] = (c > 0) == r["over"]
        rows.append(r)
    return summarise(name, f"over/under, {h}-min", rows, voids, total, signals)


def summarise(name, fmt, rows, voids, total, signals):
    favs = [r["fav"] for r in rows]
    res = {"name": name, "format": fmt, "n": len(rows), "opens": total, "void": voids,
           "fav": dist(favs), "fav_in_band": (sum(1 for f in favs if 0.3 <= f <= 0.7) / len(favs)) if favs else None,
           "moved": avg(r["moved"] for r in rows), "same_side": avg(r["same"] for r in rows),
           "base_yes": avg(r["over"] for r in rows), "signals": {}}
    for sname in signals:
        hits = [r["calls"][sname] for r in rows if sname in r["calls"]]
        res["signals"][sname] = {"calls": len(hits), "hit": avg(hits)}
    return res


def avg(it):
    xs = list(it)
    return round(sum(xs) / len(xs), 3) if xs else None


def momentum(lookback_min, max_age, scale=1.0):
    def call(t, ser):
        a, b = value_at(ser, t - lookback_min * MIN, max_age), value_at(ser, t, max_age)
        if a is None or b is None or a == b:
            return 0
        return 1 if (b - a) * scale > 0 else -1
    return call


def reversion(lookback_min, max_age):
    """Bet back toward the trailing mean."""
    def call(t, ser):
        xs = [v for ts, v in ser if t - lookback_min * MIN <= ts <= t]
        b = value_at(ser, t, max_age)
        if len(xs) < 3 or b is None:
            return 0
        m = statistics.mean(xs)
        return 0 if b == m else (-1 if b > m else 1)
    return call


# ---------------------------------------------------------------- races (versus)


def race(name, ser, h, step, max_age, mode="level", signals=None, options=None, warm=30):
    """mode level: highest value at settle wins. mode gain: biggest change from open wins.
    House price: joint bootstrap over past h-min changes (one past time per draw keeps co-movement)."""
    signals = signals or {}
    options = options or list(ser[-1][1])
    rows, voids, total = [], 0, 0
    for t in opens(ser, h, step, warm):
        x0 = value_at(ser, t, max_age)
        if x0 is None or any(o not in x0 for o in options):
            continue
        total += 1
        pasts = []
        for s in grid(ser):
            if s + h * MIN > t:
                break
            a, b = value_at(ser, s, max_age), value_at(ser, s + h * MIN, max_age)
            if a and b and all(o in a and o in b for o in options):
                pasts.append({o: b[o] - a[o] for o in options})
        if len(pasts) < MIN_PAST:
            continue
        x1 = value_at(ser, t + h * MIN, max_age)
        if x1 is None or any(o not in x1 for o in options):
            voids += 1
            continue
        wins = {o: 0 for o in options}
        for _ in range(400):
            d = RNG.choice(pasts)
            if mode == "level":
                sc = {o: x0[o] + d[o] for o in options}
            else:  # gain: add own noise so ties in a flat past break randomly
                sc = {o: d[o] + RNG.choice(pasts)[o] * 0.5 for o in options}
            wins[max(options, key=lambda o: sc[o])] += 1
        probs = {o: w / 400 for o, w in wins.items()}
        if mode == "level":
            winner = max(options, key=lambda o: x1[o])
            leader = max(options, key=lambda o: x0[o])
        else:
            winner = max(options, key=lambda o: x1[o] - x0[o])
            leader = max(options, key=lambda o: probs[o])
        r = {"t": t, "probs": probs, "fav": max(probs.values()), "winner": winner,
             "over": winner == leader, "moved": winner != leader, "same": winner == leader, "calls": {}}
        for sname, fn in signals.items():
            pick = fn(t, ser, options, h)
            if pick:
                r["calls"][sname] = pick == winner
        rows.append(r)
    res = summarise(name, f"race ({mode}), {len(options)} options, {h}-min", rows, voids, total, signals)
    res["chance"] = round(1 / len(options), 3)
    res["winners"] = {o: sum(1 for r in rows if r["winner"] == o) for o in options}
    return res


def race_momentum(lookback_min, max_age):
    def pick(t, ser, options, h):
        a, b = value_at(ser, t - lookback_min * MIN, max_age), value_at(ser, t, max_age)
        if not a or not b or any(o not in a or o not in b for o in options):
            return None
        return max(options, key=lambda o: b[o] - a[o])
    return pick


def race_typical_slope(t, ser, options, h):
    """Pick the hub whose typical Saturday rises most over the next h minutes (the 'usual Saturday' chart)."""
    best, best_v = None, None
    for o in options:
        nap = S.HUBS[o]
        a, b = S.typical(nap, t), S.typical(nap, t + h * MIN)
        if a and b:
            v = b / a
            if best_v is None or v > best_v:
                best, best_v = o, v
    return best


def race_level_leader(t, ser, options, h):
    x0 = value_at(ser, t, 6 * MIN)
    return max(options, key=lambda o: x0[o]) if x0 else None


def race_lowest(t, ser, options, h):
    """Reversion for gain races: the hub furthest below its usual catches up."""
    x0 = value_at(ser, t, 6 * MIN)
    return min(options, key=lambda o: x0[o]) if x0 else None


# ---------------------------------------------------------------- yes/no events on line status


def line_event(name, h, step, kind, signals=None, warm=30):
    """kind 'any_change': any Tube line changes severity in (t, t+h].
    kind 'any_worse': any Tube line gets worse.  kind 'clean': every Tube line Good at open is still Good at t+h
    and was Good on every reading in between."""
    snaps = S.line_snaps()
    signals = signals or {}

    def happened(t0, t1):
        base = value_at(snaps, t0, 3 * MIN)
        win = [s for ts, s in snaps if t0 < ts <= t1]
        if base is None or not win:
            return None
        if kind == "clean":
            good = [l for l, (sev, _) in base.items() if sev == S.GOOD]
            return not all(s.get(l, (S.GOOD, ""))[0] == S.GOOD for s in win for l in good)
        for s in win:
            for l, (sev, _) in s.items():
                b = base.get(l, (sev, ""))[0]
                if (kind == "any_change" and sev != b) or (kind == "any_worse" and sev < b):
                    return True
        return False

    rows, voids, total = [], 0, 0
    for t in opens(snaps, h, step, warm):
        total += 1
        past = []
        for s in grid(snaps, 5 * MIN):
            if s + h * MIN > t:
                break
            e = happened(s, s + h * MIN)
            if e is not None:
                past.append(e)
        if len(past) < MIN_PAST:
            continue
        out = happened(t, t + h * MIN)
        if out is None:
            voids += 1
            continue
        p = sum(past) / len(past)
        p = min(max(p, 0.03), 0.97)
        r = {"t": t, "p_over": p, "fav": max(p, 1 - p), "over": out, "moved": out, "same": not out, "calls": {}}
        for sname, fn in signals.items():
            c = fn(t, snaps)
            if c:
                r["calls"][sname] = (c > 0) == out
        rows.append(r)
    yes_word = {"any_change": "any change", "any_worse": "any worse", "clean": "streak broken"}[kind]
    res = summarise(name, f"yes/no ({yes_word}), {h}-min", rows, voids, total, signals)
    return res


def delays_now_signal(threshold):
    """YES (trouble) when >= threshold Tube lines are already delayed for unplanned reasons."""
    def call(t, snaps):
        s = value_at(snaps, t, 3 * MIN)
        if s is None:
            return 0
        unplanned = sum(1 for sev, why in s.values() if sev != S.GOOD and "closure" not in why.lower()
                        and "planned" not in why.lower() and sev >= 6)
        return 1 if unplanned >= threshold else -1
    return call


def recent_changes_signal(lookback_min, threshold):
    def call(t, snaps):
        win = [s for ts, s in snaps if t - lookback_min * MIN <= ts <= t]
        n = sum(1 for a, b in zip(win, win[1:]) for l in b if l in a and a[l][0] != b[l][0])
        return 1 if n >= threshold else -1
    return call


# ---------------------------------------------------------------- brackets on a count


def count_brackets(name, ser, h, step, max_age, warm=30):
    """Three brackets around the open: fewer / same / more."""
    rows, voids, total = [], 0, 0
    for t in opens(ser, h, step, warm):
        x0 = value_at(ser, t, max_age)
        if x0 is None:
            continue
        total += 1
        past = past_deltas(ser, t, h, max_age)
        if len(past) < MIN_PAST:
            continue
        x1 = value_at(ser, t + h * MIN, max_age)
        if x1 is None:
            voids += 1
            continue
        p = {"fewer": sum(1 for d in past if d < 0) / len(past), "same": sum(1 for d in past if d == 0) / len(past),
             "more": sum(1 for d in past if d > 0) / len(past)}
        res = "fewer" if x1 < x0 else ("same" if x1 == x0 else "more")
        rows.append({"t": t, "fav": max(p.values()), "over": res == "same", "moved": x1 != x0,
                     "same": res == max(p, key=p.get), "calls": {}})
    return summarise(name, f"brackets fewer/same/more, {h}-min", rows, voids, total, {})


# ---------------------------------------------------------------- VIX


def vix_series():
    path = HERE / "out" / "vix.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text())["rows"]
    # the first hour is the engine's cold start (VIX decays from ~16 to ~2.3); markets only run after it
    return sorted((r["ts"], round(r["vix"], 2)) for r in rows
                  if r.get("ts") and r.get("vix") is not None and hhmm(r["ts"]) >= VIX_WARM_HHMM)


def vix_spike(h, step):
    """Does the VIX jump >= 2 points and >= 20% within the next h minutes (engine's own spike flag)?"""
    ser = vix_series()
    if not ser:
        return None

    def spiked(t0, t1):
        base = value_at(ser, t0, 3 * MIN)
        win = [v for ts, v in ser if t0 < ts <= t1]
        if base is None or not win:
            return None
        return any(v - base >= 2 and v >= base * 1.2 for v in win) or any(
            b - a >= 2 and b >= a * 1.2 for a, b in zip([base] + win, win))

    rows = []
    for t in opens(ser, h, step, 10):
        past = [e for s in grid(ser) if s + h * MIN <= t and (e := spiked(s, s + h * MIN)) is not None]
        out = spiked(t, t + h * MIN)
        if len(past) < MIN_PAST or out is None:
            continue
        p = min(max(sum(past) / len(past), 0.03), 0.97)
        rows.append({"t": t, "fav": max(p, 1 - p), "over": out, "moved": out, "same": not out, "calls": {}})
    return summarise("vix_spike", f"yes/no (spike), {h}-min", rows, 0, len(rows), {})


# ---------------------------------------------------------------- run


def main():
    age_crowd, age_line, age_cam = 6 * MIN, 3 * MIN, 3 * MIN
    ci = S.crowd_index()

    def ci_typical(t, ser):
        d = S.crowd_index_typical_slope(t, 15)
        return 0 if abs(d) < 1e-9 else (1 if d > 0 else -1)

    results = []
    add = results.append
    # heartbeat candidates
    add(overunder("crowd_index_5", ci, 5, 5, age_crowd,
                  {"momentum_15": momentum(15, age_crowd), "typical_sat_slope": ci_typical,
                   "reversion_30": reversion(30, age_crowd)}))
    add(overunder("cams_total_5", S.cams_total(12), 5, 5, age_cam,
                  {"momentum_10": momentum(10, age_cam), "reversion_20": reversion(20, age_cam)}, integer=True,
                  warm=15))
    add(overunder("cams_total8_5", S.cams_total(8), 5, 5, age_cam,
                  {"momentum_10": momentum(10, age_cam), "reversion_20": reversion(20, age_cam)}, integer=True,
                  warm=15))
    add(overunder("bikes_docked_5", S.bikes_docked(), 5, 5, 3 * MIN,
                  {"momentum_15": momentum(15, 3 * MIN), "reversion_30": reversion(30, 3 * MIN)}, integer=True))
    add(overunder("holding_5", S.holding(), 5, 5, 3 * MIN,
                  {"momentum_10": momentum(10, 3 * MIN), "reversion_30": reversion(30, 3 * MIN)}, integer=True))
    add(overunder("grid_5", S.grid_demand(), 5, 5, 6 * MIN, {"momentum_15": momentum(15, 6 * MIN)}, integer=True))
    hr, hraw = S.hub_ratio(), S.hub_raw()
    five = list(S.HUBS)
    three = ["Oxford Circus", "King's Cross", "Waterloo"]
    add(race("hub_busiest_raw_5", hraw, 5, 5, age_crowd, "level", {"leader": race_level_leader}, five))
    add(race("hub_vs_usual_level_5", hr, 5, 5, age_crowd, "level", {"leader": race_level_leader}, five))
    add(race("hub_vs_usual_gain_5", hr, 5, 5, age_crowd, "gain",
             {"momentum_10": race_momentum(10, age_crowd), "typical_slope": race_typical_slope,
              "furthest_below": race_lowest}, five))
    add(race("hub_gain_raw_5", hraw, 5, 5, age_crowd, "gain",
             {"momentum_10": race_momentum(10, age_crowd), "typical_slope": race_typical_slope,
              "furthest_below": race_lowest}, five))
    add(race("kx_ox_wl_gain_15", hraw, 15, 5, age_crowd, "gain",
             {"momentum_15": race_momentum(15, age_crowd), "typical_slope": race_typical_slope}, three))
    def ci_gap(t, ser):
        v = value_at(ser, t, age_crowd)
        return 0 if v is None or abs(v - 100) < 2 else (-1 if v > 100 else 1)

    def ci_typical_h(h):
        def call(t, ser):
            d = S.crowd_index_typical_slope(t, h)
            return 0 if abs(d) < 1e-9 else (1 if d > 0 else -1)
        return call

    add(overunder("crowd_index_15", ci, 15, 5, age_crowd,
                  {"typical_sat_slope": ci_typical_h(15), "gap_to_100": ci_gap, "momentum_15": momentum(15, age_crowd)}))
    add(overunder("crowd_index_30", ci, 30, 5, age_crowd,
                  {"typical_sat_slope": ci_typical_h(30), "gap_to_100": ci_gap, "momentum_30": momentum(30, age_crowd)}))
    for h in (5, 15, 30):
        add(overunder(f"crowd_raw_{h}", S.crowd_raw(), h, 5, age_crowd,
                      {"typical_sat_slope": ci_typical_h(h), "momentum": momentum(h, age_crowd)}))
    add(race("hub_vs_usual_level_15", hr, 15, 5, age_crowd, "level", {"leader": race_level_leader}, five))
    add(race("hub_vs_usual_level_30", hr, 30, 5, age_crowd, "level", {"leader": race_level_leader}, five))
    add(race("hub_vs_usual_gain_15", hr, 15, 5, age_crowd, "gain",
             {"typical_slope": race_typical_slope, "furthest_below": race_lowest,
              "momentum_15": race_momentum(15, age_crowd)}, five))
    add(race("kx_ox_wl_vs_usual_gain_15", hr, 15, 5, age_crowd, "gain",
             {"typical_slope": race_typical_slope, "furthest_below": race_lowest,
              "momentum_15": race_momentum(15, age_crowd)}, three))
    add(count_brackets("lines_disrupted_5", S.lines_disrupted(), 5, 5, age_line))
    add(line_event("line_any_change_5", 5, 5, "any_change", {"delays_now_2": delays_now_signal(2)}))
    add(line_event("line_any_change_15", 15, 5, "any_change",
                   {"delays_now_2": delays_now_signal(2), "recent_60_ge2": recent_changes_signal(60, 2)}))
    # headline candidates
    add(line_event("line_any_change_60", 60, 15, "any_change",
                   {"delays_now_2": delays_now_signal(2), "recent_60_ge2": recent_changes_signal(60, 2)}))
    add(line_event("line_any_worse_60", 60, 15, "any_worse",
                   {"delays_now_2": delays_now_signal(2), "recent_60_ge2": recent_changes_signal(60, 2)}))
    add(line_event("line_clean_streak_60", 60, 15, "clean",
                   {"delays_now_2": delays_now_signal(2), "recent_60_ge2": recent_changes_signal(60, 2)}))
    add(line_event("line_clean_streak_30", 30, 10, "clean",
                   {"delays_now_2": delays_now_signal(2), "recent_60_ge2": recent_changes_signal(60, 2)}))
    add(count_brackets("lines_disrupted_60", S.lines_disrupted(), 60, 15, age_line))
    add(overunder("crowd_index_60", ci, 60, 15, age_crowd,
                  {"momentum_30": momentum(30, age_crowd), "typical_sat_slope": lambda t, s: (
                      1 if S.crowd_index_typical_slope(t, 60) > 0 else -1)}))
    add(race("hub_vs_usual_level_60", hr, 60, 15, age_crowd, "level", {"leader": race_level_leader}, five))
    add(race("hub_gain_raw_60", hraw, 60, 15, age_crowd, "gain",
             {"momentum_30": race_momentum(30, age_crowd), "typical_slope": race_typical_slope,
              "furthest_below": race_lowest}, five))
    add(overunder("holding_60", S.holding(), 60, 15, 3 * MIN, {"momentum_30": momentum(30, 3 * MIN)}, integer=True))
    add(overunder("bikes_docked_60", S.bikes_docked(), 60, 15, 3 * MIN, {"momentum_30": momentum(30, 3 * MIN)},
                  integer=True))
    vix = vix_series()
    if vix:
        add(overunder("vix_5", vix, 5, 5, 3 * MIN, {"momentum_5": momentum(5, 3 * MIN),
                                                    "reversion_15": reversion(15, 3 * MIN)}, warm=15))
        add(vix_spike(15, 5))
    # the lineup's exact formats: line = the value at open ("higher or lower than now")
    add(overunder("LINEUP_city_pulse_5", S.crowd_raw(), 5, 5, age_crowd,
                  {"momentum_5": momentum(5, age_crowd), "typical_sat_slope": ci_typical_h(5)}, strike_mode="open"))
    if vix:
        add(overunder("LINEUP_vix_5", vix, 5, 5, 3 * MIN, {"fade_last_5": lambda t, s: -momentum(5, 3 * MIN)(t, s),
                                                           "reversion_15": reversion(15, 3 * MIN)},
                      warm=15, strike_mode="open"))
    add(overunder("LINEUP_heathrow_stack_5", S.holding(), 5, 5, 3 * MIN,
                  {"fade_last_10": lambda t, s: -momentum(10, 3 * MIN)(t, s), "reversion_30": reversion(30, 3 * MIN)},
                  integer=True, strike_mode="open"))
    rain = S.rain_mm()
    add({"name": "rain", "format": "yes/no", "n": len(rain),
         "max_mm_today": max(v for _, v in rain) if rain else None,
         "note": "every Open-Meteo reading today is 0.0 mm and every nowcast step 0.0: a rain market is ~97% No"})
    rs = S.road_serious()
    add({"name": "road_serious", "format": "count", "n": len(rs),
         "distinct_values": sorted({v for _, v in rs}), "note": "3 flips in 6 h, all on the A4 within 2 min"})

    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "results.json").write_text(json.dumps(results, indent=1))
    for r in results:
        print(f"\n## {r['name']} ({r.get('format')})")
        for k, v in r.items():
            if k not in ("name", "format"):
                print(f"   {k}: {v}")


if __name__ == "__main__":
    main()
