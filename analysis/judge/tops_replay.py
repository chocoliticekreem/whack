"""Out-of-sample check of the TOPS loop (services/city/tops.py) on the recorded data: the old loop against the new.

    python3 analysis/judge/tops_replay.py <raw_dir> <start ISO> <end ISO> [step_s] [split ISO ...]

Runs the real Exchange over the recorded data with an empty in-memory ledger and logs every TOPS open (the futures
prior and each trader's stance) and settle, in the order the Exchange made them. Each variant of the loop then walks
that log from an empty record, so every opening blend uses only markets settled before it opened (walk-forward, no
look-ahead) and every variant sees exactly the same markets, priors and stances (paired):

  old          softmax(shrunk score / 0.01) weights, lam = 0.3 S / (S + 0.05)    (PR #93)
  new          P(skill > 0) weights from the empirical-Bayes posterior, lam from the pool's record   (tops.py now)
  new-weights  the new weights with the old lam rule
  new-lambda   the old weights with the new lam rule
  lam-102      the new weights with PR #102's lam: sum r / sum r^2 shrunk by n / (n + 10), capped at 0.3

Nothing is fitted on the window: every constant is fixed in tops.py before the walk. For every settled market it
compares each variant's opening price with the plain futures prior (Brier and log loss, paired diff ± 1 SE per series)
and new against old. Then the two known leaks, each 10 points at the opening quote on an empty book: the copycat (back
Tube Crowd's hub_surge pick) and the City Pulse leak (back Tube Crowd's city_pulse pick), priced on each opening price.
Rows for the whole window and for markets opened at or after each split (default 17:00 UTC, after the futures' fit
window). Read-only: never writes the recorder data.
"""
from __future__ import annotations

import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "services" / "city"))

import pricing  # noqa: E402
import tops  # noqa: E402
from data import Store, load_hotspots  # noqa: E402
from exchange import Clock, Exchange  # noqa: E402
from ledger import Ledger  # noqa: E402

BOOT = 2000
STAKE = 10
LEAKS = {"copycat": "hub_surge", "pulse leak": "city_pulse"}
OLD_TEMP = 0.01
OLD_S_HALF = 0.05
OLD_K = 10.0
K_102 = 10.0


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def brier(p: dict, w: str) -> float:
    return sum((v - (o == w)) ** 2 for o, v in p.items())


def logloss(p: dict, w: str) -> float:
    return -math.log(max(1e-9, p[w]))


