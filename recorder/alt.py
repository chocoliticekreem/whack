"""Alternative-data parsers for the recorder: aircraft, grid, rain, GDELT news, Wikipedia.

Pure functions (no network) so they can be tested against saved fixtures.
"""

import csv
import io
import json
import math
import zipfile
from datetime import datetime, timedelta

OXFORD_CIRCUS = (51.5152, -0.1418)
HEATHROW = (51.47, -0.45)
AIRCRAFT_URL = "https://api.adsb.lol/v2/lat/51.47/lon/-0.45/dist/40"

# Heathrow holding stacks: centre = the VOR the stack is built on (UK AIP, rounded); a hold
# racetrack stays within ~6 nm of its fix, so a 6 nm circle at or above 7000 ft approximates "holding".
HOLDING_STACKS = {
    "BNN": (51.7261, -0.5500),  # Bovingdon, NW
    "LAM": (51.6461, 0.1517),  # Lambourne, NE
    "BIG": (51.3311, 0.0347),  # Biggin Hill, SE
    "OCK": (51.3050, -0.4472),  # Ockham, SW
}
STACK_RADIUS_NM = 6.0
STACK_MIN_ALT_FT = 7000
LOW_ALT_FT = 8000
AIRCRAFT_KEYS = ("hex", "flight", "lat", "lon", "alt_baro", "gs", "track", "squawk")

NEWS_RADIUS_KM = 5.0
# GDELT 2.0 event export columns (61 tab-separated fields, no header).
GDELT_COLS = {"GLOBALEVENTID": 0, "SQLDATE": 1, "EventCode": 26, "NumMentions": 31, "AvgTone": 34,
              "ActionGeo_FullName": 52, "ActionGeo_CountryCode": 53, "ActionGeo_Lat": 56,
              "ActionGeo_Long": 57, "SOURCEURL": 60}

WIKI_WATCHLIST = [
    "Oxford Street", "Oxford Circus", "Oxford Circus tube station", "Regent Street", "Carnaby Street",
    "London Palladium", "Soho", "Bond Street", "Bond Street station", "Tottenham Court Road",
    "Tottenham Court Road station", "Liberty (department store)", "Hamleys", "Selfridges",
    "Piccadilly Circus", "Leicester Square", "Covent Garden", "Marble Arch", "Hyde Park, London",
    "Trafalgar Square", "Mayfair", "Marylebone", "Fitzrovia", "West End of London", "City of Westminster",
    "Elizabeth line", "Central line", "Bakerloo line", "Victoria line", "Jubilee line", "Northern line",
    "Piccadilly line", "Transport for London", "London Underground", "London Buses",
    "Heathrow Airport", "Piccadilly Circus tube station", "Metropolitan Police", "London", "Sadiq Khan",
]
WIKI_TITLES = frozenset(WIKI_WATCHLIST)
WIKI_EDIT_KEYS = ("title", "type", "timestamp", "minor", "bot", "comment")


def dist_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def dist_nm(a: tuple[float, float], b: tuple[float, float]) -> float:
    return dist_km(a, b) / 1.852


def parse_aircraft(doc: dict) -> dict:
    """adsb.lol /v2 response -> {"summary": {...}, "aircraft": [trimmed rows]}."""
    rows = []
    for ac in doc.get("ac", []):
        row = {k: ac.get(k) for k in AIRCRAFT_KEYS}
        if isinstance(row["flight"], str):
            row["flight"] = row["flight"].strip()
        rows.append(row)
    stacks = {name: 0 for name in HOLDING_STACKS}
    airborne = low = ground = 0
    for r in rows:
        alt = r["alt_baro"]
        if alt == "ground":
            ground += 1
            continue
        if not isinstance(alt, (int, float)):
            continue
        airborne += 1
        low += alt < LOW_ALT_FT
        if r["lat"] is None or r["lon"] is None or alt < STACK_MIN_ALT_FT:
            continue
        for name, fix in HOLDING_STACKS.items():
            if dist_nm((r["lat"], r["lon"]), fix) <= STACK_RADIUS_NM:
                stacks[name] += 1
    summary = {"count": len(rows), "airborne": airborne, "ground": ground, "below_8000ft": low,
               "holding": stacks, "holding_total": sum(stacks.values()), "now": doc.get("now")}
    return {"summary": summary, "aircraft": rows}


