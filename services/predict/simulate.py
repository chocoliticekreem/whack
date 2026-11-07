"""Deterministic forward simulation on the real street graph.

Groups walk shortest paths (Dijkstra) from their zone to their destination anchor inside their
departure window. Segment capacity slows them (queueing); a group facing a segment far over capacity
avoids it (spill-over). Interventions edit the graph: closures remove edges, incidents cut capacity,
events add an attractor, weather flips the rain reactions. Affected groups re-plan by rule, or by one
batched LLM decision-point call when enabled. Seeded RNG, so the same seed + inputs give the same run.
"""
from __future__ import annotations

import heapq
import json
import math
import random
from typing import Any

import llm
from personas import DEFAULT_REACTIONS, MOVING_SHARE, REACTIONS
from seed import dist, midpoint

STEP_S = 60
CAPACITY_PER_100M = {"path": 40.0, "minor": 70.0, "major": 110.0}
AVOID_RATIO = 1.5
LINGER_MIN = {"tourist": 20.0, "school_group": 20.0, "event_goer": 20.0, "default": 8.0}
CLOSURE_RADIUS_M = 45.0
INCIDENT_RADIUS_M = 70.0
WAIT_MIN = 5.0
# Farther than this and the group is sealed in: it waits at the closure edge rather than teleporting.
WALK_OUT_M = 150.0
# A walking group occupies at least this much street, so 20 m field segments do not read as a crush.
MIN_SEG_M = 50.0


def node_key(p: list[float] | tuple[float, float]) -> tuple[int, int]:
    return (int(round(p[0])), int(round(p[1])))


class Graph:
    def __init__(self, segments: list[dict]) -> None:
        self.adj: dict[tuple[int, int], list[tuple[tuple[int, int], float, str]]] = {}
        self.seg: dict[str, dict] = {s["id"]: s for s in segments}
        self.closed: set[str] = set()
        self.capacity: dict[str, float] = {}
        self.penalty: dict[str, float] = {}
        for s in segments:
            if s["kind"] == "rail":
                continue
            self.capacity[s["id"]] = CAPACITY_PER_100M.get(s["kind"], 50.0)
            line = s["line"]
            for a, b in zip(line, line[1:]):
                ka, kb = node_key(a), node_key(b)
                d = math.hypot(b[0] - a[0], b[1] - a[1])
                if d < 0.5:
                    continue
                self.adj.setdefault(ka, []).append((kb, d, s["id"]))
                self.adj.setdefault(kb, []).append((ka, d, s["id"]))
        self.nodes = list(self.adj)

    def nearest_node(self, p: tuple[float, float]) -> tuple[int, int]:
        return min(self.nodes, key=lambda n: (n[0] - p[0]) ** 2 + (n[1] - p[1]) ** 2)

    def nearest_reaching(self, p: tuple[float, float], dst: tuple[int, int]) -> tuple[int, int] | None:
        """Closest node that can still reach dst: where someone sealed inside a closure walks out to."""
        seen, stack = {dst}, [dst]
        while stack:
            n = stack.pop()
            for m, _, sid in self.adj.get(n, ()):
                if sid not in self.closed and m not in seen:
                    seen.add(m)
                    stack.append(m)
        return min(seen, key=lambda n: (n[0] - p[0]) ** 2 + (n[1] - p[1]) ** 2, default=None)

    def route(self, src: tuple[int, int], dst: tuple[int, int]) -> list[tuple[str, float, tuple[int, int]]]:
        """Dijkstra; returns [(segmentId, lengthM, toNode)] or [] when no path survives the closures."""
        best = {src: 0.0}
        prev: dict[tuple[int, int], tuple[tuple[int, int], str, float]] = {}
        heap = [(0.0, src)]
        while heap:
            cost, n = heapq.heappop(heap)
            if n == dst:
                break
            if cost > best.get(n, math.inf):
                continue
            for m, d, sid in self.adj.get(n, ()):
                if sid in self.closed:
                    continue
                c = cost + d * self.penalty.get(sid, 1.0)
                if c < best.get(m, math.inf):
                    best[m] = c
                    prev[m] = (n, sid, d)
                    heapq.heappush(heap, (c, m))
        if dst not in prev and src != dst:
            return []
        path, n = [], dst
        while n != src:
            p, sid, d = prev[n]
            path.append((sid, d, n))
            n = p
        return path[::-1]

    def segments_near(self, p: tuple[float, float], radius: float) -> list[str]:
        return [sid for sid, s in self.seg.items() if s["kind"] != "rail" and dist(midpoint(s["line"]), p) <= radius]