def mean_se(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    return m, (sum((x - m) ** 2 for x in xs) / max(1, n - 1) / n) ** 0.5


def roi_ci(rets: list[float]) -> str:
    if not rets:
        return "-"
    rng = random.Random(0)
    boot = sorted(sum(rng.choice(rets) for _ in rets) / len(rets) for _ in range(BOOT))
    return f"{sum(rets) / len(rets):+.1%} [{boot[int(.025 * BOOT)]:+.0%}, {boot[int(.975 * BOOT)]:+.0%}]"


def odds_on(model: dict, option: str) -> float | None:
    book = pricing.Book(list(model))
    return pricing.locked_odds(book.quote(model, option, STAKE), STAKE)


# ---------------------------------------------------------------- the variants


def old_skill(rec: dict | None) -> float:
    return rec["s"] / (rec["n"] + OLD_K) if rec else 0.0


def old_weights(skill: dict[str, float]) -> dict[str, float]:
    if not skill:
        return {tops.FUTURES: 1.0}
    m = len(skill)
    lo = min(tops.FLOOR, 1 / m)
    top = max(skill.values())
    soft = tops.normalise({k: math.exp((v - top) / OLD_TEMP) for k, v in skill.items()})
    w = {k: min(tops.CAP, lo + (1 - m * lo) * v) for k, v in soft.items()}
    return w | {tops.FUTURES: max(0.0, 1 - sum(w.values()))}


def old_lam(skill: dict[str, float]) -> float:
    total = sum(max(0.0, v) for v in skill.values())
    return tops.LAMBDA_MAX * total / (total + OLD_S_HALF)


class Old(tops.Tops):
    def lean(self, series, tids):
        skill = {t: old_skill(self.records.get((t, series))) for t in tids}
        return old_weights(skill), old_lam(skill)


class NewWeights(tops.Tops):
    def lean(self, series, tids):
        return super().lean(series, tids)[0], old_lam({t: old_skill(self.records.get((t, series))) for t in tids})


class NewLambda(tops.Tops):
    def lean(self, series, tids):
        return old_weights({t: old_skill(self.records.get((t, series))) for t in tids}), super().lean(series, tids)[1]


def lam_102(rec: dict | None) -> float:
    if not rec or rec["r2"] <= 0:
        return 0.0
    return min(tops.LAMBDA_MAX, max(0.0, rec["r1"] / rec["r2"] * rec["n"] / (rec["n"] + K_102)))


class Lam102(tops.Tops):
    def lean(self, series, tids):
        return super().lean(series, tids)[0], lam_102(self.pools.get(series))


VARIANTS = {"old": Old, "new": tops.Tops, "new-weights": NewWeights, "new-lambda": NewLambda, "lam-102": Lam102}


# ---------------------------------------------------------------- the event log and the walk


class Fut:
    def __init__(self, p: dict):
        self.p = p

    def probs(self) -> dict:
        return dict(self.p)


class Mkt:
    """Just what Tops reads and writes on a market."""

    def __init__(self, ev: dict):
        self.id, self.sub, self.settles_at = ev["id"], ev["series"], ev["settles_at"]
        self.futures = Fut(ev["prior"])
        self.tilt = self.tops = None

    def option_ids(self) -> list[str]:
        return list(self.futures.p)

    def model(self) -> dict:
        return tops.tilted(self.futures.probs(), self.tilt)


def capture(raw: Path, start: int, end: int, step: int) -> tuple[list[dict], Exchange]:
    """Run the Exchange and log every TOPS open and settle in the order it made them."""
    spots = load_hotspots()
    ex = Exchange(Store(raw, spots), Clock(start), spots, Ledger(":memory:"))
    log: list[dict] = []
    real_open, real_settle = ex.tops.open, ex.tops.settle

    def on_open(m, stances, now):
        log.append({"kind": "open", "id": m.id, "settles_at": m.settles_at, "series": m.sub, "now": now,
                    "opened": m.opened_at, "prior": tops.clamp(m.futures.probs()), "stances": dict(stances)})
        real_open(m, stances, now)
        log[-1]["live_lambda"] = m.tops["lambda"]

    def on_settle(mid, settles_at, winner, now):
        log.append({"kind": "settle", "id": mid, "settles_at": settles_at, "winner": winner, "now": now})
        real_settle(mid, settles_at, winner, now)

    ex.tops.open, ex.tops.settle = on_open, on_settle
    t = start
    while t <= end:
        ex.tick(t)
        t += step
    return log, ex


def walk(log: list[dict], cls, winners: dict | None = None) -> tuple[dict, tops.Tops]:
    """Replay the log through a fresh Tops of class cls; winners overrides the outcomes (permutation tests).
    Returns {(market, settles_at): opening model} and the Tops at the end."""
    t = cls(Ledger(":memory:"))
    opened: dict[tuple, dict] = {}
    for ev in log:
        key = (ev["id"], ev["settles_at"])
        if ev["kind"] == "open":
            m = Mkt(ev)
            t.open(m, ev["stances"], ev["now"])
            opened[key] = {"model": m.open_model, "lam": m.tops["lambda"]}
        else:
            w = ev["winner"] if winners is None else winners.get(key, ev["winner"])
            t.settle(ev["id"], ev["settles_at"], w, ev["now"])
    return opened, t


def settled_rows(log: list[dict]) -> list[dict]:
    """Every opened market that settled with a winner: series, open time, prior, Tube Crowd's pick, winner."""
    opens = {(e["id"], e["settles_at"]): e for e in log if e["kind"] == "open"}
    out = []
    for e in log:
        key = (e["id"], e["settles_at"])
        if e["kind"] == "settle" and e["winner"] is not None and key in opens:
            o = opens[key]
            tube = o["stances"].get("tube")
            out.append({"key": key, "series": o["series"], "opened": o["opened"], "prior": o["prior"],
                        "winner": e["winner"], "tube": tube[1] if tube and tube[0] == "pick" else None})
    return out


def report(rows: list[dict], runs: dict[str, dict], label: str) -> None:
    by = defaultdict(list)
    for r in rows:
        by[r["series"]].append(r)
    print(f"\n[{label}] opening price vs the futures prior, per variant (paired per market; diff = variant - prior, "
          f"negative is better)")
    print(f"{'series':15} {'variant':12} {'n':>4} {'lam>0':>5} {'mean lam':>8} {'Brier':>7} {'dBrier ± SE':>18} "
          f"{'LL':>7} {'dLL ± SE':>18}")
    for s in sorted(by) + ["ALL"]:
        got = rows if s == "ALL" else by[s]
        n = len(got)
        bp = [brier(r["prior"], r["winner"]) for r in got]
        lp = [logloss(r["prior"], r["winner"]) for r in got]
        print(f"{s:15} {'prior':12} {n:4d} {'':5} {'':8} {sum(bp) / n:7.4f} {'':18} {sum(lp) / n:7.4f}")
        for v, run in runs.items():
            mods = [run[r["key"]]["model"] for r in got]
            lams = [run[r["key"]]["lam"] for r in got]
            bv = [brier(m, r["winner"]) for m, r in zip(mods, got)]
            lv = [logloss(m, r["winner"]) for m, r in zip(mods, got)]
            db, sb = mean_se([b - a for a, b in zip(bp, bv)])
            dl, sl = mean_se([b - a for a, b in zip(lp, lv)])
            print(f"{'':15} {v:12} {n:4d} {sum(x > 0 for x in lams):5d} {sum(lams) / n:8.4f} {sum(bv) / n:7.4f} "
                  f"{db:+.4f} ± {sb:.4f}  {sum(lv) / n:7.4f} {dl:+.4f} ± {sl:.4f}")
    for base in ("old", "lam-102"):
        print(f"\n[{label}] new - {base}, paired per market (negative: new is better)")
        for s in sorted(by) + ["ALL"]:
            got = rows if s == "ALL" else by[s]
            old = [runs[base][r["key"]]["model"] for r in got]
            new = [runs["new"][r["key"]]["model"] for r in got]
            db, sb = mean_se([brier(b, r["winner"]) - brier(a, r["winner"]) for a, b, r in zip(old, new, got)])
            dl, sl = mean_se([logloss(b, r["winner"]) - logloss(a, r["winner"]) for a, b, r in zip(old, new, got)])
            print(f"  {s:15} n={len(got):4d}  Brier {db:+.5f} ± {sb:.5f}   log loss {dl:+.5f} ± {sl:.5f}")
    print(f"\n[{label}] leaks: {STAKE} points on Tube Crowd's pick at the opening quote, ROI [95% bootstrap CI]")
    for name, s in LEAKS.items():
        bets = [r for r in by.get(s, []) if r["tube"]]
        wins = sum(r["tube"] == r["winner"] for r in bets)
        line = f"  {name:11} {s:11} n={len(bets):3d} won={wins:3d}"
        for v, models in [("prior", {r["key"]: r["prior"] for r in bets})] + \
                         [(v, {k: x["model"] for k, x in run.items()}) for v, run in runs.items()]:
            rets = []
            for r in bets:
                o = odds_on(models[r["key"]], r["tube"])
                if o:
                    rets.append((o if r["tube"] == r["winner"] else 0) - 1)
            line += f"\n      {v:12} {roi_ci(rets)}"
        print(line)


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    step = int(sys.argv[4]) * 1000 if len(sys.argv) > 4 else 30_000
    splits = [ms(a) for a in sys.argv[5:]] or [ms(sys.argv[2][:10] + "T17:00:00Z")]
    log, ex = capture(raw, start, end, step)
    runs = {}
    for v, cls in VARIANTS.items():
        runs[v], last = walk(log, cls)
        if v == "new":
            final = last
    live = {(e["id"], e["settles_at"]): e["live_lambda"] for e in log if e["kind"] == "open"}
    drift = max(abs(runs["new"][k]["lam"] - lam) for k, lam in live.items())
    rows = settled_rows(log)
    print(f"TOPS replay {sys.argv[2]} -> {sys.argv[3]}, step {step // 1000}s, "
          f"{sum(e['kind'] == 'open' for e in log)} markets opened, {len(rows)} settled with a winner; "
          f"offline 'new' vs the live Exchange's lambda: max |diff| {drift:.2e}")
    report(rows, runs, "all")
    for split in splits:
        report([r for r in rows if r["opened"] >= split], runs,
               f"opened >= {datetime.fromtimestamp(split / 1000, timezone.utc):%m-%d %H:%M} UTC")

    print("\nTrack records at the end (new loop, GET /tops): posterior skill vs the futures prior")
    out = final.json()
    print(f"  empirical-Bayes prior strength K = {out['priorK']} markets (cap {tops.K_MAX:.1f})")
    for s, got in out["series"].items():
        print(f"  {s:15} lambda now {got['lambdaNow']:.4f}  futures weight {got['futuresWeight']:.3f}")
        for tid, v in sorted(got["traders"].items(), key=lambda kv: -kv[1]["weight"]):
            print(f"    {tid:11} mean {v['score']:+.4f}  sd {v['sd']:.4f}  P(skill>0) {v['pSkill']:.3f}  "
                  f"n {v['n']:4d}  nEff {v['nEff']:6.2f}  weight {v['weight']:.3f}")
    bets = [b for m in ex.markets.values() for b in m.ai_bets]
    print(f"\nAI bets placed in the run (quarter Kelly): {len(bets)}; by trader "
          + ", ".join(f"{t} {sum(b['trader'] == t for b in bets)}" for t in sorted({b['trader'] for b in bets}))
          + f"; mean stake {sum(b['stake'] for b in bets) / max(1, len(bets)):.1f}; final points "
          + ", ".join(f"{t} {r['points']}" for t, r in sorted(ex.record.items())))


if __name__ == "__main__":
    main()
