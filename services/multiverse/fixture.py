"""Synthetic Oxford Circus fixture: a seed and a recorded-format observation stream, no network.

The street grid is synthetic (80 m blocks); the anchors and camera names are real Oxford Circus
places placed at their real offsets from the origin. Observations come from a hidden "true" world
(TRUE_ASSUMPTIONS) plus Poisson noise, one frame a minute for FRAMES minutes, written as recorder NDJSON
so replay can prove that pruning converges on the world that produced the data. The truth is rolled
into a new five-minute epoch exactly as the engine does (re-seeded from the last frame's counts).

Alternative-data feeds are written in the recorder's own payload shapes, generated from the truth plus
noise. Informative: rain_now (its 15-min forecast shows the rain ~3 min before it starts), bikepoint (docks
empty at a rate set by movingShare), crowding (Oxford Circus vs typical = touristFactor). Pure noise:
aircraft, grid, news, wiki_views. So the eval can check the market finds which data has alpha.

    python3 services/multiverse/fixture.py   # regenerates services/multiverse/fixtures/
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import engine
import worlds
from worlds import seedmod

ORIGIN = (51.5152, -0.1419)
AREA = "Oxford Circus"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
SEED_PATH = FIXTURE_DIR / "seed.json"
RAW_DIR = FIXTURE_DIR / "raw"
SOURCE = "jamcam_counts"
START = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
HORIZON = 5
OBS_EVERY_MIN = 1
FRAMES = 10
CRN_SEED = 47
BLOCK_M = 80
HALF = 6

ANCHORS = [
    ("Oxford Circus station", 51.5152, -0.1419, 1.0, "station"),
    ("Bond Street station", 51.5142, -0.1494, 0.8, "station"),
    ("Tottenham Court Road station", 51.5165, -0.1310, 0.8, "station"),
    ("Piccadilly Circus station", 51.5100, -0.1347, 0.7, "station"),
    ("Liberty", 51.5139, -0.1400, 0.7, "poi"),
    ("Carnaby Street", 51.5131, -0.1388, 0.9, "poi"),
    ("London Palladium", 51.5141, -0.1403, 0.5, "poi"),
    ("John Lewis Oxford Street", 51.5155, -0.1446, 0.8, "poi"),
    ("Cavendish Square", 51.5166, -0.1448, 0.3, "poi"),
    ("Hanover Square offices", 51.5139, -0.1437, 0.6, "poi"),
    ("Soho Square", 51.5153, -0.1322, 0.4, "poi"),
    ("Great Portland Street offices", 51.5180, -0.1416, 0.5, "poi"),
]
CAMS = [
    ("JamCams_00001.01001", "Oxford St / Regent St", 0, 0, 34),
    ("JamCams_00001.01002", "Oxford St / Holles St", -240, 0, 22),
    ("JamCams_00001.01003", "Oxford St / Poland St", 160, 0, 18),
    ("JamCams_00001.01004", "Regent St / Great Marlborough St", 0, 160, 16),
    ("JamCams_00001.01005", "Regent St / Conduit St", 0, 320, 11),
    ("JamCams_00001.01006", "Oxford St / Duke St", -400, 0, 14),
    ("JamCams_00001.01007", "Great Portland St / Margaret St", 0, -240, 6),
    ("JamCams_00001.01008", "Oxford St / Dean St", 400, 0, 12),
]
BUS_STOPS = [("Oxford Circus Stn (OE)", 40, 8, ["7", "98", "390"]), ("Regent St (RB)", 8, 120, ["88", "453"])]
TRUE_ASSUMPTIONS = {"movingShare": 0.78, "touristFactor": 1.5, "capacityScale": 0.8, "alightPerBus": 10,
                    "busDelayMin": 4.0, "walkPace": 0.95, "rainAtMin": 3.0, "incident": None, "events": []}


def kind_for(fixed: int, axis: str) -> str:
    if fixed == 0:
        return "major"
    return "path" if abs(fixed) == HALF * BLOCK_M else "minor"


def grid_segments() -> list[dict]:
    segs = []
    coords = [i * BLOCK_M for i in range(-HALF, HALF + 1)]
    for fixed in coords:
        for a, b in zip(coords, coords[1:]):
            for axis, line in (("ew", [[a, fixed], [b, fixed]]), ("ns", [[fixed, a], [fixed, b]])):
                segs.append({"id": f"{axis}{fixed}:{a}", "a": line[0], "b": line[1], "line": line,
                             "kind": kind_for(fixed, axis), "lengthM": float(BLOCK_M), "peds": 0.0,
                             "vehicles": 6.0 if fixed == 0 else 2.0, "source": "inferred", "confidence": 0.1})
    return segs


def build_seed(at: datetime = START) -> dict:
    segments = grid_segments()
    cams = [{"id": cid, "name": name, "x": float(x), "z": float(z), "people": p, "vehicles": 4,
             "at": int(at.timestamp() * 1000)} for cid, name, x, z, p in CAMS]
    seedmod.attach_cam_loads(segments, cams)
    anchors = []
    for name, lat, lon, weight, kind in ANCHORS:
        x, z = seedmod.to_local(lat, lon, ORIGIN)
        anchors.append({"name": name, "x": round(x, 1), "z": round(z, 1), "weight": weight, "kind": kind})
    field = {"segments": segments, "cams": cams}
    zones = seedmod.build_zones(field, anchors)
    buses = [{"stop": stop, "indicator": None, "x": float(x), "z": float(z),
              "arrivalsSec": [60 * m for m in (2, 6, 11, 15, 19, 24, 28)], "lines": lines}
             for stop, x, z, lines in BUS_STOPS]
    return {"at": int(at.timestamp() * 1000), "source": "fixture(synthetic grid)",
            "time": seedmod.london_time(at.timestamp()), "weather": {"kind": "cloudy", "precipitationMm": 0.0},
            "origin": {"lat": ORIGIN[0], "lon": ORIGIN[1]}, "area": AREA, "disruptions": [], "buses": buses,
            "zones": zones, "segments": segments, "anchors": anchors, "fieldPoiCount": 0, "cams": cams,
            "interventions": [], "assumptions": ["Synthetic 80 m street grid; anchors are real Oxford Circus places."]}


ALT_NOISE_SEED = 77
# Synthetic bike stations within 800 m of Oxford Circus.
BIKE_STATIONS = [(f"BikePoints_9{i:02d}", 51.5152 + 0.002 * math.cos(i), -0.1418 + 0.003 * math.sin(i)) for i in range(8)]
BIKES_START = 30
TYPICAL_CROWDING = 0.6
RAIN_FORECAST_SD_MIN = 0.7


def write(source: str, ts: datetime, payload: object, written: list[Path]) -> None:
    path = RAW_DIR / source / ts.strftime("%Y-%m-%d") / f"{ts.strftime('%H')}.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if path in written else "w") as f:
        f.write(json.dumps({"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source, "payload": payload}) + "\n")
    if path not in written:
        written.append(path)


def alt_payloads(minute: int, ts: datetime, rng: random.Random, bikes: dict[str, int]) -> dict[str, object]:
    t = TRUE_ASSUMPTIONS
    onset = START + timedelta(minutes=t["rainAtMin"])
    raining = ts >= onset
    seen_onset = onset + timedelta(minutes=rng.gauss(0, RAIN_FORECAST_SD_MIN))
    steps = [{"time": (seen_onset - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M"), "precipitation": 0.0, "rain": 0.0},
             {"time": seen_onset.strftime("%Y-%m-%dT%H:%M"), "precipitation": 0.8, "rain": 0.8}]
    rain = {"current": {"time": ts.strftime("%Y-%m-%dT%H:%M"), "precipitation": 0.6 if raining else 0.0,
                        "rain": 0.6 if raining else 0.0, "cloud_cover": 95, "weather_code": 61 if raining else 3},
            "minutely_15": steps}
    taken = max(0, round(rng.gauss(BIKES_PER_MIN_TRUE * t["movingShare"], 1.5)))
    for _ in range(taken):
        sid = rng.choice(list(bikes))
        bikes[sid] = max(0, bikes[sid] - 1)
    bikepoint = [{"id": sid, "lat": lat, "lon": lon, "NbBikes": str(bikes[sid]), "NbEBikes": "0",
                  "NbEmptyDocks": str(BIKES_START + 10 - bikes[sid]), "modified": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}
                 for sid, lat, lon in BIKE_STATIONS]
    crowding = [{"naptan": "940GZZLUOXC", "name": "Oxford Circus", "dataAvailable": True,
                 "percentageOfBaseline": round(TYPICAL_CROWDING * t["touristFactor"] * (1 + rng.gauss(0, 0.05)), 4),
                 "timeUtc": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")}]
    holding = {k: max(0, round(rng.gauss(3, 2))) for k in ("BNN", "LAM", "BIG", "OCK")}
    aircraft = {"summary": {"count": 120, "airborne": 70, "ground": 50, "below_8000ft": max(0, round(rng.gauss(30, 6))),
                            "holding": holding, "holding_total": sum(holding.values())}, "aircraft": []}
    demand = round(rng.gauss(30_000, 3_000))
    grid = {"fuelinst": {"startTime": ts.strftime("%Y-%m-%dT%H:%M:00Z"), "total_mw": demand},
            "demand": {"startTime": ts.strftime("%Y-%m-%dT%H:%M:00Z"), "demand": demand}}
    news = {"file": f"{ts.strftime('%Y%m%d%H%M%S')}.export.CSV.zip", "scanned": 1000,
            "events": [{"GLOBALEVENTID": str(minute * 100 + i), "EventCode": rng.choice(["010", "014", "141", "190"]),
                        "NumMentions": rng.randint(1, 20), "AvgTone": round(rng.gauss(-2, 3), 2)}
                       for i in range(rng.randint(0, 6))]}
    day = START.date()
    wiki = [{"title": title, "views": {(day - timedelta(days=d)).isoformat(): max(1, round(rng.gauss(300, 90)))
                                      for d in range(1, 15)}} for title in ("Oxford Street", "Oxford Circus", "Regent Street")]
    return {"rain_now": rain, "bikepoint": bikepoint, "crowding": crowding, "aircraft": aircraft, "grid": grid,
            "news": news, "wiki_views": wiki}


BIKES_PER_MIN_TRUE = 20.0


def profile_payload() -> dict:
    day = START.strftime("%a").upper()
    bands = [{"timeBand": f"{h:02d}:{m:02d}-x", "percentageOfBaseLine": TYPICAL_CROWDING} for h in range(24) for m in (0, 15, 30, 45)]
    return {"name": "Oxford Circus", "naptan": "940GZZLUOXC", "daysOfWeek": [{"dayOfWeek": day, "timeBands": bands}]}


def write_observations(seed: dict) -> list[Path]:
    """FRAMES one-minute frames from the true world, rolled into a new epoch every HORIZON minutes."""
    rng = random.Random(2026)
    alt_rng = random.Random(ALT_NOISE_SEED)
    written: list[Path] = []
    for path in RAW_DIR.glob("*/*/*.ndjson"):
        path.unlink()
    write("crowding_profile", START - timedelta(minutes=5), profile_payload(), written)
    bikes = {sid: BIKES_START for sid, _, _ in BIKE_STATIONS}
    for source, payload in alt_payloads(0, START, alt_rng, bikes).items():
        write(source, START, payload, written)
    epoch_seed, truth, epoch_start = seed, TRUE_ASSUMPTIONS, START
    run = worlds.run_world(epoch_seed, truth, HORIZON, CRN_SEED)
    last: tuple[datetime, dict] | None = None
    for minute in range(OBS_EVERY_MIN, FRAMES + 1, OBS_EVERY_MIN):
        ts = START + timedelta(minutes=minute)
        if ts > epoch_start + timedelta(minutes=HORIZON) and last:
            elapsed = (last[0] - epoch_start).total_seconds() / 60
            epoch_seed = engine.reseed(epoch_seed, int(last[0].timestamp() * 1000), last[1])
            truth = worlds.shift_assumptions(truth, elapsed)
            epoch_start = last[0]
            run = worlds.run_world(epoch_seed, truth, HORIZON, CRN_SEED)
        m = round((ts - epoch_start).total_seconds() / 60)
        counts = {cid: poisson(rng, series[m]) for cid, series in run["cams"].items()}
        readings = [{"camId": cid, "people": c, "vehicles": 4, "at": int(ts.timestamp() * 1000)} for cid, c in counts.items()]
        bus = {"meanDelayMin": round(TRUE_ASSUMPTIONS["busDelayMin"] + rng.gauss(0, 0.8), 1), "buses": 12}
        write(SOURCE, ts, {"readings": readings}, written)
        write("bus_delay", ts, bus, written)
        for source, payload in alt_payloads(minute, ts, alt_rng, bikes).items():
            write(source, ts, payload, written)
        last = (ts, {cid: float(c) for cid, c in counts.items()})
    return written


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    # Knuth is fine for frame counts (lambda < ~60).
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    seed = build_seed()
    SEED_PATH.write_text(json.dumps(seed))
    paths = write_observations(seed)
    print(f"seed: {SEED_PATH} ({len(seed['segments'])} segments, {len(seed['zones'])} zones, {len(seed['cams'])} cams)")
    for p in paths:
        print(f"obs: {p}")


if __name__ == "__main__":
    main()
