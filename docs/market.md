---
title: Market structure — how prices, bets and settlement work
date: 2026-11-07
code_as_of: 4ad6ed2 (#33)
sources:
  - services/city/ (exchange.py, pricing.py, markets.py, traders.py, data.py)
  - services/multiverse/ (engine.py, brokers.py)
  - apps/stream/src/app/multiverse/ (City.tsx, city-store.ts, MarketCard.tsx)
---

# Market structure

The rules our markets follow, as built. Two venues share one idea: **simulated futures set the
price, and real London data settles it.** The pitch is in [idea.md](idea.md).

| | **Trade London** (the city exchange) | **The Oxford Circus multiverse** |
|---|---|---|
| Page | `/multiverse` | `/multiverse/oxford` (game), `/multiverse/board` |
| Service | `services/city` (:8051) | `services/multiverse` (:8049) |
| What is priced | Questions about the next 2–15 min at 5 hotspots: crowding, cameras, buses, Tube lines, rain | 50 whole "worlds" of Oxford Circus, 5 min ahead |
| Who trades | Players and 6 AI traders, with points | Nobody: only data moves prices |
| The AI | 6 rule-based traders, one feed each | 10 Claude Haiku brokers, one feed each (TOPS) |
| Headline number | Odds per option | The London VIX |

## 1. Trade London: the city exchange

### 1.1 Participants
- **Players** start with 1,000 points.
- **AI traders** (6, §1.7) bet through the same book as players.
- **The house** quotes every price. A model of 50 simulated futures sets the base probability, an
  LMSR market maker moves it as bets come in, and a 5% fee comes off every stake.

### 1.2 Contracts
Every market is an event contract with 2 or 3 options. **A winning share pays 1 point**; a losing
one pays nothing. Hotspots: Oxford Circus, King's Cross St Pancras, Piccadilly Circus, Waterloo, Bank.

| Kind | Question | Feed | Settles on |
|---|---|---|---|
| `busiest` (versus) | Which station is busiest at 17:50? | TfL Crowding (% of usual) | each station's reading at T |
| `level` (over/under) | Over/under *x*% of usual busyness at Waterloo at *T*? | TfL Crowding | the station's reading at T |
| `bus` (over/under) | Over/under *n* buses due at Waterloo at *T*? | TfL Bus Arrivals | buses due within 5 min at the hotspot's stops, reading at T |
| `cam` (over/under) | Over/under 12 people on the Oxford Circus camera at 17:52? | JamCam + YOLO | median count over [T − 60 s, T]; the hotspot's next camera as a fallback |
| `lines_any` (yes/no) | Will any Tube line get worse by 17:22? | TfL line status | reading at T vs the snapshot at open |
| `line` (yes/no) | Will the Central line be back to Good Service by 17:22? | TfL line status | reading at T |
| `rain` (yes/no) | Rain in central London before 17:25? | Open-Meteo | any reading above 0.1 mm in [open, T] |

- "Reading at T" = the last reading in the 2 min before T, else the first one up to 3 min after.
- Exactly on an over/under line counts as **Under**.
- Every card shows its settlement source and full rule text.

### 1.3 Listing
- The book keeps **3–5 markets open**, with at least one closing within 2 min ("quick" markets:
  bus, camera and line markets). A new market opens as an old one closes; at most 2 of a kind are
  open at once.
- Kinds take turns: level, busiest, bus, cam, level, rain, bus, line, lines_any, cam.
- **Horizon:** 2 min for quick markets, 5–15 min for the rest. Crowding and rain markets settle
  just after one of the feed's 5-minute publishes, so a fresh reading exists.
- **Strike:** an over/under line sits at the median of the futures. A versus market picks the
  closest pair or trio of stations.
- **No lopsided listings:** a candidate whose favourite is above 85% at open is dropped and the
  next one is tried.

### 1.4 Lifecycle
| Status | When | Bets |
|---|---|---|
| OPEN | from listing until 60 s before T (90 s for cameras) | taken |
| HALTED | the feed is stale (6 min for crowding and rain, 3 min otherwise), the camera is missing from the latest snapshot, or the market's own series jumps more than 3σ. Resumes after 2 clean readings | refused |
| LOCKED | the last 60 s (90 s for cameras) before T | refused |
| AWAITING DATA | after T, until a settling reading arrives (up to 3 min) | refused |
| SETTLED | a reading decided it. The value, source and time are frozen on the card | paid |
| VOID | no reading by T + 3 min. "By T" yes/no markets (`lines_any`, `line`, `rain`) resolve **No** instead; the rest void | refunded |

The server reports LOCKED and AWAITING DATA as one status, `closed`; the UI splits them by time.

### 1.5 Pricing
**Layer 1, the model.** Each market runs 50 futures. Each starts from the last real reading and
steps minute by minute, with step sizes fitted from today's data and any single step capped at 2.5σ:
- crowding: a log random walk per station, with half the last hour's trend as drift;
- cameras: sticky counts (how often and how much a count changes, fitted per camera);
- buses: each minute the count either holds (persistence fitted per hotspot) or is redrawn from
  today's real counts at those stops;
- Tube lines: worsening and recovery hazards, fitted from today's status changes;
- rain: one wet/dry draw per 15-min slot, with odds from that slot's nowcast.

Each new reading rules out the futures that disagree with it, never more than 60% (30) over the
market's life. **Model probability = share of surviving futures**, with half a future added to each
option and a clamp to [3%, 97%], so no side is ever free.

**Layer 2, order flow (LMSR).** Bets tilt the model price through Hanson's logarithmic market
scoring rule, with the model as the base:

```
price_i = model_i · e^(q_i / b) / Σ_j model_j · e^(q_j / b)      b = 1200, q_i = shares sold of option i
```

A stake buys shares after the 5% fee. The model keeps moving as futures are ruled out, while the
bets (`q`) stay, so **the price is the model's view tilted by net order flow.** A 100-point stake
on one side of a two-option market (computed with `pricing.Book`):

| Price before | Shares (= payout if it wins) | Price after |
|---|---|---|
| 50% | 183 | 53.8% |
| 70% | 134 | 72.3% |
| 85% | 111 | 86.1% |
| 95% | 99.8 | 95.4% |
| 97% | 97.8 | 97.2% |

Shown odds = 0.95 / price, rounded to 0.1 and held to ×1.05–×20.

### 1.6 Accounts, limits and payouts
- **Points:** players start at 1,000, with a refill when broke. The wallet is kept in the browser and
  posted to `/score` for the leaderboard.
- **Position cap:** a player's total stake in one market is at most 25% of their points (250 at
  the start).
