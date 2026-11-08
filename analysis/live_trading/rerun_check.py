"""Checks for the re-run (RERUN.md) that report.py does not make:

1. Every settled crowding market (hub_surge, city_pulse) against the raw recorder files: which reading decided it,
   when TfL stamped it (timeUtc) and when the recorder first saw it, versus closesAt. A violation is a deciding
   reading TfL stamped at or before close, or one the recorder already had before close.
2. City Pulse at open: the favourite's price, and Brier at open per series.
3. Frozen prices: over/under markets whose price did not move over the last 60 s before close although the live
   value did, plus the first run's measure (exactly 20 futures alive at the last sample).

    python3 analysis/live_trading/rerun_check.py [--raw /Users/sonionring/orca/whack/data/raw]

Read-only. Uses services/city/data.Store only for TfL's typical-day profile.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from client import SCRATCH

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "city"))
from data import Store, load_hotspots  # noqa: E402


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def hm(t) -> str:
    return "-" if t is None else datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%H:%M:%S")


def table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return "\n".join(out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def polls(raw: Path, start: int, end: int) -> list[tuple[int, dict]]:
    """[(recorder fetch ms, {naptan: (fraction of baseline, TfL stamp ms)})] sorted by fetch time."""
    out = []
    for p in sorted((raw / "crowding").glob("*/*.ndjson*")):
        opener = gzip.open if p.suffix == ".gz" else open
        try:
            with opener(p, "rt") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    t = ms(rec["ts"])
                    if not start <= t <= end:
                        continue
                    snap = {r["naptan"]: (r["percentageOfBaseline"], ms(r["timeUtc"]))
                            for r in rec.get("payload") or []
                            if r.get("dataAvailable") and (r.get("percentageOfBaseline") or 0) > 0 and r.get("timeUtc")}
                    out.append((t, snap))
        except (OSError, EOFError):
            continue
    return sorted(out)


def first_seen(ps: list, nap: str, stamp: int) -> int:
    return min(t for t, s in ps if nap in s and s[nap][1] == stamp)


def after_close(ps: list, naps: list[str], closes: int) -> dict:
    """{naptan: (pct, stamp, fetch)} for the first reading TfL stamped after closes (independent of the server)."""
    got = {}
    for t, s in ps:
        if t <= closes:
            continue
        for n in naps:
            if n not in got and n in s and s[n][1] > closes:
                got[n] = (s[n][0], s[n][1], t)
    return got


def before_close(ps: list, closes: int) -> tuple[int, dict] | None:
    pre = [(t, s) for t, s in ps if t <= closes]
    return pre[-1] if pre else None


def check_settlements(markets: dict, ps: list, store: Store, naptan: dict) -> None:
    rows, viol = [], []
    for m in sorted(markets.values(), key=lambda m: (m["settlesAt"], m["id"])):
        if m["series"] not in ("hub_surge", "city_pulse") or not m.get("winner"):
            continue
        snap, closes = m.get("settlement") or {}, m["closesAt"]
        pre = before_close(ps, closes)
        if m["series"] == "city_pulse":
            first = next((s for t, s in ps if t > closes), {})
            naps = list(first)
            got = after_close(ps, naps, closes)
            v_after = round(100 * sum(x[0] for x in got.values()) / len(got), 2) if got else None
            v_pre = round(100 * sum(x[0] for x in pre[1].values()) / len(pre[1]), 2) if pre else None
            matches = "after" if v_after == snap.get("value") else "pre-close" if v_pre == snap.get("value") else "?"
            line = float(re.findall(r"(\d+(?:\.\d+)?)\?$", m["question"])[0])
            pre_side = None if v_pre is None else ("over" if v_pre > line else "under")
            detail = f"line {line}; pre-close {v_pre} ({pre_side}), settled {snap.get('value')} ({m['winner']})"
        else:
            naps = [naptan[h] for h in m["open"]]
            got = after_close(ps, naps, closes)
            vs_after = {n: x[0] / store.typical(n, x[1]) for n, x in got.items() if store.typical(n, x[1])}
            vs_pre = {n: pre[1][n][0] / store.typical(n, pre[1][n][1]) for n in naps
                      if pre and n in pre[1] and store.typical(n, pre[1][n][1])}
            vals = snap.get("values") or {}
            ok_after = all(abs(vals.get(h, -9) - vs_after.get(naptan[h], 9)) < 5e-4 for h in m["open"])
            ok_pre = all(abs(vals.get(h, -9) - vs_pre.get(naptan[h], 9)) < 5e-4 for h in m["open"])
            matches = "after" if ok_after else "pre-close" if ok_pre else "?"
            detail = (m.get("reading") or "")[:60]
        stamps = [x[1] for x in got.values()]
        seen = [first_seen(ps, n, x[1]) for n, x in got.items()]
        bad = [n for n, x in got.items() if x[1] <= closes or first_seen(ps, n, x[1]) <= closes]
        if matches != "after" or bad or len(got) < len(naps):
            viol.append((m["id"], matches, bad, len(got), len(naps)))
        rows.append([m["id"], hm(closes), m["winner"], f"{len(got)}/{len(naps)}",
                     f"{hm(min(stamps))}–{hm(max(stamps))}" if stamps else "-",
                     f"{(min(stamps) - closes) / 1000:+.0f}" if stamps else "-",
                     f"{(min(seen) - closes) / 1000:+.0f}" if seen else "-",
                     f"{(snap.get('fetchedAt', 0) - closes) / 1000:+.0f}", matches, detail])
    print("## 1. Deciding readings vs close (raw recorder files)\n")
    print(table(["market", "closes", "winner", "stations", "TfL timeUtc of deciding readings",
                 "earliest stamp − close (s)", "first seen by recorder − close (s)", "settle fetchedAt − close (s)",
                 "snapshot matches", "detail"], rows))
    print(f"\nViolations (deciding reading stamped ≤ close, seen before close, or snapshot not the after-close "
          f"reading): **{len(viol)}** of {len(rows)}. {viol}\n")


def check_open(markets: dict) -> None:
    rows, brier = [], defaultdict(lambda: [0, 0.0, 0.0])
    for m in sorted(markets.values(), key=lambda m: m["settlesAt"]):
        if m["series"] not in ("city_pulse", "hub_surge", "london_vix", "heathrow_stack"):
            continue
        if m.get("openLagMs") is None or m["openLagMs"] > 15_000:
            continue
        fav = max(m["open"], key=m["open"].get)
        if m["series"] == "city_pulse":
            rows.append([m["id"], f"{fav} {m['open'][fav]:.2f}", m.get("winner"),
                         "yes" if m.get("winner") == fav else ("-" if not m.get("winner") else "no"),
                         m["question"]])
        if m.get("winner"):
            k = len(m["open"])
            b = brier[m["series"]]
            b[0] += 1
            b[1] += sum((p - (o == m["winner"])) ** 2 for o, p in m["open"].items())
            b[2] += sum((1 / k - (o == m["winner"])) ** 2 for o in m["open"])
    print("## 2. City Pulse at open\n")
    print(table(["market", "favourite at open", "winner", "favourite won", "question"], rows))
    favs = sorted(float(r[1].split()[1]) for r in rows)
    if favs:
        print(f"\nFavourite at open: min {favs[0]:.2f}, median {favs[len(favs) // 2]:.2f}, max {favs[-1]:.2f}; "
              f"above 0.85: {sum(f > 0.85 for f in favs)} of {len(favs)}.\n")
    print(table(["series", "markets", "Brier at open", "Brier for a coin"],
                [[s, v[0], f"{v[1] / v[0]:.3f}", f"{v[2] / v[0]:.3f}"] for s, v in sorted(brier.items())]))
    print()


def check_frozen(markets: dict) -> None:
    path = SCRATCH / "prices.jsonl"
    series: dict[str, list] = defaultdict(list)
    for line in open(path):
        r = json.loads(line)
        m = markets.get(r["m"])
        if m and m["series"] in ("london_vix", "city_pulse", "heathrow_stack") and r["t"] < m["closesAt"]:
            series[r["m"]].append(r)
    rows, frozen, alive20 = [], 0, 0
    for mid, rs in sorted(series.items()):
        m = markets[mid]
        last60 = [r for r in rs if r["t"] >= m["closesAt"] - 60_000 and r["status"] in ("open", "halted")]
        if not last60:
            continue
        prices = {round(r["p"].get("over", 0), 4) for r in last60}
        vals = {json.dumps(r["clue"], sort_keys=True) for r in last60}
        all_prices = [round(r["p"].get("over", 0), 4) for r in rs if r["status"] in ("open", "halted")]
        moves = sum(1 for a, b in zip(all_prices, all_prices[1:]) if a != b)
        is_frozen = len(prices) == 1 and len(vals) > 1
        frozen += is_frozen
        alive20 += last60[-1]["alive"] == 20
        rows.append([mid, len(rs), moves, len(prices), len(vals), last60[-1]["alive"],
                     f"{last60[-1]['p'].get('over', 0):.2f}", last60[-1]["clue"], m.get("winner"),
                     "**yes**" if is_frozen else "no"])
    print("## 3. Frozen prices before close\n")
    print(table(["market", "samples", "price moves (whole round)", "distinct prices last 60 s",
                 "distinct live values last 60 s", "futures alive at last sample", "Over price at last sample",
                 "live value", "winner", "frozen"], rows))
    print(f"\nFrozen in the last 60 s while the live value moved: **{frozen} of {len(rows)}**. Exactly 20 futures "
          f"alive at the last sample (first run's measure): {alive20} of {len(rows)}.\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="/Users/sonionring/orca/whack/data/raw")
    a = ap.parse_args()
    raw = Path(a.raw)
    markets = json.loads((SCRATCH / "state.json").read_text())["markets"]
    t0 = min(m["closesAt"] for m in markets.values()) - 3_600_000
    t1 = max(m["closesAt"] for m in markets.values()) + 3_600_000
    spots = load_hotspots()
    store = Store(raw, spots)
    store.load_profile()
    naptan = {h["id"]: h["naptan"] for h in spots}
    check_settlements(markets, polls(raw, t0, t1), store, naptan)
    check_open(markets)
    check_frozen(markets)


if __name__ == "__main__":
    main()
