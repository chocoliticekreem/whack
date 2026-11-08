"""The visible clues next to each market (docs/questions.md §4) and the top strip (GET /signals).

Every market carries `signals`: a list of at most 2 clues, each {"id", "text", "data"}. `id` names the clue and
fixes the shape of `data`; `text` is the one line to show as is. Ids:

- vs_usual     {day, asOf, laggard, leader, hubs: [{id, name, live, typical, vsUsual, typicalNext15: [{at, typical}],
               usualTrend: "up"|"down"|"flat", furthestBelow, leader}]}   (hub_surge, hub_hour, oxford-busy)
- usual_curve  {hub, name, vsUsual, typicalNow, typicalIn15, usualTrend}  (hub_surge: the laggard's ghost line)
- fixture      {home, away, score, status, egressEst, stations, hub, minutesSinceEgress}       (hub_hour)
- pulse        {pulse, usual, typicalIn15, usualTrend, change5, asOf}                          (city_pulse)
- vix_driver   {vix, driver, driverDelta, change5, asOf}                                       (london_vix)
- stacks       {total, stacks: {BNN, LAM, BIG, OCK}, avg30, change10, asOf}                    (heathrow_stack)
- lines        {good, of, sinceLastChangeMin, disrupted: [{id, name, sev, desc, reason, sinceMin}]} (clean_sheet)

Numbers are raw (fractions of baseline, not percent); `text` carries the formatted version. Everything reads only
data at or before `now`.
"""
from __future__ import annotations

import statistics

from data import Store, day_name, feeds, load_hotspots, local_time
from pricing import MIN_MS

SHORT = {"oxford-circus": "Ox", "kings-cross": "KX", "piccadilly": "Picc", "waterloo": "Wloo", "bank": "Bank"}
ARROW = {"up": "▲", "down": "▼", "flat": "▶"}
WINDOW_MS = 3 * 3_600_000
FIXTURE_WINDOW_MS = 90 * MIN_MS
SPOTS = load_hotspots()
SHORT_NAME = {h["id"]: h["short"] for h in SPOTS}
# Tube lines at the stations TheSportsDB lists for London grounds, so a final whistle maps to the hub fans head for.
STATION_LINES = {
    "Arsenal": ["piccadilly"], "Holloway Road": ["piccadilly"], "Finsbury Park": ["piccadilly", "victoria"],
    "Highbury & Islington": ["victoria"], "Putney Bridge": ["district"],
    "Hammersmith": ["piccadilly", "district", "hammersmith-city", "circle"],
    "Wembley Park": ["jubilee", "metropolitan"],
    "Stratford": ["central", "jubilee"], "Seven Sisters": ["victoria"], "Tottenham Hale": ["victoria"],
    "Fulham Broadway": ["district"], "West Ham": ["district", "hammersmith-city", "jubilee"],
}


def trend(a: float | None, b: float | None) -> str:
    if not a or not b or abs(b - a) < 1e-9:
        return "flat"
    return "up" if b > a else "down"


def pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def signed(v: float, dp: int = 1) -> str:
    return f"{v:+.{dp}f}"


# ---------------------------------------------------------------- hubs vs usual


def hub_rows(store: Store, spots: list[dict], now: int) -> dict | None:
    last = store.latest("crowding", now)
    if not last:
        return None
    ts, snap = last
    rows = []
    for h in spots:
        nap, typ = h["naptan"], store.typical(h["naptan"], ts)
        if nap not in snap or not typ:
            continue
        curve = [{"at": now + k * 5 * MIN_MS, "typical": store.typical(nap, now + k * 5 * MIN_MS)} for k in range(4)]
        rows.append({"id": h["id"], "name": h["short"], "live": round(snap[nap], 4), "typical": round(typ, 4),
                     "vsUsual": round(snap[nap] / typ, 4), "typicalNext15": curve,
                     "usualTrend": trend(curve[0]["typical"], curve[-1]["typical"]),
                     "furthestBelow": False, "leader": False})
    if not rows:
        return None
    low = min(rows, key=lambda r: r["vsUsual"])
    top = max(rows, key=lambda r: r["vsUsual"])
    low["furthestBelow"], top["leader"] = True, True
    day = day_name(ts)
    text = (" · ".join(f"{SHORT.get(r['id'], r['name'])} {pct(r['vsUsual'])}" for r in rows) +
            f" of a usual {day} · furthest below: {low['name']}")
    return {"day": day, "asOf": ts, "laggard": low["id"], "leader": top["id"], "hubs": rows, "text": text}


