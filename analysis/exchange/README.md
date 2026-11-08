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

## Results (216 contracts per scenario; fees off and market makers widening from 70% one-sided flow, the defaults)

| Scenario | Contracts | Error vs truth at close: forecast → price (points) | Price closer than forecast | Quoted spread (points) | Expected P&L per contract traded (points): informed / crowd / uninformed / market makers / random taker | Informed + crowd share of fund capital |
|---|---|---|---|---|---|---|
| baseline (20 informed, 20 uninformed) | 216 | 4.1 → 2.7 (-33.0%) | 85% | 7.2 | +4.0 / — / -2.5 / -0.4 / -2.7 | 40% → 74% |
| no informed funds | 216 | 4.1 → 4.0 (-2.3%) | 51% | 5.2 | — / — / -2.0 / +1.9 / -1.3 | 0% → 0% |
| few informed funds (4) | 216 | 4.1 → 3.2 (-21.9%) | 75% | 6.2 | +5.2 / — / -2.3 / +0.3 / -1.9 | 12% → 44% |
| unpriced edge (half the forecasts 10 points low) | 216 | 3.4 → 2.2 (-34.3%) | 87% | 6.7 | +2.9 / — / -2.3 / +0.1 / -2.8 | 40% → 62% |
| noise traders only (60 uninformed) | 216 | 4.1 → 4.0 (-1.2%) | 50% | 4.7 | — / — / -1.8 / +1.8 / -1.2 | 0% → 0% |
| wisdom of a crowd (40 weak readers, no expert) | 216 | 4.1 → 3.1 (-24.4%) | 74% | 4.7 | — / -1.0 / — / +1.0 / -1.8 | 80% → 54% |

On the biased contracts in "unpriced edge", the forecast ended 4.8 points below the truth and the traded price 3.1
below: about a third of the edge was traded away. The ledger audit was clean in every run.

## What it shows

Scored as a market (accuracy, liquidity, orderliness, sustainability), not by any AI group's P&L:

**Working:**
1. **Accuracy: informed money makes prices more accurate.** A third closer to the truth than the forecast with 20
   informed funds, on 85% of contracts; 22% with only four; 24% from a crowd of weak readers with no expert.
2. **Accuracy: uninformed money does no harm.** With no informed funds, or 60 noise traders, prices stay as accurate as
   the forecast: market makers anchored on it absorb the noise.
3. **Liquidity: two-sided books with 4.7-7.2-point quoted spreads,** tightest when flow is uninformed and widest when
   informed money is trading, as on a real exchange.
4. **Sustainability: market makers break even or better in every scenario** (−0.4 to +1.9 points per contract), so
   liquidity provision is viable. Before the fix below they lost 1.9 points per contract to informed funds.
5. **Capital moves to the funds that know more** (market selection): informed funds go from 40% to 74% of fund capital.
6. **Orderliness:** no crossed books, no trades after close, clean ledger audits (the venue's own checks and tests).

**Still open:**
1. **Edges are only partly closed** (about a third of a 10-point bias). Informed capital is limited by fractional Kelly and
   the 25% position limit, and market makers anchored on the forecast lean against the informed flow.
2. **Funds that just trade the forecast lose 3-8 points per contract** in every scenario: they take the other side of
   better information. They shouldn't be a default participant.

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
