---
title: Our idea — the current thinking
date: 2026-11-07
status: OverWatch · live at overwatchcapital.vercel.app/multiverse · see build.md §0
---

# Our idea

Built on **Mirrorverse**, our October build: a live mirror of real London fed by TfL cameras,
buses and disruptions (see [build.md](build.md)). This doc covers *what we make with it*. The ★ section,
"Why simulate" and "Superseded ideas" are current; §1–§5 are Saturday morning's reasoning, kept as history.

## ★ The pitch (current, as shipped)
**OverWatch: a live stock exchange on London's next five minutes.** Every price comes from 50 futures: statistical
walks fitted to today's data, with a share of them (half by default, `SIM_WEIGHT`) drawn from our central-London agent
simulation when it's running. Each reading rules out the futures that disagree, up to 30 of the 50. You trade against
seven AI traders that each read one stream of alternative data and build a public track record: the first half of
TOPS. Weighting their picks into the price is next. The London VIX is an uncertainty gauge for the area the agent
simulation covers (Oxford Circus by default): the spread of 50 simulated worlds, listed after a 60-minute warm-up.

Live at https://overwatchcapital.vercel.app/multiverse. Points only, never real money.

| Layer | What ships today | Role in the pitch |
|---|---|---|
| **1. The futures** (engine) | Each market runs 50 futures from the latest real readings. City markets use statistical walks fitted to today's data; on the crowding markets a share of them (half by default, `SIM_WEIGHT`) is drawn from the central-London agent simulation when it's running. The London VIX comes from the agent simulation of Oxford Circus on the real street graph (50 worlds, common random numbers) | *The new world*: futures that don't exist yet |
| **2. The market** (exchange) | City-wide 5-minute rounds on the London clock. Each new reading prunes the futures that disagree with it (particle-filter style, up to 30 of the 50; after that the market stops narrowing), and LMSR turns the survivors and the bets into prices. Kalshi-style rules: a source and rule on every market, betting closes before settle, void and refund, a frozen reading | *The interaction*: you bet, reality settles it |
| **3. The traders** (the first half of TOPS) | 7 AI traders, each reading one live feed: Tube Crowd (station crowding), Camera Eye (the VIX), Line Watcher (Tube status), Rain Radar (rain), Plane Spotter (Heathrow holding stacks), The Contrarian (where the points are going), Simulation (the agent simulation's forecast). Each bets through the same LMSR, states a reason citing a real number and builds a track record (in memory; a restart resets it). Their picks don't move the model price yet | *The MW hook*: their founding idea, pointed at a city |
| **Output: the London VIX** | The price-weighted spread of predicted crowds across the simulated worlds, as one index, for the area the engine covers. Lists only after a 60-minute warm-up | *The new data* |

**Every round lists:**
- **hub_surge:** "Which hub surges most by HH:MM?" 5 hubs (Oxford Circus, King's Cross, Piccadilly Circus,
  Waterloo, Bank) against their usual Saturday.
- **city_pulse:** over/under on average busyness across 20 TfL stations, with the line on a .5.
- **london_vix:** higher or lower. When the VIX is unavailable or in its first hour, **heathrow_stack** (planes
  holding over Heathrow) stands in.
- **Hourly headlines:** hub_hour (which hub ends the hour furthest above its usual) and clean_sheet (do all Tube
  lines on Good Service stay Good for the hour).

**Rule:** lead with the stock-exchange image. Say TOPS once, after it lands. Betting is how people touch it, not the
point.

**10 s:** "A live stock exchange on London's next five minutes. Every price comes from 50 futures, narrowed by real
TfL data as it arrives. You trade against seven AI traders, each reading one live feed."

**30 s:** "Every five minutes OverWatch lists new markets on London: which of five hubs surges most against a usual
Saturday, whether the city's pulse across 20 stations ends over or under the line, and whether the London VIX goes up
or down *(creativity)*. Each price comes from 50 futures: statistical walks fitted to today's data, part of them
drawn from our agent simulation when it's live. Every new TfL reading rules some out, up to 30 of the 50, and an LMSR
market maker prices what's left *(technical)*. Seven AI traders, each reading one feed (Tube crowding, Tube status,
rain, planes over Heathrow, the simulation), bet against you, say why and build a public track record, and real data
settles every market on the map *(presentation)*. It's the first half of Marshall Wace's TOPS idea pointed at a city:
weighting the traders into the price is next."

