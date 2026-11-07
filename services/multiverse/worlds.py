"""One world = one Predict run under one set of assumptions.

The Predict engine (services/predict) keeps its knobs as module constants. A world patches them for
the length of its run and restores them afterwards, so runs must stay sequential (the server holds a
lock). Shared randomness comes from one `crn_seed` for every world: the persona swarm, departures and
destinations are drawn from the same stream, so two worlds differ because of their assumptions, not
because of noise. Each world's own assumptions are drawn from a separate per-world stream.
"""
from __future__ import annotations

import contextlib
import copy
import random
import sys
from pathlib import Path
from typing import Any, Iterator

# Appended, not prepended: services/predict also has a server.py, and ours must win.
sys.path.append(str(Path(__file__).resolve().parent.parent / "predict"))

import personas  # noqa: E402
import seed as seedmod  # noqa: E402
import simulate  # noqa: E402

PEOPLE_PER_FRAME = seedmod.CAM_VIEW_M / 100.0

RANGES: dict[str, tuple[float, float]] = {
    "movingShare": (0.3, 0.9),
    "touristFactor": (0.5, 1.8),
    "capacityScale": (0.6, 1.4),
    "alightPerBus": (3.0, 15.0),
    "busDelayMin": (0.0, 8.0),
    "walkPace": (0.8, 1.2),
}
RAIN_PROB = 0.25
INCIDENT_PROB = 0.1
JITTER_SD = 0.08
FLIP_PROB = 0.1

LABELS = {
    "movingShare": lambda v: f"{v * 100:.0f}% of people set off",
    "touristFactor": lambda v: f"tourists x{v:.2f}",
    "capacityScale": lambda v: f"pavement capacity x{v:.2f}",
    "alightPerBus": lambda v: f"{v:.0f} riders per bus",
    "busDelayMin": lambda v: f"buses {v:.1f} min late",
    "walkPace": lambda v: f"walking pace x{v:.2f}",
}


def sample_assumptions(rng: random.Random, horizon: int, zone_ids: list[str]) -> dict[str, Any]:
    a: dict[str, Any] = {k: round(rng.uniform(lo, hi), 3) for k, (lo, hi) in RANGES.items()}
    a["alightPerBus"] = round(a["alightPerBus"])
    a["rainAtMin"] = round(rng.uniform(0, horizon * 0.8), 1) if rng.random() < RAIN_PROB else None
    a["incident"] = ({"zoneId": rng.choice(zone_ids), "atMin": round(rng.uniform(0, horizon * 0.6), 1)}
                     if zone_ids and rng.random() < INCIDENT_PROB else None)
    a["events"] = []
    return a


def jitter_assumptions(parent: dict[str, Any], rng: random.Random, horizon: int,
                       zone_ids: list[str], sds: dict[str, float] | None = None) -> dict[str, Any]:
    """A child near its parent. `sds` overrides the kernel per knob (and for rainAtMin), absolute units."""
    child = copy.deepcopy(parent)
    sds = sds or {}
    for k, (lo, hi) in RANGES.items():
        child[k] = round(min(hi, max(lo, parent[k] + rng.gauss(0, sds.get(k, JITTER_SD * (hi - lo))))), 3)
    child["alightPerBus"] = round(child["alightPerBus"])
    if rng.random() < FLIP_PROB:
        child["rainAtMin"] = None if parent["rainAtMin"] is not None else round(rng.uniform(0, horizon * 0.8), 1)
    elif child["rainAtMin"] is not None:
        child["rainAtMin"] = round(min(horizon, max(0.0, child["rainAtMin"] + rng.gauss(0, sds.get("rainAtMin", 2.0)))), 1)
    if rng.random() < FLIP_PROB / 2 and zone_ids:
        child["incident"] = None if parent["incident"] else {"zoneId": rng.choice(zone_ids),
                                                             "atMin": round(rng.uniform(0, horizon * 0.6), 1)}
    return child


def describe(a: dict[str, Any], zone_names: dict[str, str]) -> list[str]:
    out = [LABELS[k](a[k]) for k in RANGES]
    if a.get("rainAtMin") is not None:
        out.append(f"rain from +{a['rainAtMin']:.0f} min")
    if a.get("incident"):
        out.append(f"incident at {zone_names.get(a['incident']['zoneId'], a['incident']['zoneId'])} "
                   f"+{a['incident']['atMin']:.0f} min")
    for ev in a.get("events", []):
        out.append(f"{ev['kind']} at {zone_names.get(ev.get('zoneId'), ev.get('zoneId'))} +{float(ev.get('atMin', 0)):.0f} min")
    return out


