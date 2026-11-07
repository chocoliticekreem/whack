"""Six rule-based AI traders. Each reads exactly one feed, bets only on markets that feed informs, and says why
in one line citing the real number it saw. Deterministic: no LLM calls."""
from __future__ import annotations

import statistics

from data import Store, local_time
from markets import HISTORY_MS, Market
from pricing import GOOD_SERVICE, MIN_MS, RAIN_MM

STAKE = 100

TRADERS = [
    {"id": "tube", "name": "Tube Crowd", "emoji": "🚇", "feed": "crowding",
     "watches": "Live busyness at 20 Tube stations (TfL Crowding API)",
     "style": "Backs whichever station is busiest against its usual level right now."},
    {"id": "cams", "name": "Camera Eye", "emoji": "📷", "feed": "jamcams",
     "watches": "YOLO people counts on TfL JamCams at the hotspots",
     "style": "Bets the camera stays where it is: over if the count is already above the line, under if not."},
    {"id": "lines", "name": "Line Watcher", "emoji": "🚦", "feed": "line_status",
     "watches": "TfL status of every Tube line, minute by minute",
     "style": "Expects more trouble after a busy hour of status changes; expects short delays to clear."},
    {"id": "rain", "name": "Rain Radar", "emoji": "🌧️", "feed": "rain_now",
     "watches": "Open-Meteo precipitation now and 15-min nowcast for central London",
     "style": "Follows the 15-minute nowcast for the slot the market settles in."},
    {"id": "bus", "name": "Bus Spotter", "emoji": "🚌", "feed": "bus",
     "watches": "TfL bus arrivals due in the next 5 min at each hotspot's stops",
     "style": "More buses pulling in means more people; bus counts drift back to each stop's normal."},
    {"id": "contrarian", "name": "The Contrarian", "emoji": "🙃", "feed": "bets",
     "watches": "Where players and the other traders are putting their points",
     "style": "Fades the crowd favourite and takes the best-priced alternative."},
]
BY_ID = {t["id"]: t for t in TRADERS}


def pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def tube(m: Market, store: Store, now: int, ctx: dict):
    if m.sub == "level":
        return tube_level(m, store, now)
    if m.sub != "busiest":
        return None
    last = store.latest("crowding", now)
    if not last:
        return None
    snap = last[1]
    vals = {o: snap[n] for o, n in m.spec["stations"].items() if n in snap}
    if len(vals) < len(m.spec["stations"]):
        return None
    ranked = sorted(vals, key=lambda o: -vals[o])
    top, second = ranked[0], ranked[1]
    day = local_time(last[0], "%A")
    return top, (f"{m.spec['names'][top]} is at {pct(vals[top])} of its usual {day} level at {local_time(last[0])}, "
                 f"ahead of {m.spec['names'][second]} at {pct(vals[second])}")


def tube_level(m: Market, store: Store, now: int):
    """Momentum: over if the station got busier over the last 15 min, under if it got quieter."""
    nap = m.spec["naptan"]
    hist = [(ts, s[nap]) for ts, s in store.between("crowding", now - 16 * MIN_MS, now) if nap in s]
    if len(hist) < 2 or hist[-1][1] == hist[0][1]:
        return None
    (t0, a), (t1, b) = hist[0], hist[-1]
    side = "over" if b > a else "under"
    return side, (f"{m.spec['name']} went from {a * 100:.1f}% to {b * 100:.1f}% of usual between {local_time(t0)} "
                  f"and {local_time(t1)}; the line is {m.spec['line']:g}%")


def cams(m: Market, store: Store, now: int, ctx: dict):
    if m.sub != "cam":
        return None
    last = store.latest("jamcams", now)
    cam = m.spec["cam"]
    if not last or cam not in last[1]:
        return None
    v, line = last[1][cam], m.spec["line"]
    side = "over" if v > line else "under"
    return side, f"{m.spec['camName']} camera counts {v:g} people at {local_time(last[0])}; the line is {line}"


