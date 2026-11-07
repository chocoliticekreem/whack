"""Persona groups: each one stands for N real people counted in the seed.

Two generators share one schema. `rules` is a transparent heuristic from time, weather and anchors.
`llm` asks W&B Inference for the same JSON in a single batched call; every group is validated and
repaired against the schema, and anything unusable is replaced by the rules group it was meant to be.
"""
from __future__ import annotations

import json
import math
import random
from typing import Any

import llm

ARCHETYPES = ("commuter", "tourist", "office_lunch", "resident", "delivery_rider", "cyclist",
              "school_group", "event_goer", "transit_arrival")
SPEED_MPS = {"commuter": 1.5, "tourist": 1.0, "office_lunch": 1.3, "resident": 1.2, "delivery_rider": 4.5,
             "cyclist": 4.0, "school_group": 0.8, "event_goer": 1.1, "transit_arrival": 1.4}
REACTIONS = {"rain": ("shelter", "transit", "continue"), "closure": ("reroute", "wait", "abandon"),
             "crowding": ("avoid", "continue", "slow")}
DEFAULT_REACTIONS = {
    "commuter": {"rain": "continue", "closure": "reroute", "crowding": "avoid"},
    "tourist": {"rain": "shelter", "closure": "reroute", "crowding": "continue"},
    "office_lunch": {"rain": "shelter", "closure": "abandon", "crowding": "avoid"},
    "resident": {"rain": "continue", "closure": "reroute", "crowding": "avoid"},
    "delivery_rider": {"rain": "continue", "closure": "reroute", "crowding": "avoid"},
    "cyclist": {"rain": "continue", "closure": "reroute", "crowding": "avoid"},
    "school_group": {"rain": "shelter", "closure": "wait", "crowding": "slow"},
    "event_goer": {"rain": "continue", "closure": "reroute", "crowding": "continue"},
    "transit_arrival": {"rain": "transit", "closure": "reroute", "crowding": "continue"},
}
# Share of the people currently counted in a zone who set off within the horizon (the rest linger).
MOVING_SHARE = {"morning_rush": 0.8, "lunch": 0.7, "evening_rush": 0.8, "daytime": 0.6, "night": 0.4}
# Riders alighting per bus is not observed by TfL; this is the assumption behind transit_arrival groups.
ALIGHT_PER_BUS = 8
MAX_GROUPS = 80
MIN_GROUPS = 30
# People walking as one blob; bigger counts become several groups with staggered departures.
MAX_GROUP_SIZE = 40


def is_raining(seed: dict) -> bool:
    w = seed.get("weather") or {}
    return (w.get("precipitationMm") or 0) > 0.2 or str(w.get("kind", "")).lower() in ("rain", "drizzle", "showers")


def anchors_by_kind(seed: dict, kind: str) -> list[dict]:
    return [a for a in seed["anchors"] if a["kind"] == kind]


def pick_destination(seed: dict, zone: dict, archetype: str, rng: random.Random) -> str:
    """Weighted by anchor pull, biased by archetype and daypart; never the origin zone itself."""
    daypart = seed["time"]["daypart"]
    stations, pois = anchors_by_kind(seed, "station"), anchors_by_kind(seed, "poi")
    if archetype in ("commuter", "transit_arrival"):
        pool = [a for a in pois if "office" in a["name"].lower()] if daypart == "morning_rush" else stations
        pool = pool or stations
    elif archetype == "office_lunch":
        pool = [a for a in pois if "office" not in a["name"].lower()]
    elif archetype in ("tourist", "school_group", "event_goer"):
        pool = [a for a in pois if "office" not in a["name"].lower()]
        if is_raining(seed):
            pool = pool + stations
    else:
        pool = seed["anchors"]
    pool = [a for a in pool if a["name"] != zone["name"]] or [a for a in seed["anchors"] if a["name"] != zone["name"]]
    return rng.choices(pool, weights=[a["weight"] for a in pool])[0]["name"]


