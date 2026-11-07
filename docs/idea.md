---
title: Our idea — the current thinking
date: 2026-11-07
status: leading idea chosen (Exchange of Worlds) · building on local branch · see build.md §0
---

# Our idea

Built on **Mirrorverse**, our October build: a live mirror of real London fed by TfL cameras,
buses and disruptions (see [build.md](build.md)). This doc covers *what we make with it*.

## ★ The pitch (consolidated)
**The Exchange of Worlds**, built on Mirrorverse. One product in three layers. The image leads
and the mechanism comes after.

| Layer | What it is | Role in the pitch |
|---|---|---|
| **1. The worlds** (engine) | 50 parallel versions of Oxford Circus, 30 min ahead, forked from live TfL data | *The new world*: futures that don't exist yet |
| **2. The market** (betting) | Every world is a stock; price = probability it happens. Each real camera frame reprices it, and wrong worlds are **delisted** | *The interaction*: judges bet, reality settles it |
| **3. The brokers** (TOPS) | AI personas (commuter, tourist, bus driver, contrarian) bet on the futures they believe in. Reality scores their track record, and the right ones get more say | *The MW hook*: their founding idea, pointed at a city |

**Rule:** lead with the stock-market image. Say TOPS once, after it lands. Betting is how
judges touch it, not the point. Points only, never real money.

**10 s** (technical · presentation · creativity):
"50 parallel Londons, **simulated live from TfL data**, **drawn in 3D**, and **traded like
stocks**. Reality is the market."

**30 s:**
"Right now we're simulating 50 versions of Oxford Circus, 30 minutes from now. Each is a full
agent simulation on the real street map, seeded from live TfL cameras and buses *(technical)*.
You see them in 3D as ghost cities over real London: where they agree it's solid, where they
disagree it shimmers *(presentation)*. Every world is a stock, priced by how likely it is. AI
brokers bet on them, and every real camera frame reprices the market. Wrong worlds shatter, and
brokers who called it gain track record. It's Marshall Wace's TOPS idea, pointed at a city
*(creativity)*."

**Close:** "Marshall Wace built TOPS to find out which brokers to trust. We built it to find out
which London is real."

**Mapped to MW's three criteria (the idea ties them together):**
| Criterion | What delivers it | What the judge sees |
|---|---|---|
| **Creativity** (the idea) | A stock exchange of possible futures, with AI brokers held to account TOPS-style | "I've never seen that" |
| **Technical complexity** (the engine) | Live TfL feeds → 50 simulated worlds on the real street graph with common random numbers → likelihood repricing → broker track records | "That's hard, and it works" |
| **Presentation** (the visualisation) | **The 3D twin of real London with the parallel worlds as ghost layers.** On each camera frame the wrong worlds shatter and fade, and the survivors glow | "That looks incredible" |

**The visualisation:**
- Real London in 3D around Oxford Circus (OSM buildings and streets, from the Mirrorverse twin).
- **Ghost crowds:** each live world's people and buses as translucent particles, brightness set
  by its price. Where worlds agree the city looks solid; where they disagree it shimmers.
  **The shimmer is the uncertainty, made visible.**
- **The kill moment:** a camera frame lands, a pulse goes out from that camera, the contradicted
  worlds shatter, the survivors snap into focus, and the board reprices.
- **Placing an event:** click a street, a red fork ripples out, and the cloud splits.

**Visual references, from God's Eye View** ([repo](https://github.com/bilawalsidhu/gods-eye-view), MIT):
- **Take: one shader look.** Its GLSL "reskin reality" looks (FLIR thermal, night vision, CRT,
  noir) are why it went viral. A thermal or noir treatment on the ghost worlds makes the
  shimmer and shatter moment hit harder. Small, self-contained shaders are the cheapest thing to
  bring into our three.js twin, with MIT attribution.
- **Take: a scripted camera ("scene director").** Pre-authored camera moves for a reliable
  backup demo video and the Devpost GIFs.
- **Take: detection-box overlays** on the camera frames that reprice the market (YOLO boxes
  are already there).
- **Avoid:** opening on a satellite globe of live flights. "Live public data on a 3D globe" is
  *its* identity, and judges have probably seen it (#1 on GitHub Trending, Aug 2026). **It shows
  the present; we show the futures and let reality price them.** Lead with the multiverse,
  not the map.
- **Don't adopt its codebase.** It's Cesium + Vue, our twin is three.js + React, and merging
  them would eat the night.

**3-minute demo (open on the visual):**
1. **0:00 presentation:** the 3D twin, shimmering. "These are the 50 Londons that could exist
   in 30 minutes. The blur is how uncertain the city is right now."
2. **0:20 creativity:** cut to the board. "Every one is a stock. Its price is the chance it
   happens. Buy one." A judge bets.
3. **0:45 the moment:** a frame lands. Worlds shatter in 3D, prices jump on the board, with the
   reason given.
4. **1:20 brokers:** "Our AI brokers bet too. The bus driver has called it three frames
   running." The TOPS line goes here.
5. **2:00 technical:** one line per layer, on the architecture: live feeds → simulation →
   repricing → track records.
6. **2:40 close:** "Marshall Wace built TOPS to find out which brokers to trust. We built it to
   find out which London is real."

**Build gap:** the prototype's `/multiverse` page has a 2D zone map. Wiring the worlds into the
3D twin as ghost layers is the highest-value presentation task. Do it after the broker layer
lands (same page).

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

### ② The Exchange of Worlds (the presentation layer, clones TOPS)
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
