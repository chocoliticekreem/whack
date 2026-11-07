"""Courtsiding check: is the TfL crowding reading that settles a market already public before bets close?

    python3 analysis/judge/crowd_latency.py <raw_dir>

Settlement uses the first recorder poll whose *poll* timestamp is after bets close (close = settle - 60 s, rounds
every 5 min). TfL stamps each row with its own timeUtc. If timeUtc < close, anyone polling TfL directly saw the
settling number while bets were still open. Prints the lag between TfL's stamp and our poll, and how many 5-min
rounds would have settled on a number published before the close. Read-only.
"""
import glob
import gzip
import json
import statistics
import sys
from datetime import datetime

MIN = 60_000


def ms(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


raw = sys.argv[1]
polls = []
for f in sorted(glob.glob(f"{raw}/crowding/*/*.ndjson*")):
    op = gzip.open if f.endswith(".gz") else open
    with op(f, "rt") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            stamps = [ms(r["timeUtc"]) for r in rec.get("payload") or [] if r.get("timeUtc")]
            if stamps:
                polls.append((ms(rec["ts"]), max(stamps), min(stamps)))
polls.sort()
lags = [(p - newest) / 1000 for p, newest, _ in polls]
print(f"polls={len(polls)}  poll ts minus newest TfL timeUtc: median {statistics.median(lags):.0f}s, "
      f"p10 {sorted(lags)[len(lags) // 10]:.0f}s, p90 {sorted(lags)[9 * len(lags) // 10]:.0f}s, max {max(lags):.0f}s")
gaps = [(b[0] - a[0]) / 1000 for a, b in zip(polls, polls[1:])]
print(f"gap between polls: median {statistics.median(gaps):.0f}s, max {max(gaps):.0f}s")
early, n = 0, 0
t = polls[0][0] - polls[0][0] % (5 * MIN) + 5 * MIN
while t < polls[-1][0]:
    close = t - MIN
    nxt = next((p for p in polls if p[0] > close), None)
    if nxt and nxt[0] <= close + 6 * MIN:
        n += 1
        early += nxt[2] < close
    t += 5 * MIN
print(f"5-min rounds: {n}; settled on a reading whose TfL timeUtc (oldest station) is before bets closed: {early} "
      f"({early / max(1, n):.0%})")

# Per station: share of rounds whose settling row TfL stamped before bets closed.
rows_by_poll = {}
for f in sorted(glob.glob(f"{raw}/crowding/*/*.ndjson*")):
    op = gzip.open if f.endswith(".gz") else open
    with op(f, "rt") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rows_by_poll[ms(rec["ts"])] = [(r["naptan"], ms(r["timeUtc"])) for r in rec.get("payload") or []
                                           if r.get("timeUtc") and r.get("dataAvailable")]
poll_ts = sorted(rows_by_poll)
per: dict[str, list[int]] = {}
t = poll_ts[0] - poll_ts[0] % (5 * MIN) + 5 * MIN
while t < poll_ts[-1]:
    close = t - MIN
    p = next((x for x in poll_ts if x > close), None)
    if p and p <= close + 6 * MIN:
        for nap, stamp in rows_by_poll[p]:
            per.setdefault(nap, [0, 0])
            per[nap][0] += stamp < close
            per[nap][1] += 1
    t += 5 * MIN
hubs = {"940GZZLUOXC": "Oxford Circus", "940GZZLUKSX": "King's Cross", "940GZZLUPCC": "Piccadilly",
        "940GZZLUWLO": "Waterloo", "940GZZLUBNK": "Bank"}
stale = sum(a for a, _ in per.values())
total = sum(b for _, b in per.values())
print(f"station-rounds settled on a TfL number stamped before bets closed: {stale}/{total} = {stale / total:.0%}")
for nap, name in hubs.items():
    a, b = per.get(nap, [0, 0])
    print(f"  {name:14} {a}/{b}")
