"""Prediction report: the few changes worth saying out loud, with the inputs that drive them.

Items come from the timeline (largest zone swings, when they peak). Reasons name the persona groups
that end up in or pass through the zone and the live inputs behind them. Prose is LLM-written when
enabled, templated otherwise. Assumptions are always listed explicitly.
"""
from __future__ import annotations

import json
from collections import Counter

import llm

MIN_ABS_CHANGE = 3.0
TOP_N = 5


def zone_series(timeline: list[dict]) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for step in timeline:
        for z in step["zones"]:
            out.setdefault(z["id"], []).append((step["minute"], z["people"]))
    return out


def peak_change(series: list[tuple[float, float]]) -> tuple[float, float]:
    base = series[0][1]
    minute, value = max(series, key=lambda mv: abs(mv[1] - base))
    return minute, value - base


def drivers(zone: dict, groups: list[dict], anchor_zone: dict[str, str]) -> list[str]:
    inbound = Counter(g["archetype"] for g in groups if anchor_zone.get(g["destination"]) == zone["id"])
    outbound = Counter(g["archetype"] for g in groups if g["zone"] == zone["id"])
    out = []
    if inbound:
        out.append("arriving: " + ", ".join(f"{n} {a.replace('_', ' ')} group{'s' if n > 1 else ''}" for a, n in inbound.most_common(3)))
    if outbound:
        out.append("leaving: " + ", ".join(f"{n} {a.replace('_', ' ')} group{'s' if n > 1 else ''}" for a, n in outbound.most_common(3)))
    return out


def confidence(zone: dict, mode: str, magnitude: float) -> float:
    c = 0.25 + 0.5 * zone.get("observedShare", 0.0) + (0.15 if zone.get("camIds") else 0.0)
    if abs(magnitude) > 40:
        c -= 0.1
    return round(min(0.9, max(0.1, c)), 2)


def build_items(seed: dict, groups: list[dict], sim: dict, mode: str) -> list[dict]:
    anchor_zone = {z["name"]: z["id"] for z in seed["zones"]}
    for iv in sim["interventions"]:
        if iv.get("kind") == "event" and iv.get("note"):
            anchor_zone.setdefault(iv["note"], None)
    zones = {z["id"]: z for z in seed["zones"]}
    series = zone_series(sim["timeline"])
    forced = {zid for iv in sim["interventions"] if iv.get("source") != "observed" for zid in map_zone_ids(iv, seed)}
    items = []
    for zid, s in series.items():
        minute, delta = peak_change(s)
        if abs(delta) < MIN_ABS_CHANGE and zid not in forced:
            continue
        z = zones[zid]
        items.append({"zone": z["name"], "zoneId": zid, "atMin": minute, "now": round(s[0][1], 1),
                      "predicted": round(s[0][1] + delta, 1), "delta": round(delta, 1),
                      "direction": "busier" if delta > 0 else "quieter", "reasons": drivers(z, groups, anchor_zone),
                      "inputs": zone_inputs(seed, z, sim), "confidence": confidence(z, mode, delta),
                      "intervention": zid in forced})
    items.sort(key=lambda i: (not i["intervention"], -abs(i["delta"])))
    return items[:TOP_N]


def zone_inputs(seed: dict, zone: dict, sim: dict) -> list[str]:
    out = []
    if zone["camIds"]:
        out.append(f"{len(zone['camIds'])} JamCam(s) observed {zone['observedPeople']} people now")
    w = seed.get("weather") or {}
    if w:
        out.append(f"weather {w.get('kind')} {w.get('temperatureC')}C, {w.get('precipitationMm', 0)} mm rain")
    buses = [s for s in seed["buses"] if any(a <= 1800 for a in s["arrivalsSec"])]
    if zone["kind"] == "station" and buses:
        out.append(f"{sum(1 for s in buses for a in s['arrivalsSec'] if a <= 1800)} TfL bus arrivals due within 30 min nearby")
    for iv in sim["interventions"]:
        if zone["id"] in {sim_zone for sim_zone in map_zone_ids(iv, seed)}:
            out.append(f"{iv['kind']}: {iv.get('note', '')}".strip())
    return out


def map_zone_ids(iv: dict, seed: dict) -> set[str]:
    seg_zone = {s["id"]: s.get("zoneId") for s in seed["segments"]}
    return {seg_zone.get(sid) for sid in iv.get("affectedSegments", []) if seg_zone.get(sid)}


def headline(seed: dict, items: list[dict], mode: str) -> str:
    t = seed["time"]
    where = f"{seed.get('area') or 'Tower Hill'}, {t['weekday']} {t['iso'][11:16]} ({t['daypart'].replace('_', ' ')})"
    if not items:
        return f"{where}: no zone moves by more than {MIN_ABS_CHANGE:.0f} people in the next half hour."
    top = items[0]
    return (f"{where}: {top['zone']} gets {top['direction']} by {abs(top['delta']):.0f} people around "
            f"+{top['atMin']:.0f} min; {len(items) - 1} other zones shift.")


def template_prose(items: list[dict]) -> list[str]:
    out = []
    for it in items:
        why = "; ".join(it["reasons"]) or "background flow only"
        out.append(f"{it['zone']} goes from {it['now']:.0f} to {it['predicted']:.0f} people by +{it['atMin']:.0f} min "
                   f"({why}). Confidence {it['confidence']:.2f}.")
    return out


def llm_prose(seed: dict, items: list[dict], assumptions: list[str]) -> list[str] | None:
    from seed import seed_summary
    user = json.dumps({"seed": seed_summary(seed), "items": items, "assumptions": assumptions})
    system = (f"Write the prediction report for a {seed.get('area') or 'Tower Hill'} (London) digital twin: one sentence per item, concrete numbers, "
              "name the inputs, no hype. Return {\"sentences\": [string per item in order]}.")
    try:
        data = llm.chat_json(system, user, max_tokens=1500)
        sentences = data.get("sentences") if isinstance(data, dict) else None
        if isinstance(sentences, list) and len(sentences) == len(items) and all(isinstance(s, str) for s in sentences):
            return sentences
    except Exception:
        return None
    return None


def build_report(seed: dict, groups: list[dict], sim: dict, mode: str) -> dict:
    items = build_items(seed, groups, sim, mode)
    assumptions = list(seed["assumptions"]) + list(sim["assumptions"])
    if mode == "rules":
        assumptions.append("LLM off: personas, re-planning and prose come from transparent rules.")
    prose = (llm_prose(seed, items, assumptions) if mode == "llm" else None) or template_prose(items)
    for it, text in zip(items, prose):
        it["text"] = text
    return {"headline": headline(seed, items, mode), "items": items, "assumptions": assumptions, "mode": mode,
            "events": [e for step in sim["timeline"] for e in step["events"]][:40]}
