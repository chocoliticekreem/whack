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

import brokers
import worlds

DEFAULT_N = 50
DEFAULT_HORIZON = 5
VIX_SCALE = 100.0
# A VIX point counts as a spike when it jumps by this much over the previous one, relative and absolute.
VIX_SPIKE_REL = 0.2
VIX_SPIKE_ABS = 2.0
DELIST_FRACTION = 0.1
# Counts compared on the sqrt scale: Poisson noise gives sd 0.5 there; model error adds MODEL_SD.
POISSON_SD = 0.5
MODEL_SD = 1.0
BUS_SD_MIN = 2.0
FORK_PRIOR = 0.5
MAX_LIVE_FACTOR = 2
# A sharp frame can price out most of the market at once; keep at least 40 % so lineages stay diverse.
MAX_DELIST_SHARE = 0.6
# Brokered refills jitter each knob by this fraction of the market's own price-weighted spread on it
# (a shrunk kernel, as in Liu-West), so children stay inside the posterior and still explore it.
KERNEL_H = 0.3
KERNEL_MIN = 0.01
# This share of market-maker children swap one random knob for a partner's, drawn from the previous
# frame's market. A sharp frame (rain onset) can leave one lineage standing; recombining it with what the
# market knew a frame earlier recovers the knobs that lineage happened to get wrong.
CROSSOVER = 0.5
ROUND = 5
# With brokers on, likelihood delisting may use all but SPLIT_RESERVE of the per-frame delist budget; the
# reserve recycles the cheapest worlds into neighbours of any world worth more than HEAVY, so one lucky
# world cannot hold the market alone.
SPLIT_RESERVE = 0.2
HEAVY = 0.1
# One in this many of a broker's refills tests its fresh idea (at least one per frame); the rest refine.
FRESH_SHARE = 2


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


def delist_mask(prices: list[float], fraction: float = DELIST_FRACTION, max_share: float = 1.0,
                protected: list[bool] | None = None) -> list[bool]:
    """True = delist. The best world always survives, so the market never empties.

    At most `max_share` of the worlds go in one frame (cheapest first); protected worlds never go.
    """
    if not prices:
        return []
    threshold = fraction / len(prices)
    best = max(range(len(prices)), key=lambda i: prices[i])
    keep = protected or [False] * len(prices)
    cand = sorted((i for i, p in enumerate(prices) if p < threshold and i != best and not keep[i]),
                  key=lambda i: prices[i])
    kill = set(cand[: int(max_share * len(prices))])
    return [i in kill for i in range(len(prices))]


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


def allocate_refill(deficits: dict, k: int) -> dict:
    """Split k new worlds among owners by how far each is under quota (largest remainder)."""
    total = sum(deficits.values())
    if k <= 0 or total <= 0:
        return {o: 0 for o in deficits}
    if total <= k:
        return dict(deficits)
    shares = {o: k * d / total for o, d in deficits.items()}
    out = {o: int(v) for o, v in shares.items()}
    for o in sorted(deficits, key=lambda o: -(shares[o] - out[o]))[: k - sum(out.values())]:
        out[o] += 1
    return out


def _group(items: list[tuple[str | None, dict | None]]) -> dict[str | None, int]:
    out: dict[str | None, int] = {}
    for owner, _ in items:
        out[owner] = out.get(owner, 0) + 1
    return out


def kernel_sds(ws: list[dict], horizon: int) -> dict[str, float]:
    """KERNEL_H x the price-weighted sd of each knob across ws (floored at KERNEL_MIN of its range)."""
    total = sum(w["price"] for w in ws) or 1.0
    out = {}
    for k, (lo, hi) in [*worlds.RANGES.items(), ("rainAtMin", (0.0, float(horizon)))]:
        vals = [(w["price"], float(w["assumptions"][k])) for w in ws if w["assumptions"].get(k) is not None]
        mass = sum(p for p, _ in vals) or total
        mean = sum(p * v for p, v in vals) / mass if vals else 0.0
        sd = math.sqrt(sum(p * (v - mean) ** 2 for p, v in vals) / mass) if vals else 0.0
        out[k] = max(KERNEL_H * sd, KERNEL_MIN * (hi - lo))
    return out


