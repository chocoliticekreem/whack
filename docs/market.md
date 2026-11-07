---
title: Market makers and funds — who makes the market, who trades it, and is it efficient
date: 2026-11-07
status: §1–3 as built (main at 1053c8d, #51) · §4–6 a proposal
sources:
  - services/city/ (pricing.py, exchange.py, ledger.py, traders.py, markets.py)
  - docs/questions.md (the measured cues)
  - docs/business.md (roadmap: peer-to-peer order book with market makers)
---

# Market makers and funds

The series, pricing and integrity rules are in [services/city/README.md](../services/city/README.md),
and the money and roadmap in [business.md](business.md). This doc covers the **roles**: who makes the
market, who trades against it, whether the prices end up efficient, and how adding market makers and
funds would get them there.

## 1. Who makes the market now: the house
The house is the only market maker and the counterparty to every bet.

| What it does | How |
|---|---|
| Sets a fair price | 50 simulated futures per market. The price is the share of surviving futures, clamped to 3–97% |
| Absorbs flow | LMSR on top of the model (b = 1200): price_i ∝ model_i · e^(q_i / b), where q_i is the shares sold of option i. A 100-point bet at 50% moves it to 53.8% |
| Charges for it | 5% off every stake. Odds are 0.95 / price, and payouts round down |
| Refuses bad trades | Bets that would pay under ×1.05 are refused. Markets close 60 s before settlement, and as soon as their outcome is decided |
| Limits its risk | A 100,000-point house bankroll and a worst-case exposure cap, netted across each market's outcomes (`ledger.py`) |

What it doesn't do:
- **No two-sided market.** There is no sell or cash-out: every position is held to settlement. To
  lean against a price, you buy the other options.
- **No inventory management.** The LMSR prices against the model and the bets so far. It never skews
  its quotes to shed risk, and it never trades.
- **Nobody shares its risk.** The house takes the other side of every bet.

## 2. Who trades now: players and six single-strategy "funds"
The six AI traders (`traders.py`) behave like small quant funds, each running one strategy on one feed.

| Trader | Fund style | Strategy | Measured on Saturday ([questions.md](questions.md)) |
|---|---|---|---|
| Tube Crowd | Mean reversion, plus momentum | Hub surge: back the hub furthest below its usual level. Hub of the hour: back the leader, unless a match crowd heads for a hub. City Pulse: follow the usual daily curve, else the last 5-min move | The laggard won 23/66 = 35%, against 20% at random. The leader held the hour 9/16 = 56% |
| Camera Eye | Volatility mean reversion | London VIX: bet the last 5-min move reverses | 26/40 = 65% |
| Plane Spotter | Flow mean reversion | Heathrow stacks: bet the last 10-min build drains | 28/47 = 60% |
| Line Watcher | Event-driven | Clean sheet: No when fresh unplanned trouble is on the board, else Yes | — |
| Rain Radar | Catalyst | City Pulse Over, only while it's raining | — |
| The Contrarian | Fades the crowd | From halfway through the round, backs the best-priced option against the most-staked one | — |

How they trade (`exchange.ai_bets`):
- **A flat 100 points, once per market,** whatever the edge or the price.
- **Outside the ledger.** Their bets move the price players get, but they have no account, no bankroll
  limit, no position cap and no exposure check. Their record (won, lost, points from 1,000) is kept in
  memory.
- **No learning.** A trader's record doesn't change its stake or its access to capital.

The Contrarian is the nearest thing to a second market maker, because it takes the other side of the
crowd's flow. But it trades a fixed size with no spread, so it adds risk rather than liquidity.

## 3. Is the price efficient?
**Efficient here** means nobody makes money on average after the fee. Buying at price p pays only if
the true chance q is above p / 0.95. So an efficient price sits inside a **fee band** around the true
chance: no lower than 0.95 · q, and no higher than 1 − 0.95 · (1 − q).

Today the prices aren't efficient. These figures come from the real pricing code (`pricing.Book`):
1. **Known edges stay open.** The VIX reversal wins 65% and the stack drain 60%, but the London VIX
   and Heathrow markets open near even: the line is the current value, and the futures replay today's
   moves whatever the last one was. One 100-point trader bet moves 50% to 53.8%. Reaching 61.7%, where
   a 65% belief stops paying after the fee, takes about **340 points**.
2. **Old bets tilt the price for good.** Each bet adds a fixed amount to the price's log-odds that never
   decays. A 250-point bet at 50% adds +0.36. If the model later moves to 90%, the price shows 92.8%,
   and nobody trades it back.
3. **The house is the only liquidity, and its own bots trade against players.** With real money, that
   is a conflict of interest.

Caveat: these cues come from one dry Saturday, with 40–66 samples each. The 65% has a 95% interval of
roughly 50–80%.

## 4. Target: market makers and funds (proposal)
Every participant gets a ledger account and a bankroll, and pays the same fee and limits as a player.

| Role | Its fair value | How it trades | What it earns | What it fixes |
|---|---|---|---|---|
| **Market makers** (liquidity providers) | none | Fund a market's LMSR liquidity, and share its result pro rata | The 5% fee, minus the market maker's losses when the price was wrong | The house being the only liquidity. Shows whether the fee covers losses to informed traders |
| **Model fund** | the futures model | When the price is outside the fee band of the model, buys it back towards the model. Kelly-sized from a finite bankroll | Mispricing left by player and trader flow | Leftover tilt (§3, point 2) |
| **Data funds** (the six traders, upgraded) | worked out from their own feed, as a probability rather than just a side | Kelly-sized. Capital follows the track record (TOPS) | Their edge, until the price absorbs it | Unpriced edges (§3, point 1) |
| **Simulation fund** (the planned Simulation trader, [build.md](build.md)) | the agent simulation's forecast | As a data fund | Whatever the simulation knows that the statistics don't | Tests whether simulating beats reading the data |

**Sizing.** For a belief q at price p, with the 5% fee, the net odds are o = 0.95 / p − 1, and the
Kelly stake is f = (q · (o + 1) − 1) / o of the bankroll, capped at the 25% position cap. A 65% belief
at a 50% price stakes 26%, so 25% after the cap.

**Trade size to reach a price.** In a two-option market, moving option i from p to t takes
Δq_i = b · ln( t / (1 − t) · (1 − p) / p ) shares.

**Why prices then converge:**
- **Market selection** (Blume & Easley, 2006). When traders size with Kelly, wealth flows to the most
  accurate beliefs, so prices come to reflect them.
- **Every fund has a finite bankroll, the model fund included.** If it could always trade the price
  fully back to the model, it would also wipe out the data funds' information, and the model would
  always win.
- **Keep the fee band.** If prices were always exactly fair, nobody would be paid to gather information
  (Grossman & Stiglitz, 1980). The band and the players' noisy bets are what pay the informed funds.

**Later:** the peer-to-peer order book in [business.md](business.md), with market-maker agents quoting
both sides around their fair value and skewing their quotes by inventory. Selling and cash-out come
with it.

## 5. How we'd show it's efficient
- **Calibration** (already in `/stats`): do 70% favourites win 70% of the time, for both the traded
  price and the model?
- **A random fund** in replay should lose about the fee: no more, no less.
- **Edges close.** Once the data funds trade, the VIX and Heathrow cues should stop winning outside the
  fee band.
- **Recovery.** After a 250-point bet, how many minutes until the price is back inside the fee band.
- **Who earns.** P&L per fund, by feed. That is the TOPS leaderboard: which data has alpha.

## 6. Where it plugs in
| Change | Where |
|---|---|
| Fund and liquidity-provider accounts | `ledger.py`: an account per fund, betting through `place_bet`, so the caps and the exposure check apply |
| Probabilities and Kelly sizing | `traders.py`: each strategy returns a probability as well as a side, and the flat `STAKE` goes |
| Model fund | A new trader in `traders.py`, using `pricing.Book` to size the move back to the model |
| Liquidity providers' share | `exchange.settle`: split each market's house result pro rata to the liquidity funded |
| Measurement | `exchange.stats_json` (`/stats`): per-fund P&L and the fee-band check |