**100 words:**
> Prediction markets like Polymarket and Kalshi price the questions someone thought to ask. **OverWatch prices a
> city's next five minutes, starting with London.**
>
> Every five minutes it lists markets on central London: which hub surges, the city's pulse, the London VIX. Each
> price comes from 50 futures that real TfL data narrows as it arrives. Seven AI traders, each reading one stream
> of alternative data, bet against you, explain why and build a public track record. Real readings settle every
> market under published rules, on a double-entry ledger.
>
> Nobody knows the outcome in advance. The edge is reading the data better. Today our own Tube Crowd trader proves
> the house can be read better. That's the leaderboard's job.

**Close:** "Marshall Wace built TOPS to find out which brokers to trust. We built it to find out which data reads a
city best."

**Q&A, honestly:**
- *"Is the forecast accurate?"* On real Oxford Circus camera counts the simulation ties "nothing changes": MAE 2.06
  vs 2.04 over the latest 30 minutes (48 counts), 79% inside its 80% range (`eval_real.py --windows 6`). Central
  London loses to persistence by 15% (station MAE 4.14 vs 3.61, 919 readings; `--central`). Neither beats
  persistence yet. Lines are set per series so markets stay uncertain: the hourly hub market widens
  its fit until the favourite opens at 70% or less, the clean sheet holds 30–70% at open, and a bracket market whose
  favourite is over 85% isn't listed.
- *"Real betting?"* No. Points only. The ledger is real-money grade; a house-banked LMSR is bookmaking, so the
  peer-to-peer order book (roadmap) and licensing are the work left (business.md).
- *"Can someone know the answer?"* We list aggregates of many people's behaviour only. Clean sheet is the exception:
  a line failure is one event TfL staff see first. The market closes the moment a reading shows a break,
  bets fill 45 s after you place them, the market pauses whenever any line's status changes, and stakes are capped at 10% of bankroll (#61). A head start shorter than the delay gains nothing; longer leads are bounded by the cap. (business.md).

**Horizon: 5-minute rounds.** The floor is how fast reality reports back: TfL crowding publishes every 5 minutes;
cameras, buses and Tube status every minute. A judge sees one or two settlements inside the 3-minute slot.

**Mapped to MW's three criteria:**
| Criterion | What delivers it | What the judge sees |
|---|---|---|
| **Creativity** (the idea) | A stock exchange on a city's next five minutes, AI traders that each read one alternative-data feed and build a track record (the first half of TOPS), and an uncertainty gauge for Oxford Circus | "I've never seen that" |
| **Technical complexity** (the engine) | A 16-source recorder (YOLO on 13 cameras) → 50 futures per market, pruned live → LMSR → settlement under Kalshi-style rules (source and rule on every market, close before settle, void and refund, frozen reading) on a double-entry ledger; an agent simulation with common random numbers for the VIX | "That's hard, and it works" |
| **Presentation** (the visualisation) | A MapLibre map of London in the style of RAISE, Kalshi × PostHog market cards. Picking a market flies to its hubs; on settle the winner lights up and the payout pops | "That looks good, and I get it" |

**3-minute demo:**
1. **0:00:** the map and this round's markets. "This is London's next five minutes, priced."
2. **0:20:** pick hub_surge. The hubs light up on the map, and the card shows each hub against its usual Saturday. A
   judge bets.
3. **0:50:** the AI traders' bets and reasons on the card ("Bank is at 84% of a usual Saturday, the furthest below:
   it catches up"). The TOPS line goes here.
4. **1:30:** a round settles on the real TfL reading. The card shows the frozen reading and the wallet updates.
5. **2:00 technical:** one line per layer: feeds → 50 futures → pruning → LMSR → settlement, then the VIX and the
   simulation.
6. **2:40 close:** the TOPS line.

