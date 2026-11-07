"""Synthetic Oxford Circus fixture: a seed and a recorded-format observation stream, no network.

The street grid is synthetic (80 m blocks); the anchors and camera names are real Oxford Circus
places placed at their real offsets from the origin. Observations come from a hidden "true" world
(TRUE_ASSUMPTIONS) plus Poisson noise, written as recorder NDJSON so replay can prove that pruning
converges on the world that produced the data.

    python3 services/multiverse/fixture.py   # regenerates services/multiverse/fixtures/
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import worlds
from worlds import seedmod

ORIGIN = (51.5152, -0.1419)
AREA = "Oxford Circus"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
SEED_PATH = FIXTURE_DIR / "seed.json"
RAW_DIR = FIXTURE_DIR / "raw"
SOURCE = "jamcam_counts"
START = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
HORIZON = 30
OBS_EVERY_MIN = 5
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
                    "busDelayMin": 4.0, "walkPace": 0.95, "rainAtMin": 12.0, "incident": None, "events": []}


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


def write_observations(seed: dict) -> list[Path]:
    truth = worlds.run_world(seed, TRUE_ASSUMPTIONS, HORIZON, CRN_SEED)
    rng = random.Random(2026)
    written = []
    for minute in range(OBS_EVERY_MIN, HORIZON + 1, OBS_EVERY_MIN):
        ts = START + timedelta(minutes=minute)
        readings = [{"camId": cid, "people": poisson(rng, series[minute]), "vehicles": 4,
                     "at": int(ts.timestamp() * 1000)} for cid, series in truth["cams"].items()]
        bus = {"meanDelayMin": round(TRUE_ASSUMPTIONS["busDelayMin"] + rng.gauss(0, 0.8), 1), "buses": 12}
        for source, payload in ((SOURCE, {"readings": readings}), ("bus_delay", bus)):
            path = RAW_DIR / source / ts.strftime("%Y-%m-%d") / f"{ts.strftime('%H')}.ndjson"
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if path in written else "w"
            with path.open(mode) as f:
                f.write(json.dumps({"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": source, "payload": payload}) + "\n")
            if path not in written:
                written.append(path)
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
