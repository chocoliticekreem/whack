"""Stale-quote edge: what a bot that reads TfL directly makes by betting before the exchange ingests a reading.

    python3 analysis/judge/stale_edge.py <raw_dir> <start ISO> <end ISO>

The real Exchange replays the window (in-memory ledger, 30 s ticks). Whenever a tick ingests a new reading into an
open market, the bot compares the prices just before and just after. Knowing the reading early (TfL stamps it 4-7
min before the recorder polls it, crowd_latency.py), it backs the option whose price rose most, by 5%+ relative,
at the quote from before the tick: 10 points, no book impact. The control backs the same option at the quote after.
Prints both ROIs per series with a bootstrap 95% CI. Assumes TfL serves a reading from its own stamp, the worst case.
Read-only: never writes the recorder data.
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402
from exchange import Clock, Exchange  # noqa: E402
from ledger import Ledger  # noqa: E402

STAKE, BOOT = 10, 2000


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def odds(m, option: str) -> float | None:
    return pricing.locked_odds(m.book.quote(m.model(), option, STAKE), STAKE)


def ci(rois: list[float]) -> str:
    rng = random.Random(0)
    boot = sorted(sum(rng.choice(rois) for _ in rois) / len(rois) for _ in range(BOOT))
    return f"{sum(rois) / len(rois):+.0%} [{boot[int(.025 * BOOT)]:+.0%}, {boot[int(.975 * BOOT)]:+.0%}]"


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    spots = load_hotspots()
    ex = Exchange(Store(raw, spots), Clock(start), spots, Ledger(":memory:"))
    picks: dict[str, tuple[str, float, float]] = {}
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
                picks[mid] = (best, pre_odds[best], after)
        t += 30_000
    rows = defaultdict(lambda: ([], []))
    for mid, (best, stale, fresh) in picks.items():
        m = ex.markets[mid]
        if not m.result or m.result["winner"] is None:
            continue
        won = m.result["winner"] == best
        rows[m.sub][0].append(stale * won - 1)
        rows[m.sub][1].append(fresh * won - 1)
    print(f"{'series':15} {'bets':>4}  {'ROI at the stale quote':>26}  {'ROI after ingest':>26}")
    for s, (stale, fresh) in sorted(rows.items()):
        print(f"{s:15} {len(stale):4d}  {ci(stale):>26}  {ci(fresh):>26}")


if __name__ == "__main__":
    main()
