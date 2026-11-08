# Does the market structure work? (known-truth simulation)

A controlled test of the parallel exchange venue (`services/city/venue*.py`, #90, #94). Every agent, rule, fee and
limit is the venue's own code; only the markets are synthetic, so that the **true probability p\* of every contract
is known**. That lets us measure directly whether trading moves prices towards the truth, and who earns from whom.

```bash
python3 analysis/exchange/simulate.py           # 6 scenarios x 3 seeds x 12 rounds (about 12 min)
python3 analysis/exchange/simulate.py --fees kalshi   # the same with Kalshi's fees and rebates
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

## Results (216 contracts per scenario; fees off, the venue's default)

| Scenario | Error vs truth at close: forecast → price (points) | Price closer than forecast | Expected P&L per contract (points): informed / crowd / uninformed / market makers / random taker | Informed + crowd share of fund capital |
|---|---|---|---|---|
| baseline (20 informed, 20 uninformed) | 4.1 → 2.7 (−33%) | 89% | +3.8 / — / −2.7 / −0.3 / −3.0 | 40% → 73% |
| no informed funds | 4.1 → 4.0 (−2%) | 51% | — / — / −2.2 / +2.1 / −1.6 | — |
| few informed funds (4) | 4.1 → 3.1 (−23%) | 79% | +4.9 / — / −2.4 / +0.5 / −2.1 | 12% → 43% |
| unpriced edge (half the forecasts 10 points low) | 3.4 → 2.2 (−35%) | 84% | +2.9 / — / −2.6 / +0.5 / −3.0 | 40% → 59% |
| noise traders only (60 uninformed) | 4.1 → 4.1 (0%) | 48% | — / — / −2.0 / +2.0 / −1.2 | — |
| wisdom of a crowd (40 weak readers, no expert) | 4.1 → 3.2 (−23%) | 75% | — / −1.2 / — / +1.3 / −2.0 | 80% → 44% |

On the biased contracts in "unpriced edge", the forecast ended 4.8 points below the truth and the traded price 2.9
below: about 40% of the edge was traded away. The ledger audit was clean in every run.

## What it shows

**Working:**
1. **Informed money makes prices more accurate:** a third closer to the truth than the forecast with 20 informed
   funds, on 89% of contracts; still 23% with only four.
2. **Uninformed money does no harm:** with no informed funds, or 60 noise traders, prices stay as accurate as the
   forecast. Market makers anchored on it absorb the noise.
3. **Traders are paid for information, not for trading:** informed funds earn +3-5 points per contract, uninformed
   funds lose 2-3, a random taker pays 1-3 (the spread it crosses).
4. **Capital moves to the funds that know more** (market selection): informed funds go from 40% to 73% of fund capital.
5. **A crowd of weak readers informs prices** (23% closer to the truth) even though each crowd fund loses.
6. **Market makers break even against informed flow** (fixed in this PR, below).

**Still open:**
1. **Edges are only partly closed** (about 40% of a 10-point bias). Informed capital is limited by fractional Kelly and
   the 25% position limit, and market makers anchored on the forecast lean against the informed flow.
2. **Funds that just trade the forecast lose 3-8 points per contract** in every scenario: they take the other side of
   better information. They shouldn't be a default participant.

## The market-maker fix

The first run found market makers losing 1.9 points per contract to informed funds: the 6-point spread cap couldn't
widen when informed flow picked up, which Glosten & Milgrom say a market maker must do to break even. Faster learning
from order flow only trimmed the loss and made prices noisier, so this PR does what real venues do instead:
- **Toxicity widening:** a market maker's half-spread grows with how one-sided the last minute's flow has been (up to
  6 more points at fully one-sided flow; none below 30% imbalance), since one-sided flow is what informed traders look
  like.
- **Fast-market relief:** while the venue flags a contract as fast (one-sided flow on at least 20 contracts in a
  minute, or a 3-point forecast jump), the obligation allows designated market makers up to 12 points instead of 6.

Fees off, 3 seeds each:

| Scenario | Market makers per contract: old → new | Error cut vs forecast: old → new | Random taker per contract: old → new |
|---|---|---|---|
| baseline | −1.24 → −0.26 | 37% → 33% | −1.5 → −3.0 |
| unpriced edge | −0.68 → +0.51 | 37% → 35% | −1.8 → −3.0 |
| few informed funds | −0.26 → +0.48 | 18% → 23% | −0.8 → −2.1 |
| no informed funds | +1.65 → +2.11 | 1% → 2% | −0.8 → −1.6 |

The trade-off is the textbook one: when flow turns one-sided, spreads widen, so uninformed traders pay more to trade,
and that is what keeps the market makers whole.

## Fees

The venue's fees are off by default (a points game has no exchange to fund). `--fees kalshi` turns on Kalshi's taker
fee and the market-maker rebate it pays for; the simulation, the demo and the server all take the flag. The live LMSR
exchange's 5% fee is separate and unchanged.

## Caveats
- Synthetic markets: the forecast's errors are random and shrink at a fixed rate, and informed funds read a noisy p\*
  directly. The real test is a replay on recorded London data (`services/city/venue_demo.py --replay-from`).
- Brier scores against outcomes (in `results.json`) are too noisy to separate the forecast from the price at 216
  contracts; distance to the known truth is the reliable measure.
