# Does the market structure work? (known-truth simulation)

A controlled test of the parallel exchange venue (`services/city/venue*.py`, #90, #94). Every agent, rule, fee and
limit is the venue's own code; only the markets are synthetic, so that the **true probability p\* of every contract
is known**. That lets us measure directly whether trading moves prices towards the truth, and who earns from whom.

```bash
python3 analysis/exchange/simulate.py           # 6 scenarios x 3 seeds x 12 rounds (about 9 min)
python3 analysis/exchange/simulate.py --quick   # 1 seed, 6 rounds
```

Outputs go to `out/` (gitignored): `results.json` and `summary.md`.

## Set-up
- Each 5-minute round lists 6 binary contracts that trade for 4 minutes. p\* ~ Beta(2, 2).
- The **public forecast** (what market makers anchor on) is p\* plus an error (sd 10 points at open) that shrinks by
  half by close, the way the 50 futures narrow as data arrives.
- Participants: 6 market makers (4 designated), 10 model funds (trade the forecast), 300 biased retail, 20 random
  takers, plus per scenario **informed funds** (read p\* with 2-4 points of noise), **uninformed funds** (the forecast
  plus 20-30 points of noise: no information) or a **crowd** (read p\* with 20-30 points of noise each).
- **Expected P&L** marks every fill to the truth (a YES bought at x is worth 100·p\* − x), minus fees plus rebates.
  Realised P&L is mostly luck at this sample size, since each contract settles at 0 or 100.

## Results (216 contracts per scenario)

| Scenario | Error vs truth at close: forecast → price (points) | Price closer than forecast | Expected P&L per contract (points): informed / crowd / uninformed / market makers / random taker | Informed + crowd share of fund capital |
|---|---|---|---|---|
| baseline (20 informed, 20 uninformed) | 4.1 → 2.7 (−34%) | 90% | +3.9 / — / −3.7 / −1.9 / −2.9 | 40% → 72% |
| no informed funds | 4.1 → 4.0 (−3%) | 51% | — / — / −3.0 / +1.8 / −2.2 | — |
| few informed funds (4) | 4.1 → 3.4 (−17%) | 76% | +5.2 / — / −3.4 / −0.7 / −2.1 | 12% → 41% |
| unpriced edge (half the forecasts 10 points low) | 3.4 → 2.3 (−33%) | 85% | +3.1 / — / −3.6 / −0.8 / −3.3 | 40% → 38%\* |
| noise traders only (60 uninformed) | 4.1 → 4.0 (−1%) | 48% | — / — / −3.2 / +1.9 / −1.9 | — |
| wisdom of a crowd (40 weak readers, no expert) | 4.1 → 3.2 (−21%) | 73% | — / −2.0 / — / +0.9 / −2.6 | 80% → 33% |

\*Realised capital, which moved against the informed funds by luck in these seeds; their expected return was +61%.

On the biased contracts in "unpriced edge", the forecast ended 4.8 points below the truth and the traded price 3.1
below: the edge was cut by a third, not closed. The ledger audit was clean in every run.

## What it shows

**Working:**
1. **Informed money makes prices more accurate.** With 20 informed funds the closing price is 34% closer to the truth
   than the forecast, on 90% of contracts. Four informed funds still get 17%.
2. **Uninformed money does no harm.** With no informed funds, or 60 noise traders, prices stay within 1-3% of the
   forecast's accuracy: market makers anchored on the forecast absorb the noise.
3. **Traders are paid for information, not for trading.** Informed funds earn +3-5 points per contract; uninformed
   funds lose 3-4; a random taker pays about 2-3 points per contract (half the spread plus the fee), as on a real
   exchange.
4. **Capital moves to the funds that know more** (market selection): informed funds go from 40% to 72% of fund capital
   in an hour.
5. **A crowd of weak readers informs prices even though each one loses** (21% closer to the truth; every crowd fund
   −2.0 points per contract), the classic wisdom-of-crowds effect.

**Not working yet:**
1. **Market makers lose money when informed funds trade** (−1.9 points per contract in the baseline). They earn
   +1.8 against uninformed flow, but the 6-point spread cap that designated market makers accept can't widen when
   informed flow picks up, which Glosten & Milgrom say a market maker must do to break even, and the rebate (25% of
   taker fees) doesn't cover the gap. Faster learning from order flow is not the fix:

   | Market makers' flow learning | Baseline: error cut / closer / MM per contract | Unpriced edge: edge left / MM per contract |
   |---|---|---|
   | today (alpha 0.05, cap 5 points) | 34% / 90% / −1.87 | −3.1 points / −0.77 |
   | faster (alpha 0.15, cap 12) | 35% / 83% / −1.30 | −2.9 / −0.81 |
   | fastest (alpha 0.30, cap 20) | 43% / 81% / −1.49 | −2.7 / −0.61 |

   It trims the loss but adds noise. The likely fixes are a spread obligation that widens with measured volatility
   or order-flow imbalance, or a larger rebate.
2. **Edges are only partly closed** (a third of a 10-point bias). Informed capital is limited by fractional Kelly,
   the 25% position limit and the fee, and market makers anchored on the forecast lean against the informed flow.
3. **Funds that just trade the forecast lose 3-8 points per contract when informed money is present**: they take the
   other side of better information. They shouldn't be a default participant.

## Caveats
- Synthetic markets: the forecast's errors are random and shrink at a fixed rate, and informed funds read a noisy p\*
  directly. The real test is a replay on recorded London data (`services/city/venue_demo.py --replay-from`).
- Brier scores against outcomes (in `results.json`) are too noisy to separate the forecast from the price at 216
  contracts; distance to the known truth is the reliable measure.
