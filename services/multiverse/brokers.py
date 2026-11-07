"""Data-source brokers: the TOPS clone, where each broker reads ONE alternative-data stream.

Each broker is tied to one feed (Heathrow holding stacks, 15-min rain, the grid, GDELT news, bike docks,
Tube crowding, Wikipedia attention, the bus feed, the street cameras) and sees only that feed's latest
summary plus the clock. Per observation it submits a trade idea: values for the knobs its feed can
plausibly inform (FEED_KNOBS; everything else is forced to null), a conviction and a one-line rationale.
An idea spawns worlds built on a world drawn from the market's posterior, with only the viewed knobs
nudged toward the idea (by its conviction). After each observation a broker scores the log of its
worlds' posterior price mass over the mass it held before (its log-score against the market), capped;
a decayed sum is its track record. Slots: a market-maker slice (a plain particle filter, no broker)
plus a tempered softmax of track records mixed with uniform, with a floor per broker. The leaderboard
is therefore a live ranking of which data has alpha on the city.

Ideas come from Claude Haiku 4.5, BATCH brokers per request with the requests for a frame sent
concurrently (strict JSON schema, validated and repaired per broker). Without ANTHROPIC_API_KEY, or for
any broker whose idea fails validation, a deterministic rules broker reads the same feed with a simple
heuristic, so everything runs offline.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import worlds
import llm  # noqa: E402  (services/predict, on sys.path via worlds)

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 2000
TIMEOUT_S = 30
RETRYABLE = {429, 500, 502, 503, 504, 529}
# Brokers per Haiku request; the requests for one frame run concurrently (latency ~ output length).
BATCH = 5

IDEA_KEYS = ("movingShare", "touristFactor", "capacityScale", "busDelayMin")
EVENT_KINDS = ("closure", "incident", "event", "weather")
DRY = -1.0
BASE_SPREAD = 0.1
MIN_SPREAD = 0.02
DECAY = 0.8
SCORE_CAP = 2.0
TEMPERATURE = 1.5
UNIFORM_MIX = 0.2
SLOT_FLOOR = 2
MM_SHARE = 0.25
# Pseudo-mass added to both sides of a broker's score: a broker holding a sliver of the market can't
# swing its track record on noise.
SHRINK_MASS = 0.01
RATIONALE_MAX = 240
# Rules weather nerd: a cloudy sky turns to rain about this many minutes into the horizon.
RAIN_FROM_CLOUD_MIN = 12.0

# Which knobs each feed can plausibly inform (README has the reasoning).
FEED_KNOBS: dict[str, tuple[str, ...]] = {
    "aviation": ("touristFactor",),
    "weather": ("rainAtMin",),
    "grid": ("movingShare",),
    "news": ("capacityScale",),
    "bikes": ("movingShare",),
    "tube": ("touristFactor",),
    "attention": ("touristFactor",),
    "bus": ("busDelayMin",),
    "cameras": ("capacityScale",),
    "contrarian": ("movingShare", "touristFactor"),
}
# Rules heuristics: holding aircraft above this count bring tourists; demand above this MW means a busy city.
HOLDING_NORMAL = 8
TOURIST_PER_HOLDING = 0.04
DEMAND_NORMAL_MW = 30_000.0
DEMAND_PER_SHARE_MW = 20_000.0
# Bikes leaving the Oxford Circus docks per minute if every local set off (calibrated to the fixture).
BIKES_PER_MIN_ALL = 20.0

ROSTER: list[dict[str, Any]] = [
    {"id": "B-AIR", "feed": "aviation", "persona": "Heathrow plane spotter", "archetype": "tourist",
     "voice": "counts holding stacks like sheep", "conviction": 0.4},
    {"id": "B-RAIN", "feed": "weather", "persona": "Radar watcher", "archetype": "commuter",
     "voice": "nerdy, quotes millimetres and radar steps", "conviction": 0.7},
    {"id": "B-GRID", "feed": "grid", "persona": "Grid engineer", "archetype": "resident",
     "voice": "dry, thinks in megawatts", "conviction": 0.4},
    {"id": "B-NEWS", "feed": "news", "persona": "Newsroom stringer", "archetype": "resident",
     "voice": "breathless, every headline is a crowd", "conviction": 0.35},
    {"id": "B-BIKES", "feed": "bikes", "persona": "Bike dock watcher", "archetype": "cyclist",
     "voice": "counts empty docks, knows who left", "conviction": 0.6},
    {"id": "B-TUBE", "feed": "tube", "persona": "Tube supervisor", "archetype": "transit_arrival",
     "voice": "calm, reads the gateline", "conviction": 0.6},
    {"id": "B-WIKI", "feed": "attention", "persona": "Wikipedia editor", "archetype": "resident",
     "voice": "pedantic, trusts pageviews", "conviction": 0.35},
    {"id": "B-BUS", "feed": "bus", "persona": "Bus driver on the 7", "archetype": "transit_arrival",
     "voice": "dry, reads the road through a windscreen", "conviction": 0.75},
    {"id": "B-CAMS", "feed": "cameras", "persona": "CCTV operator", "archetype": "resident",
     "voice": "flat, has seen every Saturday on a screen", "conviction": 0.45},
    {"id": "B-CONTRA", "feed": "contrarian", "persona": "Contrarian", "archetype": "resident",
     "voice": "smug, bets against whatever the cameras just showed", "conviction": 0.35},
]

ROOT = Path(__file__).resolve().parent.parent.parent
_env_loaded = False


def _main_checkout_env() -> Path | None:
    """In a git worktree `.git` is a file pointing into the main checkout's .git/worktrees/."""
    dotgit = ROOT / ".git"
    if not dotgit.is_file():
        return None
    gitdir = dotgit.read_text().strip().removeprefix("gitdir:").strip()
    if "/.git/worktrees/" not in gitdir:
        return None
    return Path(gitdir.split("/.git/worktrees/")[0]) / ".env"