def vs_usual_signal(store: Store, spots: list[dict], now: int) -> dict | None:
    got = hub_rows(store, spots, now)
    if not got:
        return None
    text = got.pop("text")
    return {"id": "vs_usual", "text": text, "data": got}


def usual_curve_signal(hubs: dict) -> dict:
    row = next(r for r in hubs["hubs"] if r["id"] == hubs["laggard"])
    a, b = row["typicalNext15"][0]["typical"], row["typicalNext15"][-1]["typical"]
    word = {"up": "rises", "down": "falls", "flat": "is flat"}[row["usualTrend"]]
    return {"id": "usual_curve",
            "text": f"{row['name']} {pct(row['vsUsual'])} of a usual {hubs['day']} · usual curve {word} next 15 min "
                    f"{ARROW[row['usualTrend']]}",
            "data": {"hub": row["id"], "name": row["name"], "vsUsual": row["vsUsual"], "typicalNow": a,
                     "typicalIn15": b, "usualTrend": row["usualTrend"]}}


# ---------------------------------------------------------------- football


def fixture_hub(fx: dict, spots: list[dict]) -> str | None:
    """The hub sharing the most Tube lines with the ground's stations (ties: fixed hub order)."""
    lines = {ln for st in fx.get("stations") or [] for ln in STATION_LINES.get(st, [])}
    best = max(spots, key=lambda h: len(lines & set(h["lines"])), default=None)
    return best["id"] if best and lines & set(best["lines"]) else None


def latest_fixture(store: Store, now: int) -> dict | None:
    """The most recent London fixture that has kicked off (in play or finished), newest kickoff first."""
    last = store.latest("scrape_fixtures", now)
    if not last:
        return None
    started = []
    for fx in last[1]["fixtures"]:
        try:
            ko = feeds.parse_ts(fx["kickoff_utc"])
            egress = feeds.parse_ts(fx["egress_est_utc"]) if fx.get("egress_est_utc") else None
        except (KeyError, TypeError, ValueError):
            continue
        if ko <= now and fx.get("status") not in (None, "NS"):
            started.append((ko, egress, fx))
    if not started:
        return None
    ko, egress, fx = max(started, key=lambda x: x[0])
    return dict(fx, kickoffMs=ko, egressMs=egress, asOf=last[0])


def football(store: Store, spots: list[dict], now: int) -> dict | None:
    fx = latest_fixture(store, now)
    if not fx:
        return None
    by_id = {h["id"]: h for h in spots}
    score = f"{fx['home_score']}-{fx['away_score']}" if fx.get("home_score") is not None else "0-0"
    hub = fixture_hub(fx, spots)
    status = fx.get("status") or ""
    text = f"{fx['home']} {score} {fx['away']} · {status}"
    if status == "FT":
        heading = " / ".join((fx.get("stations") or [])[:2] + ([by_id[hub]["short"]] if hub else []))
        text += f" · fans heading for {heading}"
    return {"home": fx["home"], "away": fx["away"], "score": score, "status": status, "venue": fx.get("venue"),
            "kickoff": fx["kickoffMs"], "egressEst": fx["egressMs"], "stations": fx.get("stations") or [],
            "hub": hub, "asOf": fx["asOf"], "text": text}