def archetype_mix(seed: dict, zone: dict) -> list[tuple[str, float]]:
    t = seed["time"]
    daypart, weekend = t["daypart"], t["isWeekend"]
    office = "office" in zone["name"].lower()
    if zone["kind"] == "station":
        if weekend:
            return [("tourist", 0.7), ("resident", 0.2), ("delivery_rider", 0.1)]
        if daypart in ("morning_rush", "evening_rush"):
            return [("commuter", 0.75), ("tourist", 0.15), ("delivery_rider", 0.1)]
        return [("commuter", 0.35), ("tourist", 0.45), ("delivery_rider", 0.1), ("cyclist", 0.1)]
    if office and not weekend:
        if daypart == "lunch":
            return [("office_lunch", 0.7), ("commuter", 0.2), ("delivery_rider", 0.1)]
        return [("commuter", 0.6), ("office_lunch", 0.2), ("delivery_rider", 0.2)]
    mix = [("tourist", 0.6), ("resident", 0.2), ("cyclist", 0.1), ("delivery_rider", 0.1)]
    if not weekend and daypart in ("daytime", "lunch"):
        mix.append(("school_group", 0.15))
    return mix


def make_group(gid: int, archetype: str, size: int, zone: dict, destination: str, depart: float,
               rationale: str, source: str) -> dict:
    return {"id": f"g{gid}", "archetype": archetype, "size": max(1, int(round(size))), "zone": zone["id"],
            "zoneName": zone["name"], "origin": {"x": zone["x"], "z": zone["z"]}, "destination": destination,
            "departMin": round(depart, 1), "speedMps": SPEED_MPS[archetype],
            "reactions": dict(DEFAULT_REACTIONS[archetype]), "rationale": rationale, "source": source}


def zone_groups(seed: dict, zone: dict, horizon_min: int, rng: random.Random, start_id: int) -> list[dict]:
    moving = zone["people"] * MOVING_SHARE[seed["time"]["daypart"]]
    if moving < 1:
        return []
    out = []
    for archetype, share in archetype_mix(seed, zone):
        size = moving * share
        if size < 0.5:
            continue
        dest = pick_destination(seed, zone, archetype, rng)
        depart = rng.uniform(0, horizon_min * 0.6)
        why = (f"{seed['time']['daypart'].replace('_', ' ')} on a {seed['time']['weekday']}: "
               f"{archetype.replace('_', ' ')}s counted near {zone['name']} head for {dest}")
        if is_raining(seed) and DEFAULT_REACTIONS[archetype]["rain"] != "continue":
            why += "; rain pushes them towards shelter/transit"
        out.append(make_group(start_id + len(out), archetype, size, zone, dest, depart, why, "rules"))
    return out


def bus_arrival_groups(seed: dict, horizon_min: int, start_id: int) -> list[dict]:
    """Real TfL arrivals become transit_arrival groups appearing at the stop when the bus is due."""
    out = []
    station_zones = [z for z in seed["zones"] if z["kind"] == "station"]
    for stop in seed["buses"]:
        zone = min(station_zones, key=lambda z: (z["x"] - stop["x"]) ** 2 + (z["z"] - stop["z"]) ** 2, default=None)
        if zone is None:
            continue
        for sec in stop["arrivalsSec"]:
            if sec > horizon_min * 60:
                continue
            origin_zone = dict(zone, x=stop["x"], z=stop["z"])
            dest = "Tower of London" if seed["time"]["isWeekend"] or seed["time"]["daypart"] in ("daytime", "lunch") \
                else zone["name"]
            why = f"bus {'/'.join(stop['lines'])} due at {stop['stop']} in {sec // 60} min (TfL live); " \
                  f"assumes {ALIGHT_PER_BUS} alight"
            out.append(make_group(start_id + len(out), "transit_arrival", ALIGHT_PER_BUS, origin_zone, dest,
                                  sec / 60, why, "rules"))
    return out


def split_large(groups: list[dict]) -> list[dict]:
    out = []
    for g in groups:
        parts = max(1, math.ceil(g["size"] / MAX_GROUP_SIZE))
        for i in range(parts):
            part = dict(g, id=g["id"] if i == 0 else f"{g['id']}.{i}", size=g["size"] // parts + (1 if i < g["size"] % parts else 0),
                        departMin=round(g["departMin"] + 2.5 * i, 1))
            out.append(part)
    return out


