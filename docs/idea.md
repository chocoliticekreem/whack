---
title: Our idea — the current thinking
date: 2026-11-07
status: OverWatch · live at overwatchcapital.vercel.app/multiverse · checked against main through #81 · see build.md §0
---

# Our idea

Built on **Mirrorverse**, our October build: a live mirror of real London fed by TfL cameras,
buses and disruptions (see [build.md](build.md)). This doc covers *what we make with it*. The ★ section,
"Why simulate" and "Superseded ideas" are current; §1–§5 are Saturday morning's reasoning, kept as history.

## ★ The pitch (current, as shipped through #81)
**OverWatch: a live stock exchange on London's next five minutes.** Every 5 minutes it lists plain-language markets
on the city ("Will London's Tube get busier or quieter by 21:10?"). Each price comes from 50 futures fitted to today's
data and TfL's usual Saturday; each new reading reweights them. You trade against seven computer traders that each read
one stream of alternative data and build a public track record, and the market leans on the ones who've been right:
TOPS pointed at a city. Each opening price blends in the traders' views by their decayed, shrunk track record, and
only proven skill counts (services/city/README.md "TOPS loop"). Click anywhere on the map to trade the nearest real sensor, or ask "what if this street closed?"
and our central-London agent simulation shows how the odds would move.

Live at https://overwatchcapital.vercel.app/multiverse. Points only, never real money.

