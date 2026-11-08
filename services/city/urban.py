"""Eight five-minute London questions. Frozen baskets, empirical joint-change pricing and source-time settlement.

This module deliberately doesn't claim calibrated skill. It lists only with eight distinct historical five-minute
pairs, and bootstraps their joint changes (not independent camera/station shocks). No settlement uses model output.
"""
from __future__ import annotations

import math
import statistics
from functools import partial

import data
import markets as mk
import pricing

MIN = pricing.MIN_MS
WAIT = 20 * MIN
MAX_ALIGN = 150_000
MIN_PAIRS = 8
WEST_END = (51.5152, -0.1418)
WATERLOO = (51.5036, -0.1143)

# id, primary feed, title, hotspot ids
QUESTIONS = {
    "waterloo_bikes": ("bikepoint", "Will hire bikes become harder to find around Waterloo", ["waterloo"]),
    "west_end_docks": ("bikepoint", "Will a West End bike dock have no space", ["oxford-circus", "piccadilly"]),
    "kings_cross_bunching": ("bus_detail", "Will buses near King's Cross become more bunched together", ["kings-cross"]),
    "oxford_bus_wait": ("bus_detail", "Will the wait for a bus near Oxford Circus improve", ["oxford-circus"]),
    "piccadilly_divergence": ("crowding", "Will street crowds and Tube activity around Piccadilly move in opposite directions", ["piccadilly"]),
    "west_end_counts": ("camera_detail", "Will vehicle counts rise more than pedestrian counts in the West End", ["oxford-circus", "piccadilly"]),
    "london_breadth": ("crowding", "Will crowd growth spread across most of central London's monitored stations", []),
    "bank_waterloo_swing": ("crowding", "Will activity around Bank change more sharply than around Waterloo", ["bank", "waterloo"]),
}


def distance(lat, lon, centre):
    return 6_371_000 * math.hypot(math.radians(lat - centre[0]),
                                math.radians(lon - centre[1]) * math.cos(math.radians(centre[0])))


def baskets(sid, store, spots, now):
    """Freeze sensor identities; empty/missing measurements never mean zero."""
    by_id = {h["id"]: h for h in spots}
    spec = {"urban": True, "question": sid, "bikes": [], "stops": [], "groups": [], "cameras": [], "stations": {}}
    if sid in ("waterloo_bikes", "west_end_docks"):
        last = store.latest("bikepoint", now)
        if not last:
            return None
        centre = WATERLOO if sid == "waterloo_bikes" else WEST_END
        spec["bikes"] = sorted(k for k, v in last[1].items() if distance(v["lat"], v["lon"], centre) <= 800
                               and (sid != "west_end_docks" or v["spaces"] > 0))
        if len(spec["bikes"]) < 3:
            return None
    if sid in ("kings_cross_bunching", "oxford_bus_wait"):
        last = store.latest("bus_detail", now)
        if not last:
            return None
        hub = by_id.get("kings-cross" if sid == "kings_cross_bunching" else "oxford-circus")
        if not hub:
            return None
        if sid == "oxford_bus_wait":
            spec["stops"] = sorted({v["stop"] for v in last[1].values() if v["stop"] in hub["busStops"]})
            if len(spec["stops"]) < 2:
                return None
        else:
            groups = {}
            for v in last[1].values():
                if v["stop"] in hub["busStops"]:
                    groups.setdefault((v["stop"], v["route"]), []).append(v["wait"])
            spec["groups"] = [list(k) for k, v in sorted(groups.items()) if len(v) >= 2]
            if len(spec["groups"]) < 2:
                return None
    if sid in ("piccadilly_divergence", "west_end_counts"):
        ids = ["piccadilly"] if sid == "piccadilly_divergence" else ["oxford-circus", "piccadilly"]
        last = store.latest("camera_detail", now)
        if not last:
            return None
        spec["cameras"] = sorted({cid for hid in ids for cid in by_id.get(hid, {}).get("cams", [])
                                  if cid in last[1] and 0 <= now - last[1][cid]["at"] <= 10 * MIN
                                  and (sid != "west_end_counts" or last[1][cid]["vehicles"] is not None)})
        if len(spec["cameras"]) < (1 if sid == "piccadilly_divergence" else 2):
            return None
    if sid in ("piccadilly_divergence", "london_breadth", "bank_waterloo_swing"):
        last = store.latest("crowding", now)
        if not last:
            return None
        if sid == "london_breadth":
            spec["stations"] = {nap: nap for nap in sorted(last[1])}
            if len(spec["stations"]) < 18:
                return None
        else:
            ids = ["piccadilly"] if sid == "piccadilly_divergence" else ["bank", "waterloo"]
            if any(h not in by_id for h in ids):
                return None
            spec["stations"] = {h: by_id[h]["naptan"] for h in ids}
    return spec


