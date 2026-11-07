"""Read the recorder's raw files into (ts_ms, payload) lists. Stdlib only, read-only.

Root: $QUESTIONS_RAW, else the main checkout's data/raw (this file lives in a worktree or the main checkout).
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
MIN = 60_000


def raw_root() -> Path:
    env = os.environ.get("QUESTIONS_RAW")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "data" / "raw"
        if cand.is_dir() and any(cand.iterdir()):
            return cand
    return Path("/Users/sonionring/orca/whack/data/raw")


def parse_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def load(source: str, day: str = "2026-11-07") -> list[tuple[int, object]]:
    d = raw_root() / source / day
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.iterdir()):
        opener = gzip.open if f.suffix == ".gz" else open
        try:
            with opener(f, "rt") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        out.append((parse_ts(r["ts"]), r["payload"]))
                    except (ValueError, KeyError, TypeError):
                        continue
        except (OSError, EOFError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, LONDON).strftime("%H:%M")


def at(series: list[tuple[int, object]], t: int, max_age_ms: int = 3 * MIN):
    """Last value at or before t, if no older than max_age_ms."""
    best = None
    for ts, v in series:
        if ts > t:
            break
        best = (ts, v)
    if best and t - best[0] <= max_age_ms:
        return best[1]
    return None


def pctl(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else float("nan")


def dist(xs: list[float]) -> str:
    if not xs:
        return "n=0"
    return (f"n={len(xs)} p10={pctl(xs, .1):.2f} p50={pctl(xs, .5):.2f} p90={pctl(xs, .9):.2f} "
            f"in[0.3,0.7]={sum(1 for x in xs if 0.3 <= x <= 0.7) / len(xs):.0%}")