def load_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    env = next((p for p in (ROOT / ".env", _main_checkout_env()) if p and p.is_file()), None)
    if env is None:
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_key() -> str:
    load_env()
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


# ---------------------------------------------------------------- context


def build_context(seed: dict, horizon: int, minute: int, readings: dict[str, float] | None,
                  bus_delay: float | None, bus_seen: list[float] | None = None,
                  alt: dict[str, Any] | None = None) -> dict:
    """The clock plus the latest summary of every feed; each broker is later shown only its own feed."""
    t = seed.get("time") or {}
    cams = []
    for c in seed.get("cams", []):
        row = {"cam": c.get("name") or c["id"], "seedPeople": c.get("people")}
        if readings and c["id"] in readings:
            row["seenNow"] = readings[c["id"]]
        cams.append(row)
    seen = [r for r in cams if "seenNow" in r and r["seedPeople"]]
    ratio = (sum(r["seenNow"] for r in seen) / sum(r["seedPeople"] for r in seen)) if seen else None
    camera = {"crowdVsSeed": round(ratio, 2) if ratio is not None else None,
              "cams": [r for r in cams if "seenNow" in r][:8]} if seen else None
    bus = ({"observedDelayMin": bus_delay, "meanDelayMin": round(sum(bus_seen) / len(bus_seen), 2), "reads": len(bus_seen)}
           if bus_seen else None)
    feeds = {name: (alt or {}).get(name) for name in ("aviation", "weather", "grid", "news", "bikes", "tube", "attention")}
    feeds |= {"bus": bus, "cameras": camera, "contrarian": camera}
    return {"area": seed.get("area"), "clock": f"{t.get('weekday', '?')} {t.get('iso', '')[11:16]} London",
            "minute": minute, "horizonMin": horizon, "feeds": feeds}


def feed_of(broker: dict, ctx: dict) -> dict | None:
    return (ctx.get("feeds") or {}).get(broker.get("feed", "insider"))


# ---------------------------------------------------------------- rules broker


