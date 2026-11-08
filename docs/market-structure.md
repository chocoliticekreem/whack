---
title: OverWatch market structure — who makes the market, how prices form, and whether the points economy balances
date: 2026-11-07
status: reference · read against services/city at 1053c8d · simulation in analysis/economy/
---

# OverWatch market structure

Ground truth is the code in `services/city` (cited as `file:line`), the live service on :8051 (read-only GETs at
20:14 UTC, 7 Nov 2026) and the simulation in `analysis/economy/simulate.py`. Outside claims link to a source in
[References](#references). Numbers marked *sim* come from `analysis/economy/out/summary.md` (16 seeds × 7 days,
1,008 rounds per seed).

## What changed on main since this was written (checked at e59773b, #81)

The design and the simulation below are unchanged. These facts about the code are now different; `file:line`
citations below point at 1053c8d.
- **Seven AI traders, not six:** the Simulation trader (#55) bets from the central sim. Each stake is min(100, 10% of
  the trader's points), not a fixed 100, and a trader sits out under 10 points (`traders.py`, #72).
- **AI records persist** in the ledger DB (`ai_records`, #72), so a restart no longer resets them. They are still not
  double-entry, but AI open stakes now count toward the house exposure cap (#81).
- **clean_sheet closes with its first 5-minute round** (R1 applied, #72) and has a 45 s bet delay, a pause on any line
  change and a 10% cap (#61). The ~59-minute window in section 7.2 is gone.
- **Courtsiding defences on every series** (#61): bet delay 5 s on hubs and pulse, 2 s on VIX and Heathrow; suspend on
  a big feed move; caps 10% on locally observable series, 25% elsewhere.
- **Crowding settles on the first reading TfL stamped after close** (#72), and the settlement record holds
  `measuredAt`; a value measured at or before the close voids (#81). Station rounds take about 14 min to settle.
- **Pricing:** City Pulse walks follow the usual-Saturday curve and its line is the futures' median on a step that
  fits their spread (#72, #77); hub_surge uses `RelativeRaceSim` (#77). After the 60% ruled-out cap, likelihood weights keep
  moving the price (#72). `SIM_WEIGHT` defaults to 0.
- **Local markets** (#71) on 33 real sensors use the same LMSR, the strictest integrity setting (45 s, 10%), at most 12
  per round.
- **Restart** rebuilds open hourly markets from their records and reads the position cap from the ledger (#81).

**An order-book venue implements [exchange-design.md](exchange-design.md), live at `/multiverse/exchange` beside this
one** (#90, #94, #112, #121). `services/city/venue_server.py` (:8062) lists the same markets on one order book per binary contract with no
house: AI market makers quote both sides, hedge-fund and retail agents trade, takers pay Kalshi's fee and makers pay
none, and the same speed bumps, position limits, halts and settlement sources apply. It keeps its books and ledger in
memory and reads the recorder files read-only, so nothing in this document changes: the main screen still runs the LMSR
house described below. How the two compare on price is not measured yet. Details in
[services/city/README.md](../services/city/README.md#parallel-exchange-venue-8062).

## The 2-minute version

- **The house makes every market.** It runs a logarithmic market scoring rule (LMSR) market maker
  ([Hanson 2003][hanson03]). The starting price is not 50/50: it comes from the 50 simulated futures. The house always
  quotes, so nobody needs a counterparty (`pricing.py:321-347`).
- **Prices move two ways.** Bets move them through the LMSR, with b = 1200 and a 5% fee (`pricing.py:18-19`). New
  data rules out futures that disagree with it, and that moves the starting price underneath (`pricing.py:295-318`).
- **Losses are bounded twice.** The LMSR's worst case per market is b·ln(1/p_winner): 832 points on a 50/50 binary
  at b = 1200. The ledger also refuses any bet that would push the worst case across all open bets above
  min(100,000, bankroll + house P&L) (`ledger.py:432-452, 506-510`).
- **Six AI traders.** They are directional traders, not market makers. Each reads one feed and stakes a fixed 100
  through the same book (`exchange.py:234-257`). Their points are a scoreboard kept outside the ledger.
- **Faucets and sinks.**
  - The only faucet is the one-time 1,000-point grant (`ledger.py:41, 293`).
  - The only sink is net house winnings: the 5% fee, rounding down, and losers' stakes.
  - *sim:* over a week the house absorbs 3.1% of stakes, which is 13.6% of the points minted.
  - Supply grows only with sign-ups.
- **Does it balance?** Yes, but thinly.
  - At b = 1200 the fee is what keeps the house positive: fee 0% gives −5,129/week, 5% gives +11,928/week (*sim*).
  - Skilled players win: +14.5% ROI and 8.7 of the top 10 (*sim*).
  - A perfect-information leak drains the 100,000 bankroll within days (*sim*), which makes betting windows and
    courtsiding the real risk.

## 1. Participants and the flow of points

```mermaid
flowchart LR
  G[Grant: 1,000 once per player<br/>ledger.py:293] -->|"+cash"| PC[player_cash]
  PC -->|"bet_hold: stake"| PE[player_escrow]
  PE -->|"bet_win: payout"| PC
  PE -->|"bet_loss: stake"| H[house account<br/>per player]
  H -->|"bet_win: payout - stake"| PC
  PE -->|"void_refund"| PC
  subgraph Book["Per market: LMSR book (pricing.py:321)"]
    F[50 futures → model prob] --> L[prob_i ∝ model_i · e^(q_i/b)]
  end
  PC -. "quote: shares, locked odds" .- L
  AI[6 AI traders<br/>fixed 100 stake] -->|"shares move q, no ledger entry"| L
  AI --> R["record: points (exchange.py:58)<br/>not in the ledger"]
  BR[House bankroll 100,000<br/>notional, ledger.py:53] -. "caps worst case" .- H
```

| Participant | Role | Where its points live |
|---|---|---|
| Player | Trades against the house. Must hold enough cash, stay under the 25% position cap and the daily loss limit | `player_cash` and `player_escrow` entries; equity is their sum (`ledger.py:330-332`) |
| House (LMSR) | Counterparty to every bet; always quotes | `house` entries, one leg per player bet (`ledger.py:543-552`); bankroll 100,000 is a config number, not an account (`ledger.py:53`) |
| 50 futures | Set and update the prior the LMSR prices around | n/a |
| AI traders (6) | Directional bettors with stated reasons; their shares move the book | `Exchange.record[...]["points"]`, starting at 1,000, in memory (`exchange.py:58, 271-279`) |
| Ledger | Double-entry and append-only; balances are sums of entries | SQLite; `audit()` re-checks every invariant (`ledger.py:669-701`) |

Two structural facts matter for the economy:

1. **AI bets are not double-entry.** Their shares are committed to the book (`exchange.py:256`), so they change the
   price players get. Their wins and losses only change their own in-memory scoreboard, so they never touch the
   house account, the exposure cap or the point supply.
2. **The bankroll is notional.** `house()["cap"] = min(EXPOSURE_CAP, bankroll + pnl)` (`ledger.py:451-452`). If house
   P&L reaches −100,000 the cap hits zero and every bet is refused.

## 2. How a price is formed

1. **Prior from 50 futures.** Each future is a simulated path from the last real reading (`pricing.py:74-210`). The
   probability is each option's share of surviving futures, plus a half-future prior, clamped to [3%, 97%] and
   renormalised (`pricing.py:39-45`).
2. **LMSR quotes on top.** `prob_i ∝ model_i · e^(q_i/b)`, where q_i is the net shares sold on option i
   (`pricing.py:328-331`). This is Hanson's LMSR with the initial q set to b·ln(model_i), the "seed at the prior" form
   in the [Gnosis primer][gnosis].
3. **A bet buys shares.** The book takes 5% off the stake, then spends the rest:
   `shares = b·ln((w_i + S(e^(spend/b) − 1)) / w_i)`, where w are the weights and S their sum (`pricing.py:333-339`).
   Locked odds are shares ÷ stake, floored to 0.01 and capped at 20. Under 1.05 the bet is refused
   (`pricing.py:28-32`). The payout is stake × odds, floored to a whole point (`ledger.py:174-176`).
4. **New data re-weights the futures.** Each reading rules out futures that disagree with it, never more than 30 of
   the 50 (`pricing.py:303-311`). q is unchanged, so the price moves without a trade.
5. **Settlement.** Settlement uses a reading published after bets close. Winning shares pay 1 point each, and bets pay
   at their locked odds (`ledger.py:543-552`).

**Worked example (live, `city_pulse-2020`, read at 20:14 UTC).**

| Step | Numbers |
|---|---|
| Futures | 38 of 50 end over 22.5, 12 under |
| Model prior | over (38+0.5)/51 = **75.5%**, under **24.5%** |
| Tube Crowd bets 100 on Under | spend 95 → 347.7 shares → locked odds **3.47** (the live `aiBets` shows 3.47) |
| Price after | under 0.245·e^(347.7/1200) / Σ = **30.3%**, over **69.7%**. Live `/markets` shows 0.3026 / 0.6974 |
| A player then bets 100 on Over | 134.0 shares → odds **1.33**, payout 133. Price moves to 72.0% / 28.0% |
| If the next reading rules out 10 Over futures | the model goes to 69.5% / 30.5%; with the same q the price goes to **63.1% / 36.9%** with no trade |
| House worst case on this market | Hanson bound b·ln(1/p): 1,687 if Under wins, 337 if Over wins |

Reproduce with `smooth`, `Book` and `locked_odds` in `analysis/economy/simulate.py`, which mirror the code line for
line.

## 3. Who takes the other side

**The house, through the LMSR.** For a cost-function market maker seeded at a prior p, the worst-case loss equals b
times the entropy of p, at most b·ln(1/p_winner). For a uniform prior that is b·ln n ([Hanson 2003][hanson03];
[Gnosis primer][gnosis]: funding = b·ln n).

| b | binary at 50% | binary, winner at the 3% floor | 5-way uniform | 5-way, winner at the floor |
|---|---|---|---|---|
| 300 | 208 | 1,052 | 483 | 1,078 |
| 600 | 416 | 2,104 | 966 | 2,156 |
| **1200 (coded)** | **832** | **4,208** | **1,931** | **4,311** |
| 2400 | 1,664 | 8,416 | 3,863 | 8,623 |

Three things change the textbook bound in our code:

- **The prior moves mid-market** (futures are pruned). Hanson's bound holds for a fixed prior. When pruning moves
  the price toward what happens, it usually helps the house, since late bettors pay the informed price. The formal
  bound only holds piecewise.
- **Fee, floors and the 20× cap.** All three make payouts at most the LMSR shares (`pricing.py:28-32`), so the real
  liability is at most the LMSR liability.
- **The ledger's exposure cap.** This is the real backstop. Every player bet is checked against the worst single
  outcome per market, netted against the other stakes, summed over all open bets (`ledger.py:432-446`). It must stay
  under min(100,000, 100,000 + house P&L) (`ledger.py:506-510`). It counts locked payouts, not LMSR theory, so it is
  exact for player bets. AI bets are outside it.

**What b = 1200 implies.**
- A 100-point bet on a 50/50 binary moves the price **3.8 points** with the 5% fee (`impact100` in the simulation;
  the README rounds this to "about 4").
- The house risks up to **832 points per 50/50 binary** and ~1,931 per 5-way race, so a three-market round can lose
  ~3,600 at most when the priors are mid-range.
- The 100,000 bankroll covers about 27 such rounds of maximum loss in a row.
- Liquidity is deep relative to stakes: a typical 50–250 stake moves the price 2–9 points.
  [Othman et al.][othman] warn that a b set too high "barely moves after large bets", which wastes the information in
  a trade.

## 4. The AI traders

| Trader | Feed | Series (`traders.py:199-201`) | Rule | Evidence for the rule |
|---|---|---|---|---|
| Tube Crowd | TfL crowding | hub_surge, city_pulse, hub_hour | back the hub furthest below usual; ride the usual curve; back the hour's leader | 36% vs 20% luck (README); leader held 9/16 (`traders.py:98`) |
| Camera Eye | London VIX | london_vix | fade the last 5-min move | 65%, 26/40 (`traders.py:128`) |
| Plane Spotter | adsb.lol stacks | heathrow_stack | fade the 10-min move | 60%, 28/47 (`traders.py:140`) |
| Line Watcher | Line Status | clean_sheet | NO on fresh unplanned trouble, else YES | unproven |
| Rain Radar | Open-Meteo | city_pulse (in rain) | Over when it rains | unproven |
| The Contrarian | bets | all, after half the window | fade the stake favourite, take the best-priced alternative | none: a deliberate foil |

**Market makers or directional traders?** Directional. They never quote two-sided prices and never provide resting
liquidity. They take one side at the house's price for a fixed 100 stake, once per market (`exchange.py:236-257`).
They still do two useful jobs:

- **Price discovery.** Their bets move the book toward their signal before players arrive, as in the
  "TOPS" idea of one trader per alternative-data feed. *sim:* Brier score falls from **0.559 at open to 0.485 at
  close** at b = 1200. Pruning contributes to that too, and the run does not separate the two.
- **A visible opponent with reasons** (`traders.json` shows each one's `why`).

**Track record (*sim*, 7 days, from 1,000 points).**
- Tube Crowd: +52,928 (2,079 bets)
- Camera Eye: +9,553
- Plane Spotter: +4,993
- Line Watcher: +188
- Rain Radar: +39
- **The Contrarian: −62,544** (3,160 bets, −20% per bet)

The specialists' gains depend on the assumed hit rates, which come from one day of data (n = 40–47). The Contrarian's
loss is structural: it always buys the second favourite after informed flow has moved the price. Live at 20:14 it was
already 2–4 with 992 points.

**Should they get separate bankrolls?** Yes, for two reasons.
1. Nothing stops their points going negative: there is no cash check, cap or limit on AI bets
   (`exchange.py:252-257`). *sim:* The Contrarian loses about 62 points a round (−8,935 per 12-hour day), so it goes
   below zero after about 16 rounds, roughly 80 minutes of play.
2. Leaving them off the ledger hides real house risk. If their bets were ledgered, the base house book would be
   +12,770 instead of +11,928. At fee 0% it would be −34,208, and with the overconfident model
   −33,244.

See recommendation R2.

## 5. The points economy

**Faucets.**
- The grant is 1,000 points, once, at `POST /player/new` (`ledger.py:276-294`), limited to 5 per hour per IP.
- There are no refills, streaks or daily points in the code. `business.md` proposes "free daily points and streaks".

**Sinks.**
- Net house winnings: the 5% fee inside every quote (`pricing.py:335`), rounding down of odds and payouts
  (`pricing.py:31`, `ledger.py:176`), and losing stakes, net of winners.
- Points that go into the `house` account never return to players. Nothing spends house profit.
- No sink applies to AI points.

**Balance (*sim*, base, 40 players at the start + 8 per day, each plays 30% of rounds).**

| Day | Minted | Player equity | Held by the house |
|---|---|---|---|
| 1 | 40,000 | 39,284 | 716 |
| 3 | 56,000 | 51,606 | 4,394 |
| 7 | 88,000 | 76,072 | 11,928 (13.6% of minted) |

- **Inflation.** Total supply rises only with sign-ups (+8,000 a day here). The points each player holds deflate:
  random players end at −519 on average, and only 16% finish up.
- **House margin.** 3.1% of stakes (95% CI of the 7-day P&L: ±2,524). The house lost money on **26% of days**; its
  worst day was −4,714 and its worst 7-day run +4,535.
- **Daily refill +100.** Minted rises to 128,000 (+45%) and player equity to 108,448. Leaderboard numbers inflate,
  and long-tenured accounts out-rank better forecasters. This is the classic faucet-over-sink inflation of MMO
  economies ([Ludgate][faucets]; [EVE monthly economic reports][eve]).
- **Throttle.** In the simulation, **29,318 bet attempts per run found no room**: they were blocked by the daily
  loss limit (500 gross, counting escrow, `ledger.py:419-424`), the 25% cap or cash. The loss limit therefore also
  caps how fast the sink works. A player can never have more than 500 points in play.
- **Skill.**

  | Player type | ROI | Finish up | Average P&L |
  |---|---|---|---|
  | Informed (prior with noise + a private signal right 65% binary / 40% 5-way) | +14.5% | 76% | +798 |
  | Herding | −4.5% | | |
  | Random | −14.8% | | |
  | Contrarian | −16.9% | | |

  Informed players hold 8.7 of the leaderboard top 10. Without the private signal, the same informed players lose
  20.5%: knowing the opening probability is not an edge, because the price is already there and later data moves
  away from a stale view. **Skill wins only when it is real information.** That is the intended design (see section
  7).
- **Can the house be drained?**
  - By ordinary skill: slowly. "Sharp" players (signal right 75% / 55%) take the house to −31,323 a week, losing on
    91% of days.
  - By a perfect-information leak: yes, fast. Ten oracle accounts drain the full 100,000 within the week. The
    exposure cap then refuses **68,578 bets per run**, so it freezes the whole exchange: a fail-safe that also works
    as a denial of service.
- **Leaderboard and seasons.** The board ranks equity (`ledger.py:657-665`) with no reset. Equity rewards tenure and
  multi-accounting. The per-IP limit can be dodged by sending one's own `X-Forwarded-For` (debt marker at
  `server.py:123-125`). Manifold faced the same pressure: it removed loans and transfers and capped subsidies when
  mana gained value ([Manifold 2024][manifold-prize]), and it now uses leagues and seasons ([Manifold FAQ][manifold-faq]).

**Verdict.** Without refills the economy is deflationary per player and inflationary only through sign-ups. The house
keeps a small positive margin at the coded parameters, and that margin depends on the 5% fee. Any daily faucet needs
a season reset to go with it.

## 6. Integrity mechanisms

| Mechanism | In the code | Precedent |
|---|---|---|
| Close before settle | `closes_at = settles_at − 60 s` (`markets.py:109`, `CLOSE_MS` `markets.py:29`) | Kalshi Last Trading Time vs Expiration ([NHIGH terms][kalshi-nhigh]) |
| Settle on a reading published after close | crowding waits for the first reading after `closes_at`, up to 6 min (`markets.py:205`) | Kalshi KXBTC15M settles on a 60-s average, not one print (see `market-design.md`) |
| Decided-outcome lock | a final answer while open closes the market at once (`exchange.py:86-93`) | Kalshi early close on hitting the criterion ([RAINNYCM][kalshi-rain]) |
| Odds floor 1.05 and cap 20 | `pricing.py:21, 28-32` | the [3%, 97%] clamp means "no side is ever free" (`pricing.py:40`) |
| Position cap 25% of equity per market | `exchange.py:208-212` | Kalshi $25k per-member limit ([CFTC filing][kalshi-limit]); PredictIt $850, now $3,500 ([CFTC 25-20][predictit]) |
| House exposure cap | `ledger.py:432-452, 506-510` | the bounded subsidy of an LMSR ([Hanson 2003][hanson03]) |
| Halts: stale feed (3 / 6 min), 3σ jump, 2 clean ticks to resume | `exchange.py:165-168`, `data.py:28`, `markets.py:654-681` | Betfair in-play suspension ([tech-insider][betfair-delay], secondary) |
| Void and refund | no reading in time → void (`markets.py:637-648`); restart refunds only unsettleable bets (`exchange.py:97-120`) | Polymarket 50-50 / refund; Kalshi "last fair price" (`market-design.md`) |
| Settles exactly once; idempotent bets | unique indexes `entries_once` and `entries_settle_once` (`ledger.py:110-113`) | exchange clearing practice |
| Responsible play | 500 daily gross-loss limit, confirm above 25% of cash, 1 h / 24 h breaks (`ledger.py:42-46, 412-428`) | UK Gambling Commission expects in-play risk controls ([UKGC][ukgc]) |

## 7. Courtsiding and oracle reliability

**The question.** Our source of truth is TfL data. A rider sees a train stop before the TfL API updates and bets
while the market is open. Is that exploitable?

### 7.1 Precedent

- **Tennis courtsiding.** In January 2014 Daniel Dobson was arrested at the Australian Open. He was accused of
  relaying points from a device in his shorts seconds before the official feed, so a syndicate could bet before
  odds moved. The charge was withdrawn in March 2014 ([Wikipedia][courtsiding]; [SBS][sbs]). The UK Gambling
  Commission defines courtsiding as "using or transmitting information from a live sporting event for the purpose of
  gambling" and notes that broadcast latency varies ([UKGC][ukgc]).
- **Betfair.** In-play orders wait out a bet delay of 1–12 s (the `betDelay` field), and markets suspend on material
  events such as goals and red cards. Unmatched orders lapse during a suspension ([tech-insider][betfair-delay],
  secondary).
- **Polymarket.** Its 15-minute crypto markets drew "algorithmic traders, bots … seeking to arbitrage mispricings".
  Polymarket answered with a taker fee of up to ~3%, highest at 50%, that funds maker rebates
  ([The Block][polymarket-15m]). Its fee schedule now applies C·rate·p(1−p) widely ([Polymarket fees][polymarket-fees]).
- **Kalshi.** Its 15-minute markets settle on a 60-s average, so one stale or early print cannot decide them
  (`market-design.md` §1, secondary). Its fees follow the same p(1−p) curve ([Kalshi fees][kalshi-fees];
  [Allium][kalshi-formula], secondary).

### 7.2 Which series are exposed

The exploit needs two things: a **single, locally observable event** that **decides** the market, and a betting
window that is **still open** when it happens.

| Series | Settles on | Betting window | Exposure | Why |
|---|---|---|---|---|
| **clean_sheet** | first non-Good Line Status reading among all Good Tube lines (`markets.py:600-610`) | **~59 min**: opens at :29, closes 60 s before the next :30 (`markets.py:109, 513-514, 569-596`) | **High** | A single incident on one line decides it. A rider on the affected train knows before TfL posts the status, the recorder polls it (60 s, `recorder/README.md:52`) and the 1-s tick locks the market (`server.py:30`) |
| heathrow_stack | adsb.lol holding total, last reading ≤ T | 5 min, closes T−60 s | Low–medium | A private ADS-B receiver is faster than adsb.lol, but bets close 60 s before T. A spotter still needs to forecast the count, not just see it |
| hub_hour | highest vs-usual at the next :00 | ~59 min | Low | An aggregate of 5 hubs. Each 5-min TfL reading reprices it through pruning; one person on a platform sees one station |
| hub_surge, city_pulse | first reading after close; 20 stations | 5 min | **Low** | Aggregates. Nobody sees the next published reading early, because it is published after betting closes |
| london_vix | multiverse model output | 5 min | **Very low** | A model output. Nobody can observe it before we compute it |

**Quantified (*sim*, `courtsider()`; one account staking 250 at the 25% cap; TfL publish lag assumed U(0, 180) s
plus a 60-s poll):**

| Variant | Bet delay | P(break while betting is open) | P(courtsider's bet accepted, given a break) | Profit per hourly market | Per 12-h day, 10 accounts |
|---|---|---|---|---|---|
| As coded (59-min window) | 0 s | 50% | 99% | 252 | 30,297 |
| As coded | 30 s | 50% | 92% | 230 | 27,562 |
| As coded | 60 s | 50% | 77% | 189 | 22,639 |
| As coded | 120 s | 50% | 44% | 104 | 12,432 |
| Suspend when a faster related feed moves (assumed to show the stop in 30–90 s) | 30 s | 49% | 77% | 190 | 22,831 |
| Same | 60 s | 49% | 26% | 62 | 7,444 |
| **5-min betting window** (close with the first round) | 0 s | **5%** | 96% | **8** | 950 |
| 5-min window + 30 s delay | 30 s | 5% | 78% | 6 | 771 |
| 5-min window + suspend + 30 s delay | 30 s | 5% | 65% | 5 | 644 |

A bet delay alone does little, because TfL's lag is minutes, not seconds: even 120 s leaves 44% of breaks
exploitable. A delay helps only when it is at least as long as the time a faster feed takes to show the stop: 60 s
plus suspension cuts the take by 75%. That figure rests on the assumed 30–90 s detection, and the model's 0% at 90 s
is an artefact of that bound. **The window is the lever.** Closing clean_sheet with its first 5-minute round cuts the courtsider's
expected take by ~97%. For scale, the house's honest edge is about +1,700 a day (*sim*), so ten courtsider accounts
on the coded window out-earn it about 18 times.

### 7.3 Informed trading is the product; riskless extraction is the leak

- **Informed trading is the product.** Reading public data better than the model is what the AI traders do and what
  we want players to do (TOPS: one trader, one alternative-data feed). It moves prices toward the truth (*sim* Brier
  0.559 → 0.485) and costs the house only what the fee and b let it cost. It is risky: the informed player can be
  wrong.
- **Extraction is a leak.** Betting on an outcome that has already happened but is not yet published has no risk.
  It earns stake × (odds − 1) every time. It adds no information, because the market would have learned it seconds
  later anyway ([Hanson 2003][hanson03] on subsidies buying information, not latency). Exchanges treat it as a fee
  and latency problem, not a skill ([Polymarket 15-min fees][polymarket-15m]; [Betfair delays][betfair-delay]).

The design test: **the decisive fact must not exist while betting is open.** Settling on a reading published after
the close passes this test for hub_surge and city_pulse. clean_sheet fails it.

### 7.4 Mitigations, with parameters

| Mitigation | Parameter | Applies to | Effect |
|---|---|---|---|
| Short betting window on single-event markets | clean_sheet `close_before_ms = 55 min` (close with its first round) | clean_sheet | *sim* −97% courtsider take. Keeps the hour-long settle and the live drift toward YES for viewers, without bets |
| Per-series bet delay | new `Series.bet_delay_ms`: 60 s for clean_sheet if it stays open; 0 for aggregates | single-event series | *sim:* 60 s alone cuts the take by 25% (252 → 189); with suspension on a related feed, by 75% (→ 62) |
| Suspend on any change in a related feed | clean_sheet halts when any watched line's severity or reason changes, or the Tube arrivals feed (~30 s cadence, `docs/data-sources.md:37`) shows a gap on a Good line; resume after 2 clean ticks (the existing `CLEAN_TICKS`) | clean_sheet; heathrow_stack on a holding jump | closes the gap between a real stop and its Line Status publish |
| Position caps | keep 25% of equity; add a per-market cap on aggregate player stake (e.g. 2,000) on single-event series | clean_sheet | caps take per market, even across sybil accounts |
| Prefer aggregate markets | list series that settle on an aggregate published after the close | lineup policy | already the pattern for S1/S2/S3/S5 (`docs/questions.md`) |
| Keep close-before-settle and settle-after-close | `CLOSE_MS = 60 s`; crowding settles on the first reading after close | all | already in place; this is why S1/S2 are low exposure |
| Close the sybil hole | trust `X-Forwarded-For` only from a known edge (`server.py:123-125`) | all | stops 10 accounts per IP per 2 h becoming unlimited |

### 7.5 Oracle reliability

**Failure modes, measured today.**
- **Lag.** Line Status is polled every 60 s (`recorder/README.md:52`). On 7 Nov there were 496 readings from 12:06
  to 20:19 UTC, median gap 60 s, none over 90 s (measured from `data/raw/line_status/2026-11-07`). TfL Crowding
  publishes every 5 min, so stale halts are 3 min and 6 min (`data.py:28`).
- **Outages.** The recorder logged TfL `/Mode/bus/Arrivals` **HTTP 504 at 14:58:01 and HTTP 503 at 14:59:58**
  (`data/recorder.log`, lines 30–31). No series settles on bus arrivals today, but the same API family serves Line
  Status and Crowding.
- **Revisions and flicker.** A line can show a non-Good status for one reading and then return to Good. clean_sheet
  settles NO on the first non-Good reading (`markets.py:603-608`), so a one-reading flicker decides it.

**What we already do.**
- **Window.** Settlement uses the first reading after close, or the last reading at most 2 min before T
  (`markets.py:205, 367`).
- **Fallback ladder.** At listing time S4 replaces S3 when the VIX can't be priced (README). There is **no
  settlement fallback source**: a missing reading voids the market.
- **Void.** Stakes are refunded (`markets.py:637-648`). clean_sheet voids after more than 5 min of gaps
  (`markets.py:51-52, 613-616`).
- **Frozen snapshot.** The `settlement` value, source and `fetchedAt` are stored on the result and shown
  (`exchange.py:280-281`). Restart recovery settles from a market record (`ledger.py:122-128`).
- **Stale halt.** `exchange.py:165-168`.

**Proposed cross-check: hold settlement on contradictory readings.** Today a reading settles as soon as it arrives.
Add a hold step between the reading and settlement:
1. **Two-reading confirmation for event series.** clean_sheet settles NO only when two consecutive readings show the
   same line non-Good, or one reading carries an unplanned reason (the `TROUBLE` regex at `traders.py:17`). This costs
   at most 60 s.
2. **Model plausibility for aggregates.** If the settle reading is more than 4σ outside all surviving futures (the
   σ is already fitted per series, `pricing.py:51-68`), wait one more reading before settling. Settle if the next
   reading agrees. If it does not, take the median of the two. If there is still none by the void deadline, void.
3. **Neighbouring sensors.**
   - For a hub, compare against the other stations on the same lines.
   - For clean_sheet, compare against Tube arrivals for that line.
   - For heathrow_stack, compare against the four stacks' individual counts.
   - Disagreement holds settlement until the next reading.
   - Kalshi does the same when its climate-report high conflicts with the METAR highs (see `market-design.md` §1).

## 8. Path to real money

| Dimension | Now (points) | Real-money exchange (`business.md` stage 2) |
|---|---|---|
| Matching | LMSR house quotes everything | Central limit order book: Kalshi (DCM), Polymarket CLOB, off-chain matching and on-chain settlement ([Polymarket CLOB][polymarket-clob]); Betfair back/lay |
| Liquidity | house subsidy, b·ln n per market | designated market makers (Susquehanna at Kalshi, unverified [link][kalshi-mm]); Polymarket liquidity rewards ([rewards][polymarket-rewards]) and maker rebates ([rebates][polymarket-rebates]). A hybrid of AMM and book, as Manifold runs today ([FAQ][manifold-faq]), keeps quotes in thin markets |
| House risk | the house is the counterparty | none in a pure exchange; LMSR loss becomes an explicit, budgeted market-making subsidy |
| Fees | 5% inside the quote | Kalshi 0.07·C·P(1−P) ([fees][kalshi-fees]; the formula is from a secondary source, [Allium][kalshi-formula]); Polymarket C·rate·p(1−p), takers only ([fees][polymarket-fees]); Betfair 2–8% commission on net winnings per market ([Sporting Life][betfair-comm]); PredictIt 10% of profit ([PredictIt][predictit-fees], unverified) |
| Position limits | 25% of equity | Kalshi $25k default, now set per contract ([CFTC filing][kalshi-limit]); PredictIt $3,500 ([CFTC 25-20][predictit]) |
| Settlement | automatic from machine-readable feeds | the same: Polymarket automates short crypto markets and uses UMA (a $750 bond, a 2-h challenge window) elsewhere ([Polymarket resolution][polymarket-uma]) |
| AI traders | scoreboard players | they would need to be funded market makers or be removed. Unfunded "house bots" that move a real-money book would be a conduct problem |

The ledger already separates client cash, escrow and house (`business.md`, "Built for real money"). What changes is
**who funds the other side**. The path is LMSR as a seeded market maker beside a book: the house subsidy becomes a
capped liquidity budget per market, and fees fund maker rebates, as Polymarket's 15-min markets do.

## 9. Simulation

Run it with `python3 analysis/economy/simulate.py` (~4 min, stdlib only). The model is described in
`analysis/economy/README.md`, the full tables are in `analysis/economy/out/summary.md` and every metric is in
`out/results.json`. The exchange rules are mirrored from the code with line citations. The world around them is
assumed: market truths, model error, information arrival, player behaviour and AI hit rates (`ASSUMPTIONS` in the
script).

| Scenario (7 days, 16 seeds) | House P&L (mean ± 95% CI) | Margin on stakes | Days house lost | Price move per 100 on a 50/50 | Informed ROI | Random ROI | Brier at close |
|---|---|---|---|---|---|---|---|
| **Base as coded** (b 1200, fee 5%, grant 1,000) | **+11,928 ± 2,524** | 3.1% | 26% | 3.8 | +14.5% | −14.8% | 0.485 |
| b = 300 | +25,335 ± 1,403 | 7.0% | 2% | 13.6 | +13.6% | −18.0% | 0.519 |
| b = 600 | +21,664 ± 1,767 | 5.8% | 8% | 7.3 | +12.6% | −17.9% | 0.493 |
| b = 2400 | +3,047 ± 3,213 | 0.8% | 42% | 1.9 | +17.1% | −14.1% | 0.485 |
| fee 0% | −5,129 ± 2,283 | −1.3% | 58% | 4.0 | +19.3% | −11.9% | 0.484 |
| fee 1% | −2,104 ± 2,931 | −0.5% | 54% | 4.0 | +17.5% | −12.6% | 0.486 |
| fee 2% | +2,676 ± 3,456 | 0.7% | 39% | 3.9 | +17.4% | −13.8% | 0.487 |
| b = 600, fee 2% | +14,280 ± 1,671 | 3.7% | 15% | 7.5 | +15.3% | −15.3% | 0.493 |
| grant 500 | +385 ± 2,474 | 0.2% | 44% | 3.8 | +15.4% | −15.9% | 0.484 |
| grant 2,000 | +21,686 ± 3,221 | 5.2% | 13% | 3.8 | +11.2% | −17.1% | 0.487 |
| overconfident model | +3,490 ± 3,774 | 1.0% | 43% | 3.8 | +20.0% | −13.2% | 0.493 |
| informed, no private signal | +45,095 ± 2,455 | 13.7% | 0% | 3.8 | −20.5% | −15.3% | 0.490 |
| informed, sharp signal | −31,323 ± 3,579 | −7.2% | 91% | 3.8 | +34.9% | −17.0% | 0.478 |
| daily refill +100 | +19,552 ± 2,957 | 4.7% | 18% | 3.8 | +12.6% | −14.8% | 0.485 |
| 10 perfect-info accounts | **−100,000** (cap hit) | −40% | — | 3.8 | — | — | — |

**How to read it.**
- **Lower b makes the house safer but the market worse.** At b = 300, Brier at close is worse (0.519 vs 0.485),
  herders lose 20%, and one 100-point bet moves a binary 14 points.
- **The fee is the margin.** At b = 1200, fees of 0–2% leave the house at break-even or below against a population
  where a quarter of players have a real edge.
- **Larger grants mean larger stakes.** A bigger grant raises house profit with no other change.
- **The run-to-run spread is wide.** The confidence intervals overlap between neighbouring fee levels.

## 10. Recommended changes

Ranked by impact. "Must" means before the demo. None is applied; the owner of `services/city` decides.

**R1. Must: close clean_sheet with its first round.** Evidence: courtsider take per market falls from 252 to 8
(section 7.2). It keeps the hour-long settle and the live drift.

```diff
# services/city/markets.py:629
-register(Series("clean_sheet", "line_status", build_clean_sheet, settle_clean_sheet, due=hourly(30),
-                settles_early=True))
+register(Series("clean_sheet", "line_status", build_clean_sheet, settle_clean_sheet, due=hourly(30),
+                settles_early=True, close_before_ms=55 * 60_000))
```

Check: `closes_at` = settles_at − 55 min = :35, the end of the opening round. The rule text and the `/round`
headline copy should say "betting closes at :35". The card's countdown already reads `closesAt`.

**R2. Must: give the AI traders a bankroll floor.** Evidence: The Contrarian reaches −62,544 points in 7 days
(*sim*), about −62 a round. It goes negative after about 80 minutes of continuous rounds and then shows on the
leaderboard beside humans.

```diff
# services/city/exchange.py:252 (in ai_bets)
-            try:
-                shares, odds = self.quote(m, option, traders.STAKE)
+            stake = min(traders.STAKE, int(0.1 * self.record[tid]["points"]))
+            if stake < 10:
+                continue  # bust: sits out, like a player under the minimum stake
+            try:
+                shares, odds = self.quote(m, option, stake)
```

The `ai_bets` entry at `exchange.py:257` must then record `stake` instead of `traders.STAKE`. The settle at
`exchange.py:278` already reads `b["stake"]`. Nice-to-have later: ledger accounts for each trader, so AI risk shows in `house()` and the exposure
cap.

**R3. Must: keep the 5% fee and show it.** Evidence: at b = 1200, fee 0% gives −5,129 and fee 2% gives +2,676 ± 3,456
a week. Do not cut it to 1–2% as a "small sink"; the sink already exists and is the house's only margin. The UI
should say "5% fee included in the odds".

**R4. Nice-to-have: b = 600.** Evidence: house +21,664 vs +11,928, losing days 8% vs 26%, and a 100-point bet visibly
moves a binary 7.3 points instead of 3.8 (better on stage). It costs a slightly worse Brier at close (0.493 vs 0.485).
Worst case per 50/50 binary falls to 416. Do not go to b = 300: Brier at close is 0.519 and prices whipsaw. This
belongs in a separate PR from R1–R3. The sweep was global, so b per market type (binary 600, 5-way 1,200) is a
reasonable refinement the simulation did not test.

```diff
# services/city/pricing.py:18
-LMSR_B = 1200.0
+LMSR_B = 600.0
```

Update the README's "about 4 points" line to "about 7".

**R5. Nice-to-have: weekly seasons and a skill board.** Rank on season P&L ÷ staked with at least 20 settled bets,
and show equity separately. Reset the season grant on Monday 00:00 Europe/London.
- Evidence: equity rewards tenure and sybils.
- Evidence: a +100 daily refill inflates supply 45% in a week (*sim*). If daily points ship (`business.md`), seasons
  must ship with them.
- Manifold's move to leagues follows the same logic ([FAQ][manifold-faq]).

**R6. Nice-to-have: per-series bet delay and suspension on related feeds.** Add `Series.bet_delay_ms` (60 s on
single-event series). Halt clean_sheet when any watched line's severity or reason changes. See section 7.4.

**R7. Nice-to-have: two-reading confirmation and a plausibility hold before settling.** See section 7.5.

**R8. Nice-to-have: lower `CITY_EXPOSURE_CAP` to 25,000.** Evidence: the base never comes near it (worst *sim* day
−4,714). A leak would then freeze betting at a quarter of the bankroll instead of all of it. This is an env change
with no code change.

**R9. Before any public deploy: close the `X-Forwarded-For` sybil hole** (`server.py:123-125`).

## Glossary

- **LMSR**: logarithmic market scoring rule. A market maker whose cost function is C(q) = b·ln Σ e^(q_i/b).
  Prices are a softmax of shares sold ([Hanson 2003][hanson03]).
- **b**: the liquidity parameter. Higher b means prices move less per bet and the house's worst case is larger.
- **Model prior**: the probability from the 50 futures, which the book prices around.
- **Locked odds**: the payout multiple fixed when a bet is placed, = shares ÷ stake, floored to 0.01 and capped at 20.
- **Exposure**: the house's worst-case loss across open player bets.
- **Faucet / sink**: where points enter the economy (the grant) and where they leave it (net house winnings)
  ([Ludgate][faucets]).
- **Courtsiding**: betting on an event you saw before the official feed published it ([UKGC][ukgc]).
- **Brier score**: the mean squared error of the probabilities against outcomes. Lower is better.
- **CLOB**: central limit order book. **DCM**: CFTC-designated contract market. **CPMM**: constant-product market
  maker (Manifold's "Maniswap").

## References

Code: `services/city/{pricing,exchange,ledger,markets,traders,server,data}.py`, `services/city/README.md`,
`recorder/README.md`, `docs/business.md`, `docs/questions.md`, `docs/data-sources.md`. The platform research is in
`market-design.md` (Kalshi contract PDFs, Polymarket docs).

- [hanson03]: Hanson, *Combinatorial Information Market Design*, Information Systems Frontiers 5(1), 2003 — https://mason.gmu.edu/~rhanson/combobet.pdf
- [hanson07]: Hanson, *Logarithmic Market Scoring Rules for Modular Combinatorial Information Aggregation*, J. Prediction Markets 1(1), 2007 — https://mason.gmu.edu/~rhanson/mktscore.pdf
- [gnosis]: Gnosis LMSR primer (cost function, softmax prices, funding = b·ln n) — https://gnosis-pm-js.readthedocs.io/en/v1.3.0/lmsr-primer.html
- [othman]: Othman, Pennock, Reeves, Sandholm, *A Practical Liquidity-Sensitive Automated Market Maker*, ACM TEAC 2013 — https://www.cs.cmu.edu/~sandholm/liquidity-sensitive%20automated%20market%20maker.teac.pdf
- [chenpennock07]: Chen & Pennock, *A Utility Framework for Bounded-Loss Market Makers*, UAI 2007 — https://arxiv.org/abs/1206.5252
- [pennock04]: Pennock, *A Dynamic Pari-Mutuel Market*, EC 2004 — https://courses.cs.duke.edu/spring07/cps296.3/pennock-ec-2004-dynamic-parimutuel.pdf
- [manifold-cpmm]: Manifold moves binary markets from DPM to CPMM, April 2022 — https://manifold.markets/Manifold/which-betting-system-will-manifold
- [manifold-prize]: Manifold, *Exploring real cash prizes* (loans end, transfers end, subsidy ends), April 2024 — https://www.lesswrong.com/posts/jMccY2X5aRZmED2yv/manifold-exploring-real-cash-prizes
- [manifold-faq]: Manifold FAQ (order book + AMM, liquidity, loans, leverage, leagues) — https://docs.manifold.markets/faq
- [manifold-loans]: critique of Manifold's loan design — https://zeroexpectation.substack.com/p/problems-with-manifolds-loan-system
- [kalshi-fees]: Kalshi fees — https://help.kalshi.com/trading/fees
- [kalshi-formula]: Allium, Kalshi fee formula 0.07·C·P·(1−P) (secondary) — https://allium.so/blog/kalshi-fees-why-the-cost-peaks-near-50-cents/
- [kalshi-limit]: Kalshi rule amendment moving the $25,000 position limit into contract terms — https://www.cftc.gov/filings/orgrules/rule022123kexdcm002.pdf
- [kalshi-nhigh]: Kalshi NHIGH contract terms — https://kalshi-public-docs.s3.amazonaws.com/contract_terms/NHIGH.pdf
- [kalshi-rain]: Kalshi RAINNYCM contract terms — https://kalshi-public-docs.s3.amazonaws.com/contract_terms/RAINNYCM.pdf
- [kalshi-mm]: Susquehanna as Kalshi's first institutional market maker (unverified, 403) — https://www.businesswire.com/news/home/20240403664852/en/Kalshi-Onboards-Its-First-Dedicated-Institutional-Market-Maker
- [polymarket-clob]: Polymarket CLOB — https://docs.polymarket.com/developers/CLOB/introduction
- [polymarket-fees]: Polymarket taker fees, C·rate·p(1−p) — https://docs.polymarket.com/polymarket-learn/trading/fees
- [polymarket-rebates]: Polymarket maker rebates — https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program
- [polymarket-rewards]: Polymarket liquidity rewards — https://help.polymarket.com/en/articles/13364466-liquidity-rewards
- [polymarket-uma]: Polymarket resolution via UMA — https://help.polymarket.com/en/articles/13364518-how-are-prediction-markets-resolved
- [polymarket-15m]: The Block, *Polymarket adds taker fees to 15-minute crypto markets*, 6 Jan 2026 — https://www.theblock.co/post/384461/polymarket-adds-taker-fees-to-15-minute-crypto-markets-to-fund-liquidity-rebates
- [betfair-comm]: Betfair commission on net winnings — https://www.sportinglife.com/free-bets/guides/betfair-exchange-education/how-does-commission-work
- [betfair-expert]: Betfair replaces the Premium Charge with the Expert Fee, Jan 2025 — https://racingpost.com/news/britain/betfair-exchange-to-introduce-new-commission-system-for-2025-as-premium-charge-is-dropped-a7wbg0v4GCAJ
- [betfair-delay]: in-play bet delay 1–12 s and suspension (secondary) — https://tech-insider.org/?p=24089
- [ukgc]: UK Gambling Commission, *In-play (in-running) betting* — https://www.gamblingcommission.gov.uk/licensees-and-businesses/guide/in-play-or-in-running-betting
- [courtsiding]: Courtsiding — https://en.wikipedia.org/wiki/Courtsiding
- [sbs]: SBS, *Aust Open "courtsider" bailed to UK* — https://www.sbs.com.au/news/article/aust-open-courtsider-bailed-to-uk/w7e6zgf11
- [predictit]: CFTC No-Action Letter 25-20 (PredictIt: 5,000-trader cap removed, $3,500 limit) — https://www.cftc.gov/csl/25-20/download
- [predictit-fees]: PredictIt fees (login wall, unverified) — https://predictit.freshdesk.com/support/solutions/articles/5000516383-what-are-your-fees-
- [augur]: Augur whitepaper — https://arxiv.org/abs/1501.01042 ; Augur Turbo AMM — https://github.com/AugurProject/turbo
- [gnosis-ct]: Gnosis conditional tokens and the LMSR market-maker factory — https://conditional-tokens-docs.netlify.app/docs/pmtutorial5
- [wz04]: Wolfers & Zitzewitz, *Prediction Markets*, JEP 2004 — https://www.nber.org/papers/w10504
- [wz06]: Wolfers & Zitzewitz, *Interpreting Prediction Market Prices as Probabilities*, 2006 — https://www.frbsf.org/research-and-insights/publications/working-papers/2006/04/interpreting-prediction-market-prices-as-probabilities/
- [pennock01]: Pennock et al., *The Real Power of Artificial Markets*, Science 2001 (play-money markets forecast well) — https://pure.psu.edu/en/publications/the-real-power-of-artificial-markets-4/
- [servan04]: Servan-Schreiber et al., *Prediction Markets: Does Money Matter?*, 2004 (play money as accurate as real) — https://researchr.org/publication/Servan-SchreiberWPG04
- [tetlock]: Tetlock, *Liquidity and Prediction Market Efficiency* (more liquidity did not reduce mispricing) — https://business.columbia.edu/faculty/research/liquidity-and-prediction-market-efficiency
- [chenpennock10]: Chen & Pennock, *Designing Markets for Prediction*, AI Magazine 2010 — https://dblp.uni-trier.de/rec/journals/aim/ChenP10.html
- [faucets]: Ludgate, *The F-Words of MMOs: Faucets*, Game Developer 2011 — https://www.gamedeveloper.com/design/the-f-words-of-mmos-faucets
- [eve]: EVE Online Monthly Economic Report (sinks, faucets, money supply) — https://www.eveonline.com/news/view/monthly-economic-report-november-2024
- [lehdonvirta]: Lehdonvirta & Castronova, *Virtual Economies: Design and Analysis*, MIT Press 2014 — https://econpapers.repec.org/RePEc:mtp:titles:0262027259

[hanson03]: https://mason.gmu.edu/~rhanson/combobet.pdf
[gnosis]: https://gnosis-pm-js.readthedocs.io/en/v1.3.0/lmsr-primer.html
[othman]: https://www.cs.cmu.edu/~sandholm/liquidity-sensitive%20automated%20market%20maker.teac.pdf
[manifold-prize]: https://www.lesswrong.com/posts/jMccY2X5aRZmED2yv/manifold-exploring-real-cash-prizes
[manifold-faq]: https://docs.manifold.markets/faq
[kalshi-fees]: https://help.kalshi.com/trading/fees
[kalshi-formula]: https://allium.so/blog/kalshi-fees-why-the-cost-peaks-near-50-cents/
[kalshi-limit]: https://www.cftc.gov/filings/orgrules/rule022123kexdcm002.pdf
[kalshi-nhigh]: https://kalshi-public-docs.s3.amazonaws.com/contract_terms/NHIGH.pdf
[kalshi-rain]: https://kalshi-public-docs.s3.amazonaws.com/contract_terms/RAINNYCM.pdf
[kalshi-mm]: https://www.businesswire.com/news/home/20240403664852/en/Kalshi-Onboards-Its-First-Dedicated-Institutional-Market-Maker
[polymarket-clob]: https://docs.polymarket.com/developers/CLOB/introduction
[polymarket-fees]: https://docs.polymarket.com/polymarket-learn/trading/fees
[polymarket-rebates]: https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program
[polymarket-rewards]: https://help.polymarket.com/en/articles/13364466-liquidity-rewards
[polymarket-uma]: https://help.polymarket.com/en/articles/13364518-how-are-prediction-markets-resolved
[polymarket-15m]: https://www.theblock.co/post/384461/polymarket-adds-taker-fees-to-15-minute-crypto-markets-to-fund-liquidity-rebates
[betfair-comm]: https://www.sportinglife.com/free-bets/guides/betfair-exchange-education/how-does-commission-work
[betfair-delay]: https://tech-insider.org/?p=24089
[ukgc]: https://www.gamblingcommission.gov.uk/licensees-and-businesses/guide/in-play-or-in-running-betting
[courtsiding]: https://en.wikipedia.org/wiki/Courtsiding
[sbs]: https://www.sbs.com.au/news/article/aust-open-courtsider-bailed-to-uk/w7e6zgf11
[predictit]: https://www.cftc.gov/csl/25-20/download
[predictit-fees]: https://predictit.freshdesk.com/support/solutions/articles/5000516383-what-are-your-fees-
[faucets]: https://www.gamedeveloper.com/design/the-f-words-of-mmos-faucets
[eve]: https://www.eveonline.com/news/view/monthly-economic-report-november-2024