@contextlib.contextmanager
def patched(a: dict[str, Any], daypart: str) -> Iterator[None]:
    saved_share = dict(personas.MOVING_SHARE)
    saved_cap = dict(simulate.CAPACITY_PER_100M)
    saved_speed = dict(personas.SPEED_MPS)
    saved_alight = personas.ALIGHT_PER_BUS
    saved_mix = personas.archetype_mix
    tourist = float(a["touristFactor"])

    def mix(seed: dict, zone: dict) -> list[tuple[str, float]]:
        return [(arch, w * tourist if arch == "tourist" else w) for arch, w in saved_mix(seed, zone)]

    try:
        personas.MOVING_SHARE[daypart] = float(a["movingShare"])
        for k in simulate.CAPACITY_PER_100M:
            simulate.CAPACITY_PER_100M[k] = saved_cap[k] * float(a["capacityScale"])
        for k in personas.SPEED_MPS:
            personas.SPEED_MPS[k] = saved_speed[k] * float(a["walkPace"])
        personas.ALIGHT_PER_BUS = int(a["alightPerBus"])
        personas.archetype_mix = mix
        yield
    finally:
        personas.MOVING_SHARE.clear()
        personas.MOVING_SHARE.update(saved_share)
        simulate.CAPACITY_PER_100M.clear()
        simulate.CAPACITY_PER_100M.update(saved_cap)
        personas.SPEED_MPS.clear()
        personas.SPEED_MPS.update(saved_speed)
        personas.ALIGHT_PER_BUS = saved_alight
        personas.archetype_mix = saved_mix


def interventions_for(a: dict[str, Any], seed: dict) -> list[dict]:
    first_zone = seed["zones"][0]["id"] if seed["zones"] else None
    out = list(copy.deepcopy(seed.get("interventions") or []))
    if a.get("rainAtMin") is not None and first_zone:
        out.append({"kind": "weather", "where": first_zone, "note": "rain", "atMin": a["rainAtMin"]})
    if a.get("incident"):
        out.append({"kind": "incident", "where": a["incident"]["zoneId"], "note": "assumed incident",
                    "atMin": a["incident"]["atMin"]})
    for ev in a.get("events", []):
        out.append({"kind": ev["kind"], "where": ev["zoneId"], "note": ev.get("note") or ev["kind"],
                    "atMin": float(ev.get("atMin", 0))})
    return out


def delay_buses(buses: list[dict], delay_min: float) -> list[dict]:
    shift = int(delay_min * 60)
    return [dict(b, arrivalsSec=[s + shift for s in b["arrivalsSec"]]) for b in buses]


def cam_segments(seed: dict) -> dict[str, dict]:
    return {s["camId"]: s for s in seed["segments"] if s.get("camId")}


class RecordingSimulation(simulate.Simulation):
    """Predict's timeline lists a segment only when it moved >= 50 %; scoring needs every camera's load."""

    def __init__(self, seed: dict, groups: list[dict], horizon: int, rng: random.Random, watch: set[str]) -> None:
        super().__init__(seed, groups, horizon, False, rng)
        self.watch = watch
        self.records: list[dict[str, float]] = []

    def snapshot(self, minute: float, people: dict, per100: dict, base: dict | None) -> dict:
        self.records.append({sid: per100.get(sid, 0.0) for sid in self.watch})
        return super().snapshot(minute, people, per100, base)


def run_world(seed: dict, a: dict[str, Any], horizon: int, crn_seed: int) -> dict[str, Any]:
    """Run Predict under assumptions `a`; return per-minute people per camera frame and per zone."""
    s = copy.deepcopy(seed)
    s["interventions"] = interventions_for(a, s)
    s["buses"] = delay_buses(s.get("buses", []), float(a["busDelayMin"]))
    cams = cam_segments(seed)
    with patched(a, s["time"]["daypart"]):
        groups = personas.rules_personas(s, horizon, random.Random(crn_seed))
        sim = RecordingSimulation(s, groups, horizon, random.Random(crn_seed), {c["id"] for c in cams.values()})
        timeline = sim.run()
    cam_series = {cid: [round(rec[seg["id"]] * PEOPLE_PER_FRAME, 2) for rec in sim.records] for cid, seg in cams.items()}
    zone_series: dict[str, list[float]] = {z["id"]: [] for z in seed["zones"]}
    for step in timeline:
        for z in step["zones"]:
            zone_series[z["id"]].append(z["people"])
    events = [e for step in timeline for e in step["events"]]
    return {"cams": cam_series, "zones": zone_series, "events": events[:12], "groups": len(groups)}
