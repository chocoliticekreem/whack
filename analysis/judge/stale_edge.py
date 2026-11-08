"""Stale-quote edge: what a bot that reads TfL directly makes by betting before the exchange ingests a reading.

    python3 analysis/judge/stale_edge.py <raw_dir> <start ISO> <end ISO> [availability s, default 150]

The real Exchange replays the window (in-memory ledger, 30 s ticks). Whenever a tick ingests a new reading into an
open market, the bot compares the prices just before and just after. Knowing the reading early, it backs the option
whose price rose most, by 5%+ relative, 10 points a bet.

1. Quotes, no book impact. "Stale" is the quote before the tick, assuming TfL serves a reading from its own stamp
   (the worst case). "Realistic" keeps only the picks the bot could make: TfL serves a crowding reading `availability`
   s after its stamp (crowd_availability.py measured 105-375 s by station, median 150 s), every reading of the
   market's stations in the new poll must be public, the bet goes in after the tick before our poll (so the quote
   it takes is the one the tick then left), and it must clear the bet delay before our poll. adsb.lol is live, so a
   radar reading is public as soon as that tick. "After ingest" backs the same pick at the quote after the tick.
2. Through the exchange: the realistic picks placed with Exchange.bet at that earliest moment, once with the fill hold
   switched off (the old code: fill after the delay) and once as shipped (fill only after a poll of the feed taken
   after the bet). ROI of the fills; a bet refused or cancelled (halted market) has no fill.
Bootstrap 95% CIs. Read-only: never writes the recorder data.
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import integrity  # noqa: E402
import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402
from exchange import Clock, Exchange  # noqa: E402
from ledger import Ledger  # noqa: E402

STAKE, BOOT, STEP = 10, 2000, 30_000


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def odds(m, option: str) -> float | None:
    return pricing.locked_odds(m.book.quote(m.model(), option, STAKE), STAKE)


def ci(rois: list[float]) -> str:
    if not rois:
        return "-"
    rng = random.Random(0)
    boot = sorted(sum(rng.choice(rois) for _ in rois) / len(rois) for _ in range(BOOT))
    return f"{sum(rois) / len(rois):+.0%} [{boot[int(.025 * BOOT)]:+.0%}, {boot[int(.975 * BOOT)]:+.0%}]"


def public_at(store: Store, m, ts: int, avail_ms: int) -> int:
    """When the bot can first read everything the poll at ts brought for this market: radar any time (adsb.lol is
    live), crowding `avail_ms` after the newest TfL stamp of the market's stations."""
    if m.feed != "crowding":
        return 0
    naps = list(m.spec.get("stations", {}).values()) or list(store.latest("crowding", ts)[1])
    stamps = [s for n in naps if (s := store.tfl_stamp(ts, n))]
    return (max(stamps) if stamps else ts) + avail_ms


def exchange(raw: Path, start: int) -> Exchange:
    spots = load_hotspots()
    return Exchange(Store(raw, spots), Clock(start), spots, Ledger(":memory:"))


def find_picks(raw: Path, start: int, end: int, avail_ms: int) -> tuple[Exchange, dict]:
    """{market id: (option, stale odds, fresh odds, ingest ts, earliest bet time)} for each market's first 5%+ jump.
    The earliest bet time is when the reading is public, and never before the tick that listed the market or ingested
    its previous reading (the quote the bot takes must already be the stale one)."""
    ex = exchange(raw, start)
    picks: dict[str, tuple] = {}
    since: dict[str, tuple[int, int]] = {}  # market id: (last reading it observed, tick that observed it)
    t = start
    while t <= end:
        before = {m.id: (m.last_obs_ts, m.prices(), {o: odds(m, o) for o in m.option_ids()})
                  for m in ex.active() if ex.status(m, t) == "open" and m.id not in picks}
        ex.tick(t)
        for mid, (seen, pre, pre_odds) in before.items():
            m = ex.markets[mid]
            if m.last_obs_ts == seen or ex.status(m, t) != "open":
                continue
            post = m.prices()
            best = max(pre, key=lambda o: post[o] / pre[o])
            after = odds(m, best)
            if post[best] / pre[best] > 1.05 and pre_odds[best] and after:
                ts = m.last_obs_ts
                picks[mid] = (best, pre_odds[best], after, ts, max(public_at(ex.store, m, ts, avail_ms),
                                                                   since[mid][1] + 1))
        for m in ex.active():
            if m.id not in since or since[m.id][0] != m.last_obs_ts:
                since[m.id] = (m.last_obs_ts, t)
        t += STEP
    return ex, picks