def rules_views(broker: dict, f: dict, horizon: int) -> dict[str, Any]:
    """One simple heuristic per feed; {} = the feed gives no reason for a view."""
    feed = broker["feed"]
    if feed == "aviation" and f.get("holdingTotal") is not None:
        return {"touristFactor": 1.0 + TOURIST_PER_HOLDING * (f["holdingTotal"] - HOLDING_NORMAL)}
    if feed == "weather":
        if (f.get("precipNowMm") or 0) >= 0.1:
            return {"rainAtMin": 0.0}
        if f.get("rainInMin") is not None:
            return {"rainAtMin": min(float(f["rainInMin"]), float(horizon))} if f["rainInMin"] <= horizon else {"rainAtMin": DRY}
        return {"rainAtMin": DRY}
    if feed == "grid" and f.get("demandMw"):
        return {"movingShare": 0.6 + (f["demandMw"] - DEMAND_NORMAL_MW) / DEMAND_PER_SHARE_MW}
    if feed == "news" and f.get("eventsNearOxfordCircus") is not None:
        tense = f["eventsNearOxfordCircus"] >= 3 and (f.get("avgTone") or 0) < -2
        return {"capacityScale": 0.8 if tense else 1.05}
    if feed == "bikes" and f.get("bikesTaken") is not None and f.get("minutes"):
        return {"movingShare": f["bikesTaken"] / f["minutes"] / BIKES_PER_MIN_ALL}
    if feed == "tube" and f.get("vsTypical"):
        return {"touristFactor": f["vsTypical"]}
    if feed == "attention" and f.get("viewsVsMean"):
        return {"touristFactor": f["viewsVsMean"]}
    if feed == "bus" and f.get("meanDelayMin") is not None:
        return {"busDelayMin": f["meanDelayMin"]}
    if feed == "cameras" and f.get("crowdVsSeed"):
        return {"capacityScale": 1.25 - 0.35 * f["crowdVsSeed"]}
    if feed == "contrarian" and f.get("crowdVsSeed"):
        busy = f["crowdVsSeed"] > 1.05
        return {"movingShare": 0.4, "touristFactor": 0.7} if busy else {"movingShare": 0.85, "touristFactor": 1.4}
    return {}


def rules_idea(broker: dict, ctx: dict, zone_ids_by_name: dict[str, str] | None = None) -> dict:
    """Views only on the broker's own knobs, from its own feed; everything else is null."""
    horizon = int(ctx.get("horizonMin") or 5)
    if broker.get("lean"):
        f: dict | None = {"insider": True}
        views = dict(broker["lean"])
    else:
        f = feed_of(broker, ctx)
        views = rules_views(broker, f, horizon) if f else {}
    idea: dict[str, Any] = {k: None for k in IDEA_KEYS} | {"rainAtMin": None, "event": None}
    for k, v in views.items():
        if k == "rainAtMin":
            idea[k] = v if v < 0 else round(clamp(v, 0.0, float(horizon)), 1)
        else:
            idea[k] = round(clamp(float(v), *worlds.RANGES[k]), 3)
    idea["conviction"] = float(broker.get("conviction", 0.5)) if views else 0.0
    idea["rationale"] = rules_rationale(broker, idea, f)
    return idea


def rules_rationale(broker: dict, idea: dict, f: dict | None) -> str:
    if not f:
        return f"No {broker['feed']} data, no view."
    bits = []
    if idea.get("movingShare") is not None:
        bits.append(f"{idea['movingShare'] * 100:.0f}% set off")
    if idea.get("touristFactor") is not None:
        bits.append(f"tourists x{idea['touristFactor']:.2f}")
    if idea.get("capacityScale") is not None:
        bits.append(f"pavements x{idea['capacityScale']:.2f}")
    if idea.get("busDelayMin") is not None:
        bits.append(f"buses {idea['busDelayMin']:.1f} min late")
    rain = idea.get("rainAtMin")
    if rain is not None:
        bits.append("stays dry" if rain < 0 else f"rain from +{rain:.0f} min")
    head = ", ".join(f"{k} {v}" for k, v in list(f.items())[:2] if not isinstance(v, (list, dict)))
    return f"{head}: " + (", ".join(bits) or "no view") + "."


# ---------------------------------------------------------------- Haiku broker


