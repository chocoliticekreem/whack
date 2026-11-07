---
title: OverWatch — business, money and market integrity
date: 2026-11-07
status: strategy · for the pitch, Q&A and Devpost "What's next"
---

# OverWatch: from game to exchange

## The principle that makes it fair
**For crowding markets, nobody knows the outcome in advance, not us, not TfL, not anyone standing on the street.**
How many people pass Oxford Circus at 18:35, or how uncertain the city is in five minutes, emerges
from millions of people's choices. Everyone sees the same public data, so the edge comes from
*interpreting* it better, not from having access to it.

**Design rule:** only list markets on aggregates of **many people's behaviour**, never on
**decisions one person makes**.
- **Yes:** crowd flow and footfall, busyness vs a typical day, the London VIX.
- **No:** planned closures, engineering works, "will TfL close X".
- **The exception: clean sheet** (does every Tube line on Good Service stay Good for the hour). A line failure is one
  discrete event, and TfL staff see it before the feed does. What limits it today: the market closes the moment a
  reading shows a break, each player's stake is capped at 25% of their points per market, and it's points only. A bet
  delay (or closing betting early in the hour) is next.

**Faster feeds.** Betting stops 60 s before settlement, and crowding markets settle on the first reading our recorder
polls after bets close. TfL stamps that reading with its own time, which can be before the close, so someone polling
faster than us could see it first. Settling on TfL's own timestamp is next.

## Why people would play
- **Useful:** the market is a live commute forecast ("King's Cross peaks at 18:20, leave at 18:35").
- **Status for being right:** an accuracy score and weekly "top forecaster in London".
- **A clear opponent:** 7 AI traders, each reading one live data feed and building a public track record (the
  first half of TOPS; weighting their picks into the price is next).
- **Social:** office and friend leagues.
- **Prizes without gambling:** free entry, sponsored prizes. Free daily points and streaks.
- **The urge to bet:** comes from information and competition. Catalyst alerts ("match just ended,
  60,000 heading to King's Cross"), live prices that move with the crowd, a countdown, and a
  satisfying reveal on the real reading. **Never** from loss-chasing nudges or deposit bonuses.

## Defusing the fears
| Fear | Answer |
|---|---|
| "It's gambling" | It launches as a points-only forecasting game. Real money comes only where licensed. A daily loss limit and breaks are built in; reality checks and self-exclusion come next |
| "Profiting from misery" | No markets on harm: no accidents, crime, injuries, protests or emergencies |
| "Surveillance" | Counts only, no faces, aggregated across many cameras |
| "Rigged" | Five busy hubs and a 20-station average, each measured against its own baseline; low-traffic stations are the soft spot we'd exclude. Settled on a published reading inside a fixed window, else void. Positions capped at 25%. Halts on sudden jumps |
| "Is it legit?" | Every market shows its exact source and rule. The settled reading is frozen and shown |

## Roadmap
| Stage | What | Who pays | Money risk |
|---|---|---|---|
| **0. Game (now)** | Points, AI traders, leaderboards | Nobody: we're proving people engage and the prices are accurate | None |
| **1. Prizes** | Sponsored prizes, free entry | Venues and brands (footfall, attention) | Low |
| **2. Real-money exchange** | Peer-to-peer event contracts | Exchange fees on volume (the Kalshi and Polymarket model, no house risk) | High: licensing, ID checks, client funds |
| **3. Hedging and data** | Businesses hedge city risk; index insurance; licensed forecasts and the London VIX as alternative data | Retailers, events, logistics, insurers, transport operators, funds | Institutional |

**Stage 3 is the business:**
- **Hedging:** a West End retailer, a festival or a delivery fleet carries footfall, weather and
  disruption risk with nothing to offset it. Weather derivatives already prove that market exists.
- **Index insurance:** our markets price cover that pays automatically when a public index crosses
  a line.
- **Alternative data:** the London VIX and our live probabilities become a data feed.

## Built for real money from day one
| Requirement | Status |
|---|---|
| A ledger where entries are never edited and every movement balances | Done (#45) |
| Each bet settles exactly once, and a repeated request can't double it | Done (#45) |
| Client money kept separate (cash, money held in open bets, the house's money) | Done (#45) |
| Responsible play: loss limits, reality checks, self-exclusion | Partly done (#45): daily loss limit, large-stake confirmation, 1 h / 24 h breaks. Reality checks and self-exclusion later |
| Published settlement source and rule, with the settled reading frozen | Done |
| Market integrity: position cap, settling on a window, halts on stale data and sudden jumps | Done |
| Peer-to-peer order book with market makers (no house risk) | Later. Today the house banks every bet through an LMSR market maker, which is bookmaking, not an exchange |
| ID verification and anti-money-laundering checks | Later: the player record is designed for it |

## Open questions (need legal advice, not verified)
- **Route:** in the UK, event betting falls under the Gambling Commission (Betfair runs a licensed
  exchange). In the US, Kalshi became a regulated exchange under the CFTC. Business hedging could
  go via the financial regulator or an insurance partner. Likely order: business hedging first,
  consumer real money only where licensed.
- **Data:** settling real money on TfL and other open feeds probably needs a formal data
  agreement and backup sources.

## Pitch line
> "Nobody can know how London's crowds move in the next five minutes: not us, not TfL, not the person
> standing at Oxford Circus. Everyone sees the same data; the edge is reading it better. Today it's
> a game against AI traders. The business is pricing the city, so businesses can hedge it and funds
> can trade it. The ledger is real-money grade; the peer-to-peer exchange and licensing are the work
> left."