- **Payouts:** a winning player gets the shares they bought. An AI trader gets stake × the odds
  locked at bet time (shares / stake, held to ×1.05–×20). A void refunds the stake.
- **Leaderboard:** humans and AI traders, ranked together by points.

### 1.7 The AI traders
Rule-based and deterministic, with no LLM calls (`services/city/traders.py`). Each bets **once per
market, a fixed 100 points**, through the same LMSR, so their bets move the price players see. A
trader whose feed is stale sits out. Every bet comes with one line citing the number it read.
Records (won, lost, points from 1,000) are kept on the server.

| Trader | Feed | Rule |
|---|---|---|
| 🚇 Tube Crowd | TfL Crowding | Versus: backs the station with the highest % of usual right now. Level: momentum over the last 15 min |
| 📷 Camera Eye | JamCam + YOLO | The count stays put: over if it is already above the line |
| 🚦 Line Watcher | TfL line status | Any line worse: yes after 2+ worsenings in the last hour. One line back: yes if its delay is under 10 min old |
| 🌧️ Rain Radar | Open-Meteo | Yes if it is raining now or the nowcast for the settling slot is wet |
| 🚌 Bus Spotter | TfL bus arrivals | Bus markets: today's typical count vs the line. Versus: the station with more buses due. Cameras: over if more buses are due than usual |
| 🙃 The Contrarian | other bets | Waits until halfway, then backs the best-priced option other than the most-staked one |

### 1.8 Measurement (`GET /stats`)
- Settled on a real reading vs void, per kind.
- **Calibration:** the favourite's closing price vs how often it won, in 10-point buckets, for the
  traded price and for the model alone. Do 70% favourites win 70% of the time?
- The favourite's price at open: in a 60-min replay, 87% of markets opened at 40–75% (#33).

## 2. The Oxford Circus multiverse
The research venue behind the TOPS story. Nobody bets here: **prices are probabilities computed
from data**, and the AI brokers only decide which futures get explored.

- **What's priced:** a world is one set of assumptions (moving share, tourist mix, pavement
  capacity, riders per bus, bus delay, walking pace, rain onset, random incident), simulated 5 min
  ahead by Predict on the real street graph. 50 are live at once.
- **Price:** each minute's observation scores every world against the cameras, bus delay, rain,
  Tube crowding and bike docks. Each term is capped, so one reading can't wipe out a world. Prices
  are the normalised posterior and sum to 1.
- **Delisting:** below 0.1/N (0.2% at N = 50). The best world always survives, at most 60% go per
  minute, and worlds from a placed event are protected for one observation.
- **Market maker:** a quarter of the slots (12 of 50), refilled by a plain particle filter.
- **Brokers (TOPS):** 10, each reading one feed, with ideas from Claude Haiku (rules as a fallback).
  An idea is a target for the settings its feed can inform, plus a conviction (0–1) and a
  rationale. It becomes new worlds, copied from the market and moved towards the target by the
  conviction.