def response_schema(roster: list[dict], zone_ids: list[str]) -> dict:
    num = {"type": "number"}
    view = {"anyOf": [num, {"type": "null"}]}
    event = {"type": "object", "additionalProperties": False, "required": ["kind", "zoneId", "atMin"],
             "properties": {"kind": {"type": "string", "enum": list(EVENT_KINDS)},
                            "zoneId": {"type": "string", "enum": zone_ids}, "atMin": num}}
    item = {"type": "object", "additionalProperties": False,
            "required": ["id", *IDEA_KEYS, "rainAtMin", "event", "conviction", "rationale"],
            "properties": {"id": {"type": "string", "enum": [b["id"] for b in roster]},
                           **{k: view for k in IDEA_KEYS},
                           "rainAtMin": view,
                           "event": {"anyOf": [event, {"type": "null"}]},
                           "conviction": num, "rationale": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["ideas"],
            "properties": {"ideas": {"type": "array", "items": item}}}


def system_prompt(roster: list[dict], horizon: int) -> str:
    r = worlds.RANGES
    people = "\n".join(f"- {b['id']}: {b['persona']}. Voice: {b['voice']}. Reads ONLY the {b['feed']} feed. "
                       f"May set: {', '.join(FEED_KNOBS[b['feed']])}." for b in roster)
    return (
        "You voice the brokers on an exchange of parallel Londons around Oxford Circus. Each broker reads one "
        f"alternative-data feed and bets on how the next {horizon} minutes will go. Every bet spawns simulated "
        "worlds; street cameras and the TfL bus feed then price those worlds, and brokers whose bets match "
        "reality get more worlds next round. A bet only moves the knobs the broker may set; a confident wrong "
        "view costs that broker, and null costs nothing.\n\n"
        f"Brokers:\n{people}\n\n"
        "The user message is JSON: clock, minute, horizonMin, and per broker its feed name and `latest` (that "
        "feed's newest summary, or null when nothing recent was recorded). A broker knows ONLY its own `latest`; "
        "never use another broker's numbers.\n\n"
        "Return exactly one idea per broker. Numbers only for the knobs that broker may set, null for every "
        "other knob, and all null with conviction 0 when its latest is null or says nothing. Fields:\n"
        f"- movingShare: share of people in the area who set off within the horizon ({r['movingShare'][0]}-{r['movingShare'][1]})\n"
        f"- touristFactor: tourist multiplier vs a normal day ({r['touristFactor'][0]}-{r['touristFactor'][1]})\n"
        f"- capacityScale: usable pavement vs normal ({r['capacityScale'][0]}-{r['capacityScale'][1]}; lower = more cramped)\n"
        f"- busDelayMin: how late buses run, minutes ({r['busDelayMin'][0]}-{r['busDelayMin'][1]})\n"
        f"- rainAtMin: minute from now (0-{horizon}) when rain starts; 0 = raining now; -1 = stays dry\n"
        "- event: always null\n"
        "- conviction: 0-1, how much this feed really says about the knob\n"
        "- rationale: one first-person sentence, at most 16 words, in the broker's voice, quoting one number "
        "from its own feed\n"
    )


def haiku_call(system: str, user: str, schema: dict) -> tuple[str, dict]:
    """One Messages API request over HTTPS (stdlib). Returns (reply text, usage + latency)."""
    body = json.dumps({"model": MODEL, "max_tokens": MAX_TOKENS, "system": system,
                       "messages": [{"role": "user", "content": user}],
                       "output_config": {"format": {"type": "json_schema", "schema": schema}}}).encode()
    headers = {"x-api-key": api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"}
    t0 = time.perf_counter()
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
                data = json.loads(res.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in RETRYABLE and attempt == 0:
                time.sleep(min(5.0, float(exc.headers.get("retry-after") or 1.0)))
                continue
            raise RuntimeError(f"Anthropic API {exc.code}: {detail}") from exc
    if data.get("stop_reason") == "refusal":
        raise RuntimeError("Haiku refused the request")
    text = next((b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"), "")
    usage = data.get("usage") or {}
    return text, {"inputTokens": usage.get("input_tokens"), "outputTokens": usage.get("output_tokens"),
                  "stopReason": data.get("stop_reason"), "ms": round((time.perf_counter() - t0) * 1000)}


def validate_idea(raw: Any, horizon: int, zone_ids: list[str]) -> dict | None:
    """Repair what can be repaired (clamp ranges, coerce numeric strings, drop a bad event); None = reject."""
    if not isinstance(raw, dict):
        return None
    idea: dict[str, Any] = {}
    try:
        for k in IDEA_KEYS:
            if raw[k] is None:
                idea[k] = None
                continue
            v = float(raw[k])
            if not math.isfinite(v):
                return None
            idea[k] = round(clamp(v, *worlds.RANGES[k]), 3)
        conviction = float(raw["conviction"])
        if not math.isfinite(conviction):
            return None
        idea["conviction"] = round(clamp(conviction, 0.0, 1.0), 3)
        rain = raw.get("rainAtMin")
        idea["rainAtMin"] = (None if rain is None else DRY if float(rain) < 0
                             else round(clamp(float(rain), 0.0, float(horizon)), 1))
    except (KeyError, TypeError, ValueError):
        return None
    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return None
    idea["rationale"] = rationale.strip()[:RATIONALE_MAX]
    ev = raw.get("event")
    idea["event"] = None
    if isinstance(ev, dict) and ev.get("kind") in EVENT_KINDS and ev.get("zoneId") in zone_ids:
        try:
            idea["event"] = {"kind": ev["kind"], "zoneId": ev["zoneId"],
                             "atMin": round(clamp(float(ev.get("atMin", 0)), 0.0, float(horizon)), 1)}
        except (TypeError, ValueError):
            idea["event"] = None
    return idea


def validate_batch(text: str, roster: list[dict], horizon: int, zone_ids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Parse the batched reply; return {brokerId: idea} for the valid ones plus a list of problems."""
    errors: list[str] = []
    try:
        data = llm.parse_json(text)
    except ValueError as exc:
        return {}, [str(exc)]
    items = data.get("ideas") if isinstance(data, dict) else data if isinstance(data, list) else None
    if not isinstance(items, list):
        return {}, ["reply has no ideas array"]
    known = {b["id"] for b in roster}
    out: dict[str, dict] = {}
    for raw in items:
        bid = raw.get("id") if isinstance(raw, dict) else None
        if bid not in known:
            errors.append(f"unknown broker {bid!r}")
            continue
        if bid in out:
            continue
        idea = validate_idea(raw, horizon, zone_ids)
        if idea is None:
            errors.append(f"{bid}: invalid idea")
            continue
        out[bid] = idea
    for bid in known - set(out):
        if not any(e.startswith(f"{bid}:") for e in errors):
            errors.append(f"{bid}: missing")
    return out, errors


def own_knobs_only(idea: dict, feed: str) -> dict:
    """A broker may only hold views its feed can inform; anything else becomes 'no view'."""
    if feed not in FEED_KNOBS:
        return idea
    allowed = set(FEED_KNOBS[feed])
    out = dict(idea, event=None)
    for k in (*IDEA_KEYS, "rainAtMin"):
        if k not in allowed:
            out[k] = None
    return out


# ---------------------------------------------------------------- worlds from ideas


def assumptions_from_idea(idea: dict, rng: random.Random, horizon: int, zone_ids: list[str],
                          donor: dict) -> dict:
    """The donor world (drawn from the market) nudged toward the idea on the dimensions it has a view on.

    Each viewed value moves `conviction` of the way from the donor to the idea, plus noise that shrinks as
    conviction grows; a rain or dry call replaces the donor's sky with probability (1 + conviction) / 2.
    """
    conviction = float(idea["conviction"])
    spread = BASE_SPREAD * (1 - conviction) + MIN_SPREAD
    a: dict[str, Any] = dict(donor, events=list(donor.get("events") or []))
    for k in IDEA_KEYS:
        if idea.get(k) is not None:
            lo, hi = worlds.RANGES[k]
            target = float(donor[k]) + conviction * (float(idea[k]) - float(donor[k]))
            a[k] = round(clamp(rng.gauss(target, spread * (hi - lo)), lo, hi), 3)
    rain = idea.get("rainAtMin")
    if rain is not None and rng.random() < 0.5 + 0.5 * conviction:
        if rain < 0:
            a["rainAtMin"] = None
        else:
            was = donor.get("rainAtMin")
            start = rain if was is None else was + conviction * (rain - was)
            a["rainAtMin"] = round(clamp(rng.gauss(start, spread * horizon), 0.0, float(horizon)), 1)
    ev = idea.get("event")
    if ev and ev.get("zoneId") in zone_ids and rng.random() < conviction:
        a["events"] = [e for e in a["events"] if "broker idea" not in str(e.get("note"))]
        a["events"].append(dict(ev, note=f"{ev['kind']} (broker idea)"))
    return a


def allocate_slots(weights: dict[str, float], n: int, floor: int = 1) -> dict[str, int]:
    """Largest-remainder split of n slots by weight, at least `floor` each (lowered to fit if n is small)."""
    ids = list(weights)
    if n < len(ids):
        raise ValueError(f"need at least {len(ids)} worlds for {len(ids)} brokers")
    floor = max(1, min(floor, n // len(ids)))
    total = sum(max(w, 0.0) for w in weights.values()) or 1.0
    spare = n - floor * len(ids)
    shares = {b: spare * max(weights[b], 0.0) / total for b in ids}
    slots = {b: floor + int(shares[b]) for b in ids}
    left = n - sum(slots.values())
    for b in sorted(ids, key=lambda b: -(shares[b] - int(shares[b])))[:left]:
        slots[b] += 1
    return slots


def softmax(values: dict[str, float], temperature: float = 1.0) -> dict[str, float]:
    top = max(values.values())
    exp = {k: math.exp((v - top) / temperature) for k, v in values.items()}
    total = sum(exp.values())
    return {k: e / total for k, e in exp.items()}


# ---------------------------------------------------------------- the brokerage


class Brokerage:
    def __init__(self, seed: dict, horizon: int, roster: list[dict] | None = None,
                 caller: Callable[[str, str, dict], tuple[str, dict]] | None = None) -> None:
        self.roster = roster or ROSTER
        self.feeds = {b["id"]: b.get("feed", "insider") for b in self.roster}
        self.horizon = horizon
        self.zone_ids = [z["id"] for z in seed["zones"]]
        self.zone_ids_by_name = {z["name"]: z["id"] for z in seed["zones"]}
        self.caller = caller or (haiku_call if api_key() else None)
        self.book: dict[str, dict[str, Any]] = {
            b["id"]: {"id": b["id"], "persona": b["persona"], "archetype": b["archetype"], "trackRecord": 0.0,
                      "feed": b.get("feed", "insider"), "knobs": list(FEED_KNOBS.get(b.get("feed", "insider"), IDEA_KEYS)),
                      "weight": 1.0 / len(self.roster), "slots": 0, "priorMass": 0.0, "mass": 0.0,
                      "idea": None, "source": None, "pnl": [], "cumulative": 0.0}
            for b in self.roster}
        self.calls: list[dict[str, Any]] = []
        self.mm_slots = 0

    @property
    def llm_enabled(self) -> bool:
        return self.caller is not None

    def user_message(self, chunk: list[dict], ctx: dict) -> str:
        """Clock plus, for each broker in the chunk, only its own feed's latest summary."""
        return json.dumps({"clock": ctx.get("clock"), "minute": ctx.get("minute"), "horizonMin": ctx.get("horizonMin"),
                           "brokers": [{"id": b["id"], "feed": b["feed"], "latest": feed_of(b, ctx)} for b in chunk]},
                          separators=(",", ":"))

    def refresh(self, ctx: dict, obs_index: int) -> dict[str, dict]:
        """New ideas for every broker: concurrent batched Haiku calls, rules for whatever is missing or invalid."""
        ideas: dict[str, dict] = {}
        if self.caller is not None:
            chunks = [self.roster[i:i + BATCH] for i in range(0, len(self.roster), BATCH)]
            with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
                records = list(pool.map(lambda chunk: self.ask(chunk, self.user_message(chunk, ctx), obs_index), chunks))
            for record, got in records:
                ideas |= got
                self.calls.append(record)
        for b in self.roster:
            entry = self.book[b["id"]]
            if b["id"] in ideas:
                entry["idea"], entry["source"] = ideas[b["id"]], "haiku"
            else:
                entry["idea"], entry["source"] = rules_idea(b, ctx, self.zone_ids_by_name), "rules"
        return {bid: e["idea"] for bid, e in self.book.items()}

    def ask(self, chunk: list[dict], user: str, obs_index: int) -> tuple[dict[str, Any], dict[str, dict]]:
        record: dict[str, Any] = {"obs": obs_index, "model": MODEL, "brokers": [b["id"] for b in chunk]}
        ideas: dict[str, dict] = {}
        try:
            text, usage = self.caller(system_prompt(chunk, self.horizon), user, response_schema(chunk, self.zone_ids))
            record |= usage
            ideas, errors = validate_batch(text, chunk, self.horizon, self.zone_ids)
            ideas = {bid: own_knobs_only(idea, self.feeds[bid]) for bid, idea in ideas.items()}
            record["errors"] = errors[:10]
        except Exception as exc:
            record["errors"] = [f"{type(exc).__name__}: {exc}"[:300]]
        record["fallbacks"] = [b["id"] for b in chunk if b["id"] not in ideas]
        return record, ideas

    def slots(self, n: int) -> dict[str, int]:
        """Broker slots; `mm_slots` (the market maker's slice) makes up the rest of n."""
        self.mm_slots = max(0, min(round(MM_SHARE * n), n - len(self.book)))
        out = allocate_slots({bid: e["weight"] for bid, e in self.book.items()}, n - self.mm_slots, SLOT_FLOOR)
        for bid, k in out.items():
            self.book[bid]["slots"] = k
        return out

    def masses(self, live: list[dict]) -> dict[str, float]:
        m = {bid: 0.0 for bid in self.book}
        for w in live:
            if w.get("broker") in m:
                m[w["broker"]] += w["price"]
        return m

    def mark_prior(self, live: list[dict]) -> None:
        for bid, mass in self.masses(live).items():
            self.book[bid]["priorMass"] = self.book[bid]["mass"] = mass

    def score(self, live: list[dict]) -> None:
        """P&L = capped log((posterior mass + s) / (prior mass + s)), a shrunk log-score against the market;
        track record = decayed sum; weight = tempered softmax mixed with uniform."""
        for bid, post in self.masses(live).items():
            e = self.book[bid]
            s = clamp(math.log((post + SHRINK_MASS) / (e["priorMass"] + SHRINK_MASS)), -SCORE_CAP, SCORE_CAP)
            e["trackRecord"] = round(DECAY * e["trackRecord"] + s, 4)
            e["cumulative"] = round(e["cumulative"] + s, 4)
            e["pnl"].append(round(s, 4))
            e["mass"] = post
        soft = softmax({bid: e["trackRecord"] for bid, e in self.book.items()}, TEMPERATURE)
        for bid, wt in soft.items():
            self.book[bid]["weight"] = (1 - UNIFORM_MIX) * wt + UNIFORM_MIX / len(soft)

    def leaderboard(self, live: list[dict] | None = None) -> list[dict]:
        counts: dict[str, int] = {}
        for w in live or []:
            counts[w.get("broker")] = counts.get(w.get("broker"), 0) + 1
        rows = []
        for e in sorted(self.book.values(), key=lambda e: (-e["trackRecord"], e["id"])):
            idea = e["idea"] or {}
            rows.append({"id": e["id"], "persona": e["persona"], "archetype": e["archetype"], "feed": e["feed"],
                         "knobs": e["knobs"],
                         "trackRecord": e["trackRecord"], "weight": round(e["weight"], 4), "slots": e["slots"],
                         "liveWorlds": counts.get(e["id"], 0), "mass": round(e["mass"], 4),
                         "conviction": idea.get("conviction"), "rationale": idea.get("rationale"),
                         "idea": {k: v for k, v in idea.items() if k not in ("conviction", "rationale")},
                         "source": e["source"], "pnl": e["pnl"][-20:], "cumulative": e["cumulative"]})
        return rows