def feeds(spec):
    return (["bikepoint"] if spec["bikes"] else []) + (["bus_detail"] if spec["stops"] or spec["groups"] else []) + \
           (["camera_detail"] if spec["cameras"] else []) + (["crowding"] if spec["stations"] else [])


def extract(spec, feed, ts, snap, store):
    """{feature: (value, source measurement time)}; None for incomplete snapshots."""
    out = {}
    if feed == "bikepoint":
        for k in spec["bikes"]:
            if k not in snap:
                continue
            for field in ("bikes", "spaces"):
                out[f"{field}:{k}"] = (snap[k][field], ts)
    elif feed == "bus_detail":
        for stop in spec["stops"]:
            waits = [v["wait"] for v in snap.values() if v["stop"] == stop]
            if not waits:
                continue
            out[f"wait:{stop}"] = (min(waits), ts)
        for stop, route in spec["groups"]:
            waits = sorted(v["wait"] for v in snap.values() if v["stop"] == stop and v["route"] == route)
            if len(waits) < 2:
                continue
            out[f"gap:{stop}:{route}"] = (waits[1] - waits[0], ts)
    elif feed == "camera_detail":
        for cid in spec["cameras"]:
            row = snap.get(cid)
            if row is None or not 0 <= ts - row["at"] <= 10 * MIN:
                continue
            if spec["question"] == "west_end_counts" and row["vehicles"] is None:
                continue
            out[f"people:{cid}"] = (row["people"], row["at"])
            if spec["question"] == "west_end_counts":
                out[f"vehicles:{cid}"] = (row["vehicles"], row["at"])
    else:
        for hid, nap in spec["stations"].items():
            if nap not in snap:
                continue
            stamp = store.tfl_stamp(ts, nap)
            if stamp is None or stamp > ts or ts - stamp > WAIT:
                continue
            value = snap[nap]
            if spec["question"] == "bank_waterloo_swing":
                usual = store.typical(nap, stamp)
                if not usual:
                    continue
                value /= usual
            out[f"crowd:{hid}"] = (value, stamp)
    return out


def features(store, spec, now, after=None):
    """Read frozen features. Settlement picks each sensor's first measured-after-close value known by `now`.

    All camera/crowding measurements must fit one 150s band: never compare an old camera to a new station.
    """
    result, fetched = {}, []
    for feed in feeds(spec):
        # Feature count is fixed by the basket, even if a snapshot loses a member.
        count = (2 * len(spec["bikes"]) if feed == "bikepoint" else
                 len(spec["stops"]) + len(spec["groups"]) if feed == "bus_detail" else
                 len(spec["cameras"]) * (2 if spec["question"] == "west_end_counts" else 1)
                 if feed == "camera_detail" else len(spec["stations"]))
        if after is None:
            if not store.fresh(feed, now):
                return None
            rows = [store.latest(feed, now)]
        else:
            rows = store.between(feed, after + 1, now)
        selected = {}
        for row in rows:
            if row is None:
                continue
            ts, snap = row
            got = extract(spec, feed, ts, snap, store)
            if not got:
                continue
            for key, pair in got.items():
                if (after is None or pair[1] > after) and pair[1] <= now and key not in selected:
                    selected[key] = pair
                    fetched.append(ts)
            if len(selected) == count:
                break
        if len(selected) != count:
            return None
        result.update(selected)
    stamps = [t for _, t in result.values()]
    if not stamps or max(stamps) - min(stamps) > MAX_ALIGN:
        return None
    return {k: v for k, (v, _) in result.items()}, min(stamps), max(fetched)


def total(values, prefix):
    return sum(v for k, v in values.items() if k.startswith(prefix + ":"))