- **Broker P&L:** per observation, `log((mass after + 0.01) / (mass before + 0.01))`, capped at ±2,
  where mass is the total price of the broker's worlds. It is market-relative: if every world
  improves equally, nobody gains.
- **Track record:** 0.8 × the previous track record + this minute's P&L (about a 3-minute memory).
- **Allocation:** weight = 0.8 × softmax(track record / 1.5) + 0.2 × uniform. The other 38 slots
  go by weight, at least 2 each; a broker over its share loses its cheapest worlds.
- **London VIX:** per zone, the price-weighted spread of predicted people at the horizon relative
  to the mean, weighted by zone volume, × 100. A spike is a jump of at least 2 points and 20%. The
  driver is the feed or broker that moved it most.
- **Place an event:** every live world forks a child with the event, at 50% of the parent's price.
- **Rolling:** past the 5-min horizon, a new window is seeded from the last observation and every
  live world re-runs, keeping its price.

## 3. In market terms
| Ours | The real-market equivalent |
|---|---|
| Event contract paying 1 point | Kalshi and Polymarket contracts paying $1 |
| LMSR on a model base | Hanson's logarithmic market scoring rule, the automated market maker early prediction markets ran on |
| 5% fee | The house edge (vig) |
| Betting closes 60–90 s before T | Last trading time |
| HALTED on stale feeds and 3σ jumps | Trading halts and circuit breakers |
| Source, rule text, fallback ladder, frozen snapshot | A contract's settlement source and rulebook |
| VOID with refunds | Market cancellation |
| 25% position cap | Position limits |
| Calibration in `/stats` | How forecasters are scored |
| Broker slots by track record | TOPS alpha capture: capital follows the ideas that worked |
| London VIX | A fear gauge like the VIX, but from the spread of simulated futures, not option prices |

## 4. Known gaps (as of #33)
1. **Player points live in the browser.** `/score` accepts whatever number the client sends, and
   the position cap uses it. Fine for a demo, but anyone can edit their points.
2. **Players and AI traders are paid differently.** Players win their shares; AI traders win
   stake × locked odds, at least ×1.05. Above about 95%, a player's winning 100-point bet pays back
   less than 100 (table in §1.5). The card's "→ win" preview uses the shown odds, so it also hides
   slippage: at 50% it says 190, and the bet pays 183.
3. **AI traders don't size their bets.** A fixed 100 points, with no conviction, no bankroll or
   position cap, and no learning from their record.
4. **The pitch says Haiku brokers trade.** In Trade London the traders are rule-based. The Haiku
   brokers and the London VIX are only in the Oxford Circus multiverse.
5. **The house's loss isn't strictly bounded.** With a fixed base, LMSR loses at most
   b · ln(1 / p_min), about 4,200 points per market at the 3% floor. Here the base moves, so that
   is a guide, not a bound.
6. **State is in memory.** A restart wipes open markets, bets and the AI records.
7. **Market mix:** 36 of every 55 markets opened were bus markets (#33). Line-recovery and rain
   markets rarely pass the 85% rule on a calm, dry day.
8. **The multiverse's alpha ranking is weak.** Informative feeds beat noise feeds on only 4 of 10
   eval seeds (#24). With a 3-minute memory the leaderboard is mostly noise, and broker worlds
   start as copies of the market's, so some of their P&L is the market's own move.

## 5. Where to change things
| Parameter | Value | Where |
|---|---|---|
| Futures per market | 50 | `services/city/pricing.py` `N_FUTURES` |
| Most futures ruled out | 60% | `pricing.py` `RULED_OUT_CAP` |
| Model price clamp | 3–97% | `pricing.py` `P_FLOOR`, `P_CEIL` |
| LMSR liquidity | b = 1200 | `pricing.py` `LMSR_B` |
| Fee | 5% | `pricing.py` `FEE` |
| Open markets | 3–5 | `services/city/exchange.py` `MIN_OPEN`, `MAX_OPEN` |
| Highest favourite at open | 85% | `exchange.py` `MAX_FAVOURITE` |
| Horizons | 2 min quick, 5–15 min | `exchange.py` `QUICK_MS`, `HORIZON_MIN` |
| Position cap | 25% of points | `exchange.py` `POSITION_CAP` |
| AI stake | 100 | `services/city/traders.py` `STAKE` |
| Jump halt | 3σ, resume after 2 clean readings | `services/city/markets.py` `JUMP_SIGMA`, `CLEAN_TICKS` |
| Settlement wait | 3 min after T | `markets.py` `WAIT_AFTER_MS` |
| Stale feed | 6 min crowding and rain, 3 min others | `services/city/data.py` `STALE_S` |
| Multiverse delist line | 0.1 / N | `services/multiverse/engine.py` `DELIST_FRACTION` |
| Market-maker share | 25% | `services/multiverse/brokers.py` `MM_SHARE` |
| Track-record decay | 0.8 | `brokers.py` `DECAY` |