## Why simulate, not just read data
Owner's direction: the digital twin and the agent simulation must earn their place beyond looks, with no
special-cased scenarios, and the scope is central London, not one junction. **Status: the Oxford Circus simulation
produces the London VIX; the central-London simulation (#54) feeds part of the crowding futures and trades as the
Simulation trader (#55). It loses to persistence today. Items marked (in progress) are not shipped** (build.md §0).

**Data looks backwards; simulation looks forwards.** Sensors (13 cameras, 20 stations) show what already happened,
and statistical futures only extend trends. The agent simulation on the real street graph can:
1. **Price shocks before sensors see them** (in progress): any event from the feeds (TfL line disruptions, road
   closures, fixtures ending at any venue, crowding surges) becomes a simulation input by one general rule, and the
   markets reprice minutes before the cameras and station counts move.
2. **Answer what-ifs with no data** (in progress): a scenario mode where a user drops a closure or an event anywhere
   in central London and sees every open market reprice.
3. **Fill gaps between sensors** (in progress): crowd estimates along the street graph. Settlement still uses real
   sensors only.
4. **Check the data** (in progress): a sensor reading the model says is impossible flags the market and halts it
   (integrity).
5. **Explain prices** (in progress): show where the simulated crowd is heading.
6. **B2B** (later): councils, TfL and event organisers stress-test interventions, priced (stage 3 in business.md).

**The Simulation trader** (shipped, #55): an AI trader that bets from the simulation's forecast. Its P&L against the
data-only traders answers live whether a model of the city beats raw data. That's Marshall Wace's question (model vs
signal), asked about a city.

**Positioning:** data settles, the simulation forecasts. The twin is the scenario room, not decoration.

## Superseded ideas (history)
Kept for the reasoning; none of these ship on the main page.
- **Exchange of Worlds board** (#24, still at `/multiverse/board`): every simulated world of Oxford Circus listed as
  a stock, priced by its probability, with 10 data-source brokers (Claude Haiku 4.5 when a key is set, rules
  otherwise) scored TOPS-style. Dropped as the front page because a first-time viewer couldn't read it in seconds.
  Its engine survives as the London VIX, and the broker idea became the six city traders.
- **3D ghost worlds** (#25): parallel worlds drawn as translucent crowds over a three.js twin, shimmering where they
  disagree and shattering when a camera frame lands. The "Which London is real?" game is still at
  `/multiverse/oxford`. The city view moved to MapLibre (#44) because the three.js twin was too heavy for a
  city-wide view.
- **Trade London** (#32): the first city-wide version, renamed OverWatch and restyled (#38).
- **Judge-betting page:** a separate phone page for judges to buy worlds. Replaced by betting on the main page with a
  server-side wallet (#45).
- **Single-camera and bus markets** (#33): decided or random at 5 minutes, and answerable by anyone standing there.
  Replaced by the data-backed lineup in questions.md (#40, #42).

## 1. Where we are
- **Track: Marshall Wace.** We chose it on purpose over incident.io, which fits the old code more
  literally.
- **What doesn't work:** "drop a crash on Oxford Street and watch the cascade". It reads as seen
  before: it matches MW's 2024 brief and IC Hack 26's winner, Digital Twin. It stays as a
  *feature*, not the pitch.
- **What was missing:**
  1. **Credibility.** "How do you know that world is right?" We answer it by having reality
     grade the worlds.
  2. **The question.** No feature makes an idea novel. The hook is which world we show, and
     whether a stranger instantly wants to see it.
  3. **The breakthrough: borrow how MW think.** Run many futures, weight them, and kill the
     ones reality disproves.

## 2. What "novel" means here
**Novel can mean unconventional, not new: a proven method pointed at data nobody uses it on.**
Hedge funds could have counted cars from satellites years before they did; the method existed,
nobody had made the connection. We're building a proof of concept, so we can use any data
source, however impractical commercially.

**Formula: a proven quant method × data nobody points it at.**

| Quant method | In finance | Our clone |
|---|---|---|
| **Alpha capture (TOPS, MW's own)** | Weight brokers' ideas by their track record | Predictors' ideas about the future each become a world, and reality scores them |
| Mark-to-market | Reprice positions continuously | Reprice every parallel London on each new camera frame |
| Particle filter / ensemble | Many scenarios, weighted by fit | Reality prunes the multiverse |
| **Lead-lag / Granger causality** | Which stock moves first | **London's nervous system:** which streets lead the city ("Bond St predicts Soho 40 min later") |
| Pairs / cointegration | Two stocks that move together | Hidden twin neighbourhoods |
| Event study | A stock's reaction to news | A street's reaction to a strike or downpour, and how fast it recovers |
| Volatility surface | How uncertain prices are | The city's implied volatility: the spread of our worlds per street |
| Factor attribution | Break a return down into its drivers | Why a street looks like this right now |

**Pitch line for novelty:** *"Everything we use already exists: Marshall Wace's own alpha capture
idea, ensemble forecasting, public TfL cameras. Nobody has pointed them at a city's parallel
futures."*

## 3. Shortlist (ranked)

### ① Reality Prunes the Multiverse (the engine)
> "Right now there are 200 Londons that could exist at 3:30 this afternoon. Watch reality kill
> the ones that are wrong."
- **24h:** one area (Oxford Circus), a cloud of futures forked from live data, narrowing and
  splitting as data arrives. A street-by-street map of how uncertain each street is.
- **Demo:** worlds go dark live, or in a sped-up replay of the recorded day, ending on one live
  update.
- **Risk:** lowest. Everything else builds on it.

### ② An exchange of futures (the presentation layer, clones TOPS; became OverWatch)
> "Marshall Wace built TOPS to work out which brokers to trust. We built it to work out which
> London is real. Every parallel London is a stock: its price is the chance it's the world we're
> living in."
- **24h:** 20–50 listed worlds, repriced on each update. Predictors (AI personas and real
  people) submit ideas with a conviction level and build track records. Judges go long or short
  from their phone. **Points only, never money.**
- **Demo:** a judge buys a world, the next frame lands, and it jumps or gets delisted.
- **Risk:** the most moving parts.

### ③ London's Nervous System (lead-lag)
> "We ran a quant's lead-lag analysis on 150 London street cameras and found which streets move
> first. This is the map of who leads London."
- **24h:** Granger causality across recorded camera counts, drawn as a living network over the
  map. It also makes ① and ② smarter: worlds where the leading streets have already moved get
  repriced first.
- **Risk:** it needs several hours of recording before the signal shows.

### ④ The Smallest Change
> "Something went wrong in London today. We searched a thousand parallel worlds for the smallest
> change that would have prevented it."
- **Risk:** needs a real incident in our recorded window.

### ⑤ Factor Attribution for a City
> "Why is Oxford Street rammed right now? 41% Christmas lights, 27% the Central line delay, 18%
> rain."
- **Risk:** reads as analytics, not a new world.

### ⑥ Londons with Different Rules
> "London with no cars. Same real people, same real morning, different rules."
- **Risk:** the hardest to defend technically.

**Recommendation:** ① as the engine + ② on stage + ③ if the recording gives a signal. The
Oxford St crash is the "place an event" button. If ② runs late, ① is a complete demo on its own.

## 3b. What other teams will pitch (from James's AI research: `all-tracks-gpt.md`, `all-tracks-gemini.md`)
- **Both GPT and Gemini independently suggested the same MW idea:** "find the voids between
  research fields and synthesise the missing link" (Terra Incognita / Latent Space Voyager).
  If two models give it unprompted, many teams will get it too. **Treat it as crowded.**
- **GPT's top MW pick, ForkWorld, is a what-if branching city that is *fictional*.** That
  validates our space. Our edge is what a fictional sim can't claim: **the real city, live, with
  reality pruning the worlds, through MW's own TOPS mechanism.**

**Borrowed from ForkWorld (good ideas, our setting):**
1. **"The average looks fine. Follow this person."** Pick one simulated commuter and show why
   their day differs between two worlds. It turns a cloud of statistics into a story.
2. **Controlled randomness (common random numbers):** worlds share the same random draws except
   the one thing that differs, so a difference between worlds is caused by that change, not by
   noise. A real technical point to state in the 3 minutes.
3. **An explanation trail:** every outcome links back to the events that produced it ("why did
   this world die?").

Optional, from Brevan Howard's "Mirage": validate the pruning against a **null model** (run it
on shuffled data and show it no longer picks a winner). Strong credibility if time allows.

## 4. Decide, don't ideate forever
1. Each of us writes 5 "what if London…" questions, and we pick the top 2.
2. Gut-check: say the one-liner to 3 strangers (another team, a mentor, MW at 14:00). Leaning in
   means yes; nodding means no.
3. Ask MW: "Is a live exchange of parallel Londons novel enough for New Worlds?"
4. **Cutoff around 15:00**, then build.

## 5. Pitch rules (3-minute slot)
- Hook in the first 10 seconds. Live demo on a laptop.
- Say: "parallel present", "the London that didn't happen", "watch reality choose".
- Avoid: "digital twin", "traffic simulator", "chain of events".