def score(spec, base, end):
    sid = spec["question"]
    if sid == "waterloo_bikes":
        return total(base, "bikes") - total(end, "bikes")
    if sid == "west_end_docks":
        return 0.5 - min(v for k, v in end.items() if k.startswith("spaces:"))
    if sid in ("kings_cross_bunching", "oxford_bus_wait"):
        return statistics.fmean(base.values()) - statistics.fmean(end.values())
    if sid == "piccadilly_divergence":
        return -(total(end, "people") - total(base, "people")) * (total(end, "crowd") - total(base, "crowd"))
    if sid == "west_end_counts":
        return (total(end, "vehicles") - total(base, "vehicles")) - (total(end, "people") - total(base, "people"))
    if sid == "london_breadth":
        return sum(end[k] > v for k, v in base.items()) - (len(base) // 2 + 0.5)
    return abs(end["crowd:bank"] - base["crowd:bank"]) - abs(end["crowd:waterloo"] - base["crowd:waterloo"])


def history(store, spec, now):
    """Distinct, non-overlapping approximate five-minute source-time changes, available before opening."""
    pairs, previous, seen = [], None, set()
    start = now - mk.HISTORY_MS
    for ts in range(start, now + 1, 5 * MIN):
        got = features(store, spec, ts)
        if got is None or got[1] in seen:
            continue
        seen.add(got[1])
        if previous and 3 * MIN <= got[1] - previous[1] <= 7 * MIN:
            pairs.append({k: got[0][k] - previous[0][k] for k in got[0]})
        previous = got
    return pairs


class UrbanSim:
    """Bootstrap joint five-minute changes from the latest available complete observation.

    Recompute, rather than re-score repeated observations. Probabilities are empirical, not forced to 50/50.
    """
    tol = 2.0

    def __init__(self, store, spec, now, end, deltas):
        self.store, self.spec, self.deltas = store, spec, deltas
        self.t0, self.end, self.m = now, end, max(1, math.ceil((end - now) / MIN))
        self.refresh(now)

    def refresh(self, now):
        got = features(self.store, self.spec, now)
        if got is None:
            return
        current, measured, _ = got
        # Extrapolate to the first five-minute source slot after betting closes, allowing for publication lag.
        target = measured + max(1, math.floor((self.end - mk.CLOSE_MS - measured) / (5 * MIN)) + 1) * 5 * MIN
        scale = math.sqrt(max(1, target - measured) / (5 * MIN))
        self.current = score(self.spec, self.spec["base"], current)
        self.finals = []
        for i in range(pricing.N_FUTURES):
            delta = self.deltas[i * len(self.deltas) // pricing.N_FUTURES]
            future = {k: max(0.0, v + delta[k] * scale) for k, v in current.items()}
            if self.spec["question"] == "west_end_docks":
                future = {k: round(v) for k, v in future.items()}
            self.finals.append(score(self.spec, self.spec["base"], future))

    def exact(self, seen):
        yes = sum(v > 1e-9 for v in self.finals)
        return pricing.smooth({"yes": yes, "no": pricing.N_FUTURES - yes})

    def outcome(self, i):
        return "yes" if self.finals[i] > 1e-9 else "no"

    def error(self, i, ts, snap):
        return None

    def fan_paths(self):
        return [[(self.t0, self.current), (self.end, v)] for v in self.finals]


def build(sid, mid, store, spots, now, settles_at):
    spec = baskets(sid, store, spots, now)
    if spec is None:
        return None
    got = features(store, spec, now)
    if got is None:
        return None
    spec.update(base=got[0], openTs=now, measuredOpen=got[1], line=0.0)
    deltas = history(store, spec, now)
    if len(deltas) < MIN_PAIRS:
        return None
    spec["historyPairs"] = len(deltas)
    feed, title, hubs = QUESTIONS[sid]
    sim = UrbanSim(store, spec, now, settles_at, deltas)
    rules = {
        "waterloo_bikes": "Yes if the total available bikes across the frozen docks falls below its opening total.",
        "west_end_docks": "Yes if any frozen dock (all had space at open) has zero empty spaces at the settling reading.",
        "kings_cross_bunching": "Yes if the mean gap in predicted arrival seconds between the next two distinct vehicles "
                                "on each frozen route at each directional stop shrinks. These are predictions, not actual arrivals.",
        "oxford_bus_wait": "Yes if the mean next-bus predicted wait across the frozen stops falls. Each stop counts equally.",
        "piccadilly_divergence": "Yes if total camera people counts and station busyness change in strictly opposite directions.",
        "west_end_counts": "Yes if the increase in total camera vehicle counts exceeds the increase in total people counts. "
                           "These are visible counts, not measured journeys or traffic speed.",
        "london_breadth": "Yes if strictly more than half the frozen monitored stations have higher busyness than at open.",
        "bank_waterloo_swing": "Yes if Bank's absolute change in live/typical busyness exceeds Waterloo's absolute change, "
                               "using each station's typical profile at its source measurement time.",
    }
    source = " + ".join(feeds(spec))
    rule = (rules[sid] + " Ties or no change count as No. Baskets freeze at opening and are listed below. "
            "Use each sensor's first reading measured after bets close (camera image time; TfL timeUtc for stations; "
            "source fetch time for bikes and bus predictions). All measurement times must be within 150 seconds. "
            "Missing components, timestamps or aligned readings by 20 minutes after close void the market and refund stakes. "
            f"Frozen basket: bikes={spec['bikes']}; stops={spec['stops']}; routes/stops={spec['groups']}; "
            f"cameras={spec['cameras']}; stations={spec['stations']}.")
    return mk.Market(mid, sid, "yesno", f"{title} by {data.local_time(settles_at)}?", hubs, now, settles_at,
                     [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}], sim,
                     source + " · " + mk.settle_stamp(settles_at), rule, spec)


def settle(m, store, now):
    # Never decide from a pre-close observation, including when the exchange probes for a known answer.
    if now < m.settles_at + mk.SETTLE_LAG_MS:
        return ("wait", max(1, m.settles_at + mk.SETTLE_LAG_MS - now))
    deadline = m.closes_at + WAIT
    got = features(store, m.spec, min(now, deadline), after=m.closes_at)
    if got is None:
        return (("wait", min(5_000, deadline - now)) if now < deadline else
                ("void", "Missing or unaligned source readings — stakes refunded",
                 {"value": None, "source": m.source if hasattr(m, "source") else "London feeds",
                  "measuredAt": None, "fetchedAt": None}))
    value = score(m.spec, m.spec["base"], got[0])
    winner = "yes" if value > 1e-9 else "no"
    return ("done", winner, f"Change score {value:.4f} vs line 0 → {m.label(winner)}",
            {"value": value, "source": " + ".join(feeds(m.spec)), "measuredAt": got[1], "fetchedAt": got[2],
             "values": got[0]})


def halt_reason(m, store, now):
    return "stale feed" if features(store, m.spec, now) is None else None


def clue(m):
    s, base = m.spec, m.spec["base"]
    sid = s["question"]
    if sid == "waterloo_bikes":
        text = f"At open: {total(base, 'bikes'):g} bikes across {len(s['bikes'])} nearby docks. Yes if the total falls."
    elif sid == "west_end_docks":
        text = f"All {len(s['bikes'])} selected docks had space at open. Yes if any has no space at the next eligible reading."
    elif sid in ("kings_cross_bunching", "oxford_bus_wait"):
        word = "gap between the next two buses" if sid == "kings_cross_bunching" else "next-bus wait"
        text = f"At open: average predicted {word} {statistics.fmean(base.values()) / 60:.1f} min. Yes if it shortens."
    elif sid == "piccadilly_divergence":
        text = f"At open: cameras counted {total(base, 'people'):g} people. Yes if street counts rise while station activity falls, or vice versa."
    elif sid == "west_end_counts":
        text = f"At open: {total(base, 'vehicles'):g} vehicles and {total(base, 'people'):g} people across {len(s['cameras'])} cameras. Compare changes, not totals."
    elif sid == "london_breadth":
        text = f"Yes if at least {len(base) // 2 + 1} of the {len(base)} selected stations get busier."
    else:
        text = f"At open: Bank {base['crowd:bank'] * 100:.0f}% of usual; Waterloo {base['crowd:waterloo'] * 100:.0f}%. Compare the size of each move, up or down."
    return [{"id": "urban", "text": text, "data": {}}]


def install():
    for sid, (feed, _, _) in QUESTIONS.items():
        mk.FEED_LABEL.setdefault(feed, feed)
        mk.register(mk.Series(sid, feed, partial(build, sid), settle,
                             why_none=lambda store, now: "needs complete aligned sensors and 8 historical five-minute changes"))


install()