def fixture_signal(store: Store, spots: list[dict], now: int) -> dict | None:
    """A final whistle in the last 90 min near a hub's lines (the Arsenal → King's Cross story)."""
    fb = football(store, spots, now)
    if not fb or not fb["egressEst"] or not 0 <= now - fb["egressEst"] <= FIXTURE_WINDOW_MS or not fb["hub"]:
        return None
    mins = round((now - fb["egressEst"]) / MIN_MS)
    data = {k: fb[k] for k in ("home", "away", "score", "status", "egressEst", "stations", "hub")}
    return {"id": "fixture", "text": f"{fb['home']} {fb['score']} {fb['away']} ended ~{local_time(fb['egressEst'])} "
                                     f"({mins} min ago) · fans heading for {SHORT_NAME.get(fb['hub'], fb['hub'])}",
            "data": data | {"minutesSinceEgress": mins}}


# ---------------------------------------------------------------- City Pulse, VIX, stacks, lines


def pulse_signal(store: Store, now: int) -> dict | None:
    hist = [(ts, s) for ts, s in store.between("crowding", now - 15 * MIN_MS, now) if len(s) >= 18]
    if not hist:
        return None
    ts, snap = hist[-1]
    pulse = 100 * statistics.fmean(snap.values())
    typ_now = [t for nap in snap if (t := store.typical(nap, ts))]
    typ_15 = [t for nap in snap if (t := store.typical(nap, now + 15 * MIN_MS))]
    usual = 100 * statistics.fmean(typ_now) if typ_now else None
    usual15 = 100 * statistics.fmean(typ_15) if typ_15 else None
    prev = next((s for t, s in reversed(hist[:-1]) if ts - t >= 4 * MIN_MS), None)
    change = round(pulse - 100 * statistics.fmean(prev.values()), 2) if prev else None
    tr = trend(usual, usual15)
    text = f"City Pulse {pulse:.1f}"
    if usual:
        word = {"up": "rising", "down": "falling", "flat": "flat"}[tr]
        text += f" · a usual {day_name(ts)} {usual:.1f} now, {word} next 15 min {ARROW[tr]}"
    if change is not None:
        text += f" · last 5 min {signed(change)}"
    return {"id": "pulse", "text": text,
            "data": {"pulse": round(pulse, 2), "usual": round(usual, 2) if usual else None,
                     "typicalIn15": round(usual15, 2) if usual15 else None, "usualTrend": tr, "change5": change,
                     "asOf": ts}}


def change_over(series: list[tuple[int, float]], minutes: int) -> float | None:
    if not series:
        return None
    ts, v = series[-1]
    prev = next((x for t, x in reversed(series) if ts - t >= minutes * MIN_MS), None)
    return None if prev is None else v - prev


def vix_signal(store: Store, now: int) -> dict | None:
    hist = [(ts, s) for ts, s in store.between("vix", now - 15 * MIN_MS, now)]
    if not hist:
        return None
    ts, snap = hist[-1]
    change = change_over([(t, s["vix"]) for t, s in hist], 5)
    driver = snap.get("driver") or "?"
    text = f"VIX {snap['vix']:.2f} · moved most by: {driver} ({signed(snap.get('driverDelta') or 0.0, 2)})"
    if change is not None:
        text += f" · last 5 min {signed(change, 2)}"
    return {"id": "vix_driver", "text": text,
            "data": {"vix": snap["vix"], "driver": driver, "driverDelta": snap.get("driverDelta") or 0.0,
                     "change5": None if change is None else round(change, 2), "asOf": ts}}