def london_vix(entries: list[tuple[float, dict[str, list[float]]]], index: int) -> float:
    """London VIX: per zone, the price-weighted coefficient of variation of predicted people at `index`,
    averaged over zones weighted by their predicted volume, x VIX_SCALE (so sum of sds / sum of means).
    Identical worlds give 0; the more the priced worlds disagree about the horizon, the higher it reads."""
    if not entries:
        return 0.0
    num = den = 0.0
    for zid in entries[0][1]:
        vals = [(p, s[zid][min(index, len(s[zid]) - 1)]) for p, s in entries if s.get(zid)]
        mass = sum(p for p, _ in vals)
        if mass <= 0:
            continue
        mean = sum(p * v for p, v in vals) / mass
        if mean <= 0:
            continue
        cv = math.sqrt(sum(p * (v - mean) ** 2 for p, v in vals) / mass) / mean
        num += mean * cv
        den += mean
    return round(VIX_SCALE * num / den, 2) if den else 0.0


ALL_CAMS = "all cameras"
ALL_NEW = "new worlds"
# A group (all cameras, all of this frame's new worlds) is named as the driver only when it moves the VIX
# this many times more than any single source; otherwise the single camera, feed or broker is the better
# answer to "what moved it".
GROUP_DOMINANCE = 2.0


