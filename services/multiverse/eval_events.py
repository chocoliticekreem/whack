"""Did the derived events predict which way station busyness moved?

For every station-located event (an episode of the same rule at the same naptan) that lands on a
station TfL publishes live crowding for, compare busyness vs typical (live / crowding_profile)
just before the event with the mean over the next 15-60 min:
- raw hit: the change has the predicted sign (demand_surge up; closure/capacity down);
- vs control: the event station's change minus the mean change at stations with no event over the
  same window has the predicted sign (with vs without the event);
- base rate: how often a station with no event moves that way over any 15-60 min window.

    python3 services/multiverse/eval_events.py [raw_dir] [YYYY-MM-DD]
"""

from __future__ import annotations

import bisect
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events  # noqa: E402

DIRECTION = {"demand_surge": 1, "demand_drop": -1, "closure": -1, "capacity": -1}
FAMILIES = ("line_disruption", "line_displacement", "crowding_surge", "fixture_start", "fixture_end",
            "road_closure", "rain_onset")
LAG_MIN, LEAD_MIN = 15, 60
EPISODE_GAP_MIN = 10


def crowding_series(raw: Path, start: int, end: int, typical: events.Typical) -> dict[str, list[tuple[int, float]]]:
    series: dict[str, dict[int, float]] = defaultdict(dict)
    for _, rows in events.read_source(raw, "crowding", start, end):
        for row in rows or []:
            live, nap = row.get("percentageOfBaseline"), row.get("naptan")
            if not row.get("dataAvailable") or not isinstance(live, (int, float)) or not row.get("timeUtc"):
                continue
            t = events.parse_ms(row["timeUtc"])
            typ, real = typical.at(nap, t)
            if real and typ and typ >= events.CROWD_MIN_TYPICAL:
                series[nap][t] = live / typ
    return {k: sorted(v.items()) for k, v in series.items()}


def change(obs: list[tuple[int, float]], t0: int) -> float | None:
    times = [t for t, _ in obs]
    i = bisect.bisect_right(times, t0) - 1
    if i < 0:
        return None
    after = [v for t, v in obs if t0 + LAG_MIN * 60_000 <= t <= t0 + LEAD_MIN * 60_000]
    return (sum(after) / len(after) - obs[i][1]) if after else None


def load_profiles(raw: Path, start: int, end: int) -> events.Typical:
    profiles = {}
    for _, p in events.read_source(raw, "crowding_profile", start, end):
        if isinstance(p, dict) and p.get("naptan"):
            profiles[p["naptan"]] = p
    return events.Typical(list(profiles.values()))


def main() -> None:
    raw = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "data" / "raw"
    day = sys.argv[2] if len(sys.argv) > 2 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start, end = events.parse_ms(f"{day}T00:00:00Z"), events.parse_ms(f"{day}T23:59:59Z")
    typical = load_profiles(raw, start, end)
    series = crowding_series(raw, start, end, typical)
    watched = set(series)

    ticks: list[int] = []
    active: list[set[str]] = []
    episodes: dict[tuple, list[list[int]]] = defaultdict(list)
    all_rules: dict[str, int] = defaultdict(int)
    for ts, ivs in events.replay(raw, start, end):
        ticks.append(ts)
        here = set()
        for iv in ivs:
            for fam in iv["rule"].split("+"):
                all_rules[fam] += 1
            nap = iv["where"].get("naptan")
            if nap not in watched or iv["kind"] not in DIRECTION:
                continue
            here.add(nap)
            for fam in iv["rule"].split("+"):
                eps = episodes[(fam, iv["kind"], nap)]
                if eps and ts - eps[-1][1] <= EPISODE_GAP_MIN * 60_000:
                    eps[-1][1] = ts
                else:
                    eps.append([ts, ts])
        active.append(here)

    def quiet_at(t: int) -> set[str]:
        i = bisect.bisect_right(ticks, t) - 1
        return watched - (active[i] if i >= 0 else set())

    def quiet_window(nap: str, t0: int) -> bool:
        lo, hi = bisect.bisect_left(ticks, t0), bisect.bisect_right(ticks, t0 + LEAD_MIN * 60_000)
        return all(nap not in active[i] for i in range(lo, hi))

    base_up = base_down = base_n = 0
    for nap, obs in series.items():
        for t0, _ in obs:
            if nap in quiet_at(t0) and quiet_window(nap, t0):
                d = change(obs, t0)
                if d is not None:
                    base_n += 1
                    base_up += d > 0
                    base_down += d < 0

    rows = {}
    for fam in FAMILIES:
        n = raw_hits = ctl_n = ctl_hits = 0
        for (f, kind, nap), eps in sorted(episodes.items()):
            if f != fam:
                continue
            sign = DIRECTION[kind]
            for t0, _ in eps:
                d = change(series[nap], t0)
                if d is None:
                    continue
                n += 1
                raw_hits += (d * sign) > 0
                controls = [c for s in sorted(quiet_at(t0)) if quiet_window(s, t0)
                            and (c := change(series[s], t0)) is not None]
                if controls:
                    ctl_n += 1
                    ctl_hits += ((d - sum(controls) / len(controls)) * sign) > 0
        rows[fam] = (n, raw_hits, ctl_n, ctl_hits)

    first = datetime.fromtimestamp(ticks[0] / 1000, timezone.utc).strftime("%H:%M") if ticks else "-"
    last = datetime.fromtimestamp(ticks[-1] / 1000, timezone.utc).strftime("%H:%M") if ticks else "-"
    print(f"{day} {first}-{last} UTC, {len(ticks)} replay ticks, {len(watched)} stations with live crowding")
    print(f"window: busyness vs typical just before the event -> mean over +{LAG_MIN}..+{LEAD_MIN} min")
    print(f"base rate (no event at the station): up {base_up}/{base_n} = {base_up / max(base_n, 1):.0%}, "
          f"down {base_down}/{base_n} = {base_down / max(base_n, 1):.0%}")
    print()
    print(f"{'rule family':<18} {'predicts':<8} {'n':>4} {'raw hit':>9} {'n ctl':>6} {'vs control':>11} "
          f"{'base':>6}  derived (all ticks)")
    for fam in FAMILIES:
        n, rh, cn, ch = rows[fam]
        kinds = {k for (f, k, _) in episodes if f == fam}
        signs = {DIRECTION[k] for k in kinds}
        pred = "up" if signs == {1} else "down" if signs == {-1} else "mixed" if signs else "-"
        base = (base_up if pred == "up" else base_down) / max(base_n, 1) if pred in ("up", "down") else None
        fmt = lambda h, k: f"{h}/{k} {h / k:.0%}" if k else "-"  # noqa: E731
        print(f"{fam:<18} {pred:<8} {n:>4} {fmt(rh, n):>9} {cn:>6} {fmt(ch, cn):>11} "
              f"{(f'{base:.0%}' if base is not None else '-'):>6}  {all_rules.get(fam, 0)}")
    print()
    print("n = event episodes on a station with live crowding and at least one reading 15-60 min later.")
    print("Families with n = 0 have no station-busyness ground truth today (venue stations, streets, rain).")


if __name__ == "__main__":
    main()
