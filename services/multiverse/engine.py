"""The multiverse: N Predict worlds, repriced by reality, pruned and resampled.

A world's price is its posterior probability of being the world we are in. Each observation
multiplies it by the likelihood of what the cameras (and bus feed) saw under that world's forecast.
Worlds whose price falls under DELIST_FRACTION / N are delisted with the reason that killed them;
survivors are resampled (systematic, proportional to price) to spawn jittered children until N
worlds are live again. A parent splits its price with its children, so prices always sum to 1.
"""
from __future__ import annotations

import copy
import math
import random
import time
from typing import Any

import worlds

DEFAULT_N = 50
DELIST_FRACTION = 0.1
# Counts compared on the sqrt scale: Poisson noise gives sd 0.5 there; model error adds MODEL_SD.
POISSON_SD = 0.5
MODEL_SD = 1.0
BUS_SD_MIN = 2.0
FORK_PRIOR = 0.5
MAX_LIVE_FACTOR = 2


def log_likelihood_counts(predicted: float, observed: float) -> float:
    sd2 = POISSON_SD ** 2 + MODEL_SD ** 2
    return -0.5 * (math.sqrt(max(observed, 0.0)) - math.sqrt(max(predicted, 0.0))) ** 2 / sd2


def log_likelihood_bus(assumed_delay: float, observed_delay: float) -> float:
    return -0.5 * ((assumed_delay - observed_delay) / BUS_SD_MIN) ** 2


def normalise(log_weights: list[float]) -> list[float]:
    if not log_weights:
        return []
    top = max(log_weights)
    if top == -math.inf:
        return [1.0 / len(log_weights)] * len(log_weights)
    exp = [math.exp(lw - top) for lw in log_weights]
    total = sum(exp)
    return [e / total for e in exp]


def delist_mask(prices: list[float], fraction: float = DELIST_FRACTION) -> list[bool]:
    """True = delist. The best world always survives, so the market never empties."""
    if not prices:
        return []
    threshold = fraction / len(prices)
    best = max(range(len(prices)), key=lambda i: prices[i])
    return [p < threshold and i != best for i, p in enumerate(prices)]


def systematic_resample(prices: list[float], k: int, rng: random.Random) -> list[int]:
    """k indices drawn proportionally to prices with one uniform offset (low-variance resampling)."""
    if k <= 0 or not prices:
        return []
    total = sum(prices)
    step = total / k
    u = rng.uniform(0, step)
    out, cum, i = [], prices[0], 0
    for j in range(k):
        target = u + j * step
        while target > cum and i < len(prices) - 1:
            i += 1
            cum += prices[i]
        out.append(i)
    return out


def reseed(seed: dict, ts_ms: int, readings: dict[str, float]) -> dict:
    """A new epoch: the latest camera counts become the starting state on the same street graph."""
    s = copy.deepcopy(seed)
    s["at"] = ts_ms
    s["time"] = worlds.seedmod.london_time(ts_ms / 1000)
    for cam in s.get("cams", []):
        if cam["id"] in readings:
            cam["people"], cam["at"] = readings[cam["id"]], ts_ms
    for seg in s["segments"]:
        seg.pop("zoneId", None)
        if seg.get("camId") in readings:
            seg["peds"] = readings[seg["camId"]] * 100.0 / worlds.seedmod.CAM_VIEW_M
    s["zones"] = worlds.seedmod.build_zones(s, s["anchors"])
    return s


