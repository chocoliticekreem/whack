"""Persona brokers: the TOPS clone. MiroFish simulates opinions; here reality marks every agent to market.

Each broker (a persona from the Predict groups) submits a trade idea per observation: predicted values
for the assumptions the engine varies, a conviction and a one-line rationale in its own voice. An idea
spawns worlds centred on it, spread by (1 - conviction). After each observation a broker scores the
log of its worlds' posterior price mass over the mass it held before (its P&L against the market);
an exponentially decayed sum of those scores is its track record, and a softmax of track records
decides how many of the N world slots it gets next round. Every broker keeps at least one world.

Ideas come from one batched Claude Haiku 4.5 call per observation (strict JSON schema, validated and
repaired per broker). Without ANTHROPIC_API_KEY, or for any broker whose idea fails validation, a
deterministic rules broker answers instead, so everything runs offline.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import worlds
import llm  # noqa: E402  (services/predict, on sys.path via worlds)

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 2000
TIMEOUT_S = 30
RETRYABLE = {429, 500, 502, 503, 504, 529}

IDEA_KEYS = ("movingShare", "touristFactor", "capacityScale", "busDelayMin")
EVENT_KINDS = ("closure", "incident", "event", "weather")
DEFAULT_IDEA = {"movingShare": 0.6, "touristFactor": 1.0, "capacityScale": 1.0, "busDelayMin": 1.5}
BASE_SPREAD = 0.35
MIN_SPREAD = 0.03
DECAY = 0.7
SCORE_CAP = 5.0
MASS_FLOOR = 1e-9
RATIONALE_MAX = 240
# Rules weather nerd: a cloudy sky turns to rain about this many minutes into the horizon.
RAIN_FROM_CLOUD_MIN = 12.0

ROSTER: list[dict[str, Any]] = [
    {"id": "B-COMMUTER", "persona": "Office commuter", "archetype": "commuter",
     "voice": "terse, clock-watching, hates crowds", "conviction": 0.55,
     "lean": {"movingShare": 0.82, "touristFactor": 0.8}},
    {"id": "B-TOURIST", "persona": "Tourist on a weekend break", "archetype": "tourist",
     "voice": "excited, everything is busy and wonderful", "conviction": 0.6,
     "lean": {"touristFactor": 1.6, "movingShare": 0.6, "capacityScale": 0.9}},
    {"id": "B-LUNCH", "persona": "Lunch-crowd office worker", "archetype": "office_lunch",
     "voice": "practical, thinks in queues and sandwich shops", "conviction": 0.45,
     "lean": {"movingShare": 0.7, "capacityScale": 0.95}},
    {"id": "B-RIDER", "persona": "Delivery rider", "archetype": "delivery_rider",
     "voice": "street-smart, watches kerbs and traffic", "conviction": 0.5,
     "lean": {"capacityScale": 0.7, "movingShare": 0.75}, "busFromFeed": 1.0},
    {"id": "B-RESIDENT", "persona": "Local resident", "archetype": "resident",
     "voice": "weary, has seen every Saturday rush", "conviction": 0.4,
     "lean": {"movingShare": 0.5, "touristFactor": 1.2, "capacityScale": 1.1}},
    {"id": "B-BUS", "persona": "Bus driver on the 7", "archetype": "transit_arrival",
     "voice": "dry, reads the road through a windscreen", "conviction": 0.75, "lean": {}, "busFromFeed": 0.0},
    {"id": "B-EVENT", "persona": "Event-goer heading to the Palladium", "archetype": "event_goer",
     "voice": "buzzing, thinks the show is the centre of the world", "conviction": 0.5,
     "lean": {"touristFactor": 1.3}, "event": {"kind": "event", "zone": "London Palladium", "atMin": 20}},
    {"id": "B-SHOP", "persona": "Shop worker on Oxford Street", "archetype": "resident",
     "voice": "sees footfall through the till", "conviction": 0.55,
     "lean": {"touristFactor": 1.4, "capacityScale": 0.85, "movingShare": 0.72}},
    {"id": "B-CONTRA", "persona": "Contrarian", "archetype": "resident",
     "voice": "smug, bets against whatever the cameras just showed", "conviction": 0.35,
     "lean": {}, "contrarian": True},
    {"id": "B-WEATHER", "persona": "Weather nerd", "archetype": "commuter",
     "voice": "nerdy, quotes cloud types and radar", "conviction": 0.7,
     "lean": {"movingShare": 0.75}, "rainFromSky": True},
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
                  bus_delay: float | None, events: list[dict]) -> dict:
    t = seed.get("time") or {}
    cams = []
    for c in seed.get("cams", []):
        row = {"cam": c.get("name") or c["id"], "seedPeople": c.get("people")}
        if readings and c["id"] in readings:
            row["seenNow"] = readings[c["id"]]
        cams.append(row)
    seen = [r for r in cams if "seenNow" in r and r["seedPeople"]]
    ratio = (sum(r["seenNow"] for r in seen) / sum(r["seedPeople"] for r in seen)) if seen else None
    return {"area": seed.get("area"), "clock": f"{t.get('weekday', '?')} {t.get('iso', '')[11:16]} London",
            "daypart": t.get("daypart"), "weekend": t.get("isWeekend"), "minute": minute, "horizonMin": horizon,
            "weather": seed.get("weather") or {}, "busDelayMinObserved": bus_delay,
            "crowdVsSeed": round(ratio, 2) if ratio is not None else None, "cams": cams,
            "disruptions": [d.get("name") or d.get("description") or str(d) for d in (seed.get("disruptions") or [])][:5],
            "placedEvents": events[-5:],
            "zones": [{"id": z["id"], "name": z["name"]} for z in seed.get("zones", [])]}


# ---------------------------------------------------------------- rules broker


def rules_idea(broker: dict, ctx: dict, zone_ids_by_name: dict[str, str]) -> dict:
    weather = ctx.get("weather") or {}
    raining = (weather.get("precipitationMm") or 0) > 0.2 or str(weather.get("kind", "")).lower() in ("rain", "drizzle", "showers")
    idea: dict[str, Any] = dict(DEFAULT_IDEA) | {"rainAtMin": 0.0 if raining else None} | dict(broker.get("lean") or {})
    observed_bus = ctx.get("busDelayMinObserved")
    if observed_bus is not None:
        idea["busDelayMin"] = observed_bus + broker.get("busFromFeed", 0.0)
    elif "busFromFeed" in broker:
        idea["busDelayMin"] = 3.0 + broker["busFromFeed"]
    if broker.get("rainFromSky") and not raining and str(weather.get("kind", "")).lower() in ("cloudy", "overcast"):
        idea["rainAtMin"] = RAIN_FROM_CLOUD_MIN
    if broker.get("contrarian"):
        busy = (ctx.get("crowdVsSeed") or 1.0) > 1.05
        idea |= {"movingShare": 0.4, "touristFactor": 0.7} if busy else {"movingShare": 0.85, "touristFactor": 1.4}
    idea["busDelayMin"] = clamp(idea["busDelayMin"], *worlds.RANGES["busDelayMin"])
    idea["event"] = None
    ev = broker.get("event")
    if ev and ev.get("zone") in zone_ids_by_name:
        idea["event"] = {"kind": ev["kind"], "zoneId": zone_ids_by_name[ev["zone"]], "atMin": float(ev["atMin"])}
    idea["conviction"] = float(broker.get("conviction", 0.5))
    idea["rationale"] = rules_rationale(broker, idea)
    return idea


def rules_rationale(broker: dict, idea: dict) -> str:
    bits = [f"{idea['movingShare'] * 100:.0f}% set off", f"tourists x{idea['touristFactor']:.1f}",
            f"buses {idea['busDelayMin']:.1f} min late"]
    if idea.get("rainAtMin") is not None:
        bits.append(f"rain from +{idea['rainAtMin']:.0f} min")
    if idea.get("event"):
        bits.append(f"{idea['event']['kind']} at +{idea['event']['atMin']:.0f} min")
    return f"{broker['persona']} (rules): " + ", ".join(bits) + "."


# ---------------------------------------------------------------- Haiku broker


def response_schema(roster: list[dict], zone_ids: list[str]) -> dict:
    num = {"type": "number"}
    event = {"type": "object", "additionalProperties": False, "required": ["kind", "zoneId", "atMin"],
             "properties": {"kind": {"type": "string", "enum": list(EVENT_KINDS)},
                            "zoneId": {"type": "string", "enum": zone_ids}, "atMin": num}}
    item = {"type": "object", "additionalProperties": False,
            "required": ["id", *IDEA_KEYS, "rainAtMin", "event", "conviction", "rationale"],
            "properties": {"id": {"type": "string", "enum": [b["id"] for b in roster]},
                           **{k: num for k in IDEA_KEYS},
                           "rainAtMin": {"anyOf": [num, {"type": "null"}]},
                           "event": {"anyOf": [event, {"type": "null"}]},
                           "conviction": num, "rationale": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["ideas"],
            "properties": {"ideas": {"type": "array", "items": item}}}


def system_prompt(roster: list[dict], horizon: int) -> str:
    r = worlds.RANGES
    people = "\n".join(f"- {b['id']}: {b['persona']} ({b['voice']})" for b in roster)
    return (
        "You run the brokers on an exchange of parallel Londons. Each broker is a person on the street "
        "who submits one trade idea about the next "
        f"{horizon} minutes around the given area. Reality (street camera counts and the TfL bus feed) "
        "will score every idea, and brokers who are right get more weight next round.\n\n"
        f"Brokers:\n{people}\n\n"
        "For EVERY broker return one idea with these fields:\n"
        f"- movingShare: share of people now in the area who set off within the horizon ({r['movingShare'][0]}-{r['movingShare'][1]})\n"
        f"- touristFactor: tourist multiplier vs normal ({r['touristFactor'][0]}-{r['touristFactor'][1]})\n"
        f"- capacityScale: usable pavement capacity vs normal ({r['capacityScale'][0]}-{r['capacityScale'][1]}; lower = more cramped)\n"
        f"- busDelayMin: how late buses run in minutes ({r['busDelayMin'][0]}-{r['busDelayMin'][1]})\n"
        f"- rainAtMin: minute (0-{horizon}, measured from the start of the horizon) when rain starts, or null for no rain\n"
        "- event: null, or {kind: closure|incident|event|weather, zoneId: one of the given zone ids, atMin}\n"
        "- conviction: 0-1, how sure this broker is\n"
        "- rationale: one first-person sentence (max 25 words) in the broker's voice, citing the context\n\n"
        "Ground every idea in the context (clock, weather, camera counts vs the seed, bus delay, "
        "disruptions). Brokers should disagree where their lived experience differs; do not copy one idea "
        "across brokers. Only add an event when the persona has a concrete reason."
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
            v = float(raw[k])
            if not math.isfinite(v):
                return None
            idea[k] = round(clamp(v, *worlds.RANGES[k]), 3)
        conviction = float(raw["conviction"])
        if not math.isfinite(conviction):
            return None
        idea["conviction"] = round(clamp(conviction, 0.0, 1.0), 3)
        rain = raw.get("rainAtMin")
        idea["rainAtMin"] = None if rain is None else round(clamp(float(rain), 0.0, float(horizon)), 1)
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


# ---------------------------------------------------------------- worlds from ideas


def assumptions_from_idea(idea: dict, rng: random.Random, horizon: int, zone_ids: list[str]) -> dict:
    """A world centred on the idea; low conviction spreads it wider. Unopinionated knobs use the prior."""
    conviction = float(idea["conviction"])
    spread = BASE_SPREAD * (1 - conviction) + MIN_SPREAD
    a: dict[str, Any] = {}
    for k, (lo, hi) in worlds.RANGES.items():
        if k in IDEA_KEYS:
            a[k] = round(clamp(rng.gauss(float(idea[k]), spread * (hi - lo)), lo, hi), 3)
        else:
            a[k] = round(rng.uniform(lo, hi), 3)
    a["alightPerBus"] = round(a["alightPerBus"])
    if idea.get("rainAtMin") is not None:
        a["rainAtMin"] = (round(clamp(rng.gauss(idea["rainAtMin"], spread * horizon), 0.0, float(horizon)), 1)
                          if rng.random() < 0.5 + 0.5 * conviction else None)
    else:
        a["rainAtMin"] = (round(rng.uniform(0, horizon * 0.8), 1)
                          if rng.random() < (1 - conviction) * worlds.RAIN_PROB else None)
    a["incident"] = None
    ev = idea.get("event")
    a["events"] = ([dict(ev, note=f"{ev['kind']} (broker idea)")]
                   if ev and ev.get("zoneId") in zone_ids and rng.random() < 0.5 + 0.5 * conviction else [])
    return a


def allocate_slots(weights: dict[str, float], n: int) -> dict[str, int]:
    """Largest-remainder split of n slots by weight, with a floor of one slot per broker (needs n >= len)."""
    ids = list(weights)
    if n < len(ids):
        raise ValueError(f"need at least {len(ids)} worlds for {len(ids)} brokers")
    total = sum(max(w, 0.0) for w in weights.values()) or 1.0
    spare = n - len(ids)
    shares = {b: spare * max(weights[b], 0.0) / total for b in ids}
    slots = {b: 1 + int(shares[b]) for b in ids}
    left = n - sum(slots.values())
    for b in sorted(ids, key=lambda b: -(shares[b] - int(shares[b])))[:left]:
        slots[b] += 1
    return slots


def softmax(values: dict[str, float]) -> dict[str, float]:
    top = max(values.values())
    exp = {k: math.exp(v - top) for k, v in values.items()}
    total = sum(exp.values())
    return {k: e / total for k, e in exp.items()}


# ---------------------------------------------------------------- the brokerage


class Brokerage:
    def __init__(self, seed: dict, horizon: int, roster: list[dict] | None = None,
                 caller: Callable[[str, str, dict], tuple[str, dict]] | None = None) -> None:
        self.roster = roster or ROSTER
        self.horizon = horizon
        self.zone_ids = [z["id"] for z in seed["zones"]]
        self.zone_ids_by_name = {z["name"]: z["id"] for z in seed["zones"]}
        self.caller = caller or (haiku_call if api_key() else None)
        self.book: dict[str, dict[str, Any]] = {
            b["id"]: {"id": b["id"], "persona": b["persona"], "archetype": b["archetype"], "trackRecord": 0.0,
                      "weight": 1.0 / len(self.roster), "slots": 0, "priorMass": 0.0, "mass": 0.0,
                      "idea": None, "source": None, "pnl": [], "cumulative": 0.0}
            for b in self.roster}
        self.calls: list[dict[str, Any]] = []

    @property
    def llm_enabled(self) -> bool:
        return self.caller is not None

    def refresh(self, ctx: dict, obs_index: int) -> dict[str, dict]:
        """New ideas for every broker: one batched Haiku call, rules for whatever is missing or invalid."""
        ideas: dict[str, dict] = {}
        if self.caller is not None:
            record: dict[str, Any] = {"obs": obs_index, "model": MODEL}
            try:
                text, usage = self.caller(system_prompt(self.roster, self.horizon),
                                          json.dumps(ctx, separators=(",", ":")),
                                          response_schema(self.roster, self.zone_ids))
                record |= usage
                ideas, errors = validate_batch(text, self.roster, self.horizon, self.zone_ids)
                record["errors"] = errors[:10]
            except Exception as exc:
                record["errors"] = [f"{type(exc).__name__}: {exc}"[:300]]
            record["fallbacks"] = [b["id"] for b in self.roster if b["id"] not in ideas]
            self.calls.append(record)
        for b in self.roster:
            entry = self.book[b["id"]]
            if b["id"] in ideas:
                entry["idea"], entry["source"] = ideas[b["id"]], "haiku"
            else:
                entry["idea"], entry["source"] = rules_idea(b, ctx, self.zone_ids_by_name), "rules"
        return {bid: e["idea"] for bid, e in self.book.items()}

    def slots(self, n: int) -> dict[str, int]:
        out = allocate_slots({bid: e["weight"] for bid, e in self.book.items()}, n)
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
        """P&L = log(posterior mass / prior mass); track record = decayed sum; weight = softmax."""
        for bid, post in self.masses(live).items():
            e = self.book[bid]
            s = clamp(math.log(max(post, MASS_FLOOR) / max(e["priorMass"], MASS_FLOOR)), -SCORE_CAP, SCORE_CAP)
            e["trackRecord"] = round(DECAY * e["trackRecord"] + s, 4)
            e["cumulative"] = round(e["cumulative"] + s, 4)
            e["pnl"].append(round(s, 4))
            e["mass"] = post
        for bid, wt in softmax({bid: e["trackRecord"] for bid, e in self.book.items()}).items():
            self.book[bid]["weight"] = wt

    def leaderboard(self, live: list[dict] | None = None) -> list[dict]:
        counts: dict[str, int] = {}
        for w in live or []:
            counts[w.get("broker")] = counts.get(w.get("broker"), 0) + 1
        rows = []
        for e in sorted(self.book.values(), key=lambda e: (-e["trackRecord"], e["id"])):
            idea = e["idea"] or {}
            rows.append({"id": e["id"], "persona": e["persona"], "archetype": e["archetype"],
                         "trackRecord": e["trackRecord"], "weight": round(e["weight"], 4), "slots": e["slots"],
                         "liveWorlds": counts.get(e["id"], 0), "mass": round(e["mass"], 4),
                         "conviction": idea.get("conviction"), "rationale": idea.get("rationale"),
                         "idea": {k: v for k, v in idea.items() if k not in ("conviction", "rationale")},
                         "source": e["source"], "pnl": e["pnl"][-20:], "cumulative": e["cumulative"]})
        return rows
