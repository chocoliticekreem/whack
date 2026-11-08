"""Honest scoring of the city simulation against the statistical futures.

    python3 services/city/eval_sim.py record SIM_LOG          # poll SIM_URL every 30 s, append to SIM_LOG (ndjson)
    python3 services/city/eval_sim.py score SIM_LOG [--traders URL]

No recorded simulation history existed before this script, so `record` builds one forward from now. `score` replays
every 5-min round whose opening has a fresh simulation forecast in SIM_LOG (at most 3 min old) over the recorder's
data: each sim-informed series (hub_surge, city_pulse, hub_hour) is built twice from the same readings, once
statistical-only and once blended, then settled by its own rule. It reports Brier score, log loss and the favourite's
calibration for both, and Simaissance's P&L at statistical-only opening prices (stake 100, 5-point edge).
With --traders it also prints the live leaderboard of a running city server (every AI's points).
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import data  # noqa: E402
import markets as mk  # noqa: E402
import pricing  # noqa: E402
import traders  # noqa: E402

ROUND_MS = 5 * pricing.MIN_MS


def record(path: Path) -> None:
    feed = data.SimFeed()
    last_ts = None
    while True:
        if feed.poll() and feed.last["ts"] != last_ts:
            last_ts = feed.last["ts"]
            with path.open("a") as f:
                f.write(json.dumps(feed.last) + "\n")
            print(f"sim forecast at {data.local_time(last_ts)} ({len(feed.last['stations'])} stations)", flush=True)
        time.sleep(data.SIM_POLL_S)


SERIES = ("hub_surge", "city_pulse", "hub_hour")


def fixed_feed(snap: dict) -> data.SimFeed:
    feed = data.SimFeed(lambda: snap)
    feed.poll()
    return feed


def score_probs(probs: dict[str, float], winner: str) -> tuple[float, float]:
    """(Brier over every option, log loss of the winner)."""
    brier = sum((p - (o == winner)) ** 2 for o, p in probs.items())
    return brier, -math.log(max(1e-6, probs[winner]))


def sim_bet(m, store: data.Store, now: int, winner: str) -> dict | None:
    """Simaissance's bet at this market's opening prices and its P&L (stake 100), or None."""
    view = mk.sim_view(m, store, now)
    if not view:
        return None
    prices = m.prices()
    best = max(prices, key=lambda o: view["probs"][o] - prices[o])
    if view["probs"][best] - prices[best] < traders.SIM_EDGE:
        return None
    odds = pricing.locked_odds(m.book.quote(m.futures.probs(), best, traders.STAKE), traders.STAKE)
    if odds is None:
        return None
    return {"option": best, "odds": odds, "won": best == winner,
            "pnl": round(traders.STAKE * odds - traders.STAKE if best == winner else -traders.STAKE)}


def rounds(snaps: list[dict], now: int):
    """(settlesAt, opening time, the sim snapshot fresh at opening) for every round with one."""
    stamps = [s["ts"] for s in snaps]
    at = (stamps[0] // ROUND_MS + 1) * ROUND_MS
    while at - ROUND_MS - mk.CLOSE_MS <= now:
        opened = at - ROUND_MS - mk.CLOSE_MS
        i = bisect.bisect_right(stamps, opened) - 1
        if i >= 0 and opened - stamps[i] <= data.SIM_MAX_AGE_MS:
            yield at, opened, snaps[i]
        at += ROUND_MS


def build_pair(sid: str, store: data.Store, spots: list[dict], opened: int, at: int, snap: dict):
    series = mk.REGISTRY[sid]
    if series.due and not series.due(at):
        return None
    mid = f"{sid}-{data.local_time(at, '%H%M')}"
    store.sim_feed = None
    stat = series.build(mid, store, spots, opened, at)
    store.sim_feed = fixed_feed(snap)
    blend = series.build(mid, store, spots, opened, at)
    return (stat, blend) if stat and blend else None


def score(path: Path, traders_url: str | None) -> None:
    snaps = sorted((json.loads(line) for line in path.read_text().splitlines() if line.strip()), key=lambda s: s["ts"])
    if not snaps:
        sys.exit(f"no simulation forecasts in {path}")
    now = int(time.time() * 1000)
    spots = data.load_hotspots()
    store = data.Store(data.raw_root(), spots)
    store.refresh(snaps[0]["ts"] - mk.HISTORY_MS, now)
    rows, pending = [], 0
    for at, opened, snap in rounds(snaps, now):
        for sid in SERIES:
            pair = build_pair(sid, store, spots, opened, at, snap)
            if not pair:
                continue
            stat, blend = pair
            store.sim_feed = None
            got = mk.try_settle(stat, store, now)
            if got[0] == "wait":
                pending += 1
                continue
            if got[0] != "done":
                continue
            winner = got[1]
            store.sim_feed = fixed_feed(snap)
            view = mk.sim_view(stat, store, opened)
            rows.append({"market": stat.id, "winner": winner, "fromSim": blend.futures.from_sim,
                         "stat": stat.open_model, "blend": blend.open_model,
                         "simOnly": view["probs"] if view else None,
                         "betVsStat": sim_bet(stat, store, opened, winner),
                         "betVsBlend": sim_bet(blend, store, opened, winner)})
    report(rows, pending, snaps)
    if traders_url:
        leaderboard(traders_url)


def report(rows: list[dict], pending: int, snaps: list[dict]) -> None:
    print(f"simulation forecasts recorded: {len(snaps)}, {data.local_time(snaps[0]['ts'])}–"
          f"{data.local_time(snaps[-1]['ts'])} Europe/London; settled markets scored: {len(rows)}; "
          f"still waiting to settle: {pending}")
    for r in rows:
        bet = r["betVsStat"]
        b = f"{bet['option']} @{bet['odds']} {'won' if bet['won'] else 'lost'} {bet['pnl']:+d}" if bet else "no bet"
        print(f"  {r['market']:<18} winner {r['winner']:<14} P(winner) stat {r['stat'][r['winner']]:.2f} "
              f"blend {r['blend'][r['winner']]:.2f} sim {r['simOnly'][r['winner']] if r['simOnly'] else 'n/a'!s:.4} "
              f"| sim trader vs stat prices: {b}")
    for key in ("stat", "blend", "simOnly"):
        got = [score_probs(r[key], r["winner"]) for r in rows if r[key]]
        favs = [(max(r[key].values()), max(r[key], key=r[key].get) == r["winner"]) for r in rows if r[key]]
        if not got:
            continue
        print(f"{key:>8}: n {len(got)}  Brier {statistics.fmean(g[0] for g in got):.3f}  log loss "
              f"{statistics.fmean(g[1] for g in got):.3f}  favourite won {sum(w for _, w in favs)}/{len(favs)} at a "
              f"mean price of {statistics.fmean(p for p, _ in favs):.2f}")
    for key in ("betVsStat", "betVsBlend"):
        bets = [r[key] for r in rows if r[key]]
        print(f"Simaissance at {'statistical-only' if key == 'betVsStat' else 'blended'} opening prices: "
              f"{len(bets)} bets, {sum(b['won'] for b in bets)} won, P&L {sum(b['pnl'] for b in bets):+d} points")


def leaderboard(url: str) -> None:
    with urllib.request.urlopen(f"{url.rstrip('/')}/traders", timeout=5) as resp:
        rows = json.loads(resp.read())["traders"]
    print("live AI leaderboard (" + url + "):")
    for t in sorted(rows, key=lambda t: -t["points"]):
        print(f"  {t['name']:<16} {t['points']:>5} points  won {t['record']['won']} lost {t['record']['lost']}")


def main(argv: list[str]) -> None:
    if len(argv) >= 2 and argv[0] == "record":
        record(Path(argv[1]))
    elif len(argv) >= 2 and argv[0] == "score":
        score(Path(argv[1]), argv[3] if len(argv) >= 4 and argv[2] == "--traders" else None)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
