"""Read recorded data back.

    from recorder.load import records
    for rec in records("bus", "2026-11-07T12:00:00Z", "2026-11-07T13:00:00Z"):
        print(rec["ts"], len(rec["payload"]))

    python3 recorder/load.py              # records per source
    python3 recorder/load.py bus          # records + time range for one source
"""

import gzip
import json
import sys
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def _parse(t: str | datetime | None) -> datetime | None:
    if t is None or isinstance(t, datetime):
        return t
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc)


def records(source: str, start=None, end=None, root: Path = RAW):
    """Yield {"ts", "source", "payload"} for `source` with start <= ts < end (ISO strings or datetimes)."""
    start, end = _parse(start), _parse(end)
    for path in sorted((root / source).glob("*/*.ndjson.gz")):
        hour = datetime.strptime(f"{path.parent.name} {path.name[:2]}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        if (start and hour + timedelta(hours=1) <= start) or (end and hour >= end):
            continue
        try:
            with gzip.open(path, "rt") as f:
                for line in f:
                    rec = json.loads(line)
                    ts = _parse(rec["ts"])
                    if (start is None or ts >= start) and (end is None or ts < end):
                        yield rec
        except (EOFError, zlib.error, json.JSONDecodeError, gzip.BadGzipFile):
            continue  # a kill mid-write truncates only the last member; earlier lines were yielded


def main() -> None:
    sources = sys.argv[1:] or sorted(p.name for p in RAW.iterdir() if p.is_dir())
    for src in sources:
        recs = list(records(src))
        span = f"{recs[0]['ts']} .. {recs[-1]['ts']}" if recs else "-"
        rows = sum(len(r["payload"]) if isinstance(r["payload"], list) else 1 for r in recs)
        print(f"{src:18} {len(recs):5} records {rows:8} rows  {span}")


if __name__ == "__main__":
    main()