class Walker:
    def __init__(self, group: dict, graph: Graph, anchors: dict[str, dict]) -> None:
        self.g = group
        self.graph, self.anchors = graph, anchors
        self.node = graph.nearest_node((group["origin"]["x"], group["origin"]["z"]))
        self.depart = group["departMin"]
        self.path: list[tuple[str, float, tuple[int, int]]] = []
        self.idx, self.along = 0, 0.0
        self.arrived_at: float | None = None
        self.gone = False
        self.events: list[str] = []
        self.plan()

    def plan(self) -> None:
        a = self.anchors.get(self.g["destination"])
        if a is None:
            self.path = []
            return
        dst = self.graph.nearest_node((a["x"], a["z"]))
        self.path, self.idx, self.along = self.graph.route(self.node, dst), 0, 0.0
        if self.path or self.node == dst:
            return
        start = self.graph.nearest_reaching(self.node, dst)
        if start is None or start == self.node or math.dist(start, self.node) > WALK_OUT_M:
            return
        self.events.append(f"{self.g['id']} walks {math.dist(start, self.node):.0f} m out of the closure")
        self.node = start
        self.path = self.graph.route(start, dst)

    def current_segment(self) -> str | None:
        return self.path[self.idx][0] if self.idx < len(self.path) else None

    def state(self, minute: float) -> str:
        if self.gone:
            return "gone"
        if self.arrived_at is not None:
            return "arrived"
        return "waiting" if minute < self.depart else "walking"

    def step(self, minute: float, load_ratio: dict[str, float]) -> None:
        if self.gone or self.arrived_at is not None and self.leave(minute):
            self.gone = True
            return
        if self.arrived_at is not None or minute < self.depart or not self.path:
            return
        budget = self.g["speedMps"] * STEP_S
        avoided: set[str] = set()
        while budget > 0 and self.idx < len(self.path):
            sid, length, to_node = self.path[self.idx]
            ratio = load_ratio.get(sid, 0.0)
            speed = max(0.25, min(1.0, 1.0 / ratio if ratio > 0 else 1.0))
            if self.along == 0 and ratio > AVOID_RATIO and sid not in avoided and self.g["reactions"]["crowding"] == "avoid":
                avoided.add(sid)
                self.graph.penalty[sid] = 4.0
                self.plan()
                self.graph.penalty.pop(sid, None)
                if not self.path:
                    return
                if self.path[0][0] != sid:
                    self.events.append(f"{self.g['id']} avoids crowded {sid} (x{ratio:.1f} capacity) at +{minute:.0f} min")
                continue
            remaining = length - self.along
            move = min(remaining, budget * speed)
            self.along += move
            budget -= move / speed
            if self.along >= length - 1e-6:
                self.node, self.idx, self.along = to_node, self.idx + 1, 0.0
        if self.idx >= len(self.path):
            self.arrived_at = minute

    def leave(self, minute: float) -> bool:
        linger = LINGER_MIN.get(self.g["archetype"], LINGER_MIN["default"])
        return self.arrived_at is not None and minute - self.arrived_at >= linger

    def uses(self, segment_ids: set[str]) -> bool:
        return any(sid in segment_ids for sid, _, _ in self.path[self.idx:])


def zone_share(seed: dict) -> dict[str, list[tuple[str, float]]]:
    """How a zone's people spread over its segments: proportional to seeded load, else by length."""
    segs = {s["id"]: s for s in seed["segments"]}
    out = {}
    for z in seed["zones"]:
        ids = [sid for sid in z["segmentIds"] if segs[sid]["kind"] != "rail"]
        if not ids:
            continue
        w = [segs[s]["peds"] * segs[s]["lengthM"] for s in ids]
        if sum(w) <= 0:
            w = [segs[s]["lengthM"] for s in ids]
        tot = sum(w)
        out[z["id"]] = [(sid, wi / tot) for sid, wi in zip(ids, w)]
    return out