def stacks_signal(store: Store, now: int) -> dict | None:
    hist = [(ts, s) for ts, s in store.between("aircraft", now - 30 * MIN_MS, now)]
    if not hist:
        return None
    ts, snap = hist[-1]
    avg30 = statistics.fmean(s["total"] for _, s in hist)
    change = change_over([(t, float(s["total"])) for t, s in hist], 10)
    stacks = " · ".join(f"{k} {v}" for k, v in snap["stacks"].items())
    text = f"Stacks {snap['total']} ({stacks}) · 30-min avg {avg30:.1f}"
    if change is not None:
        text += f" · {ARROW[trend(1.0, 1.0 + change)]} vs 10 min ago ({signed(change, 0)})"
    return {"id": "stacks", "text": text,
            "data": {"total": snap["total"], "stacks": snap["stacks"], "avg30": round(avg30, 2),
                     "change10": None if change is None else int(change), "asOf": ts}}


def line_board(store: Store, now: int) -> dict | None:
    hist = store.between("line_status", now - WINDOW_MS, now)
    if not hist:
        return None
    ts, snap = hist[-1]
    tube = {lid: ln for lid, ln in snap.items() if ln.get("mode") == "tube"}

    def since(lid: str) -> int | None:
        for t, s in reversed(hist):
            if lid in s and s[lid]["sev"] != snap[lid]["sev"]:
                return round((ts - t) / MIN_MS)
        return None

    disrupted = [{"id": lid, "name": ln["name"], "sev": ln["sev"], "desc": ln["desc"], "reason": ln.get("reason", ""),
                  "sinceMin": since(lid)} for lid, ln in tube.items() if ln["sev"] != 10]
    changes = [m for m in (since(lid) for lid in tube) if m is not None]
    return {"good": len(tube) - len(disrupted), "of": len(tube), "disrupted": disrupted,
            "sinceLastChangeMin": min(changes) if changes else None, "asOf": ts}


def short_reason(reason: str) -> str:
    text = reason.split(":", 1)[-1].strip()
    return text[:90] + ("…" if len(text) > 90 else "")


def lines_text(board: dict) -> str:
    text = f"{board['good']} of {board['of']} Tube lines Good"
    for d in board["disrupted"][:2]:
        since = f" {d['sinceMin']} min" if d["sinceMin"] is not None else ""
        text += f" · {d['name']}: {d['desc']}{since}" + (f" ({short_reason(d['reason'])})" if d["reason"] else "")
    return text


def lines_signal(store: Store, now: int) -> dict | None:
    board = line_board(store, now)
    return {"id": "lines", "text": lines_text(board), "data": board} if board else None


# ---------------------------------------------------------------- per market + the strip


def market(m, store: Store, now: int) -> list[dict]:
    """The 1–2 clues shown next to market `m` (see the module docstring for the shapes)."""
    if m.spec.get("urban"):
        import urban
        return urban.clue(m)
    spots = [h for h in SPOTS if h["id"] in m.spec.get("stations", {})]
    out: list[dict | None] = []
    if m.sub in ("hub_surge", "hub_hour", "oxford-busy"):
        hubs = vs_usual_signal(store, spots, now)
        out.append(hubs)
        if hubs and m.sub == "hub_surge":
            out.append(usual_curve_signal(hubs["data"]))
        if m.sub == "hub_hour":
            out.append(fixture_signal(store, SPOTS, now))
    elif m.sub == "city_pulse":
        out.append(pulse_signal(store, now))
    elif m.sub == "london_vix":
        out.append(vix_signal(store, now))
    elif m.sub == "heathrow_stack":
        out.append(stacks_signal(store, now))
    elif m.sub == "clean_sheet":
        out.append(lines_signal(store, now))
    return [s for s in out if s][:2]


def strip(store: Store, spots: list[dict], now: int) -> dict:
    """GET /signals: the 5-hub vs-usual strip (laggard flagged), the Tube line board with reasons, the latest
    football result, the London VIX line when the multiverse feed is in."""
    hubs = hub_rows(store, spots, now)
    board = line_board(store, now)
    vix = vix_signal(store, now)
    return {"now": now, "hubs": hubs,
            "lines": (board | {"text": lines_text(board)}) if board else None,
            "football": football(store, spots, now),
            "vix": (vix["data"] | {"text": vix["text"]}) if vix else None}
