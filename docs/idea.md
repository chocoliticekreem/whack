---
title: Our idea — the current thinking
date: 2026-11-07
status: ideating · track chosen (Marshall Wace) · code reuse approved, not pulled yet
---

# Our idea

Built on **Mirrorverse**, our October build: a live mirror of real London fed by TfL cameras,
buses and disruptions (see [build.md](build.md)). This doc covers *what we make with it*.

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