def lines(m: Market, store: Store, now: int, ctx: dict):
    hist = store.between("line_status", now - 60 * MIN_MS, now)
    if m.sub == "lines_any" and hist:
        worse = []
        for (_, a), (t1, b) in zip(hist, hist[1:]):
            worse += [(t1, b[lid]["name"]) for lid in m.spec["baseline"] if lid in a and lid in b
                      and b[lid]["sev"] < a[lid]["sev"]]
        delayed = sum(1 for sev in m.spec["baseline"].values() if sev != GOOD_SERVICE)
        if worse:
            t, name = worse[-1]
            why = (f"{len(worse)} Tube line{'s' if len(worse) > 1 else ''} got worse in the last hour ({name} at "
                   f"{local_time(t)}); {delayed} of {len(m.spec['baseline'])} already delayed")
        else:
            why = f"No Tube line got worse in the last hour; {delayed} of {len(m.spec['baseline'])} delayed now"
        return ("yes" if len(worse) >= 2 else "no"), why
    if m.sub == "line" and hist:
        lid = m.spec["line"]
        since = next((ts for ts, s in reversed(hist) if lid in s and s[lid]["sev"] == GOOD_SERVICE), None)
        mins = (now - since) / MIN_MS if since else 60
        label = f"{int(mins)} min" if since else "over an hour"
        return ("yes" if mins < 10 else "no"), f"{m.spec['name']} has shown {m.spec['desc']} for {label}"
    return None


def rain(m: Market, store: Store, now: int, ctx: dict):
    if m.sub != "rain":
        return None
    last = store.latest("rain_now", now)
    if not last:
        return None
    snap = last[1]
    ahead = [(t, mm) for t, mm in snap["fc"] if now - 15 * MIN_MS < t <= m.settles_at]
    peak_t, peak = max(ahead, key=lambda x: x[1]) if ahead else (m.settles_at, 0.0)
    wet = snap["mm"] > RAIN_MM or peak > RAIN_MM
    return ("yes" if wet else "no"), (f"Open-Meteo shows {snap['mm']:g} mm now and {peak:g} mm forecast for the "
                                      f"{local_time(peak_t)} slot")


def bus(m: Market, store: Store, now: int, ctx: dict):
    last = store.latest("bus", now)
    if not last or m.sub not in ("busiest", "cam", "bus"):
        return None
    due = last[1]
    if m.sub == "bus":
        return bus_count(m, store, now, due.get(m.spec["spot"], 0))
    if m.sub == "busiest":
        counts = {o: due.get(o, 0) for o in m.option_ids()}
        ranked = sorted(counts, key=lambda o: -counts[o])
        if counts[ranked[0]] == counts[ranked[1]]:
            return None
        top, second = ranked[0], ranked[1]
        return top, (f"{counts[top]} buses due at {m.spec['names'][top]} stops in the next 5 min vs "
                     f"{counts[second]} at {m.spec['names'][second]}")
    spot = m.hotspots[0]
    hist = [s.get(spot, 0) for _, s in store.between("bus", now - HISTORY_MS, now)]
    if len(hist) < 10:
        return None
    typical, n = statistics.median(hist), due.get(spot, 0)
    if n == typical:
        return None
    name = ctx["spots"][spot]["short"]
    return ("over" if n > typical else "under"), f"{n} buses due near {name} in 5 min (today's typical {typical:g})"


def bus_count(m: Market, store: Store, now: int, n: float):
    """Bus counts revert to the stop's normal: bet on today's typical count, not on the current one."""
    spot, line = m.spec["spot"], m.spec["line"]
    hist = [s.get(spot, 0) for _, s in store.between("bus", now - HISTORY_MS, now)]
    if len(hist) < 10:
        return None
    typical = statistics.median(hist)
    if typical == line:
        return None
    side = "over" if typical > line else "under"
    return side, (f"{n:g} buses due at {m.spec['name']} stops now, but today's typical is {typical:g}; "
                  f"the line is {line}")


def contrarian(m: Market, store: Store, now: int, ctx: dict):
    stakes = ctx["stakes"]
    prices = m.prices()
    if any(stakes.values()):
        fav = max(stakes, key=lambda o: stakes[o])
        reason = f"players and traders put {stakes[fav]:g} points on {label_of(m, fav)}"
    else:
        fav = max(prices, key=lambda o: prices[o])
        reason = f"the market has {label_of(m, fav)} at {pct(prices[fav])}"
    alt = max((o for o in prices if o != fav), key=lambda o: prices[o])
    return alt, f"Fading the crowd: {reason}; taking {label_of(m, alt)} at {pct(prices[alt])}"


def label_of(m: Market, oid: str) -> str:
    return next(o["label"] for o in m.options if o["id"] == oid)


DECIDE = {"tube": tube, "cams": cams, "lines": lines, "rain": rain, "bus": bus, "contrarian": contrarian}