def through_exchange(raw: Path, start: int, end: int, plan: dict, hold: bool) -> dict[str, float]:
    """Place each planned bet with Exchange.bet; {market id: odds it filled at} (unfilled bets are left out)."""
    ex = exchange(raw, start)
    bets = sorted((at, mid, option) for mid, (option, at) in plan.items())
    times = sorted({*range(start, end + 1, STEP)} | {at for at, _, _ in bets}
                   | {at + integrity.rule_for(mid.split("-")[0]).delay_s * 1000 + 1 for at, mid, _ in bets})
    placed, k = {}, 0
    old_rule = staticmethod(lambda store, m, o, now: now >= o["fillsAt"])
    due = integrity.Integrity.__dict__["due"] if hold else old_rule
    with mock.patch.object(integrity.Integrity, "due", due):
        for t in times:
            ex.tick(t)
            while k < len(bets) and bets[k][0] <= t:
                _, mid, option = bets[k]
                k += 1
                who = ex.ledger.open_account(f"bot-{mid}")
                try:
                    placed[mid] = ex.bet({"player": who, "market": mid, "option": option, "stake": STAKE,
                                          "idempotency_key": mid}, t)["betId"]
                except (PermissionError, ValueError, LookupError):
                    continue
    out = {}
    for mid, bid in placed.items():
        o = ex.integrity.orders[bid]
        if o["status"] == "filled":
            out[mid] = o["fill"]["odds"]
    return out


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    avail_ms = int(float(sys.argv[4]) * 1000) if len(sys.argv) > 4 else 150_000
    ex, picks = find_picks(raw, start, end, avail_ms)
    winners = {mid: ex.markets[mid].result["winner"] for mid in picks
               if ex.markets[mid].result and ex.markets[mid].result["winner"] is not None}
    plan = {}
    for mid, (best, _, _, ts, at) in picks.items():
        delay = integrity.rule_for(ex.markets[mid].sub).delay_s * 1000
        if mid in winners and at <= ts - delay - 1:
            plan[mid] = (best, at)
    old = through_exchange(raw, start, end, plan, hold=False)
    new = through_exchange(raw, start, end, plan, hold=True)

    rows = defaultdict(lambda: defaultdict(list))
    for mid, (best, stale, fresh, _, _) in picks.items():
        if mid not in winners:
            continue
        won, r = winners[mid] == best, rows[ex.markets[mid].sub]
        r["stale"].append(stale * won - 1)
        r["fresh"].append(fresh * won - 1)
        if mid in plan:
            r["real"].append(stale * won - 1)
            for name, fills in (("old", old), ("new", new)):
                if mid in fills:
                    r[name].append(fills[mid] * won - 1)
    print(f"availability: TfL serves a crowding reading {avail_ms // 1000} s after its stamp")
    print(f"{'series':15} {'bets':>4} {'worst case (stamp)':>22} {'after ingest':>22} {'real':>4} "
          f"{'realistic, quotes':>22} {'fills':>5} {'exchange, no hold':>22} {'fills':>5} {'exchange, hold':>22}")
    for s, r in sorted(rows.items()):
        print(f"{s:15} {len(r['stale']):4d} {ci(r['stale']):>22} {ci(r['fresh']):>22} {len(r['real']):4d} "
              f"{ci(r['real']):>22} {len(r['old']):5d} {ci(r['old']):>22} {len(r['new']):5d} {ci(r['new']):>22}")


if __name__ == "__main__":
    main()
