"""Six rule-based AI traders. Each reads exactly one feed and bets only on the series that feed informs (PLAYS),
saying why in one line that cites the real number it saw. A trader with nothing to go on sits the round out and says
so. Deterministic: no LLM calls.

Adding a series: add `{series_id: decide_fn}` to the PLAYS entry of each trader whose feed informs it. The Contrarian
plays every series."""
from __future__ import annotations

from data import Store, local_time
from markets import Market
from pricing import MIN_MS, RAIN_MM, bracket_of

STAKE = 100

TRADERS = [
    {"id": "tube", "name": "Tube Crowd", "emoji": "🚇", "feed": "crowding",
     "watches": "Live busyness at 20 Tube stations (TfL Crowding API)",
     "style": "Projects a station's last 15-min trend to the round and backs that bracket."},
    {"id": "cams", "name": "Camera Eye", "emoji": "📷", "feed": "jamcams",
     "watches": "YOLO people counts on TfL JamCams at the hotspots",
     "style": "Bets a camera count holds where it is; sits out until a camera series is listed."},
    {"id": "lines", "name": "Line Watcher", "emoji": "🚦", "feed": "line_status",
     "watches": "TfL status of every Tube line, minute by minute",
     "style": "Expects more trouble after a busy hour of status changes; sits out until a line series is listed."},
    {"id": "rain", "name": "Rain Radar", "emoji": "🌧️", "feed": "rain_now",
     "watches": "Open-Meteo precipitation now for central London",
     "style": "Only plays when it rains: wet weather sends people underground, so it backs a busier Oxford Circus."},
    {"id": "bus", "name": "Bus Spotter", "emoji": "🚌", "feed": "bus",
     "watches": "TfL bus arrivals due in the next 5 min at each hotspot's stops",
     "style": "More buses pulling in means more people; sits out until a bus-informed series is listed."},
    {"id": "contrarian", "name": "The Contrarian", "emoji": "🙃", "feed": "bets",
     "watches": "Where players and the other traders are putting their points",
     "style": "Fades the favourite of any series and takes the best-priced alternative."},
]
BY_ID = {t["id"]: t for t in TRADERS}


def pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def tube_busy(m: Market, store: Store, now: int, ctx: dict):
    """Momentum: carry half the last 15 min's change per minute forward to the round and take that bracket."""
    nap = m.spec["naptan"]
    hist = [(ts, s[nap] * 100) for ts, s in store.between("crowding", now - 16 * MIN_MS, now) if nap in s]
    if len(hist) < 2:
        return None
    (t0, a), (t1, b) = hist[0], hist[-1]
    rate = (b - a) / max(1.0, (t1 - t0) / MIN_MS)
    proj = b + 0.5 * rate * (m.settles_at - t1) / MIN_MS
    return bracket_of(proj, m.spec["edges"]), (
        f"{m.spec['name']} went from {a:.1f}% to {b:.1f}% of usual between {local_time(t0)} and {local_time(t1)}; "
        f"that trend puts it near {proj:.1f}% at {local_time(m.settles_at)}")


def rain_busy(m: Market, store: Store, now: int, ctx: dict):
    """Rain pushes people onto the Tube: back the bracket above the one the station is in now."""
    wet, crowd = store.latest("rain_now", now), store.latest("crowding", now)
    if not wet or wet[1]["mm"] <= RAIN_MM or not crowd or m.spec["naptan"] not in crowd[1]:
        return None
    v = crowd[1][m.spec["naptan"]] * 100
    k = min(len(m.spec["edges"]), int(bracket_of(v, m.spec["edges"])[1:]) + 1)
    return f"b{k}", (f"{wet[1]['mm']:g} mm of rain at {local_time(wet[0])} (Open-Meteo) and {m.spec['name']} at "
                     f"{v:.1f}%: rain sends people underground")


def contrarian(m: Market, store: Store, now: int, ctx: dict):
    stakes = ctx["stakes"]
    prices = m.prices()
    if any(stakes.values()):
        fav = max(stakes, key=lambda o: stakes[o])
        reason = f"players and traders put {stakes[fav]:g} points on {m.label(fav)}"
    else:
        fav = max(prices, key=lambda o: prices[o])
        reason = f"the market has {m.label(fav)} at {pct(prices[fav])}"
    alt = max((o for o in prices if o != fav), key=lambda o: prices[o])
    return alt, f"Fading the crowd: {reason}; taking {m.label(alt)} at {pct(prices[alt])}"


PLAYS = {"tube": {"oxford-busy": tube_busy}, "cams": {}, "lines": {}, "rain": {"oxford-busy": rain_busy}, "bus": {}}


def decide(tid: str, m: Market, store: Store, now: int, ctx: dict):
    """(option, why) or None. Only the series in the trader's PLAYS entry (all series for the Contrarian)."""
    if tid == "contrarian":
        return contrarian(m, store, now, ctx)
    play = PLAYS[tid].get(m.sub)
    return play(m, store, now, ctx) if play else None


def sit_out(tid: str, store: Store, now: int, current: list[Market]) -> str:
    """Why a trader has no bet in the current round, citing the number it looked at."""
    if tid == "contrarian":
        return "Waiting until halfway through the round to fade the favourites"
    if not any(m.sub in PLAYS[tid] for m in current):
        return f"Sitting out: no series this round is informed by its feed ({BY_ID[tid]['watches']})"
    if tid == "rain":
        last = store.latest("rain_now", now)
        if not last:
            return "Sitting out: no Open-Meteo reading yet"
        return (f"Sitting out: {last[1]['mm']:g} mm of rain at {local_time(last[0])} (Open-Meteo); it only plays "
                f"Oxford Circus busyness when it rains")
    return "Sitting out this round: its feed has no fresh reading"
