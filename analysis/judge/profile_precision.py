"""How coarse is TfL's typical-day profile? Prints SAT evening values per station (read-only).

    python3 analysis/judge/profile_precision.py <raw_dir>
"""
import glob
import gzip
import json
import sys

raw = sys.argv[1]
f = sorted(glob.glob(f"{raw}/crowding_profile/*/*.ndjson*"))[-1]
op = gzip.open if f.endswith(".gz") else open
for line in op(f, "rt"):
    p = json.loads(line).get("payload") or {}
    for d in p.get("daysOfWeek") or []:
        if d.get("dayOfWeek") == "SAT":
            bands = {b["timeBand"][:5]: b.get("percentageOfBaseLine") for b in d.get("timeBands") or []}
            print(p.get("naptan"), [bands.get(k) for k in ("19:00", "19:30", "20:00", "20:30", "20:45", "21:00")])
