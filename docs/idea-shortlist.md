---
title: Idea shortlist — quant concepts cloned for New Worlds
date: 2026-11-07
status: ideating (Marshall Wace track chosen; code reuse approved, not pulled yet)
builds_on: idea-mirrorverse.md, past-winners.md, marshall-wace-track.md
---

# Idea shortlist

## Where we are
- **Track: Marshall Wace, New Worlds.** We chose it deliberately over incident.io. The engine is
  Mirrorverse: fork the real, live London into parallel worlds.
- **The problem:** a "what if a crash on Oxford Street" sim on its own reads as *seen before*.
  It matches MW's 2024 brief and IC Hack 26's winner, Digital Twin.
- **What we were missing:**
  1. **Credibility.** "How do you know that world is right?" Without an answer, the forks are
     just animation. Fix: show reality grading the worlds.
  2. **The question.** No feature makes an idea novel. The hook is *which world we show*, and
     whether a stranger instantly wants to see it.
  3. **The breakthrough: borrow how MW think.** Running many futures, weighting them, and
     killing the ones reality disproves is how a prediction business works. Clone an MW concept
     for a much more creative use.

## Quant concepts → creative clones
| Quant concept | In finance | Clone for "New Worlds" |
|---|---|---|
| **Alpha capture (TOPS)** | **MW's own invention, 2002**: Trade Optimised Portfolio System. Thousands of brokers submit trade ideas with a conviction level; each source's track record is measured and ideas are weighted by who has been right | Predictors submit ideas about the future; each idea becomes a world; reality scores whose world came true |
| Mark-to-market | Continuously reprice every position against the latest price | Reprice every parallel London against each new camera frame |
| Particle filter / ensemble | Run many scenarios, weight them by fit to the data | The "reality prunes the multiverse" engine |
| Long/short | Bet on one thing *relative to* another | "Long the world where Oxford St jams, short the world where it rains" |
| Factor attribution | Break a return down into its drivers | Break today's chaos down: 40% weather, 30% strike, 30% Christmas lights |
| Alternative data | Satellite car-park counts predict earnings | Turned around: the city's own data becomes the market |

## Ranked shortlist (elevator pitch, 24h version, demo moment, risk)

### 1. The Exchange of Worlds (clones TOPS)
> "Marshall Wace built TOPS to work out which brokers to trust. We built it to work out which
> London is real. Every parallel London is a stock: its price is the chance it's the world we're
> living in. Each new camera frame reprices it live."
- **24h:** one area (Oxford Circus), 20–50 worlds forked from live TfL data, prices updated on
  each camera or disruption update. Judges go long or short a world from their phone. Points
  only, never real money.
- **Predictors are the brokers:** AI persona agents, and real people, submit ideas with a
  conviction level ("Regent St jams by 16:00, high"). Each idea spawns a world, and predictors
  build track records.
- **The consensus future is the portfolio:** surviving worlds weighted by their predictors'
  track records give a live estimate of the next 30 minutes. That feed is the new data.
- **Demo moment:** a judge buys a world, the next frame lands, and it jumps or gets delisted.
- **Risk:** the most moving parts: simulation, pricing, a phone interface and a live loop.

### 2. Reality Prunes the Multiverse (clones ensemble forecasting / particle filters)
> "Right now there are 200 Londons that could exist at 3:30 this afternoon. Watch reality kill
> the ones that are wrong."
- **24h:** the engine without the market. A cloud of futures narrows and splits as data
  arrives, with a street-by-street map of how uncertain each street is.
- **Demo moment:** worlds go dark live, or in a sped-up replay of the day we recorded, ending
  on one live update.
- **Risk:** lowest. It's the core of #1 anyway.

### 3. Factor Attribution for a City
> "Quants break a fund's return into the factors that drove it. We do that to a city. Why is
> Oxford Street rammed right now? 41% Christmas lights, 27% the Central line delay, 18% rain,
> 14% noise."
- **24h:** a regression on recorded counts against weather, disruptions, time and events,
  explained by an LLM.
- **Demo moment:** pick any street and get a live breakdown of why it looks like this right now.
- **Risk:** credible, but it reads as analytics, not a "new world". Weakest fit for the theme.

### 4. The Smallest Change (counterfactual attribution)
> "Something went wrong in London today. We searched a thousand parallel worlds for the smallest
> change that would have prevented it."
- **24h:** take one real recorded incident, run many forks with one small change each, and rank
  the changes by how much they help.
- **Demo moment:** "If this one street had reopened 10 minutes earlier, 80% of the jam never
  happens."
- **Risk:** needs a real incident in our recorded window, and the sim must be believable.

### 5. What-If Cascade (the original Mirrorverse idea)
> "Drop a car crash on Oxford Street and watch the city react."
- **Use it as a feature** (the "place an event" button inside #1/#2), **not as the pitch**: it
  reads as seen before.

### 6. Londons with Different Rules
> "London with no cars. London where the Thames is a motorway. Same real people, same real
> morning, different rules of the world."
- **Risk:** the most creative and least grounded, and the hardest to defend technically.

**Current recommendation:** #2 as the engine, #1 on top as the presentation, #5 as the
"place an event" button. One system, three hooks. If the market layer runs late, #2 is still a
complete demo.

## Decision process (don't ideate forever)
1. Each of us writes 5 "what if London…" questions, and we pick the top 2.
2. **Gut-check test:** say the one-liner to 3 strangers (another team, a mentor, MW at the 14:00
   workshop). Leaning in means yes; nodding politely means no.
3. **Ask MW directly:** "Is a live exchange of parallel Londons novel enough for New Worlds?"
4. Hard cutoff around 15:00, then build.

## Record the live feeds now (needed by #1, #2 and #4)
Every hour we don't record is an incident we can never replay or test against. Recording can
start before ideating finishes. It would be a fresh script in this repo, with nothing pulled
from mirrorverse.

| Feed | What to keep | Estimated size for 24h |
|---|---|---|
| TfL JamCams (~150 central cameras, ~5 min refresh) | YOLO people/vehicle counts per snapshot, **no video** | ~45k rows, ~10 MB |
| TfL road disruptions (every 5 min) | Store only snapshots that changed (hash and skip repeats) | tens of MB at most |
| Bus arrivals (~50 stops, every 1–2 min) | Predicted arrival times; delays worked out later | ~50–100 MB |
| Optional: still frames near flagged incidents | Demo evidence | under 100 MB |

Total **well under 1 GB**; one SQLite file is enough. These are estimates, so check after the
first hour. Run it on a cloud VM, or on a laptop that stays plugged in with `caffeinate -i`,
ideally off venue wifi. Get a free TfL API key; anonymous use is rate-limited hard.

## Pitch rules
- Use: "parallel present", "the London that didn't happen", "watch reality choose".
- Avoid: "digital twin", "traffic simulator", "chain of events".
- The 3-minute slot is strict: live demo on a laptop, with one hook in the first 10 seconds.
