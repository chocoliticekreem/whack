---
title: Exchange design — a Kalshi-style live exchange with AI market makers, hedge funds and retail
date: 2026-11-07
status: target design (proposal) · what is built today is in market-structure.md
sources:
  - Kalshi fees, https://help.kalshi.com/trading/fees (taker 0.07·C·P·(1−P), makers free on standard series)
  - Kalshi Market Maker Program filings at the CFTC (quoting obligations on spread, size, uptime and coverage; fee rebates)
  - services/city/ at 50a6fd2 (#66); docs/market-structure.md; docs/judge-prep.md; analysis/economy/
---

# Exchange design

**OverWatch is an exchange, not a betting app.** As on Kalshi, every trade has a buyer and a seller.
The exchange matches them, holds the collateral, settles on the published reading and charges a fee.
It never takes a side. Prices come from the order book.

**One real player trades against thousands of AI participants** in three tiers: market makers, hedge
funds and retail. They behave as their real-world counterparts do, so the prices are as efficient as
the information in the market allows.

What runs today (the LMSR house that quotes every market, and seven AI traders staking a flat 100) is
described in [market-structure.md](market-structure.md). This doc is where that goes.

## 1. The exchange

| Rule | Design |
|---|---|
| Contract | Binary YES/NO. A YES contract pays 100 points if the outcome happens, a NO contract pays 100 if it doesn't. Prices run 1–99 |
| Multi-outcome questions | One binary contract per outcome, as Kalshi lists events: hub surge is five markets ("Will King's Cross surge most?" and so on), and a bracket ladder is one market per bracket |
| Order book | A central limit order book per contract, with price-time priority. One book serves both sides: a NO bid at 40 is an offer to sell YES at 60 |
| Orders | Limit orders rest on the book (makers). Market or immediate-or-cancel orders take liquidity (takers). Post-only orders for market makers. Cancel at any time |
| Positions | Tradable until close: buy, sell or flip at any time, not held to settlement |
| Collateral | Fully collateralised. Buying YES at 60 locks 60 per contract, NO at 40 locks 40, so a matched pair locks exactly the 100 it pays. Nobody else is exposed |
| Fees | Kalshi's schedule: takers pay ceil(0.07 · C · P · (1 − P)), where C is the number of contracts and P the price as a fraction. That is 1.75 points per contract at 50 and 0.07 at 1 or 99. Makers pay nothing. Fees are the exchange's only income and fund the market-maker rebates |
| Limits | A per-account position limit per contract, order-rate limits and self-trade prevention |
| Integrity | Unchanged from today: the source and rule on every contract, trading closes 60 s before settlement, settlement on the first reading after close, halts on stale feeds and 3σ jumps, and void with every order cancelled and collateral returned |
| Courtsiding defences (#61, carried over) | **Speed bump:** incoming orders wait the series' delay before they reach the book (45 s on clean sheet, 2–5 s elsewhere). Cancels are never delayed, so a market maker can always pull a stale quote. **Suspend on change:** when a watched feed moves past its threshold, the book halts, every resting order is cancelled and queued orders are dropped. Market makers requote around the updated forecast after the cool-off. **Position limits:** 10% of equity per contract on locally observable series, 25% elsewhere, with queued orders counted |

The 50 simulated futures (blended with the central-London simulation since #55) stay, as **the public
forecast**: shown on every card, and the starting point for market makers' fair value. They no longer
are the price.

## 2. Market makers: the same role as in real life
Market makers keep a two-sided market open and earn the spread. They don't try to predict the outcome;
they try to stay flat.

| What they do | How |
|---|---|
| Quote both sides, continuously | A bid and an ask at a set size around their fair value. Fair value starts from the public forecast and moves with fills, order-flow imbalance and related contracts |
| Earn the spread, pay for being picked off | Profit = spread captured from takers − losses to better-informed takers (adverse selection) − the cost of holding inventory |
| Manage inventory | Skew both quotes away from the side they're long, so the next fills flatten them (Avellaneda & Stoikov, 2008). Hard inventory limits per contract |
| Learn from flow | Move fair value after each fill: a run of buys means someone may know something (Glosten & Milgrom, 1985) |
| Hedge across contracts | Keep linked contracts coherent. The YES prices across hub surge's five markets should sum to about 100; City Pulse moves with the hubs |
| Widen or pull around news | Before a scheduled TfL crowding publish (every 5 min), a VIX frame or a Heathrow update; in a halt or a suspension; when the London VIX spikes; and in the last minute before close. The speed bump protects them from takers who see the news first, because a cancel always beats a delayed order |
| Meet obligations, earn rebates | Like Kalshi's Market Maker Program: a maximum spread, a minimum size, an uptime and the share of contracts covered. In return, a share of taker fees as a rebate |

About 10–20 of them, with large capital and tight risk limits. At least two designated market makers
quote every contract from the moment it opens, so no book starts empty.

## 3. Hedge funds, in tiers
Funds trade on information. They take liquidity when their edge beats the spread plus the fee, and rest
passive orders when they're patient. Every fund turns its feed into a **probability**, not just a side,
and sizes with fractional Kelly inside its risk limits.

| Tier | How many | Capital | Strategy |
|---|---|---|---|
| **1. Multi-strategy platforms** | 3–5 | Largest | Pods running different strategies on different feeds. The platform moves capital between its own pods by track record: TOPS inside the fund, the way Marshall Wace runs |
| **2. Single-strategy quant funds** | 50–150 | Medium | One strategy family each, with varied settings (lookbacks, thresholds, horizons): hub laggard reversion, VIX fade, Heathrow stack drain, line trouble, rain catalyst, contrarian. Plus the Simulation fund (the forecast of #54/#55) and a model fund (the 50 futures) |
| **3. Emerging funds** | Hundreds | Small | Simpler and noisier versions of tier 2, launched and closed continuously |

Capital follows results. Investors (a fixed capital pool per tier) top up funds that perform and redeem
from those that don't. A fund that loses half its capital is wound down and a new one launches in its
place. Over time, money moves to the strategies and feeds that actually have alpha: that ranking is the
TOPS leaderboard.

## 4. Retail
Thousands of small accounts that arrive, trade a little and leave. They mostly send market orders and
carry the documented retail biases:
- overpaying for long shots;
- herding onto the most-traded side;
- chasing the last move;
- backing the hub they're standing near;
- anchoring on round numbers.

Retail pays the spread and the fees. That flow is what market makers earn from, and what pays funds to
gather information (Grossman & Stiglitz, 1980).

## 5. The player
One account, on the same rules as everyone else. The player can take (cross the spread) or make (rest a
limit order, and so act as a market maker). They see the order book, the tape and each tier's flow, and
fund positions with a delay, like a 13F filing. Their score is a percentile against every AI account:
"you'd rank 312th of 2,000".

## 6. Why the prices end up efficient
**Efficient here** means nobody earns on average after the spread and fees, except by having better
information. A price is efficient if it sits within the spread of the true probability.
- **Kyle (1985) and Glosten & Milgrom (1985)** describe exactly these three groups. Competitive market
  makers set spreads so they break even: what they earn from retail pays for what they lose to informed
  traders. Each informed trade moves the price towards what the informed know.
- **Market selection** (Blume & Easley, 2006): Kelly-sized funds with more accurate beliefs grow, so
  prices come to reflect them.
- **Variety beats headcount.** Ten thousand copies of six rules are six rules. Funds need different
  feeds, settings and horizons, and each can read a noisy version of its feed, so their errors don't all
  line up.
- **The agents only know what our feeds and forecast know.** The test that the ecosystem adds
  information is calibration: the traded price should beat the public forecast alone at close.

## 7. How we'd show it works
Build it first in `analysis/economy/simulate.py`, which already mirrors today's exchange: add a
matching engine and the four groups, and compare against today's LMSR runs (Brier score at close 0.485
in the base scenario).
- **Calibration:** the closing price against the public forecast. The ecosystem should beat it.
- **A random taker** loses about the spread plus the fee, no more and no less.
- **Known edges close:** the VIX fade (65%) and the stack drain (60%) stop paying once funds trade them.
  So does the biggest leak found so far: copying Tube Crowd on City Pulse beat today's market maker on
  21 of 23 bets out of sample, +48% per point staked ([judge-prep.md](judge-prep.md); n = 23, one
  evening, directional). In this design, a fund running that rule would trade it away.
- **Market makers' P&L**, split into spread earned and adverse selection paid. They should roughly break
  even before rebates.
- **P&L by fund and feed:** which data has alpha.
- **Liquidity:** the spread and depth through each round, and how quickly the book refills after a
  large order.

## 8. What changes in the code
| Today | Target | Where |
|---|---|---|
| LMSR quotes every market (`pricing.Book`) | A matching engine per contract: limit, immediate-or-cancel and post-only orders, price-time priority, one YES/NO book | new `services/city/book.py` |
| A bet moves the stake from cash to escrow; the house takes the other side | Orders lock collateral; fills move it between accounts; settlement pays 100 per winning contract. The house account becomes an exchange fee account, minus rebates | `ledger.py` |
| `POST /bet`, held to settlement | `POST /order`, `/cancel`, `GET /book`, `/positions`, with selling before close | `exchange.py`, `server.py` |
| Pending bets fill after the delay at the LMSR price; a suspension resets the book (#61) | Orders queue for the delay, then reach the book. A suspension cancels resting orders and queued ones. The delay, threshold and cap table stays | `integrity.py` |
| Seven traders, flat 100, outside the ledger | Thousands of agent accounts on the ledger, in tiers, updated every few seconds. Rules, not language models; language models only write the reasons for a few named funds | new `services/city/agents/`, `traders.py` as the strategy library |
| The futures set the price | The futures are the public forecast and the market makers' anchor | `pricing.py` |
| Market cards with odds | A YES/NO price, the order book ladder, a sell button, open orders, the tier panel and the player's percentile | `apps/stream/src/app/multiverse/` |

This replaces the core money path (pricing, the ledger's bet flow, and the betting UI). Proving it in
the simulation first keeps the live exchange untouched until the new design wins on the measures in §7.
