# Traders and TOPS: sizing, skill, pooling

How the seven AI traders size their bets (`services/city/traders.py`) and how the TOPS loop turns their track
records into the opening price (`services/city/tops.py`). Every number below comes from two scripts you can re-run:

```bash
RAW=data/raw
python3 analysis/judge/tops_replay.py      $RAW 2026-11-07T12:40:00Z 2026-11-08T02:35:00Z 30 2026-11-07T17:00:00Z 2026-11-08T00:55:00Z
python3 analysis/judge/tops_permutation.py $RAW 2026-11-07T12:40:00Z 2026-11-08T02:35:00Z 200
```

## 1. Sizing: quarter Kelly on the LMSR

A trader bets on option i, priced `p` by the LMSR (`b = 1200`, 5% fee `f`). With `y = x(1 − f)/b`, a stake `x` buys

    shares(x) = b · ln(1 + (e^y − 1) / p)          and leaves the option priced  p(x) = 1 − (1 − p) e^−y

(the closed form of `pricing.Book.quote`, on any number of outcomes). A winning share pays 1 point.

**The trader's probability `q`.** Simaissance uses its forecast. The others only pick a side, so `q` is
the TOPS view of that pick: the futures' price plus the trader's shrunk excess hit rate on its past picks in the
series, never below the price (`Tops.belief`). No record means `q` = the price, which means no edge, which means no
bet. A trader earns its stake by being right first.

**Edge stake.** The marginal cost of a share after spending `x` is `p(x) / (1 − f)`. Setting it equal to `q`:

    x_edge = b / (1 − f) · ln((1 − p) / (1 − q(1 − f)))          0 when q(1 − f) ≤ p

Past `x_edge` each extra point loses money in expectation, so no sizing should spend more.

**Kelly.** Full Kelly maximises the expected log bankroll, with `W` the points not already staked on unsettled
markets and the payout capped at the 20x odds cap:

    x* = argmax over 0 ≤ x ≤ min(W, x_edge) of  q · ln(W − x + min(shares(x), 20x)) + (1 − q) · ln(W − x)

The objective is concave (shares are concave in x), so a ternary search finds it. With no slippage (`b → ∞`) it is
the textbook `W (q·o − 1)/(o − 1)` at decimal odds `o = (1 − f)/p`; with slippage it is smaller, and never more than
`x_edge`. The stake is

    stake = floor(min(100, 0.25 · x*))          0 if under 10, or if W < 10

then the per-market position cap in `integrity.py` (10% or 25% of points) applies as before. The quarter fraction is
the usual hedge against an overstated `q`: the calibration behind a side pick is shrunk, but still noisy.

**No trader can go negative.** Let `P` be a trader's points and `O` its open stakes (bets on unsettled markets,
`Exchange.ai_open_stakes`). Invariant: `O ≤ P`. At start `O = 0` and `P ≥ 0` (a negative record on file loads as 0).
A new stake is at most `0.25 (P − O) ≤ P − O`, so `O + stake ≤ P`; it is recomputed with the current `O` at fill when
the bet delay holds it. At settle the stake leaves `O`; a loss takes it from `P` (both drop by the same amount), a win
adds `round(stake · (odds − 1)) ≥ 0`, a void refunds. So the invariant holds after every event and losing every open
bet leaves `P − O ≥ 0`. The property test `SizingTest.test_no_random_sequence_of_bets_and_settles_overdraws` drives
300 random sequences of bets (random beliefs, prices, bankrolls from 0 to 3000) and settles through `stake_for` and
the real locked odds; replacing `stake_for` with the #86 behaviour (sizing on settled points) makes it fail.

On the replay (12:40–02:35, 30 s ticks) the traders placed 221 bets, mean stake 23: Tube Sigma 137, Fadewater
68, Runway72 16. Final points: Tube Sigma 1080, Fadewater 1050, Runway72 805, the rest 1000 untouched
(no edge, or no views: no recorded sim, VIX round or rain).

## 2. Skill: an empirical-Bayes posterior per trader per series

At settle each view scores `x = log p_view(winner) − log p_futures(winner)`, its log-score skill over the futures
prior. The prior is the baseline, not the blended price, so a right trader doesn't look worse the more it is trusted.

Model: `x ~ N(θ, σ²)`, `θ ~ N(0, τ²)`, each older market's likelihood discounted by `DECAY = 0.5^(1/20)` (half-life
20 settled markets, about 100 minutes). The posterior is

    θ | data ~ N( Σw·x / (Σw + K),  σ² / (Σw + K) ),   K = σ² / τ²