| Layer | What ships today | Role in the pitch |
|---|---|---|
| **1. The futures** (engine) | 50 futures per market from the latest real reading at TfL's own stamp. City Pulse walks follow the usual-Saturday curve; the hub race mean-reverts toward the five-hub average (#72, #77). The central sim can supply a share (`SIM_WEIGHT`), set to 0 live because it loses to persistence. The London VIX comes from the agent simulation of Oxford Circus (50 worlds, common random numbers) | *The new world*: futures that don't exist yet |
| **2. The market** (exchange) | City-wide 5-minute rounds on the London clock, plus click-anywhere local markets on 33 real sensors (#71). Each reading reweights the futures; LMSR turns them and the bets into prices. Kalshi-style rules: a source and rule on every market, close before settle, settlement on TfL's own timestamp, void and refund, a frozen reading, bet delays against courtsiding (#61, #72, #81) | *The interaction*: you bet, reality settles it |
| **3. The traders** (the first half of TOPS) | 7 computer traders, each reading one live feed: Tube Crowd (station crowding), Camera Eye (the VIX), Line Watcher (Tube status), Rain Radar (rain), Plane Spotter (Heathrow holding stacks), The Contrarian (where the points are going), Simulation (the central sim's forecast). Each bets through the same LMSR, gives a reason citing a real number, and keeps a record that survives restarts. The TOPS loop scores every view against the futures and blends the proven ones into the opening price (at most 30%, no trader over 40% of that); with no proven skill the price is the futures | *The MW hook*: their founding idea, pointed at a city |
| **4. The scenario room** | "What if…": click a street or station, close it or add a crowd; the central sim runs on copies and the cards show the odds it implies (#66) | *Why simulate*: answers what data can't |
| **Output: the London VIX** | The price-weighted spread of predicted crowds across the simulated worlds of Oxford Circus, as one index. An uncertainty gauge, not a fear index | *The new data* |

**Every round lists:**
- **hub_surge:** "Which of these 5 areas will see its crowd grow the most by HH:MM?" Oxford Circus, King's Cross,
  Piccadilly Circus, Waterloo, Bank, each against its usual Saturday.
- **city_pulse:** "Will London's Tube get busier or quieter by HH:MM?" Average busyness across 20 TfL stations, line on
  the half point nearest the futures' median, so it opens near 50/50.
- **london_vix:** "Will London get more unpredictable or calmer by HH:MM?" When the VIX is down, **heathrow_stack**
  (planes holding over Heathrow) stands in.
- **Hourly:** hub_hour ("Which area has the busiest evening by 00:00?") and clean_sheet ("Will every Tube line keep
  running smoothly?", betting closes with its first round).

Station markets settle on the first reading TfL stamped after betting closed, about 14 minutes later; the card says
"result about HH:MM, when TfL publishes" (#74). The VIX and Heathrow settle within a minute.

**Rule:** lead with the stock-exchange image. Say TOPS once, after it lands. Betting is how people touch it, not the
point.

**10 s:** "A live stock exchange on London's next five minutes. Every price comes from 50 futures, reweighted by real
TfL data as it arrives. You trade against seven computer traders, each reading one live feed."

**30 s:** "Every five minutes OverWatch lists new markets on London: which of five hubs grows most against a usual
Saturday, whether the Tube gets busier or quieter, whether London gets more unpredictable *(creativity)*. Each price
comes from 50 futures fitted to today's data and TfL's usual day, reweighted by every new reading, and an LMSR market
maker prices them. We settle only on numbers TfL stamped after betting closed *(technical)*. Seven computer traders,
each reading one feed, bet against you, say why and build a public track record. Click anywhere to trade the nearest
sensor, or close a street and watch the odds move *(presentation)*. It's Marshall Wace's TOPS idea
pointed at a city: the opening price leans on the traders who've been right, and only as far as their record earns."

**100 words:**
> Prediction markets like Polymarket and Kalshi price the questions someone thought to ask. **OverWatch prices a
> city's next five minutes, starting with London.**
>
> Every five minutes it lists plain-language markets on central London: which hub grows most, whether the Tube gets
> busier, whether the city gets more unpredictable. Each price comes from 50 futures that real TfL data reweights as
> it arrives. Seven computer traders, each reading one stream of alternative data, bet against you, explain why and
> build a public track record. Real readings settle every market under published rules, on a double-entry ledger.
>
> Nobody knows the outcome in advance. The edge is reading the data better. Our own Tube Crowd trader proved the house
> could be read better; we priced its signal in. That's the leaderboard's job.

**Close:** "Marshall Wace built TOPS to find out which brokers to trust. We built it to find out which data reads a
city best."

**Q&A, honestly** (full bank in [judge-prep.md](judge-prep.md)):
- *"Is the forecast accurate?"* On real Oxford Circus camera counts the simulation ties "nothing changes": MAE 1.37 vs
  1.35 over the latest 30 minutes (48 counts), 79% inside its 80% range (`eval_real.py --windows 6`). Central London
  loses to persistence (station MAE 3.94 vs 3.61), so the sim is out of the price. Out of sample, the hub race's open
  price beats a uniform guess (Brier 0.755 vs 0.800, t = 1.7, n=51); no other series does significantly.
- *"Did your own trader beat the house?"* Yes: copying Tube Crowd made +32–48% a bet. We put the usual-Saturday curve
  into the futures; out of sample it's now about +13%, with a 95% CI of −27% to +55%. Not proven gone.
- *"Real betting?"* No. Points only. The ledger is real-money grade; a house-banked LMSR is bookmaking. The order
  book (exchange-design.md) is built as a prototype beside the live site, not live; taking it live and licensing are
  the work left.
- *"Can someone know the answer?"* Station markets settle on TfL's own post-close stamp, so nobody sees it early.
  Clean sheet is the one single-event market: bets fill 45 s later, any line change pauses it, a 10% cap, and betting
  closes with its first round. A head start under 44 s gains nothing (#61).

**Horizon: 5-minute rounds.** The floor is how fast reality reports back: TfL crowding publishes every 5 minutes (and
late, hence the 14-minute station settle); cameras, the VIX and planes every minute. A judge sees a fast market settle
inside the 3-minute slot.

**Mapped to MW's three criteria:**
| Criterion | What delivers it | What the judge sees |
|---|---|---|
| **Creativity** (the idea) | A stock exchange on a city's next five minutes, computer traders that each read one alternative-data feed and build a track record (the first half of TOPS), click-anywhere markets on real sensors, and an uncertainty gauge for Oxford Circus | "I've never seen that" |
| **Technical complexity** (the engine) | A 16-source recorder (YOLO on 13 cameras) → 50 futures per market, reweighted live → LMSR → settlement on TfL's own timestamp, with bet delays and suspends, on a double-entry ledger; agent simulations with common random numbers for the VIX and what-ifs; 353 tests and a live bot pressure test | "That's hard, and it works" |
| **Presentation** (the visualisation) | A colourful 3D MapLibre London at 60 fps, a scrolling exchange ticker, cards anyone can read ("Bet 100 · win 400"), a live earnings dropdown and win confetti | "That looks good, and I get it" |

**3-minute demo** (full script with lines in judge-prep.md §5):
1. **0:00, a fast market.** The map and the ticker. Open the London VIX card, Heathrow, or click a camera spot to open
   a local market. Bet 100 ("Bet 100 · win 160"); it fills after the bet delay.
2. **0:30:** who's on the other side: the LMSR house, capped on a ledger.
3. **0:50, a station market's fair wait.** A closed hub card: "Betting closed · result about 23:50, when TfL
   publishes". We settle only on numbers TfL stamped after close.
4. **1:20, the traders dropdown.** Tube Crowd's record and reason. The TOPS line, with the honest leak number.
5. **1:50, scenario mode.** "What if…", click Regent Street, "Close this street": the odds move on the cards.
6. **2:20, settle.** The fast market settles; the earnings dropdown updates and, on a win, confetti with the profit.
7. **2:40 close:** the TOPS line.

## Why simulate, not just read data
Owner's direction: the digital twin and the agent simulation must earn their place beyond looks, with no
special-cased scenarios, and the scope is central London, not one junction. **Status: the Oxford Circus simulation
produces the London VIX; the central-London simulation (#54) powers scenario mode (#66) and trades as the Simulation
trader (#55). It loses to persistence, so it's out of the price (`SIM_WEIGHT=0`).**

**Data looks backwards; simulation looks forwards.** Sensors (13 cameras, 20 stations) show what already happened,
and statistical futures only extend trends. The agent simulation on the real street graph can:
1. **Price shocks before sensors see them** (built, no skill yet): events from the feeds (line disruptions, road
   closures, fixtures, crowding surges) become simulation inputs by general rules (#49, #60). Measured against a
   no-event control, they don't predict station busyness yet.
2. **Answer what-ifs with no data** (shipped, #66): close a street, add a crowd or shut a station and see the odds the
   sim implies. It runs on copies; live state is untouched.
3. **Fill gaps between sensors** (partly): a click with no sensor within 400 m shows the sim's people forecast on the
   nearest streets, with no bet (#71). Settlement still uses real sensors only.
4. **Check the data** (not built): a sensor reading the model says is impossible flags and halts the market.
5. **Explain prices** (partly): the 3D map draws arcs between hubs from the hub_surge futures, labelled as a stand-in
   until sim flows are exposed (#68).
6. **B2B** (later): councils, TfL and event organisers stress-test interventions, priced (stage 3 in business.md).

**The Simulation trader** (shipped, #55): bets from the central sim's forecast. Its P&L against the data-only traders
answers live whether a model of the city beats raw data. Tonight's answer: no (8 wins, 30 losses live).

**Positioning:** data settles, the simulation forecasts. The twin is the scenario room, not decoration.

## Superseded ideas (history)
Kept for the reasoning; none of these ship on the main page.
- **Exchange of Worlds board** (#24, was `/multiverse/board`, now removed): every simulated world of Oxford Circus
  listed as a stock, priced by its probability, with 10 data-source brokers (Claude Haiku 4.5 when a key is set, rules
  otherwise) scored TOPS-style. Dropped as the front page because a first-time viewer couldn't read it in seconds.
  Its engine survives as the London VIX, and the broker idea became the six city traders.
- **3D ghost worlds** (#25): parallel worlds drawn as translucent crowds over a three.js twin, shimmering where they
  disagree and shattering when a camera frame lands. The "Which London is real?" game lived at `/multiverse/oxford`;
  the page and the three.js twin are now removed. The city view moved to MapLibre (#44) because the three.js twin was
  too heavy for a city-wide view.
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