def cap_groups(groups: list[dict]) -> list[dict]:
    """Keep the swarm between MIN and MAX groups: merge the smallest same-archetype pairs, or split big ones."""
    groups = sorted(split_large(groups), key=lambda g: -g["size"])
    while len(groups) > MAX_GROUPS:
        small = groups.pop()
        twin = next((g for g in reversed(groups) if g["archetype"] == small["archetype"]), groups[-1])
        twin["size"] += small["size"]
    i = 0
    while len(groups) < MIN_GROUPS and groups and i < len(groups):
        big = groups[i]
        if big["size"] >= 4:
            half = dict(big, id=f"{big['id']}b", size=big["size"] // 2, departMin=round(big["departMin"] + 3, 1))
            big["size"] -= half["size"]
            groups.append(half)
        i += 1
    return groups


def rules_personas(seed: dict, horizon_min: int, rng: random.Random) -> list[dict]:
    groups: list[dict] = []
    for zone in sorted(seed["zones"], key=lambda z: -z["people"]):
        groups += zone_groups(seed, zone, horizon_min, rng, len(groups))
    groups += bus_arrival_groups(seed, horizon_min, len(groups))
    return cap_groups(groups)


SCHEMA_HINT = {
    "groups": [{"id": "g0", "archetype": "one of " + "|".join(ARCHETYPES), "size": "int people represented",
                "zone": "zone id from the seed", "destination": "anchor name from the seed",
                "departMin": "float minutes from now within horizon",
                "reactions": {"rain": "shelter|transit|continue", "closure": "reroute|wait|abandon",
                              "crowding": "avoid|continue|slow"},
                "rationale": "one sentence grounded in the seed numbers"}]
}


def validate_group(raw: Any, seed: dict, fallback: dict, horizon_min: int) -> dict:
    """Coerce an LLM group into the schema; any field that cannot be repaired takes the rules value."""
    g = dict(fallback)
    if not isinstance(raw, dict):
        return g
    zones = {z["id"]: z for z in seed["zones"]}
    anchors = {a["name"] for a in seed["anchors"]}
    if raw.get("archetype") in ARCHETYPES:
        g["archetype"] = raw["archetype"]
        g["speedMps"] = SPEED_MPS[g["archetype"]]
    if isinstance(raw.get("size"), (int, float)) and raw["size"] > 0:
        g["size"] = int(round(raw["size"]))
    if raw.get("zone") in zones:
        z = zones[raw["zone"]]
        g.update(zone=z["id"], zoneName=z["name"], origin={"x": z["x"], "z": z["z"]})
    if raw.get("destination") in anchors and raw["destination"] != g["zoneName"]:
        g["destination"] = raw["destination"]
    if isinstance(raw.get("departMin"), (int, float)):
        g["departMin"] = round(min(max(float(raw["departMin"]), 0.0), horizon_min * 0.9), 1)
    reactions = raw.get("reactions") if isinstance(raw.get("reactions"), dict) else {}
    g["reactions"] = {k: (reactions.get(k) if reactions.get(k) in REACTIONS[k] else DEFAULT_REACTIONS[g["archetype"]][k])
                      for k in REACTIONS}
    if isinstance(raw.get("rationale"), str) and raw["rationale"].strip():
        g["rationale"] = raw["rationale"].strip()[:240]
    g["source"] = "llm"
    return g


def llm_personas(seed: dict, horizon_min: int, rng: random.Random) -> list[dict]:
    """One batched call. The rules swarm is both the prompt's starting point and the repair fallback."""
    base = rules_personas(seed, horizon_min, rng)
    from seed import seed_summary
    summary = seed_summary(seed)
    user = json.dumps({"horizonMin": horizon_min, "seed": summary,
                       "zones": [{"id": z["id"], "name": z["name"], "kind": z["kind"], "people": z["people"]}
                                 for z in seed["zones"]],
                       "anchors": [a["name"] for a in seed["anchors"]],
                       "startingGroups": [{k: g[k] for k in ("id", "archetype", "size", "zone", "destination", "departMin")}
                                          for g in base],
                       "schema": SCHEMA_HINT})
    system = (f"You are MiroFish-style persona planner for {seed.get('area') or 'Tower Hill'}, London. Keep the SAME group ids and total "
              "people. Adjust archetype, destination, departMin, reactions and rationale so they fit the real time, "
              "weather, disruptions and bus arrivals given. Realistic city dynamics only. Return {\"groups\": [...]}.")
    try:
        data = llm.chat_json(system, user, max_tokens=6000)
    except Exception as exc:
        for g in base:
            g["rationale"] += f" (LLM unavailable: {type(exc).__name__}; rules used)"
        return base
    raw_by_id = {g.get("id"): g for g in (data.get("groups") if isinstance(data, dict) else data) or [] if isinstance(g, dict)}
    return [validate_group(raw_by_id.get(g["id"]), seed, g, horizon_min) for g in base]


def generate(seed: dict, horizon_min: int, use_llm: bool, rng: random.Random) -> tuple[list[dict], str]:
    if use_llm and llm.enabled():
        return llm_personas(seed, horizon_min, rng), "llm"
    return rules_personas(seed, horizon_min, rng), "rules"