- `σ²` per series: the pooled decayed second moment of all its traders' scores about 0, so one trader's lucky run
  cannot shrink its own noise estimate.
- `K` by empirical Bayes over every trader-series record: `E[m²/σ²] = τ²/σ² + E[1/n]` for the decayed means `m`, so
  `τ²/σ²` is the spread of the means beyond what noise explains. `K` is capped at the decayed window, `1/(1 − DECAY)
  = 29.4`: with no spread to explain, a full record still counts half. On the replay `K` ends at 22.5.

The old rule, `Σw·x / (Σw + 10)`, is this posterior mean with `K` fixed at 10 and no variance.

## 3. Pooling: weights ∝ P(skill > 0)

Per series, each trader's weight is proportional to `P(θ > 0) = Φ(mean / sd)` under its posterior, then mixed with
a 5% floor and capped at 40%; what the cap cuts stays with the futures. This replaces `softmax(score / 0.01)`, whose
temperature was chosen on the same day's data. `P(θ > 0)` needs no temperature: a trader with no information gets
one half, the same as any other, and only evidence moves it.

The pool is the linear opinion pool `pool = Σ w_i·view_i + w_futures·futures`.

## 4. λ: from the pool's own record, capped

The opening price is `(1 − λ)·futures + λ·pool`. λ is how much the pool has historically improved log loss: at
every settle where the pool differed from the futures, `r = pool(winner) / futures(winner) − 1` is kept per series
(same decay). The log likelihood of a λ-blend on those markets is `Σw·log(1 + λr)`; to second order it peaks at

    λ̂ = Σw·r / Σw·r²,    t = Σw·r / √(Σw²·r²),    λ = clamp(λ̂ · max(0, 1 − 1/t²), 0, 0.3)

λ̂ is then shrunk by its own significance (positive-part James–Stein): `t = λ̂ / SE`. If the futures are calibrated,
`E[r] = Σ_o futures(o)·(pool(o)/futures(o) − 1) = 0`, so the variance of `Σw·r` is `Σw²·r²` and
`SE = √(Σw²·r²) / Σw·r²`, computed from the r themselves. A pool whose gains are within noise (`|t| ≤ 1`, about 68%
of the time when they are pure noise) gets λ = 0; λ approaches λ̂ as t grows. 0.3 is the old hard cap. A pool that has
not beaten the futures gets λ = 0. The rule was fixed before looking at any outcome (2026-11-08), after live /tops
showed City Pulse at the 0.3 cap with every trader at P(θ > 0) 0.47–0.71 after 22 markets.

- **Why not the fitted residuals** `e = 1 − λ̂·r` (the regression's sandwich SE)? With one market `e = 0`, so SE = 0,
  t = ∞ and λ jumps to the cap on a single settle, the defect itself. Under the null-variance SE one market gives
  `|t| = 1` and λ = 0. With more data the two agree to within the size of λ̂·r.
- **The old `n/(n + 10)` shrink is dropped.** It counted markets, not evidence: live City Pulse's 22 markets (15.7
  after decay) kept 61% of λ̂ whatever the r looked like. The James–Stein factor already does the small-sample work: m equal gains give `t² = m`
  (its effective count under the decay), so `λ = λ̂·(1 − 1/m)`, and noise gives 0.
- The old rule before that, `0.3·S/(S + 0.05)` on the summed positive skill, was a guess about how skill should map
  to trust. The test `test_lambda_maximises_the_log_likelihood_to_second_order` checks λ̂ against a grid search of the
  quadratic and of the exact log likelihood; `test_lambda_is_zero_when_the_pools_gains_are_noise` and
  `test_lambda_approaches_the_fit_when_t_is_large` check the shrink.

## 5. Out of sample: old vs new

`tops_replay.py` runs the Exchange once and logs every TOPS open (the futures prior and every stance) and settle in
the order they happened. Each variant then walks that log from an empty record: every blend uses only settles
before its open (walk-forward), every variant sees the same markets (paired), and nothing is fitted on the window.
The offline `new` walk reproduces the live Exchange's λ exactly (max |diff| 0). The decision rule was set before the
run: keep the new loop if its paired log-loss difference against the old, over all markets of the whole walk, is ≤ 0.

Log loss of the opening price minus the futures prior's (negative is better), mean ± 1 SE, per series:

| series | n | old | new | new − old |
|---|---|---|---|---|
| city_pulse | 165 | −0.0012 ± 0.0010 | −0.0023 ± 0.0021 | −0.0010 ± 0.0012 |
| heathrow_stack | 155 | −0.0002 ± 0.0003 | −0.0008 ± 0.0011 | −0.0006 ± 0.0008 |
| hub_surge | 164 | +0.0001 ± 0.0002 | +0.0008 ± 0.0011 | +0.0007 ± 0.0009 |
| hub_hour | 12 | +0.0015 ± 0.0011 | +0.0082 ± 0.0055 | +0.0067 ± 0.0045 |
| clean_sheet | 13 | −0.0070 ± 0.0049 | −0.0180 ± 0.0098 | −0.0109 ± 0.0053 |
| **all** | **509** | **−0.0006 ± 0.0004** | **−0.0010 ± 0.0009** | **−0.0004 ± 0.0006** |

By window, all series together:

| window | n | Brier new − old | log loss new − old | mean λ old → new |
|---|---|---|---|---|
| all | 509 | −0.0007 ± 0.0005 | −0.0004 ± 0.0006 | 0.028 → 0.161 |
| opened ≥ 17:00 (after the futures' fit) | 354 | −0.0011 ± 0.0007 | −0.0009 ± 0.0008 | 0.031 → 0.177 |
| opened ≥ 00:55 (data no earlier analysis saw) | 54 | +0.0007 ± 0.0016 | +0.0008 ± 0.0018 | 0.011 → 0.223 |

**Verdict: the new loop is kept, by the rule, and it is not significantly better.** It leans five times as hard
(mean λ 0.16 vs 0.03) and roughly doubles the old loop's gain over the futures, but the pooled differences are within
1.2 SE; per series the largest are clean_sheet (−2.1 SE, n = 13) and hub_hour (+1.5 SE, n = 12, worse). It is also
slightly worse on hub_surge and on the 54 fresh markets after 00:55 (noise at that n). The ablations say where it comes from: the new weights with the old λ match the old loop to the fourth decimal
(two traders per series, both rules sit on the cap and the floor), and the old weights with the new λ match the new
loop. The improvement is all λ.

**Leaks** (10 points on Tube Sigma's pick at the opening quote, ROI, 95% bootstrap CI), opened ≥ 17:00:

| leak | n | futures | old | new |
|---|---|---|---|---|
| City Pulse (Tube Sigma's pick) | 112 | +19.3% [+1%, +38%] | +18.2% [−0%, +37%] | +16.8% [−1%, +35%] |
| copycat (hub_surge) | 112 | +15.1% [−13%, +45%] | +15.0% [−13%, +45%] | +13.9% [−14%, +44%] |

Shaved, not closed. The caps are why: λ ≤ 0.3 and a 40% weight move the price at most 12% of the way to one view.

The tables above are the #102 run, where `new` was λ̂·n/(n + 10). **The significance shrink (§4), same command,
2026-11-08** (City Pulse lines on the spread-fitted step by then, so the priors differ slightly from the tables
above): `tops_replay.py` keeps the #102 rule as the variant `lam-102` and pairs `new` (now the James–Stein λ)
against it, log loss per market, mean ± 1 SE, negative = James–Stein better:

| window | n | new − lam-102 | mean λ lam-102 → new | λ > 0, lam-102 → new |
|---|---|---|---|---|
| all | 509 | −0.0001 ± 0.0005 | 0.132 → 0.065 | 253 → 116 |
| opened ≥ 17:00 | 354 | −0.0000 ± 0.0006 | 0.153 → 0.088 | 206 → 109 |
| opened ≥ 00:55 | 54 | +0.0008 ± 0.0011 | 0.123 → 0.083 | 36 → 17 |

Both still beat the futures prior by the same amount (all: −0.0025 ± 0.0010 vs −0.0024 ± 0.0011). The rule halves
the lean at no cost in log loss: Heathrow's λ > 0 count drops 107 → 31 (the tiny-r cap case in §7), hub_hour's mean
λ 0.066 → 0, and City Pulse is unchanged within noise (+0.0002 ± 0.0007). Kept, as decided before the run.

## 6. Multiple testing: how many "skilled" traders by chance?

10 trader-series have 5 or more settled views. `tops_permutation.py` re-walks the whole loop (views, records,
posteriors) 200 times under each of two nulls: the winners shuffled within each series (no market-specific
information), and each winner drawn from its market's futures prior (the futures calibrated, the traders adding
nothing). Statistic: the trader's undecayed mean log score over the day; p = share of null walks at least as high.

| trader | series | n | mean score (nats) | p, shuffle | p, prior draw | P(θ > 0) at the end |
|---|---|---|---|---|---|---|
| Tube Sigma | city_pulse | 164 | +0.0099 | 0.015 | 0.020 | 0.37 |
| Runway72 | heathrow_stack | 103 | +0.0051 | 0.030 | 0.045 | 0.54 |
| Fadewater | clean_sheet | 13 | +0.1351 | 0.075 | 0.010 | 0.91 |
| Line-ennium | clean_sheet | 13 | −0.0065 | 0.44 | 0.54 | 0.48 |
| Tube Sigma | hub_surge | 164 | −0.0024 | 0.55 | 0.32 | 0.71 |
| Fadewater | hub_surge | 164 | −0.0045 | 0.70 | 0.46 | 0.41 |
| Fadewater | heathrow_stack | 155 | −0.0001 | 0.77 | 0.14 | 0.47 |
| Tube Sigma | hub_hour | 12 | −0.0276 | 0.85 | 0.71 | 0.37 |
| Fadewater | city_pulse | 165 | −0.0186 | 0.995 | 0.995 | 0.43 |
| Fadewater | hub_hour | 12 | −0.0931 | 1.000 | 0.97 | 0.12 |

- **Expected by chance at p < 0.05: 0.5 of 10.** Observed: 2 under the shuffle null (P(≥ 2 by chance) = 0.095) and
  3 under the prior null (P = 0.010).
- **None survives Bonferroni (p < 0.005) or Benjamini–Hochberg at 5%.**
- **The loop's own verdict at the end, P(θ > 0) > 0.95: 0 traders** (0.06 expected by chance). The posterior is
  doing its job: it does not call anyone skilled on this sample.
- Read it as: there is probably some signal in the set (Tube Sigma on City Pulse is the one consistent with the
  City Pulse leak), but no single trader-series is proven. The prior-draw null rejects more easily because it also
  assumes the futures are calibrated; a trader exploiting a futures mispricing (the leak) is information the
  market should absorb, which is exactly what TOPS is for.
- Tube Sigma's City Pulse edge was a daytime edge: its decayed posterior at the end of the window is P = 0.37, and
  on the 17 City Pulse markets after 00:55 its pick lost money (−26%).

## 7. Limits

- **One day of data.** 509 settled markets, five series, two traders with views on most of them (the Simulation,
  Camadel and Rain Street have none in the replay: no recorded sim, no VIX round, no rain). Nothing here is
  significant after multiple-testing correction; the comparison can only say "not worse".
- **λ̂ is scale-free; the shrink now measures evidence, the cap still binds.** `Σr / Σr²` grows as the pool's
  moves shrink, so where the pool barely differs from the futures λ̂ is large. The James–Stein factor stops noise
  from reaching the cap (a run of tiny r with |t| ≤ 1 now gives 0, not 0.3), but weak evidence times a large λ̂ still
  can: the live ledger at 03:25 on 8 Nov had City Pulse at λ̂ = 1.93, t = 1.38 (decayed; 0.87 undecayed), so
  `1.93 × (1 − 1/1.38²) = 0.92` → 0.3, and London VIX λ̂ = 1.71, t = 1.29 → 0.3. hub_surge (t = 0.59) and hub_hour
  (one market, |t| = 1) went from 0.3 to 0. The price effect at the cap is small: City Pulse's median
  |pool − futures| is 0.024, so λ = 0.3 moves its opening price about 0.7 points.
- **The weights don't matter yet.** With two traders per series, a 5% floor and a 40% cap, any monotone rule gives
  nearly the same pool. The P(θ > 0) rule removes a tuned constant; it will matter with more traders.
- **The Normal skill model is an approximation.** Log-score differences are skewed (a confident wrong view scores
  far below 0). The decayed likelihood treats Σw as the sample size, which is conservative (the effective size of
  a geometric weighting is larger).
- **Sizing trusts the calibration.** A side pick's `q` comes from its shrunk excess hit rate; quarter Kelly is the
  hedge against that being optimistic. With no record a trader does not bet, so a fresh ledger starts with only the
  Simulation trading until records build up.
- **No look-ahead, by construction and by test.** Records change only at settle (`test_no_look_ahead_unsettled_
  markets_never_count`); the replay walks the logged order of opens and settles.