def latest_fuelinst(rows: list[dict]) -> dict | None:
    """FUELINST rows (several 5-min periods) -> the newest period as {startTime, fuel{type: MW}, total_mw}."""
    if not rows:
        return None
    start = max(r["startTime"] for r in rows)
    fuel = {r["fuelType"]: r["generation"] for r in rows if r["startTime"] == start}
    return {"startTime": start, "settlementPeriod": next(r.get("settlementPeriod") for r in rows
                                                         if r["startTime"] == start),
            "fuel": fuel, "total_mw": sum(v for v in fuel.values() if isinstance(v, (int, float)) and v > 0)}


def latest_demand(rows: list[dict]) -> dict | None:
    return max(rows, key=lambda r: r["startTime"]) if rows else None


def parse_carbon(doc: dict) -> dict | None:
    regions = doc.get("data") or []
    if not regions or not regions[0].get("data"):
        return None
    period = regions[0]["data"][0]
    return {"from": period.get("from"), "to": period.get("to"), **period.get("intensity", {}),
            "mix": {m["fuel"]: m["perc"] for m in period.get("generationmix", [])}}


def parse_rain(doc: dict) -> dict:
    m = doc.get("minutely_15", {})
    steps = [dict(zip(m.keys(), vals)) for vals in zip(*m.values())] if m else []
    return {"current": doc.get("current"), "minutely_15": steps}


def gdelt_export_url(lastupdate: str) -> str | None:
    """lastupdate.txt has three lines `<size> <md5> <url>`; we want the `.export.CSV.zip` one, over https."""
    for line in lastupdate.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2].endswith(".export.CSV.zip"):
            return parts[2].replace("http://", "https://", 1)
    return None


def gdelt_previous_url(url: str) -> str:
    """.../20261107131500.export.CSV.zip -> the 15-minute slot before it."""
    base, name = url.rsplit("/", 1)
    stamp = datetime.strptime(name[:14], "%Y%m%d%H%M%S") - timedelta(minutes=15)
    return f"{base}/{stamp.strftime('%Y%m%d%H%M%S')}{name[14:]}"


def parse_gdelt_export(zip_bytes: bytes, centre=OXFORD_CIRCUS,
                       radius_km: float = NEWS_RADIUS_KM) -> tuple[int, list[dict]]:
    """-> (rows scanned, events whose ActionGeo point is within radius_km of centre)."""
    out, scanned = [], 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        with z.open(z.namelist()[0]) as f:
            for row in csv.reader(io.TextIOWrapper(f, "utf-8", errors="replace"), delimiter="\t"):
                scanned += 1
                if len(row) < 61:
                    continue
                try:
                    lat, lon = float(row[56]), float(row[57])
                except ValueError:
                    continue
                if dist_km(centre, (lat, lon)) > radius_km:
                    continue
                ev = {k: row[i] for k, i in GDELT_COLS.items()}
                ev.update(ActionGeo_Lat=lat, ActionGeo_Long=lon, AvgTone=float(row[34] or 0),
                          NumMentions=int(row[31] or 0))
                out.append(ev)
    return scanned, out


def sse_events(lines):
    """Yield (id, data-string) per SSE event from an iterable of byte lines."""
    data, ev_id = [], None
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                yield ev_id, "\n".join(data)
            data, ev_id = [], None
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line.startswith("id:"):
            ev_id = line[3:].strip()
    if data:
        yield ev_id, "\n".join(data)


def event_dt(data: str) -> str | None:
    try:
        return (json.loads(data).get("meta") or {}).get("dt")
    except ValueError:
        return None


def match_wiki_edit(data: str, titles=WIKI_TITLES) -> dict | None:
    """recentchange JSON -> trimmed row if it is an enwiki edit/new page on the watchlist."""
    try:
        ev = json.loads(data)
    except ValueError:
        return None
    if ev.get("wiki") != "enwiki" or ev.get("type") not in ("edit", "new") or ev.get("title") not in titles:
        return None
    row = {k: ev.get(k) for k in WIKI_EDIT_KEYS}
    length, rev = ev.get("length") or {}, ev.get("revision") or {}
    row.update(dt=(ev.get("meta") or {}).get("dt"), len_old=length.get("old"), len_new=length.get("new"),
               rev_old=rev.get("old"), rev_new=rev.get("new"))
    return row


def parse_pageviews(doc: dict) -> dict[str, int]:
    """Wikimedia per-article daily -> {YYYY-MM-DD: views}."""
    return {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}-{i['timestamp'][6:8]}": i["views"]
            for i in doc.get("items", [])}