class Simulation:
    def __init__(self, seed: dict, groups: list[dict], horizon_min: int, use_llm: bool, rng: random.Random) -> None:
        self.seed, self.horizon, self.use_llm, self.rng = seed, horizon_min, use_llm and llm.enabled(), rng
        self.graph = Graph(seed["segments"])
        self.anchors = {a["name"]: a for a in seed["anchors"]}
        self.zone_of = {s["id"]: s.get("zoneId") for s in seed["segments"]}
        self.zone_by_id = {z["id"]: z for z in seed["zones"]}
        self.anchor_zone = {z["name"]: z["id"] for z in seed["zones"]}
        self.share = zone_share(seed)
        stay = 1.0 - MOVING_SHARE[seed["time"]["daypart"]]
        self.background = {s["id"]: s["peds"] * (stay if s.get("zoneId") else 1.0) for s in seed["segments"]}
        self.vehicles = {s["id"]: float(s.get("vehicles", 0.0)) for s in seed["segments"]}
        self.events: list[str] = []
        self.interventions = [dict(iv, atMin=float(iv.get("atMin", 0))) for iv in seed["interventions"]]
        self.interventions += self.real_closures()
        self.walkers = [Walker(g, self.graph, self.anchors) for g in groups]
        self.assumptions: list[str] = []

    def real_closures(self) -> list[dict]:
        """TfL 'Closed' disruptions inside the field become observed vehicle closures at t=0."""
        out = []
        for d in self.seed["disruptions"]:
            if d.get("closure") != "Closed" or not d.get("where"):
                continue
            out.append({"kind": "closure", "where": d["where"], "note": f"TfL: {d['comments']}", "atMin": 0.0,
                        "source": "observed", "vehiclesOnly": True})
        return out

    def locate(self, iv: dict) -> tuple[float, float] | None:
        where = iv.get("where")
        if isinstance(where, dict) and "x" in where:
            return (float(where["x"]), float(where["z"]))
        name = where if isinstance(where, str) else iv.get("zone")
        z = next((z for z in self.seed["zones"] if name in (z["id"], z["name"])), None)
        return (z["x"], z["z"]) if z else None

    def apply_intervention(self, iv: dict, minute: float) -> None:
        p = self.locate(iv)
        if p is None:
            self.events.append(f"intervention {iv.get('kind')} ignored: no location")
            return
        kind = iv.get("kind")
        affected: set[str] = set()
        if kind == "closure":
            affected = set(self.graph.segments_near(p, CLOSURE_RADIUS_M))
            self.redistribute_vehicles(affected)
            if not iv.get("vehiclesOnly"):
                self.graph.closed |= affected
        elif kind == "incident":
            affected = set(self.graph.segments_near(p, INCIDENT_RADIUS_M))
            for sid in affected:
                self.graph.capacity[sid] *= 0.4
                self.vehicles[sid] *= 0.5
                self.graph.penalty[sid] = 2.5
        elif kind == "event":
            name = iv.get("note") or "event"
            self.anchors[name] = {"name": name, "x": p[0], "z": p[1], "weight": 1.0, "kind": "poi"}
            self.anchor_zone[name] = self.zone_of.get(self.graph.segments_near(p, 120.0)[0]) if self.graph.segments_near(p, 120.0) else None
        elif kind == "weather":
            self.seed["weather"] = dict(self.seed.get("weather") or {}, kind=iv.get("note", "rain"), precipitationMm=2.0)
        iv["affectedSegments"] = sorted(affected)
        self.events.append(f"+{minute:.0f} min {kind}: {iv.get('note', '')} -> {len(affected)} segments")
        if not iv.get("vehiclesOnly"):
            self.replan(iv, affected, minute)

    def redistribute_vehicles(self, closed: set[str]) -> None:
        for sid in closed:
            v, zid = self.vehicles.get(sid, 0.0), self.zone_of.get(sid)
            if v <= 0:
                continue
            self.vehicles[sid] = 0.0
            alt = [o for o in (self.zone_by_id.get(zid, {}).get("segmentIds") or []) if o not in closed
                   and self.graph.seg[o]["kind"] in ("major", "minor")]
            for o in alt:
                self.vehicles[o] += v / len(alt)
            self.assumptions.append(f"vehicles on closed {sid} spread evenly over {len(alt)} open streets in its zone")

    def affected_walkers(self, iv: dict, segs: set[str]) -> list[Walker]:
        kind = iv["kind"]
        if kind in ("closure", "incident"):
            return [w for w in self.walkers if not w.gone and w.arrived_at is None and w.uses(segs)]
        if kind == "event":
            pool = [w for w in self.walkers if w.state(iv["atMin"]) in ("waiting", "walking")
                    and w.g["archetype"] in ("tourist", "event_goer", "office_lunch", "resident")]
            return [w for w in pool if self.rng.random() < 0.4]
        if kind == "weather":
            return [w for w in self.walkers if w.state(iv["atMin"]) in ("waiting", "walking") and w.g["reactions"]["rain"] != "continue"]
        return []

    def replan(self, iv: dict, segs: set[str], minute: float) -> None:
        affected = self.affected_walkers(iv, segs)
        if not affected:
            return
        decisions = self.llm_decisions(iv, affected) if self.use_llm else {}
        for w in affected:
            action = decisions.get(w.g["id"]) or self.rule_action(iv, w)
            self.enact(w, action, iv, minute)
        iv["replanned"] = len(affected)
        iv["decisionMode"] = "llm" if decisions else "rules"

    def rule_action(self, iv: dict, w: Walker) -> str:
        kind = iv["kind"]
        if kind in ("closure", "incident"):
            return w.g["reactions"]["closure"] if kind == "closure" else "reroute"
        if kind == "event":
            return "divert"
        return w.g["reactions"]["rain"]

    def enact(self, w: Walker, action: str, iv: dict, minute: float) -> None:
        gid = w.g["id"]
        if action == "reroute":
            w.plan()
            msg = "reroutes" if w.path else f"finds no open route within {WALK_OUT_M:.0f} m and waits at the closure"
        elif action == "wait":
            w.depart = max(w.depart, minute) + WAIT_MIN
            w.plan()
            msg = f"waits {WAIT_MIN:.0f} min then reroutes"
        elif action == "abandon":
            w.g["destination"] = w.g["zoneName"]
            w.plan()
            msg = "abandons the trip and heads back"
        elif action == "divert":
            w.g["destination"] = iv.get("note") or "event"
            w.plan()
            msg = f"diverts to the {iv.get('kind')}"
        elif action in ("shelter", "transit"):
            stations = [a for a in self.anchors.values() if a["kind"] == "station"]
            near = min(stations, key=lambda a: dist((a["x"], a["z"]), (w.g['origin']['x'], w.g['origin']['z'])))
            w.g["destination"] = near["name"]
            w.plan()
            msg = f"heads for {near['name']} ({action})"
        else:
            msg = "continues"
        w.g.setdefault("decisions", []).append({"atMin": minute, "intervention": iv.get("kind"), "action": action})
        self.events.append(f"+{minute:.0f} min {gid} ({w.g['archetype']}, {w.g['size']} people) {msg}")

    def llm_decisions(self, iv: dict, affected: list[Walker]) -> dict[str, str]:
        """Batched decision point: one call for every affected group; invalid actions fall back to rules."""
        allowed = ["reroute", "wait", "abandon", "divert", "shelter", "transit", "continue"]
        user = json.dumps({"intervention": {k: iv.get(k) for k in ("kind", "note", "where", "atMin")},
                           "time": self.seed["time"], "weather": self.seed.get("weather"),
                           "groups": [{k: w.g[k] for k in ("id", "archetype", "size", "zoneName", "destination", "reactions", "rationale")}
                                      for w in affected],
                           "schema": {"decisions": [{"id": "g0", "action": "|".join(allowed), "why": "short"}]}})
        try:
            data = llm.chat_json(f"Decide how each {self.seed.get('area') or 'Tower Hill'} persona group reacts. Realistic, consistent with its "
                                 "reactions. Return {\"decisions\": [...]}.", user, max_tokens=3000)
        except Exception as exc:
            self.events.append(f"LLM decision point failed ({type(exc).__name__}); rules used")
            return {}
        out = {}
        for d in (data.get("decisions") if isinstance(data, dict) else []) or []:
            if isinstance(d, dict) and d.get("action") in allowed:
                out[d.get("id")] = d["action"]
        return out

    def loads(self, minute: float) -> tuple[dict[str, float], dict[str, float]]:
        """People per segment (absolute) and the resulting peds per 100 m."""
        people = {sid: self.background[sid] * self.graph.seg[sid]["lengthM"] / 100.0 for sid in self.background}
        walking: dict[str, float] = {}
        for w in self.walkers:
            st = w.state(minute)
            if st == "gone":
                continue
            if st == "walking" and w.current_segment():
                walking[w.current_segment()] = walking.get(w.current_segment(), 0.0) + w.g["size"]
                continue
            zid = w.g["zone"] if st == "waiting" else self.anchor_zone.get(w.g["destination"])
            for sid, share in self.share.get(zid, []):
                people[sid] += w.g["size"] * share
        per100 = {sid: n * 100.0 / max(self.graph.seg[sid]["lengthM"], 5.0) for sid, n in people.items()}
        for sid, n in walking.items():
            people[sid] += n
            per100[sid] += n * 100.0 / max(self.graph.seg[sid]["lengthM"], MIN_SEG_M)
        return people, per100

    def changed(self, sid: str, peds: float) -> bool:
        """Timeline segments list only what differs from now; everything else keeps its seed value."""
        s = self.graph.seg[sid]
        dp, dv = abs(peds - s.get("peds", 0.0)), abs(self.vehicles[sid] - s.get("vehicles", 0.0))
        return dp >= max(2.0, 0.5 * s.get("peds", 0.0)) or dv >= max(2.0, 0.5 * s.get("vehicles", 0.0))

    def snapshot(self, minute: float, people: dict[str, float], per100: dict[str, float], base: dict | None) -> dict:
        zones = []
        for z in self.seed["zones"]:
            p = sum(people[s] for s in z["segmentIds"])
            v = sum(self.vehicles[s] * self.graph.seg[s]["lengthM"] / 100.0 for s in z["segmentIds"])
            zones.append({"id": z["id"], "name": z["name"], "people": round(p, 1), "vehicles": round(v, 1)})
        if base:
            for zr, zb in zip(zones, base["zones"]):
                zr["deltaPeople"] = round(zr["people"] - zb["people"], 1)
                zr["deltaVehicles"] = round(zr["vehicles"] - zb["vehicles"], 1)
        segs = [{"id": sid, "peds": round(per100[sid], 1), "vehicles": round(self.vehicles[sid], 1)}
                for sid in per100 if self.changed(sid, per100[sid])]
        hot = sorted(((per100[s] / self.graph.capacity[s], s) for s in self.graph.capacity if s not in self.graph.closed), reverse=True)[:5]
        hotspots = [{"segmentId": s, "zoneId": self.zone_of.get(s), "peds": round(per100[s], 1),
                     "capacity": self.graph.capacity[s], "loadRatio": round(r, 2)} for r, s in hot if r > 0.2]
        walking = sum(1 for w in self.walkers if w.state(minute) == "walking")
        return {"t": self.seed["at"] + int(minute * 60_000), "minute": minute, "zones": zones, "segments": segs,
                "hotspots": hotspots, "walking": walking, "closedSegments": sorted(self.graph.closed)}

    def run(self) -> list[dict]:
        timeline: list[dict] = []
        pending = sorted(self.interventions, key=lambda iv: iv["atMin"])
        for minute in range(0, self.horizon + 1, STEP_S // 60):
            while pending and pending[0]["atMin"] <= minute:
                self.apply_intervention(pending.pop(0), minute)
            people, per100 = self.loads(minute)
            ratio = {s: per100[s] / self.graph.capacity[s] for s in self.graph.capacity}
            snap = self.snapshot(minute, people, per100, timeline[0] if timeline else None)
            snap["events"] = self.events + [e for w in self.walkers for e in w.events]
            self.events = []
            for w in self.walkers:
                w.events = []
            timeline.append(snap)
            for w in self.walkers:
                w.step(minute, ratio)
        return timeline


def run_simulation(seed: dict, groups: list[dict], horizon_min: int, use_llm: bool, rng_seed: int) -> dict[str, Any]:
    sim = Simulation(seed, groups, horizon_min, use_llm, random.Random(rng_seed))
    timeline = sim.run()
    assumptions = sorted(set(sim.assumptions))
    assumptions.append(f"{int(MOVING_SHARE[seed['time']['daypart']] * 100)}% of the people counted in a zone set off within "
                       "the horizon; the rest stay where they were counted.")
    assumptions.append("Pavement capacity (people per 100 m) assumed: " + ", ".join(f"{k} {v:.0f}" for k, v in CAPACITY_PER_100M.items()))
    return {"timeline": timeline, "interventions": sim.interventions, "assumptions": assumptions,
            "closed": sorted(sim.graph.closed)}