def pick_driver(effects: dict[str, float]) -> str:
    singles = {k: v for k, v in effects.items() if k not in (ALL_CAMS, ALL_NEW)}
    if not singles:
        return "no change"
    best = max(singles, key=lambda k: abs(singles[k]))
    groups = [g for g in (ALL_CAMS, ALL_NEW) if abs(effects.get(g, 0.0)) > GROUP_DOMINANCE * abs(singles[best])]
    return max(groups, key=lambda g: abs(effects[g])) if groups else best


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
    def __init__(self, seed: dict, n: int = DEFAULT_N, horizon: int = DEFAULT_HORIZON, crn_seed: int = 47,
                 rng_seed: int = 7, brokerage: brokers.Brokerage | None = None) -> None:
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
        self.brokerage = brokerage
        self.placed_events: list[dict] = []
        self.epoch_obs: list[tuple[int, dict[str, float], float | None]] = []
        self.before: list[dict] = []
        self.origin_ms = seed["at"]
        self.feeds: dict[str, Any] = {}
        self.vix_history: list[dict] = []

    def new_world(self, assumptions: dict, parent: dict | None, price: float, born: int) -> dict:
        t0 = time.perf_counter()
        run = worlds.run_world(self.seed, assumptions, self.horizon, self.crn_seed)
        w = {"id": f"W-{self.next_ticker}", "parent": parent["id"] if parent else None,
             "generation": parent["generation"] + 1 if parent else 0, "status": "live", "price": price,
             "logWeight": math.log(price) if price > 0 else -math.inf, "history": [round(price, 5)],
             "assumptions": assumptions, "labels": worlds.describe(assumptions, self.zone_names),
             "run": run, "trail": [], "bornAtObs": born, "delistReason": None,
             "broker": parent.get("broker") if parent else None, "protectedUntilObs": 0,
             "simMs": round((time.perf_counter() - t0) * 1000, 1)}
        self.next_ticker += 1
        return w

    def spawn(self) -> None:
        t0 = time.perf_counter()
        self.worlds, self.observations, self.epoch_obs, self.vix_history = [], [], [], []
        if self.brokerage:
            self.spawn_brokered()
        else:
            for _ in range(self.n):
                rng = random.Random(self.rng.random())
                a = worlds.sample_assumptions(rng, self.horizon, self.zone_ids)
                self.worlds.append(self.new_world(a, None, 1.0 / self.n, 0))
        self.spawn_ms = round((time.perf_counter() - t0) * 1000)
        self.mark_vix(0, self.seed["at"], 0, "spawn", 0.0)

    def spawn_brokered(self) -> None:
        """The market maker's slice is drawn from the prior; every broker gets its floor of slots or more."""
        bk = self.brokerage
        self.n = max(self.n, len(bk.roster) + 1)
        ideas = bk.refresh(self.broker_context(0, None, None), 0)
        slots = bk.slots(self.n)
        for _ in range(bk.mm_slots):
            a = worlds.sample_assumptions(random.Random(self.rng.random()), self.horizon, self.zone_ids)
            self.worlds.append(self.new_world(a, None, 1.0 / self.n, 0))
        for bid, k in slots.items():
            for _ in range(k):
                rng = random.Random(self.rng.random())
                donor = worlds.sample_assumptions(rng, self.horizon, self.zone_ids)
                a = brokers.assumptions_from_idea(ideas[bid], rng, self.horizon, self.zone_ids, donor)
                w = self.new_world(a, None, 1.0 / self.n, 0)
                w["broker"] = bid
                self.worlds.append(w)
        bk.mark_prior(self.live())

    def broker_context(self, minute: int, readings: dict[str, float] | None, bus_delay: float | None) -> dict:
        bus_seen = [o["busDelayMin"] for o in self.observations if o.get("busDelayMin") is not None]
        if bus_delay is not None:
            bus_seen.append(bus_delay)
        return brokers.build_context(self.seed, self.horizon, minute, readings, bus_delay, bus_seen, self.feeds)

    def vix_of(self, ws: list[dict], prices: list[float] | None = None) -> float:
        prices = [w["price"] for w in ws] if prices is None else prices
        return london_vix([(p, w["run"]["zones"]) for p, w in zip(prices, ws)], self.horizon)

    def mark_vix(self, obs_index: int, ts_ms: int, minute: int, driver: str, driver_delta: float) -> dict:
        vix = self.vix_of(self.live())
        prev = self.vix_history[-1]["vix"] if self.vix_history else vix
        delta = round(vix - prev, 2)
        entry = {"obs": obs_index, "ts": ts_ms, "minute": minute, "t": round((ts_ms - self.origin_ms) / 60_000, 1),
                 "vix": vix, "delta": delta, "driver": driver, "driverDelta": round(driver_delta, 2),
                 "spike": delta >= VIX_SPIKE_ABS and delta >= VIX_SPIKE_REL * max(prev, 1e-9)}
        self.vix_history.append(entry)
        return entry

    def source_label(self, src: str) -> str:
        return "bus feed" if src == "bus" else f"cam {self.cam_names.get(src, src)}"

    def broker_label(self, bid: str) -> str:
        b = self.brokerage.book.get(bid) if self.brokerage else None
        return f"{b['feed']} broker" if b else str(bid)

    def drivers_from_evidence(self, live: list[dict], prior: list[float], parts: list[list[dict]],
                              vix_before: float) -> dict[str, float]:
        """VIX change from each evidence source alone: reweight the pre-frame market by that source's likelihood."""
        out = {}
        for src in {p["cam"] for ps in parts for p in ps}:
            lw = [(math.log(pr) if pr > 0 else -math.inf) + sum(p["ll"] for p in ps if p["cam"] == src)
                  for pr, ps in zip(prior, parts)]
            out[self.source_label(src)] = self.vix_of(live, normalise(lw)) - vix_before
        lw = [(math.log(pr) if pr > 0 else -math.inf) + sum(p["ll"] for p in ps if p["cam"] != "bus")
              for pr, ps in zip(prior, parts)]
        out[ALL_CAMS] = self.vix_of(live, normalise(lw)) - vix_before
        return out

    def drivers_from_brokers(self, obs_index: int, vix_after: float) -> dict[str, float]:
        """VIX change from each owner's newborn worlds (a broker, or the market maker's refills): the market
        with and without them."""
        live = self.live()
        out = {}
        for bid in {w.get("broker") for w in live if w["bornAtObs"] == obs_index}:
            rest = [w for w in live if not (w["bornAtObs"] == obs_index and w.get("broker") == bid)]
            if rest:
                label = self.broker_label(bid) if bid else "market maker refills"
                out[label] = vix_after - self.vix_of(rest, normalise([w["logWeight"] for w in rest]))
        old = [w for w in live if w["bornAtObs"] != obs_index]
        if old and len(old) < len(live):
            out[ALL_NEW] = vix_after - self.vix_of(old, normalise([w["logWeight"] for w in old]))
        return out

    def advance(self, frame: dict) -> dict:
        """Observe one frame; past the horizon, first roll into a new epoch seeded at the last observed frame."""
        ts = frame["ts"]
        if ts > self.seed["at"] + self.horizon * 60_000:
            last = next((o for o in reversed(self.observations) if o.get("readings") and o["ts"] > self.seed["at"]), None)
            base = (last["ts"], last["readings"]) if last and ts - last["ts"] <= self.horizon * 60_000 else (ts, frame["readings"])
            self.roll(reseed(self.seed, *base))
        if frame.get("feeds") is not None:
            self.feeds = frame["feeds"]
        return self.observe(ts, frame["readings"], frame.get("busDelayMin"))

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

    def recombine(self, a: dict, rng: random.Random) -> dict:
        if rng.random() >= CROSSOVER:
            return a
        partner = self.before[systematic_resample([w["price"] for w in self.before], 1, rng)[0]]["assumptions"]
        knob = rng.choice(list(worlds.RANGES))
        return a | {knob: partner[knob]}

    def history_loglik(self, w: dict) -> float:
        """Log-likelihood of everything seen this epoch under w's forecast (as if it had been live all along)."""
        return sum(self.world_loglik(w, m, r, b)[0] for m, r, b in self.epoch_obs)

    def observe(self, ts_ms: int, readings: dict[str, float], bus_delay: float | None = None) -> dict:
        minute = self.minute_of(ts_ms)
        live = self.live()
        compared = [c for c in readings if c in self.cam_names]
        obs_index = len(self.observations) + 1
        if not compared and bus_delay is None:
            obs = {"index": obs_index, "ts": ts_ms, "minute": minute, "compared": 0, "delisted": [], "spawned": []}
            self.observations.append(obs)
            return obs
        self.epoch_obs.append((minute, {c: readings[c] for c in compared}, bus_delay))
        self.before = [{"price": w["price"], "assumptions": w["assumptions"]} for w in live]
        prior = [w["price"] for w in live]
        vix_before = self.vix_of(live, prior)
        all_parts = []
        for w in live:
            ll, parts = self.world_loglik(w, minute, readings, bus_delay)
            all_parts.append(parts)
            w["logWeight"] += ll
            worst = min(parts, key=lambda p: p["ll"]) if parts else None
            w["trail"].append({"obs": obs_index, "minute": minute, "ll": round(ll, 3), "worst": worst})
        effects = self.drivers_from_evidence(live, prior, all_parts, vix_before)
        self.reprice(live)
        if self.brokerage:
            self.brokerage.score(live)
        share = MAX_DELIST_SHARE - SPLIT_RESERVE if self.brokerage else MAX_DELIST_SHARE
        mask = delist_mask([w["price"] for w in live], max_share=share,
                           protected=[w["protectedUntilObs"] >= obs_index for w in live])
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
        if self.brokerage:
            spawned, cut = self.rebalance(obs_index, minute, readings, bus_delay,
                                          int(MAX_DELIST_SHARE * len(live)) - len(delisted))
            delisted += cut
        else:
            spawned = self.resample(survivors, obs_index)
        for w in self.worlds:
            if w["status"] == "live" or w.get("delistedAtObs") == obs_index:
                w["history"].append(round(w["price"], 5))
        effects |= self.drivers_from_brokers(obs_index, self.vix_of(self.live()))
        driver = pick_driver(effects)
        self.mark_vix(obs_index, ts_ms, minute, driver, effects.get(driver, 0.0))
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

    def rebalance(self, obs_index: int, minute: int, readings: dict[str, float],
                  bus_delay: float | None, cut_budget: int) -> tuple[list[str], list[str]]:
        """TOPS step. Prices stay pure likelihood; brokers only decide where the refill worlds go.

        Slots: a market-maker slice plus a track-record split among brokers (with a floor). An owner over
        its quota loses its cheapest below-average worlds (never protected ones, at most `cut_budget`).
        The refill goes to owners under quota. Market-maker refills resample the whole market by price
        (a plain particle filter); a broker's refills mostly refine its own best worlds (or the market's,
        if none of its own is worth at least an even share) and at least one starts from a world drawn
        from the posterior. Every broker child carries the broker's current idea, which moves only the
        knobs the broker has a view on.
        A newborn world is priced on everything seen this epoch, as if it had been live all along (the
        prior is flat, so price = likelihood); a child that fixes a knob the latest frames barely test
        (bus delay) or that a sharp frame got wrong is valued on all of the evidence at once.
        """
        bk = self.brokerage
        ideas = bk.refresh(self.broker_context(minute, readings, bus_delay), obs_index)
        quota: dict[str | None, int] = {None: 0, **bk.slots(self.n)}
        quota[None] = bk.mm_slots
        survivors = self.live()
        owned: dict[str | None, list[dict]] = {o: [] for o in quota}
        for w in survivors:
            owned.setdefault(w.get("broker") if w.get("broker") in quota else None, []).append(w)
        average = 1.0 / max(len(survivors), 1)
        cut: list[str] = []
        for owner, mine in owned.items():
            spare = sorted((w for w in mine if w["protectedUntilObs"] < obs_index and w["price"] < average),
                           key=lambda w: w["price"])
            for w in spare[: max(0, len(mine) - quota[owner])]:
                if len(cut) >= cut_budget - int(SPLIT_RESERVE * self.n):
                    break
                w["status"], w["price"], w["delistedAtObs"] = "delisted", 0.0, obs_index
                name = bk.book[owner]["persona"] if owner in bk.book else "market maker"
                track = bk.book[owner]["trackRecord"] if owner in bk.book else 0.0
                w["delistReason"] = f"slot cut: {name} track record {track:+.2f}"
                cut.append(w["id"])
            owned[owner] = [w for w in mine if w["status"] == "live"]
        survivors = self.live()
        self.reprice(survivors)
        deficits = {o: max(0, quota[o] - len(owned[o])) for o in quota}
        refill = allocate_refill(deficits, self.n - len(survivors))
        own: list[tuple[str | None, dict | None]] = []
        market: list[tuple[str | None, dict | None]] = []
        for owner, k in refill.items():
            if k <= 0:
                continue
            idea = ideas.get(owner) if owner is not None else None
            fresh = 0 if owner is None else max(1, k // FRESH_SHARE)
            mine = owned[owner]
            if owner is None or not any(w["price"] >= 1.0 / self.n for w in mine):
                market += [(owner, idea)] * (k - fresh)
            else:
                own += [(owner, idea)] * (k - fresh)
            if fresh:
                market += [(owner, idea)] * fresh
        # Market draws go in small rounds, each resampling the market after the last round's children are
        # priced, so a world that a newborn just discovered gets children in the same frame.
        self.rng.shuffle(market)
        spawned = self.spawn_round(own, owned, market[:ROUND], obs_index)
        for i in range(ROUND, len(market), ROUND):
            spawned += self.spawn_round([], owned, market[i:i + ROUND], obs_index)
        scale = 1.0
        while len(cut) < cut_budget:
            live = self.live()
            self.reprice(live)
            heavy = max(live, key=lambda w: w["price"])
            per = _group([(w.get("broker"), None) for w in live])
            # A broker's last world is never recycled, so every broker stays on the board.
            spare = [w for w in live if w["protectedUntilObs"] < obs_index and w["bornAtObs"] < obs_index
                     and (w.get("broker") is None or per[w.get("broker")] > 1)]
            if heavy["price"] <= HEAVY or not spare:
                break
            cheap = min(spare, key=lambda w: w["price"])
            cheap["status"], cheap["price"], cheap["delistedAtObs"] = "delisted", 0.0, obs_index
            cheap["delistReason"] = f"recycled: room for worlds near {heavy['id']} ({heavy['price']:.0%} of the market)"
            cut.append(cheap["id"])
            child = self.spawn_near(heavy, obs_index, scale)
            spawned.append(child["id"])
            gap = child["logWeight"] - heavy["logWeight"]
            scale = scale / 2 if gap < -2 else min(1.0, scale * 1.5) if gap > -0.5 else scale
        live = self.live()
        self.reprice(live)
        bk.mark_prior(live)
        return spawned, cut

    def spawn_near(self, parent: dict, obs_index: int, scale: float) -> dict:
        """One market-maker child of `parent` from the kernel (times `scale`), priced on the whole epoch."""
        live = self.live()
        rng = random.Random(self.rng.random())
        sds = {k: v * scale for k, v in kernel_sds(live, self.horizon).items()}
        a = worlds.jitter_assumptions(parent["assumptions"], rng, self.horizon, self.zone_ids, sds)
        child = self.new_world(a, parent, 0.0, obs_index)
        child["broker"] = None
        child["logWeight"] = self.history_loglik(child) + parent["logWeight"] - self.history_loglik(parent)
        self.worlds.append(child)
        return child

    def spawn_round(self, own: list[tuple[str | None, dict | None]], owned: dict,
                    market: list[tuple[str | None, dict | None]], obs_index: int) -> list[str]:
        """Children for `own` (parent from the owner's lineage) and `market` (parent from the whole market).

        Each child is priced on the whole epoch's evidence, plus its parent's offset (event priors)."""
        live = self.live()
        self.reprice(live)
        sds = kernel_sds(live, self.horizon)
        plans: list[tuple[dict, str | None, dict | None]] = []
        for owner, group in _group(own).items():
            mine = [w for w in owned[owner] if w["status"] == "live"]
            idea = next(i for o, i in own if o == owner)
            plans += [(mine[i], owner, idea) for i in systematic_resample([w["price"] for w in mine], group, self.rng)]
        picks = systematic_resample([w["price"] for w in live], len(market), self.rng)
        order = list(range(len(market)))
        self.rng.shuffle(order)
        plans += [(live[picks[j]], market[i][0], market[i][1]) for j, i in enumerate(order)]
        spawned = []
        for parent, owner, idea in plans:
            rng = random.Random(self.rng.random())
            a = worlds.jitter_assumptions(parent["assumptions"], rng, self.horizon, self.zone_ids, sds)
            if owner is None and self.before:
                a = self.recombine(a, rng)
            if idea is not None:
                a = brokers.assumptions_from_idea(idea, rng, self.horizon, self.zone_ids, a)
            child = self.new_world(a, parent, 0.0, obs_index)
            child["broker"] = owner
            child["logWeight"] = self.history_loglik(child) + parent["logWeight"] - self.history_loglik(parent)
            self.worlds.append(child)
            spawned.append(child["id"])
        return spawned

    def intervene(self, kind: str, zone_id: str, at_min: float, note: str | None = None,
                  prior: float = FORK_PRIOR) -> list[str]:
        """Fork every live world: a child with the event takes `prior` of the parent's price.

        Forked worlds belong to the market maker and survive the next observation whatever it shows;
        after that only likelihood prices them out.
        """
        if zone_id not in self.zone_names:
            raise ValueError(f"unknown zone {zone_id}")
        if kind not in ("closure", "incident", "event", "weather"):
            raise ValueError(f"unknown intervention kind {kind}")
        live = sorted(self.live(), key=lambda w: -w["price"])[: self.n * MAX_LIVE_FACTOR // 2]
        forked = []
        event = {"kind": kind, "zoneId": zone_id, "atMin": float(at_min), "note": note or f"{kind} (placed)"}
        self.placed_events.append(event)
        for parent in live:
            a = dict(parent["assumptions"], events=list(parent["assumptions"].get("events", [])) + [event])
            child = self.new_world(a, parent, parent["price"] * prior, len(self.observations))
            child["broker"] = None
            child["protectedUntilObs"] = len(self.observations) + 1
            parent["price"] *= 1 - prior
            parent["logWeight"] = math.log(parent["price"]) if parent["price"] > 0 else -math.inf
            self.worlds.append(child)
            forked.append(child["id"])
        self.mark_vix(len(self.observations), self.observations[-1]["ts"] if self.observations else self.seed["at"],
                      self.now_minute(), f"placed {kind}", 0.0)
        self.vix_history[-1]["driverDelta"] = self.vix_history[-1]["delta"]
        return forked

    def roll(self, new_seed: dict) -> None:
        """Past the horizon: re-run every live world from the new epoch, keeping prices and lineage.

        Timed assumptions (rain onset, incidents, events) move earlier by the time elapsed, so a world that
        said "rain from +3" five minutes ago now says it is raining from the start."""
        elapsed = (new_seed["at"] - self.seed["at"]) / 60_000
        self.seed = new_seed
        self.epoch_obs = []
        for w in self.live():
            w["assumptions"] = worlds.shift_assumptions(w["assumptions"], elapsed)
            w["labels"] = worlds.describe(w["assumptions"], self.zone_names)
            w["run"] = worlds.run_world(new_seed, w["assumptions"], self.horizon, self.crn_seed)
        self.placed_events = [dict(e, atMin=max(0.0, e["atMin"] - elapsed)) for e in self.placed_events]
        self.epochs += 1
        self.mark_vix(len(self.observations), new_seed["at"], 0, "new window", 0.0)
        self.vix_history[-1]["driverDelta"] = self.vix_history[-1]["delta"]

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
                                           "delistReason", "bornAtObs", "simMs", "broker")}
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
                "camsNow": self.cam_consensus(minute), "epochs": self.epochs,
                "vix": self.vix_history[-1] if self.vix_history else None, "vix_history": self.vix_history[-60:],
                "brokers": self.brokerage.leaderboard(live) if self.brokerage else None,
                "zones": [{"id": z["id"], "name": z["name"], "x": z["x"], "z": z["z"], "kind": z["kind"]}
                          for z in self.seed["zones"]]}
