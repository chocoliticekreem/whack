"""How many "skilled" traders would the TOPS loop find by chance? A permutation test on the recorded data.

    python3 analysis/judge/tops_permutation.py <raw_dir> <start ISO> <end ISO> [perms] [step_s]

Logs the Exchange's TOPS opens and settles once (tops_replay.capture), then walks the log through the loop in
services/city/tops.py with the real outcomes and with outcomes under two nulls, recomputing every view, record and
posterior each time (a side pick's view depends on the trader's past hits, so the whole walk is redone):

  shuffle   the winners permuted among the settled markets of each series (no market-specific information)
  prior     each winner drawn from that market's futures prior (the futures calibrated, the traders adding nothing)

Two statistics per trader-series (each trader with a view on at least MIN_N settled markets of a series):
  day   the undecayed mean log score over the day, sum(log view(w) - log prior(w)) / n; its permutation p-value is
        the share of null walks with a mean at least as high
  now   the loop's own verdict at the end: posterior P(skill > 0) > 0.95 counts as "skilled"
Reports how many trader-series pass at p < 0.05 and at P > 0.95 for real, the mean count under each null (the number
expected by chance), and Bonferroni / Benjamini-Hochberg survivors. Read-only: never writes the recorder data.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tops_replay import capture, ms, walk  # noqa: E402  (puts services/city on the path)

import tops  # noqa: E402

MIN_N = 5
SKILLED = 0.95
ALPHA = 0.05


def day_scores(t: tops.Tops) -> dict[tuple[str, str], list[float]]:
    """{(trader, series): every settled view's log score vs the prior} from the walk's ledger."""
    out: dict[tuple[str, str], list[float]] = {}
    rows = t.ledger.db.execute("SELECT series, prior, views, winner FROM tops_markets WHERE winner IS NOT NULL")
    for series, prior, views, winner in rows.fetchall():
        prior = json.loads(prior)
        for tid, v in json.loads(views).items():
            out.setdefault((tid, series), []).append(tops.score(v["probs"], prior, winner))
    return out


def verdicts(t: tops.Tops) -> dict[tuple[str, str], float]:
    """{(trader, series): posterior P(skill > 0)} at the end of the walk."""
    out = {}
    for s in {s for _, s in t.records}:
        tids = t.tids(s)
        for tid, (m, sd) in t.posteriors(s, tids).items():
            out[(tid, s)] = tops.p_skill(m, sd)
    return out


def stats(log: list[dict], winners: dict | None) -> tuple[dict, dict]:
    _, t = walk(log, tops.Tops, winners)
    means = {k: sum(v) / len(v) for k, v in day_scores(t).items() if len(v) >= MIN_N}
    return means, verdicts(t)


def settled(log: list[dict]) -> list[dict]:
    opens = {(e["id"], e["settles_at"]): e for e in log if e["kind"] == "open"}
    return [dict(e, series=opens[(e["id"], e["settles_at"])]["series"],
                 prior=opens[(e["id"], e["settles_at"])]["prior"])
            for e in log if e["kind"] == "settle" and e["winner"] is not None
            and (e["id"], e["settles_at"]) in opens]


def shuffled(rows: list[dict], rng: random.Random) -> dict:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["series"], []).append(r)
    out = {}
    for got in by.values():
        ws = [r["winner"] for r in got]
        rng.shuffle(ws)
        out |= {(r["id"], r["settles_at"]): w for r, w in zip(got, ws)}
    return out


def drawn(rows: list[dict], rng: random.Random) -> dict:
    out = {}
    for r in rows:
        opts = list(r["prior"])
        out[(r["id"], r["settles_at"])] = rng.choices(opts, weights=[r["prior"][o] for o in opts])[0]
    return out


def bh(ps: dict, alpha: float) -> list:
    """Benjamini-Hochberg survivors at false-discovery rate alpha."""
    ranked = sorted(ps.items(), key=lambda kv: kv[1])
    m, keep = len(ranked), 0
    for i, (_, p) in enumerate(ranked, 1):
        if p <= alpha * i / m:
            keep = i
    return [k for k, _ in ranked[:keep]]


def main() -> None:
    raw, start, end = Path(sys.argv[1]), ms(sys.argv[2]), ms(sys.argv[3])
    perms = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    step = int(sys.argv[5]) * 1000 if len(sys.argv) > 5 else 30_000
    log, _ = capture(raw, start, end, step)
    rows = settled(log)
    real_means, real_now = stats(log, None)
    keys = sorted(real_means)
    counts = {k: len(v) for k, v in day_scores(walk(log, tops.Tops)[1]).items()}
    print(f"TOPS permutation test {sys.argv[2]} -> {sys.argv[3]}: {len(rows)} settled markets, "
          f"{len(keys)} trader-series with >= {MIN_N} settled views, {perms} walks per null")
    for null, gen in (("shuffle", shuffled), ("prior", drawn)):
        rng = random.Random(0)
        ge = {k: 0 for k in keys}
        count_p, count_now = [], []
        null_means: list[dict] = []
        for _ in range(perms):
            means, now = stats(log, gen(rows, rng))
            null_means.append(means)
            for k in keys:
                ge[k] += means.get(k, float("-inf")) >= real_means[k]
            count_now.append(sum(now.get(k, 0.0) > SKILLED for k in keys))
        pvals = {k: (1 + ge[k]) / (1 + perms) for k in keys}
        for means in null_means:
            hits = 0
            for k in keys:
                above = sum(m.get(k, float("-inf")) >= means.get(k, float("-inf")) for m in null_means)
                hits += above / perms <= ALPHA
            count_p.append(hits)
        real_p = sum(p < ALPHA for p in pvals.values())
        real_now_n = sum(real_now.get(k, 0.0) > SKILLED for k in keys)
        print(f"\nnull = {null}")
        print(f"{'trader':11} {'series':15} {'n':>4} {'day mean':>9} {'perm p':>7} {'P(skill>0) now':>15}")
        for k in sorted(keys, key=lambda k: pvals[k]):
            print(f"{k[0]:11} {k[1]:15} {counts[k]:4d} {real_means[k]:+9.4f} {pvals[k]:7.3f} "
                  f"{real_now.get(k, 0.0):15.3f}")
        exp_p = sum(count_p) / perms
        exp_now = sum(count_now) / perms
        print(f"  day-mean skilled at p < {ALPHA}: real {real_p} of {len(keys)}; by chance {exp_p:.2f} on average "
              f"(the null's own count; {ALPHA * len(keys):.2f} if exact); P(count >= real) "
              f"{(1 + sum(c >= real_p for c in count_p)) / (1 + perms):.3f}")
        print(f"  end-of-day P(skill>0) > {SKILLED}: real {real_now_n}; by chance {exp_now:.2f} on average; "
              f"P(count >= real) {(1 + sum(c >= real_now_n for c in count_now)) / (1 + perms):.3f}")
        bonf = [k for k, p in pvals.items() if p < ALPHA / len(keys)]
        print(f"  Bonferroni (p < {ALPHA / len(keys):.4f}): {bonf or 'none'};  "
              f"Benjamini-Hochberg at {ALPHA}: {bh(pvals, ALPHA) or 'none'}")
    print(f"\nrun at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
