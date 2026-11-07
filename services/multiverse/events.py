"""General event layer: live London feeds -> simulation interventions, by rule.

`derive(snapshot, now)` is pure and deterministic. Every rule reads names, stations and places
from the feed data (or the public venue table in venues.py); nothing here names a line, station,
venue or date.

snapshot: {source: {"ts": iso, "payload": ...}} with the recorder's payload shapes; for
`crowding_profile` the payload is the list of per-station profiles. `tube` may be the union of
several recent arrival snapshots (replay does that) so the line graph has more links. Optional
`tube_day`: arrival rows from the whole recorded day, read only for which lines serve a station and
which stations neighbour each other (fixed network facts); whether a line is running is read from
`tube` alone.

Intervention: {id, kind, where, start, end, magnitude, source, rule, reason, planned[, shape]}
- closure / capacity at a naptan: magnitude = remaining capacity fraction (closure is 0).
- capacity on a line: where {line, naptans}, one event for a delay that covers a whole line or
  "the rest of the line"; magnitude = that line's remaining capacity fraction.
- closure on streets: where {lat, lon, radius_m}; the engine picks the segments inside it.
- demand_surge: magnitude = extra demand as a fraction of the place's typical flow.
- weather: magnitude = rain in mm/h, where = a citywide radius.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterator
from zoneinfo import ZoneInfo

try:
    from . import venues as venue_table
except ImportError:
    import venues as venue_table

log = logging.getLogger("multiverse.events")
LONDON = ZoneInfo("Europe/London")

HORIZON_MIN = 30
CLOSED_SEVERITIES = {1, 2, 3, 4, 5}
PART_SEVERITIES = {3, 5}
PLANNED_SEVERITIES = {4, 5}
PLANNED_CLOSURE = 4
CLOSURE_MAX_LIVE_RATIO = 0.5
DELAY_FACTOR = {6: 0.5, 7: 0.75}
FAMILY_WORDS = {"closed": ("no service", "suspended", "closed", "closure"),
                "severe": ("severe delays",), "reduced": ("reduced service",)}
PLANNED_WORDS = ("planned", "engineering works")
ALT_MAX = 3
ALT_HOPS = 2
ALT_OPEN_FACTOR = 0.8
SURGE_CAP = 2.0
CROWD_SURGE_RATIO = 1.3
CROWD_MIN_TYPICAL = 0.05
ROAD_PLANNED_CATEGORIES = {"works", "planned events", "special events"}
ROAD_MIN_RADIUS_M = 50
ROAD_PAD_M = 30
FIXTURE_TRANSIT_SHARE = 0.75
STATION_HOURLY_RIDERS = 5000
FIXTURE_SURGE_CAP = 3.0
FIXTURE_DECAY_MIN = 60
FIXTURE_ARRIVAL_MIN = 90
FIXTURE_ARRIVAL_SCALE = 0.5
FIXTURE_LOOKAHEAD_MIN = 6 * 60
FIXTURE_STATION_RADIUS_M = 1000
FIXTURE_VENUE_MATCH_M = 300
FINISHED = {"ft", "aet", "pen", "match finished", "after extra time", "after penalties"}
CALLED_OFF = {"pst", "postponed", "canc", "cancelled", "abd", "abandoned"}
SPORT_WORDS = {"rugby": "rugby", "cricket": "cricket", "nfl": "american_football", "tennis": "tennis"}
RAIN_MM = 0.1
RAIN_LOOKAHEAD_MIN = 60
STEP_MS = 15 * 60_000
CITY = {"lat": 51.5074, "lon": -0.1278, "radius_m": 25000}


def parse_ms(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, LONDON).strftime("%H:%M")


def norm(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\s+(underground\s+)?station$", "", s)
    s = re.sub(r"\([^)]*\)", " ", s).replace("&", " and ")
    s = re.sub(r"[.'’,]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def display(name: str) -> str:
    return re.sub(r"\s+(Underground\s+)?Station$", "", name).strip()


def dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def where_key(where: dict) -> str:
    return json.dumps(where, sort_keys=True)


def make(kind: str, where: dict, start: int, end: int, magnitude: float, source: str, rule: str,
         reason: str, planned: bool, **extra: Any) -> dict:
    iv = {"kind": kind, "where": where, "start": int(start), "end": int(end),
          "magnitude": round(float(magnitude), 3), "source": source, "rule": rule, "reason": reason,
          "planned": bool(planned), **extra}
    iv["id"] = hashlib.sha1(f"{rule}|{kind}|{where_key(where)}|{planned}".encode()).hexdigest()[:12]
    return iv


def record(snapshot: dict, source: str) -> tuple[int | None, Any]:
    rec = (snapshot or {}).get(source)
    if not rec or rec.get("payload") is None:
        return None, None
    return parse_ms(rec["ts"]), rec["payload"]


# ---- typical station flow (crowding_profile) -------------------------------------------------

def profile_value(profile: dict, at_ms: int) -> float | None:
    at = datetime.fromtimestamp(at_ms / 1000, LONDON)
    band = f"{at.hour:02d}:{at.minute // 15 * 15:02d}"
    for day in profile.get("daysOfWeek") or []:
        if day.get("dayOfWeek") == at.strftime("%a").upper():
            for tb in day.get("timeBands") or []:
                if str(tb.get("timeBand", "")).startswith(band):
                    v = tb.get("percentageOfBaseLine")
                    return float(v) if isinstance(v, (int, float)) else None
    return None


class Typical:
    """Typical flow per naptan at a time; stations without a profile get the citywide median."""

    def __init__(self, profiles: list[dict] | None):
        self.by_naptan = {p["naptan"]: p for p in profiles or [] if isinstance(p, dict) and p.get("naptan")}

    def at(self, naptan: str, at_ms: int) -> tuple[float | None, bool]:
        if naptan in self.by_naptan:
            return profile_value(self.by_naptan[naptan], at_ms), True
        vals = [v for p in self.by_naptan.values() if (v := profile_value(p, at_ms)) is not None]
        return (median(vals) if vals else None), False


# ---- tube network from arrivals --------------------------------------------------------------

def is_between(row: dict) -> bool:
    return (row.get("currentLocation") or "").lower().startswith("between ")


class Network:
    """Stations, line membership and adjacency, all read from the tube arrivals feed.

    Adjacency comes from `currentLocation` "Between A and B" strings: a train seen between two
    stations proves they are neighbours on that line. `moving` holds the lines with such a train in
    `arrivals` (not `topology`): TfL keeps parked trains listed "At Platform" on a line that is not
    running, so rows alone do not show a line is in service.
    """

    def __init__(self, arrivals: list[dict] | None, topology: list[dict] | None = None):
        self.name: dict[str, str] = {}
        self.lines: dict[str, set[str]] = defaultdict(set)
        self.by_line_name: dict[str, dict[str, str]] = defaultdict(dict)
        self.edges: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        live = [r for r in arrivals or [] if r.get("naptanId") and r.get("lineId")]
        self.moving = {r["lineId"] for r in live if is_between(r)}
        rows = live + [r for r in topology or [] if r.get("naptanId") and r.get("lineId")]
        for r in rows:
            nap, line = r["naptanId"], r["lineId"]
            self.name.setdefault(nap, display(r.get("stationName") or nap))
            self.lines[nap].add(line)
            self.by_line_name[line].setdefault(norm(r.get("stationName") or nap), nap)
        for r in rows:
            if is_between(r):
                pair = self.split_pair(r["lineId"], r["currentLocation"][8:])
                if pair:
                    a, b = pair
                    self.edges[r["lineId"]][a].add(b)
                    self.edges[r["lineId"]][b].add(a)

    def stations(self, line: str) -> set[str]:
        return set(self.by_line_name.get(line, {}).values())

    def resolve(self, line: str, text: str) -> str | None:
        return self.by_line_name.get(line, {}).get(norm(text))

    def split_pair(self, line: str, text: str) -> tuple[str, str] | None:
        for m in re.finditer(r" and ", text):
            a, b = self.resolve(line, text[:m.start()]), self.resolve(line, text[m.end():])
            if a and b and a != b:
                return a, b
        return None

    def match_prefix(self, line: str, text: str) -> tuple[str, int] | None:
        """Longest station name of `line` that `text` (normalised) starts with."""
        best = None
        for key, nap in self.by_line_name.get(line, {}).items():
            if text.startswith(key) and (len(text) == len(key) or not text[len(key)].isalnum()):
                if best is None or len(key) > best[1]:
                    best = (nap, len(key))
        return best

    def path(self, line: str, a: str, b: str) -> list[str] | None:
        adj = self.edges.get(line, {})
        prev, queue = {a: None}, deque([a])
        while queue:
            cur = queue.popleft()
            if cur == b:
                out = [cur]
                while prev[out[-1]] is not None:
                    out.append(prev[out[-1]])
                return out[::-1]
            for nxt in sorted(adj.get(cur, ())):
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        return None

    def neighbours(self, start: str, hops: int) -> list[tuple[str, int]]:
        """Stations within `hops` on any line, nearest first (ties by naptan for determinism)."""
        seen, frontier, out = {start}, [start], []
        for h in range(1, hops + 1):
            nxt = set()
            for cur in frontier:
                for line in sorted(self.lines.get(cur, ())):
                    nxt |= self.edges.get(line, {}).get(cur, set())
            nxt -= seen
            out += [(n, h) for n in sorted(nxt)]
            seen |= nxt
            frontier = sorted(nxt)
        return out


# ---- rule: tube line disruption --------------------------------------------------------------

def scope_after(net: Network, line: str, text: str, pos: int) -> tuple[str, list[str] | None, str]:
    """What a status phrase at `pos` covers: ("section", stations, label) | ("rest"|"whole", None, "")."""
    rest = text[pos:].lstrip()
    if rest.startswith("on the rest of the line"):
        return "rest", None, ""
    if not rest.startswith("between "):
        return "whole", None, ""
    rest = rest[8:]
    a = net.match_prefix(line, rest)
    if not a or not rest[a[1]:].startswith(" and "):
        return "whole", None, ""
    rest = rest[a[1] + 5:]
    b = net.match_prefix(line, rest)
    if not b:
        return "whole", None, ""
    via = None
    tail = rest[b[1]:]
    if tail.startswith(" via "):
        v = net.match_prefix(line, tail[5:])
        via = v[0] if v else None
    label = f"{net.name[a[0]]}–{net.name[b[0]]}" + (f" via {net.name[via]}" if via else "")
    legs = [(a[0], via), (via, b[0])] if via else [(a[0], b[0])]
    stations: list[str] = []
    for x, y in legs:
        p = net.path(line, x, y)
        if p is None:
            return "section", sorted({a[0], b[0]} | ({via} if via else set())), label + " (endpoints only)"
        stations += [s for s in p if s not in stations]
    return "section", stations, label


def find_scope(net: Network, line: str, reason: str, family: str) -> tuple[str, list[str] | None, str, str]:
    text = norm(reason)
    hits = sorted((m.start(), -len(w), w, m.end()) for w in FAMILY_WORDS[family] for m in re.finditer(re.escape(w), text))
    if not hits:
        return "whole", None, "", ""
    _, _, word, pos = hits[0]
    kind, stations, label = scope_after(net, line, text, pos)
    return kind, stations, label, word


def network(snapshot: dict) -> Network:
    _, arrivals = record(snapshot, "tube")
    _, topology = record(snapshot, "tube_day")
    return Network(arrivals, topology)


def live_ratios(snapshot: dict, typical: Typical) -> dict[str, float]:
    """Live busyness over typical per naptan, from the newest crowding record (stations with a real profile)."""
    ts, rows = record(snapshot, "crowding")
    out = {}
    for row in rows or []:
        nap, live = row.get("naptan"), row.get("percentageOfBaseline")
        if not nap or not row.get("dataAvailable") or not isinstance(live, (int, float)):
            continue
        typ, real = typical.at(nap, parse_ms(row["timeUtc"]) if row.get("timeUtc") else ts)
        if real and typ is not None and typ >= CROWD_MIN_TYPICAL:
            out[nap] = live / typ
    return out


def line_rule(snapshot: dict, now: int, typical: Typical) -> list[dict]:
    ts, payload = record(snapshot, "line_status")
    if ts is None:
        return []
    net = network(snapshot)
    station_lines: dict[str, dict[str, tuple[float, bool, str]]] = defaultdict(dict)
    line_wide: list[dict] = []
    end = now + HORIZON_MIN * 60_000
    for ln in payload or []:
        line, lname = ln.get("id"), ln.get("name") or ln.get("id")
        statuses = [s for s in ln.get("statuses") or [] if s.get("statusSeverity") in CLOSED_SEVERITIES | set(DELAY_FACTOR)]
        if not statuses:
            continue
        if not net.stations(line):
            log.info("line_status: no station data for %s, skipped", line)
            continue
        if line not in net.moving and any(s["statusSeverity"] == PLANNED_CLOSURE for s in statuses):
            log.info("line_status: %s Planned Closure and no train seen moving on it: not scheduled now, skipped", line)
            continue
        covered: set[str] = set()
        plans = []
        for s in sorted(statuses, key=lambda s: s["statusSeverity"]):
            sev, desc, reason = s["statusSeverity"], s.get("statusSeverityDescription") or "", s.get("reason") or ""
            family = "closed" if sev in CLOSED_SEVERITIES else "severe" if sev == 6 else "reduced"
            factor = 0.0 if family == "closed" else DELAY_FACTOR[sev]
            kind, stations, label, word = find_scope(net, line, reason, family)
            if kind == "whole" and sev in PART_SEVERITIES:
                log.info("line_status: %s %s names no section, skipped", line, desc)
                continue
            plans.append((sev, desc, reason, factor, kind, stations, label, word))
            if kind == "section":
                covered |= set(stations)
        for sev, desc, reason, factor, kind, stations, label, word in plans:
            targets = stations if kind == "section" else sorted(net.stations(line) - covered if kind == "rest" else net.stations(line))
            planned = sev in PLANNED_SEVERITIES or any(w in norm(reason) for w in PLANNED_WORDS)
            what = f"{word or desc.lower()} {label}".strip() if label else (f"{word} on the rest of the line" if kind == "rest" else (word or desc.lower()))
            text = f"{lname} {desc}: {what} (TfL line status {hhmm(ts)})"
            if kind != "section" and factor > 0:
                line_wide.append(make("capacity", {"line": line, "naptans": targets}, ts, end, factor, "line_status",
                                      "line_disruption", f"{text}. {len(targets)} stations, line capacity x{factor:.2f}",
                                      planned))
                continue
            for nap in targets:
                old = station_lines[nap].get(line)
                if old is None or factor < old[0]:
                    station_lines[nap][line] = (factor, planned, text)
    out = line_wide
    busy = live_ratios(snapshot, typical)
    for nap in sorted(station_lines):
        hit = station_lines[nap]
        n = max(len(net.lines.get(nap, ())), 1)
        factor = 1 - sum(1 - f for f, _, _ in hit.values()) / n
        closed = sum(1 for f, _, _ in hit.values() if f == 0) / n
        planned = all(p for _, p, _ in hit.values())
        texts = "; ".join(sorted({t for _, _, t in hit.values()}))
        name = net.name.get(nap, nap)
        kind = "closure" if factor <= 1e-9 else "capacity"
        if kind == "closure" and busy.get(nap, 0.0) >= CLOSURE_MAX_LIVE_RATIO:
            log.info("line_status: %s reads %.0f%% of typical on live crowding, closure not emitted (%s)",
                     nap, busy[nap] * 100, texts)
            continue
        reason = f"{texts}. {name}: {len(hit)}/{n} lines affected, capacity x{factor:.2f}"
        out.append(make(kind, {"naptan": nap}, ts, end, max(factor, 0.0), "line_status", "line_disruption", reason, planned))
        if closed > 0:
            out += displacement(net, nap, closed, station_lines, typical, ts, end, texts, planned)
    return out


def displacement(net: Network, nap: str, closed: float, station_lines: dict, typical: Typical, ts: int,
                 end: int, texts: str, planned: bool) -> list[dict]:
    def factor_of(s: str) -> float:
        hit = station_lines.get(s, {})
        n = max(len(net.lines.get(s, ())), 1)
        return 1 - sum(1 - f for f, _, _ in hit.values()) / n

    alts = [(s, h) for s, h in net.neighbours(nap, ALT_HOPS) if factor_of(s) >= ALT_OPEN_FACTOR][:ALT_MAX]
    if not alts:
        log.info("line_status: no open alternative within %d hops of %s", ALT_HOPS, nap)
        return []
    base, base_real = typical.at(nap, ts)
    weights = [1 / h for _, h in alts]
    out = []
    for (alt, hops), w in zip(alts, weights):
        alt_typ, alt_real = typical.at(alt, ts)
        ratio = (base / alt_typ) if base and alt_typ else 1.0
        mag = min(SURGE_CAP, closed * ratio * w / sum(weights))
        src = "profile" if base_real and alt_real else "citywide median profile"
        reason = (f"Riders displaced from {net.name.get(nap, nap)} ({closed:.0%} of its lines closed) to the "
                  f"nearest open station, {hops} hop(s): {texts}. Typical flow from {src}")
        out.append(make("demand_surge", {"naptan": alt}, ts, end, mag, "line_status+crowding_profile",
                        "line_displacement", reason, planned))
    return out


# ---- rule: station crowding surge ------------------------------------------------------------

def crowding_rule(snapshot: dict, now: int, typical: Typical) -> list[dict]:
    ts, rows = record(snapshot, "crowding")
    if ts is None:
        return []
    out = []
    for row in rows or []:
        nap, live = row.get("naptan"), row.get("percentageOfBaseline")
        if not nap or not row.get("dataAvailable") or not isinstance(live, (int, float)):
            continue
        at = parse_ms(row["timeUtc"]) if row.get("timeUtc") else ts
        typ, real = typical.at(nap, at)
        if not real or typ is None or typ < CROWD_MIN_TYPICAL:
            continue
        ratio = live / typ
        if ratio < CROWD_SURGE_RATIO:
            continue
        reason = (f"{row.get('name') or nap} busyness {live:.2f} vs typical {typ:.2f} for "
                  f"{datetime.fromtimestamp(at / 1000, LONDON).strftime('%a %H:%M')} (x{ratio:.2f}; TfL crowding {hhmm(at)})")
        out.append(make("demand_surge", {"naptan": nap}, at, now + HORIZON_MIN * 60_000, min(SURGE_CAP, ratio - 1),
                        "crowding+crowding_profile", "crowding_surge", reason, False))
    return out


# ---- rule: road closure ----------------------------------------------------------------------

def points_of(line_string: Any) -> list[tuple[float, float]]:
    try:
        data = json.loads(line_string) if isinstance(line_string, str) else line_string
    except (TypeError, ValueError):
        return []
    out = []

    def walk(x: Any) -> None:
        if isinstance(x, list) and len(x) == 2 and all(isinstance(v, (int, float)) for v in x):
            out.append((float(x[1]), float(x[0])))
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(data)
    return out


def enclosing(points: list[tuple[float, float]]) -> dict:
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    r = max(dist_m(lat, lon, p[0], p[1]) for p in points) + ROAD_PAD_M
    return {"lat": round(lat, 5), "lon": round(lon, 5), "radius_m": int(max(ROAD_MIN_RADIUS_M, r))}


def road_rule(snapshot: dict, now: int) -> list[dict]:
    out = []
    end_default = now + HORIZON_MIN * 60_000
    ts, rows = record(snapshot, "disruptions")
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows or []:
        if str(row.get("closure", "")).lower() != "closed":
            continue
        try:
            s, e = parse_ms(row["startDateTime"]), parse_ms(row["endDateTime"])
        except (KeyError, TypeError, ValueError):
            continue
        if s <= end_default and e >= now:
            groups[(row.get("disruptionId") or "", row.get("streetName") or "")].append(row)
    for (did, street), grp in sorted(groups.items()):
        pts = [p for r in grp for p in points_of(r.get("lineString"))]
        pts += [(r[k + "Lat"], r[k + "Lon"]) for r in grp for k in ("start", "end")
                if isinstance(r.get(k + "Lat"), (int, float)) and isinstance(r.get(k + "Lon"), (int, float))]
        if not pts:
            log.info("disruptions: %s has no geometry, skipped", did)
            continue
        first = grp[0]
        category = str(first.get("category") or "")
        planned = category.lower() in ROAD_PLANNED_CATEGORIES
        e = min(parse_ms(r["endDateTime"]) for r in grp)
        comment = re.sub(r"\s+", " ", str(first.get("comments") or "")).strip()[:140]
        reason = f"{street.title()} closed ({category}, {first.get('severity')}): {comment} (TfL disruption {did}, {hhmm(ts)})"
        out.append(make("closure", enclosing(pts), max(ts, min(parse_ms(r["startDateTime"]) for r in grp)),
                        min(e, end_default), 0.0, "disruptions", "road_closure", reason, planned))
    ts, rows = record(snapshot, "road_status")
    for row in rows or []:
        if str(row.get("statusSeverity", "")).lower() != "closure":
            continue
        pts = points_of(row.get("bounds"))
        if not pts:
            continue
        reason = f"{row.get('displayName')} {row.get('statusSeverityDescription')} (TfL road status {hhmm(ts)})"
        out.append(make("closure", enclosing(pts), ts, end_default, 0.0, "road_status", "road_closure", reason, False))
    return out


# ---- rule: fixtures --------------------------------------------------------------------------

def find_venue(fx: dict) -> tuple[str, dict] | None:
    name = fx.get("venue") or ""
    for vname, v in venue_table.VENUES.items():
        if norm(vname) == norm(name):
            return vname, v
    lat, lon = fx.get("lat"), fx.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        for vname, v in venue_table.VENUES.items():
            if dist_m(lat, lon, v["lat"], v["lon"]) <= FIXTURE_VENUE_MATCH_M:
                return vname, v
    return None


def sport_of(fx: dict, known: dict | None) -> str:
    if known:
        return known["sport"]
    league = norm(fx.get("league") or "")
    return next((s for w, s in SPORT_WORDS.items() if w in league), "football")


def fixture_rule(snapshot: dict, now: int, net: Network) -> list[dict]:
    ts, rows = record(snapshot, "scrape_fixtures")
    out = []
    for fx in rows or []:
        status = norm(str(fx.get("status") or ""))
        if status in CALLED_OFF or not fx.get("kickoff_utc"):
            continue
        hit = find_venue(fx)
        name, known = (hit[0], hit[1]) if hit else (fx.get("venue") or "unknown venue", None)
        lat, lon = (known["lat"], known["lon"]) if known else (fx.get("lat"), fx.get("lon"))
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            log.info("fixtures: %s at %s has no location and no venue entry, skipped", fx.get("idEvent"), name)
            continue
        capacity = known["capacity"] if known else venue_table.DEFAULT_CAPACITY
        cap_src = "capacity from venues table" if known else f"venue not in table, default capacity {capacity}"
        sport = sport_of(fx, known)
        kickoff = parse_ms(fx["kickoff_utc"])
        if kickoff - FIXTURE_ARRIVAL_MIN * 60_000 > now + FIXTURE_LOOKAHEAD_MIN * 60_000:
            continue
        end_est = kickoff + venue_table.DURATION_MIN.get(sport, venue_table.DURATION_MIN["football"]) * 60_000
        if status in FINISHED and ts is not None:
            end_est = min(end_est, ts)
        if end_est + FIXTURE_DECAY_MIN * 60_000 < now:
            continue
        stations = (known or {}).get("stations") or fx.get("stations") or []
        resolved = sorted({nap for s in stations for line in sorted(net.by_line_name) if (nap := net.resolve(line, s))})
        targets: list[dict] = [{"naptan": n} for n in resolved]
        if len(resolved) < len(stations) or not targets:
            targets.append({"lat": round(lat, 5), "lon": round(lon, 5), "radius_m": FIXTURE_STATION_RADIUS_M})
        per = capacity * FIXTURE_TRANSIT_SHARE / max(len(stations), 1)
        mag = min(FIXTURE_SURGE_CAP, per / STATION_HOURLY_RIDERS)
        match = f"{fx.get('home')} v {fx.get('away')} at {name}"
        when = f"kick-off {hhmm(kickoff)}, status {fx.get('status')}"
        basis = (f"{capacity} x {FIXTURE_TRANSIT_SHARE:.0%} by rail over {max(len(stations), 1)} station(s) "
                 f"vs ~{STATION_HOURLY_RIDERS}/h typical; {cap_src}; TheSportsDB {hhmm(ts)}")
        for where in targets:
            if status not in FINISHED and now < kickoff:
                out.append(make("demand_surge", where, kickoff - FIXTURE_ARRIVAL_MIN * 60_000, kickoff,
                                mag * FIXTURE_ARRIVAL_SCALE, "scrape_fixtures", "fixture_start",
                                f"Fans arriving for {match} ({when}; {basis})", False, shape="ramp_up"))
            out.append(make("demand_surge", where, end_est, end_est + FIXTURE_DECAY_MIN * 60_000, mag,
                            "scrape_fixtures", "fixture_end",
                            f"Crowd leaving {match} ({when}, est. end {hhmm(end_est)} for {sport}; {basis})",
                            False, shape="linear_decay"))
    return out


# ---- rule: rain onset ------------------------------------------------------------------------

def rain_rule(snapshot: dict, now: int) -> list[dict]:
    ts, payload = record(snapshot, "rain_now")
    if ts is None or not isinstance(payload, dict):
        return []
    steps = {}
    for m in payload.get("minutely_15") or []:
        if isinstance(m.get("precipitation"), (int, float)) and m.get("time"):
            steps[parse_ms(m["time"])] = float(m["precipitation"])
    cur = payload.get("current") or {}
    if isinstance(cur.get("precipitation"), (int, float)) and cur.get("time"):
        steps.setdefault(parse_ms(cur["time"]), float(cur["precipitation"]))
    order = sorted(steps.items())
    horizon = now + RAIN_LOOKAHEAD_MIN * 60_000
    wet = next(((t, mm) for t, mm in order if mm >= RAIN_MM and t + STEP_MS > now and t <= horizon), None)
    if not wet:
        return []
    onset = wet[0]
    end = next((t for t, mm in order if t > onset and mm < RAIN_MM), order[-1][0] + STEP_MS)
    peak = max(mm for t, mm in order if onset <= t < end)
    when = "raining now" if onset <= now else f"rain from {hhmm(onset)}"
    reason = f"{when}, up to {peak:.1f} mm per 15 min until {hhmm(end)} (Open-Meteo {hhmm(ts)})"
    return [make("weather", dict(CITY), max(onset, ts) if onset <= now else onset, end, peak * 4,
                 "rain_now", "rain_onset", reason, False)]


# ---- merge -----------------------------------------------------------------------------------

def merge(items: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for iv in items:
        groups[(iv["kind"], where_key(iv["where"]), iv["planned"])].append(iv)
    out = []
    for key, grp in groups.items():
        grp.sort(key=lambda i: (i["start"], i["end"], i["rule"], i["reason"]))
        cur = None
        for iv in grp:
            if cur and iv["start"] <= cur["end"]:
                cur = combine(cur, iv)
            else:
                if cur:
                    out.append(cur)
                cur = dict(iv)
        out.append(cur)
    return sorted(out, key=lambda i: (i["start"], i["kind"], where_key(i["where"]), i["rule"], i["id"]))


def combine(a: dict, b: dict) -> dict:
    kind = a["kind"]
    if kind == "demand_surge":
        mag = min(FIXTURE_SURGE_CAP, a["magnitude"] + b["magnitude"])
    elif kind == "weather":
        mag = max(a["magnitude"], b["magnitude"])
    else:
        mag = min(a["magnitude"], b["magnitude"])
    rules = "+".join(sorted(set(a["rule"].split("+")) | set(b["rule"].split("+"))))
    sources = "+".join(sorted(set(a["source"].split("+")) | set(b["source"].split("+"))))
    reasons = " | ".join(dict.fromkeys(a["reason"].split(" | ") + b["reason"].split(" | ")))
    extra = {k: v for k, v in {**b, **a}.items() if k not in {"kind", "where", "start", "end", "magnitude",
                                                            "source", "rule", "reason", "planned", "id"}}
    return make(kind, a["where"], min(a["start"], b["start"]), max(a["end"], b["end"]), mag, sources, rules,
                reasons, a["planned"], **extra)


def derive(snapshot: dict, now: int) -> list[dict]:
    """Every intervention the current feeds imply at `now` (ms). Pure: same input, same output."""
    _, profiles = record(snapshot, "crowding_profile")
    typical = Typical(profiles if isinstance(profiles, list) else [profiles] if profiles else [])
    net = network(snapshot)
    items = (line_rule(snapshot, now, typical) + crowding_rule(snapshot, now, typical) + road_rule(snapshot, now)
             + fixture_rule(snapshot, now, net) + rain_rule(snapshot, now))
    return merge(items)


# ---- replay over recorded data ---------------------------------------------------------------

SOURCES = ("line_status", "tube", "crowding", "crowding_profile", "disruptions", "road_status",
           "scrape_fixtures", "rain_now")
TICK_SOURCES = {"line_status", "crowding", "disruptions", "road_status", "scrape_fixtures", "rain_now"}
LOOKBACK_H = 36


def read_source(raw_dir: Path, source: str, start: int, end: int) -> Iterator[tuple[int, Any]]:
    first = datetime.fromtimestamp(start / 1000, timezone.utc) - timedelta(hours=LOOKBACK_H)
    for path in sorted((Path(raw_dir) / source).glob("*/*.ndjson*")):
        try:
            hour = datetime.strptime(f"{path.parent.name} {path.name[:2]}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if hour < first.replace(minute=0, second=0, microsecond=0) or hour.timestamp() * 1000 > end:
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    yield parse_ms(rec["ts"]), rec["payload"]
                except (ValueError, KeyError):
                    continue


def add_tube_rows(rows: dict, payload: Any) -> bool:
    """Fold one arrivals snapshot into the unique (line, station, between-location) rows."""
    grew = False
    for r in payload or []:
        loc = r.get("currentLocation") or ""
        key = (r.get("lineId"), r.get("naptanId"), r.get("stationName"), loc if loc.lower().startswith("between ") else "")
        if key not in rows:
            rows[key] = {"lineId": key[0], "naptanId": key[1], "stationName": key[2], "currentLocation": key[3]}
            grew = True
    return grew


def replay(raw_dir: str | Path, start: int, end: int) -> Iterator[tuple[int, list[dict]]]:
    """(ts, interventions) at every recorded update of a rule's feed between start and end (ms).

    `tube` is the union of every arrivals snapshot so far: a suspended section has no trains on it,
    so its links only show up in earlier snapshots. `tube_day` adds the rows of the whole recorded
    (UTC) day, so a station's lines are known before a train on each has been seen; only which lines
    serve which stations is read from it.
    """
    day_end = parse_ms(datetime.fromtimestamp(end / 1000, timezone.utc).strftime("%Y-%m-%dT23:59:59Z"))
    events = []
    topology: dict[tuple, dict] = {}
    for source in SOURCES:
        for ts, payload in read_source(Path(raw_dir), source, start, day_end if source == "tube" else end):
            if source == "tube":
                add_tube_rows(topology, payload)
            if ts <= end:
                events.append((ts, SOURCES.index(source), source, payload))
    events.sort(key=lambda e: (e[0], e[1]))
    snap: dict[str, dict] = {}
    if topology:
        snap["tube_day"] = {"ts": datetime.fromtimestamp(day_end / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "payload": [topology[k] for k in sorted(topology, key=str)]}
    profiles: dict[str, dict] = {}
    tube: dict[tuple, dict] = {}
    tube_cache: list[dict] = []
    tube_dirty = False
    for ts, _, source, payload in events:
        iso = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if source == "crowding_profile":
            if isinstance(payload, dict) and payload.get("naptan"):
                profiles[payload["naptan"]] = payload
            snap[source] = {"ts": iso, "payload": [profiles[k] for k in sorted(profiles)]}
        elif source == "tube":
            tube_dirty = add_tube_rows(tube, payload) or tube_dirty
        else:
            snap[source] = {"ts": iso, "payload": payload}
        if ts >= start and source in TICK_SOURCES:
            if tube_dirty:
                tube_cache, tube_dirty = [tube[k] for k in sorted(tube, key=str)], False
            if tube_cache:
                snap["tube"] = {"ts": iso, "payload": tube_cache}
            yield ts, derive(snap, ts)
