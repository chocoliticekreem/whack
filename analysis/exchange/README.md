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

## Results (216 contracts per scenario; the venue's defaults)

Defaults: fees off, market makers widening from 70% one-sided flow and taking 30% of their fair value from the last
minute's traded price, and no forecast-only funds.

| Scenario | Contracts | Error vs truth at close: forecast → price (points) | Price closer than forecast | Quoted spread (points) | Expected P&L per contract traded (points): informed / crowd / uninformed / market makers / random taker | Informed + crowd share of fund capital |
|---|---|---|---|---|---|---|
| baseline (20 informed, 20 uninformed) | 216 | 4.1 → 2.0 (-51.6%) | 88% | 6.9 | +3.5 / — / -2.5 / -0.2 / -2.7 | 50% → 97% |
| no informed funds | 216 | 4.1 → 4.1 (+1.4%) | 42% | 5.1 | — / — / -1.9 / +1.9 / -1.1 | 0% → 0% |
| few informed funds (4) | 216 | 4.1 → 2.9 (-28.6%) | 71% | 6.2 | +4.7 / — / -2.2 / +0.5 / -2.3 | 17% → 90% |
| unpriced edge (half the forecasts 10 points low) | 216 | 3.4 → 1.7 (-51.2%) | 86% | 6.5 | +2.7 / — / -2.3 / +0.3 / -2.8 | 50% → 96% |
| noise traders only (60 uninformed) | 216 | 4.1 → 4.2 (+2.0%) | 45% | 4.6 | — / — / -1.8 / +1.8 / -1.2 | 0% → 0% |
| wisdom of a crowd (40 weak readers, no expert) | 216 | 4.1 → 3.0 (-26.0%) | 70% | 4.6 | — / -1.1 / — / +1.1 / -1.9 | 100% → 100% |

On the biased contracts in "unpriced edge", the forecast ended 4.8 points below the truth and the traded price 2.1
below: more than half of the edge was traded away. The ledger audit was clean in every run.

## What it shows

Scored as a market (accuracy, liquidity, orderliness, sustainability), not by any AI group's P&L:

**Working:**
1. **Accuracy: informed money makes prices much more accurate.** Half as far from the truth as the forecast with 20
   informed funds (closer on 88% of contracts); 29% better with only four; 26% from a crowd of weak readers.
2. **Accuracy: uninformed money does almost no harm.** With no informed funds, or 60 noise traders, prices end within
   2% of the forecast's accuracy (0.1 points).
3. **Liquidity: two-sided books with 4.6-6.9-point quoted spreads,** tightest when flow is uninformed.
4. **Sustainability: market makers break even or better in every scenario** (−0.2 to +1.9 points per contract).
5. **Capital moves to the funds that know more:** informed funds go from 50% to 97% of fund capital.
6. **Orderliness:** no crossed books, no trades after close, clean ledger audits.

**Still open:**
1. **Edges are only partly closed** (a little over half of a 10-point bias). Informed capital is limited by fractional
   Kelly and the 25% position limit.
2. **With no information in the market at all, prices are 1-2% less accurate than the forecast**, because noise
   trades now move market makers' quotes a little (the cost of anchoring on trades; about 0.1 points).

## Accuracy levers

Tested against the previous defaults (fees off, 3 seeds each; error cut vs forecast: baseline / unpriced edge / no
informed funds, then the baseline's quoted spread and market makers per contract):

| Setup | Error cut | Spread | Market makers |
|---|---|---|---|
| previous defaults | 33% / 34% / +2% | 7.23 | −0.43 |
| no forecast-only funds | 35% / 37% / +2% | 7.05 | −0.61 |
| market makers anchor 30% on trades | 45% / 46% / −1% | 6.64 | 0.00 |
| capital allocator (TOPS-style rebalancing every 5 min) | 33% / 31% / +3% | 7.31 | −0.51 |
| **no forecast-only funds + 30% anchor (now the default)** | **52% / 51% / −1%** | **6.90** | **−0.19** |
| no forecast-only funds + 20% anchor | 46% / 48% / +1% | 7.09 | −0.31 |

- **Forecast-only funds** took the other side of better information and pulled prices back to the forecast; they're
  out of the default population.
- **Anchoring on trades** lets market makers learn from where informed money has pushed the book, which is the
  biggest single gain. 30% beats 20% everywhere information exists; its cost when there's none is about 0.06 points.
- **The capital allocator didn't help:** five minutes of realised P&L is mostly luck, so it moved capital almost at
  random. It isn't in the code.

## The market-maker fix

The first run found market makers losing 1.9 points per contract to informed funds: the 6-point spread cap couldn't
widen when informed flow picked up, which Glosten & Milgrom say a market maker must do to break even. Faster learning
from order flow only trimmed the loss and made prices noisier, so this PR does what real venues do instead:
- **Toxicity widening:** a market maker's half-spread grows with how one-sided the last minute's flow has been (up to
  6 more points at fully one-sided flow; none below 70% imbalance), since one-sided flow is what informed traders look
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

**Where widening starts.** At first it started at 30% one-sided flow, which noise traders alone often reach, so spreads
widened with no informed money around. Raising the threshold (fees off, 3 seeds each; quoted spread / error cut /
market makers per contract):

| Scenario | 30% | 50% | **70% (default)** |
|---|---|---|---|
| baseline | 7.93 / 32.9% / −0.26 | 7.83 / 33.3% / −0.46 | **7.23 / 33.0% / −0.43** |
| unpriced edge | 7.19 / 34.7% / +0.51 | 7.24 / 34.8% / +0.28 | **6.70 / 34.3% / +0.06** |
| few informed funds | 7.12 / 23.3% / +0.48 | 6.80 / 21.9% / +0.42 | **6.18 / 21.9% / +0.26** |
| no informed funds | 5.71 / 2.3% / +2.11 | 5.45 / 2.5% / +2.02 | **5.23 / 2.3% / +1.94** |
| noise traders only | 5.09 / 0.2% / +2.00 | 4.98 / 1.9% / +1.92 | **4.69 / 1.2% / +1.82** |

70% gives 7-13% tighter spreads in every scenario with the same accuracy, and market makers still about break even.

## Fees

The venue's fees are off by default (a points game has no exchange to fund). `--fees kalshi` turns on Kalshi's taker
fee and the market-maker rebate it pays for; the simulation, the demo and the server all take the flag. The live LMSR
exchange's 5% fee is separate and unchanged.

## Caveats
- Synthetic markets: the forecast's errors are random and shrink at a fixed rate, and informed funds read a noisy p\*
  directly. The real test is a replay on recorded London data (`services/city/venue_demo.py --replay-from`).
- Brier scores against outcomes (in `results.json`) are too noisy to separate the forecast from the price at 216
  contracts; distance to the known truth is the reliable measure.