class Multiverse:
    def __init__(self, seed: dict, n: int = DEFAULT_N, horizon: int = 30, crn_seed: int = 47,
                 rng_seed: int = 7) -> None:
        self.seed, self.n, self.horizon, self.crn_seed = seed, n, horizon, crn_seed
        self.rng = random.Random(rng_seed)
        self.zone_ids = [z["id"] for z in seed["zones"]]
        self.zone_names = {z["id"]: z["name"] for z in seed["zones"]}
        self.cam_names = {c["id"]: c.get("name") or c["id"] for c in seed.get("cams", [])}
        self.worlds: list[dict[str, Any]] = []
        self.next_ticker = 101
        self.observations: list[dict[str, Any]] = []
        self.spawn_ms = 0
        self.epochs = 0

    def new_world(self, assumptions: dict, parent: dict | None, price: float, born: int) -> dict:
        t0 = time.perf_counter()
        run = worlds.run_world(self.seed, assumptions, self.horizon, self.crn_seed)
        w = {"id": f"W-{self.next_ticker}", "parent": parent["id"] if parent else None,
             "generation": parent["generation"] + 1 if parent else 0, "status": "live", "price": price,
             "logWeight": math.log(price) if price > 0 else -math.inf, "history": [round(price, 5)],
             "assumptions": assumptions, "labels": worlds.describe(assumptions, self.zone_names),
             "run": run, "trail": [], "bornAtObs": born, "delistReason": None,
             "simMs": round((time.perf_counter() - t0) * 1000, 1)}
        self.next_ticker += 1
        return w

    def spawn(self) -> None:
        t0 = time.perf_counter()
        self.worlds, self.observations = [], []
        for _ in range(self.n):
            rng = random.Random(self.rng.random())
            a = worlds.sample_assumptions(rng, self.horizon, self.zone_ids)
            self.worlds.append(self.new_world(a, None, 1.0 / self.n, 0))
        self.spawn_ms = round((time.perf_counter() - t0) * 1000)

    def live(self) -> list[dict]:
        return [w for w in self.worlds if w["status"] == "live"]

    def minute_of(self, ts_ms: int) -> int:
        return max(0, min(self.horizon, round((ts_ms - self.seed["at"]) / 60_000)))

    def world_loglik(self, w: dict, minute: int, readings: dict[str, float],
                     bus_delay: float | None) -> tuple[float, list[dict]]:
        parts = []
        for cam_id, observed in readings.items():
            series = w["run"]["cams"].get(cam_id)
            if not series:
                continue
            predicted = series[min(minute, len(series) - 1)]
            parts.append({"cam": cam_id, "predicted": predicted, "observed": observed,
                          "ll": log_likelihood_counts(predicted, observed)})
        if bus_delay is not None:
            assumed = float(w["assumptions"]["busDelayMin"])
            parts.append({"cam": "bus", "predicted": assumed, "observed": bus_delay,
                          "ll": log_likelihood_bus(assumed, bus_delay)})
        return sum(p["ll"] for p in parts), parts

    def observe(self, ts_ms: int, readings: dict[str, float], bus_delay: float | None = None) -> dict:
        minute = self.minute_of(ts_ms)
        live = self.live()
        compared = [c for c in readings if c in self.cam_names]
        obs_index = len(self.observations) + 1
        if not compared and bus_delay is None:
            obs = {"index": obs_index, "ts": ts_ms, "minute": minute, "compared": 0, "delisted": [], "spawned": []}
            self.observations.append(obs)
            return obs
        for w in live:
            ll, parts = self.world_loglik(w, minute, readings, bus_delay)
            w["logWeight"] += ll
            worst = min(parts, key=lambda p: p["ll"]) if parts else None
            w["trail"].append({"obs": obs_index, "minute": minute, "ll": round(ll, 3), "worst": worst})
        self.reprice(live)
        mask = delist_mask([w["price"] for w in live])
        delisted = []
        for w, kill in zip(live, mask):
            if kill:
                w["status"] = "delisted"
                w["delistReason"] = self.reason(w, minute)
                w["delistedAtObs"] = obs_index
                w["price"] = 0.0
                delisted.append(w["id"])
        survivors = self.live()
        self.reprice(survivors)
        spawned = self.resample(survivors, obs_index)
        for w in self.worlds:
            if w["status"] == "live" or w.get("delistedAtObs") == obs_index:
                w["history"].append(round(w["price"], 5))
        obs = {"index": obs_index, "ts": ts_ms, "minute": minute, "compared": len(compared),
               "busDelayMin": bus_delay, "delisted": delisted, "spawned": spawned,
               "readings": {c: readings[c] for c in compared}}
        self.observations.append(obs)
        return obs

    def reprice(self, ws: list[dict]) -> None:
        for w, p in zip(ws, normalise([w["logWeight"] for w in ws])):
            w["price"] = p
            w["logWeight"] = math.log(p) if p > 0 else -math.inf

    def reason(self, w: dict, minute: int) -> str:
        worst = w["trail"][-1]["worst"] if w["trail"] else None
        if not worst:
            return f"priced out at +{minute} min"
        if worst["cam"] == "bus":
            return f"assumed buses {worst['predicted']:.1f} min late; TfL said {worst['observed']:.1f} min"
        name = self.cam_names.get(worst["cam"], worst["cam"])
        return f"predicted {worst['predicted']:.0f} people at {name} (+{minute} min), cameras saw {worst['observed']:.0f}"

    def resample(self, survivors: list[dict], obs_index: int) -> list[str]:
        need = self.n - len(survivors)
        if need <= 0 or not survivors:
            return []
        picks = systematic_resample([w["price"] for w in survivors], need, self.rng)
        children: dict[int, int] = {}
        for i in picks:
            children[i] = children.get(i, 0) + 1
        spawned = []
        for i, k in children.items():
            parent = survivors[i]
            share = parent["price"] / (k + 1)
            parent["price"], parent["logWeight"] = share, math.log(share)
            for _ in range(k):
                a = worlds.jitter_assumptions(parent["assumptions"], random.Random(self.rng.random()),
                                              self.horizon, self.zone_ids)
                child = self.new_world(a, parent, share, obs_index)
                self.worlds.append(child)
                spawned.append(child["id"])
        return spawned

    def intervene(self, kind: str, zone_id: str, at_min: float, note: str | None = None,
                  prior: float = FORK_PRIOR) -> list[str]:
        """Fork every live world: a child with the event takes `prior` of the parent's price."""
        if zone_id not in self.zone_names:
            raise ValueError(f"unknown zone {zone_id}")
        if kind not in ("closure", "incident", "event", "weather"):
            raise ValueError(f"unknown intervention kind {kind}")
        live = sorted(self.live(), key=lambda w: -w["price"])[: self.n * MAX_LIVE_FACTOR // 2]
        forked = []
        event = {"kind": kind, "zoneId": zone_id, "atMin": float(at_min), "note": note or f"{kind} (placed)"}
        for parent in live:
            a = dict(parent["assumptions"], events=list(parent["assumptions"].get("events", [])) + [event])
            child = self.new_world(a, parent, parent["price"] * prior, len(self.observations))
            parent["price"] *= 1 - prior
            parent["logWeight"] = math.log(parent["price"]) if parent["price"] > 0 else -math.inf
            self.worlds.append(child)
            forked.append(child["id"])
        return forked

    def roll(self, new_seed: dict) -> None:
        """Past the horizon: re-run every live world from the new epoch, keeping prices and lineage."""
        self.seed = new_seed
        for w in self.live():
            w["run"] = worlds.run_world(new_seed, w["assumptions"], self.horizon, self.crn_seed)
        self.epochs += 1

    def consensus(self, minute: int) -> list[dict]:
        live = self.live()
        out = []
        for zid in self.zone_ids:
            values = [(w["price"], w["run"]["zones"][zid][min(minute, len(w["run"]["zones"][zid]) - 1)]) for w in live]
            total = sum(p for p, _ in values) or 1.0
            mean = sum(p * v for p, v in values) / total
            var = sum(p * (v - mean) ** 2 for p, v in values) / total
            sd = math.sqrt(var)
            out.append({"zoneId": zid, "name": self.zone_names[zid], "mean": round(mean, 1), "sd": round(sd, 1),
                        "iv": round(sd / mean, 3) if mean > 0 else 0.0})
        return out

    def cam_consensus(self, minute: int) -> list[dict]:
        live = self.live()
        out = []
        for cid, name in self.cam_names.items():
            vals = [(w["price"], w["run"]["cams"][cid][min(minute, self.horizon)]) for w in live if cid in w["run"]["cams"]]
            if not vals:
                continue
            total = sum(p for p, _ in vals) or 1.0
            mean = sum(p * v for p, v in vals) / total
            sd = math.sqrt(sum(p * (v - mean) ** 2 for p, v in vals) / total)
            out.append({"camId": cid, "name": name, "mean": round(mean, 1), "sd": round(sd, 1)})
        return out

    def now_minute(self) -> int:
        return self.observations[-1]["minute"] if self.observations else 0

    def board(self) -> list[dict]:
        rows = []
        for w in sorted(self.worlds, key=lambda w: (w["status"] != "live", -w["price"])):
            rows.append({k: w[k] for k in ("id", "parent", "generation", "status", "price", "history", "labels",
                                           "delistReason", "bornAtObs", "simMs")}
                        | {"assumptions": w["assumptions"], "trail": w["trail"][-3:], "events": w["run"]["events"][:4]})
        return rows

    def state(self) -> dict:
        minute = self.now_minute()
        live = self.live()
        ess = 1.0 / sum(w["price"] ** 2 for w in live) if live else 0.0
        return {"area": self.seed.get("area"), "seedAt": self.seed["at"], "seedSource": self.seed.get("source"),
                "horizonMin": self.horizon, "n": self.n, "crnSeed": self.crn_seed, "minute": minute,
                "live": len(live), "delisted": sum(1 for w in self.worlds if w["status"] == "delisted"),
                "effectiveWorlds": round(ess, 1), "spawnMs": self.spawn_ms,
                "observations": self.observations[-20:], "board": self.board(),
                "consensusNow": self.consensus(minute), "consensusHorizon": self.consensus(self.horizon),
                "camsNow": self.cam_consensus(minute),
                "zones": [{"id": z["id"], "name": z["name"], "x": z["x"], "z": z["z"], "kind": z["kind"]}
                          for z in self.seed["zones"]]}
