"""Run the real city Exchange over the recorded day (in-memory ledger, no network) and score its prices.

    python3 analysis/judge/replay_score.py <raw_dir> <start ISO> <end ISO> [step_s]

For every settled market: the model's probabilities at open and at close, the LMSR price at close, the winner.
Reports per series: n, Brier and log loss of the open model vs a uniform prior, calibration of every option
(favourite-longshot check), the AI traders' P&L, and the house's P&L from a naive 'back the open favourite'
bettor and a 'back the trader's pick' bettor (10 points each, through the real quote, without moving the book).
Read-only: never writes the recorder data.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import markets as mk  # noqa: E402
import pricing  # noqa: E402
from data import Store, load_hotspots  # noqa: E402
from exchange import Clock, Exchange  # noqa: E402
from ledger import Ledger  # noqa: E402


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    step = int(sys.argv[4]) * 1000 if len(sys.argv) > 4 else 30_000
    spots = load_hotspots()
    ex = Exchange(Store(raw, spots), Clock(start), spots, Ledger(":memory:"))
    probe: dict[str, dict] = {}
    t = start
    while t <= end:
        ex.tick(t)
        for m in ex.active():
            if m.id not in probe and ex.status(m, t) == "open":
                probe[m.id] = {"fav_quote": None}
                fav = max(m.open_model, key=m.open_model.get)
                shares, odds = None, None
                try:
                    shares, odds = ex.quote(m, fav, 10)
                except Exception:  # noqa: BLE001 - price too short
                    pass
                longs = {}
                for o, p in m.open_model.items():
                    if p < 0.10:
                        try:
                            longs[o] = ex.quote(m, o, 10)[1]
                        except Exception:  # noqa: BLE001
                            pass
                probe[m.id] = {"fav": fav, "fav_odds": odds, "longs": longs}
        t += step

    rows = defaultdict(list)
    for mid in ex.order:
        m = ex.markets[mid]
        if not m.result or m.result["winner"] is None:
            continue
        rows[m.sub].append(m)

    allopt = []
    skill: dict[str, tuple[float, float]] = {}
    print(f"replay {sys.argv[2]} -> {sys.argv[3]}, step {step // 1000}s, rounds {len(ex.rounds)}")
    print(f"{'series':15} {'n':>3} {'void':>4} {'Brier open':>10} {'Brier unif':>10} {'LL open':>8} {'LL unif':>8}"
          f" {'Brier close':>11} {'fav won':>8} {'mean fav':>8} {'favbet ROI':>10}")
    for s, ms_ in rows.items():
        k = len(ms_[0].options)
        bo = bu = lo = lu = bc = 0.0
        fw, fp, roi_num, roi_n = 0, 0.0, 0.0, 0
        for m in ms_:
            w = m.result["winner"]
            po, pc = m.open_model, (m.close_model or m.open_model)
            bo += sum((po[o] - (o == w)) ** 2 for o in po)
            bc += sum((pc[o] - (o == w)) ** 2 for o in pc)
            bu += sum((1 / k - (o == w)) ** 2 for o in po)
            lo += -math.log(max(1e-9, po[w]))
            lu += -math.log(1 / k)
            fav = max(po, key=po.get)
            fw += fav == w
            fp += po[fav]
            pr = probe.get(m.id) or {}
            if pr.get("fav_odds"):
                roi_n += 1
                roi_num += (pr["fav_odds"] if pr["fav"] == w else 0) - 1
            for o in po:
                allopt.append((s, po[o], o == w))
        n = len(ms_)
        diffs = [sum((1 / k - (o == m.result["winner"])) ** 2 - (m.open_model[o] - (o == m.result["winner"])) ** 2
                     for o in m.open_model) for m in ms_]
        mean_d = sum(diffs) / n
        se = (sum((d - mean_d) ** 2 for d in diffs) / max(1, n - 1) / n) ** 0.5
        skill[s] = (mean_d, se)
        void = ex.stats[s]["void"]
        roi = f"{roi_num / roi_n:+.1%}" if roi_n else "-"
        print(f"{s:15} {n:3d} {void:4d} {bo / n:10.3f} {bu / n:10.3f} {lo / n:8.3f} {lu / n:8.3f} {bc / n:11.3f}"
              f" {fw:3d}/{n:<4d} {fp / n:8.2f} {roi:>10}")

    print("\nBrier improvement of the open model over uniform, mean ± 1 SE (paired, per market):")
    for s, (d, se) in skill.items():
        print(f"    {s:15} {d:+.3f} ± {se:.3f}  (t = {d / se if se else 0:.1f})")

    print("\nall options, model prob at open vs hit rate (favourite-longshot check)")
    for lo_, hi in ((0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, 1.01)):
        b = [(p, y) for _, p, y in allopt if lo_ <= p < hi]
        if b:
            print(f"  {lo_:.1f}-{min(hi, 1):.1f}: n={len(b):3d} mean p={sum(p for p, _ in b) / len(b):.3f} "
                  f"hit={sum(y for _, y in b) / len(b):.3f}")

    ln, lw, lnet = 0, 0, 0.0
    by_s = defaultdict(lambda: [0, 0, 0.0])
    for s, ms_ in rows.items():
        for m in ms_:
            for o, odds in (probe.get(m.id) or {}).get("longs", {}).items():
                won = o == m.result["winner"]
                ln += 1
                lw += won
                lnet += (odds if won else 0) - 1
                r = by_s[s]
                r[0] += 1
                r[1] += won
                r[2] += (odds if won else 0) - 1
    if ln:
        print(f"\nback every option the model opens under 10% (1 point each at the real quote): n={ln} won={lw} "
              f"ROI={lnet / ln:+.1%}")
        for s, (n, w, net) in by_s.items():
            print(f"    {s:15} n={n:3d} won={w:3d} ROI={net / n:+.1%}")

    print("\nAI traders (stake 100 each, locked odds):")
    for tid, rec in ex.record.items():
        print(f"  {tid:11} won {rec['won']:3d} lost {rec['lost']:3d} points {rec['points']}")
    per = defaultdict(lambda: [0, 0, 0.0])
    for s, ms_ in rows.items():
        for m in ms_:
            for b in m.ai_bets:
                r = per[(b["trader"], s)]
                won = b["option"] == m.result["winner"]
                r[0] += 1
                r[1] += won
                r[2] += (b["stake"] * b["odds"] if won else 0) - b["stake"]
    print("  by series (bets, wins, net points, ROI per point staked):")
    for (tid, s), (n, w, net) in sorted(per.items()):
        print(f"    {tid:11} {s:15} n={n:3d} won={w:3d} net={net:+8.0f} ROI={net / (100 * n):+.1%}")
    st = ex.stats_json()
    print("\nopen favourite share 30-70%:", st["openFavourite"]["share30to70"], "n", st["openFavourite"]["n"],
          "rejected>85%:", st["openFavourite"]["rejectedOver85"])
    capped = sum(1 for i in ex.order if ex.markets[i].futures.ruled_out >= int(pricing.RULED_OUT_CAP * 50))
    print(f"markets that hit the 60% ruled-out cap (pruning stops): {capped}/{len(ex.order)}")


if __name__ == "__main__":
    main()
