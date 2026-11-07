"""Observations: recorded NDJSON (replay) or the twin's live routes (live).

Recorder format: data/raw/<source>/YYYY-MM-DD/HH.ndjson(.gz), one {"ts","source","payload"} per line.
Camera counts are read from any payload carrying `readings: [{camId|id, people}]` (the shape of the
YOLO /counts reply); the bus signal from `meanDelayMin`. Unknown sources are skipped, not guessed.
"""
from __future__ import annotations

import gzip
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parent.parent.parent
CAM_SOURCES = tuple(s for s in os.environ.get("MULTIVERSE_CAM_SOURCES", "jamcam_counts,jamcam_yolo,counts").split(",") if s)
BUS_SOURCES = tuple(s for s in os.environ.get("MULTIVERSE_BUS_SOURCES", "bus_delay").split(",") if s)


def parse_ts(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


def read_lines(path: Path) -> Iterator[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (OSError, EOFError):
        return


def readings_of(payload: Any) -> dict[str, float]:
    rows = payload.get("readings") if isinstance(payload, dict) else payload if isinstance(payload, list) else None
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cid = r.get("camId") or r.get("id")
        people = r.get("people")
        if cid and isinstance(people, (int, float)):
            out[str(cid)] = float(people)
    return out


def load_frames(raw_root: Path, start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
    """Merge cam and bus records into frames keyed by timestamp, oldest first."""
    frames: dict[int, dict] = {}
    for source in CAM_SOURCES + BUS_SOURCES:
        for path in sorted((raw_root / source).glob("*/*.ndjson*")):
            for rec in read_lines(path):
                try:
                    ts = parse_ts(rec["ts"])
                except (KeyError, ValueError, TypeError):
                    continue
                if (start_ms is not None and ts < start_ms) or (end_ms is not None and ts > end_ms):
                    continue
                frame = frames.setdefault(ts, {"ts": ts, "readings": {}, "busDelayMin": None})
                if source in CAM_SOURCES:
                    frame["readings"].update(readings_of(rec.get("payload")))
                else:
                    delay = (rec.get("payload") or {}).get("meanDelayMin")
                    if isinstance(delay, (int, float)):
                        frame["busDelayMin"] = float(delay)
    return [frames[k] for k in sorted(frames)]


def live_frame(twin_url: str, cam_ids: list[str]) -> dict:
    """One fresh frame from the twin's /api/around/counts (YOLO over TfL JamCams)."""
    url = f"{twin_url}/api/around/counts?ids={urllib.parse.quote(','.join(cam_ids))}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=60) as res:
        data = json.loads(res.read().decode("utf-8"))
    readings = data.get("readings") or []
    ts = max((int(r.get("at") or 0) for r in readings), default=0)
    return {"ts": ts or None, "readings": readings_of(readings), "busDelayMin": None}
