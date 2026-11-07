---
title: Devpost submission draft
event: WHACK 26 (https://warwickhack.co.uk)
track: Marshall Wace — New Worlds
status: draft — fill every [FILL] and re-check every [VERIFY] against what actually works before submitting (Sun 09:00–11:00)
---

# Devpost submission

**Submission rules (opening ceremony):**
- Project name = **our group name**. Add **every teammate**.
- Add images and the description.
- Submit **after 09:00 Sun, before 11:00**. Submit even if it's unfinished: no Devpost entry
  means no prize.

Copy each section below into the matching Devpost field.

---

## Project name
[FILL: group name]

## Tagline (≤ 200 chars)
We price the world itself, starting with London: parallel futures simulated live from TfL data, drawn in 3D and traded like stocks. Reality is the market.

## Inspiration
Prediction markets like Polymarket and Kalshi price the questions someone thought to ask. We
wanted to price the world itself, starting with London.

Every city runs one timeline. We wanted to see the others.

Marshall Wace built **TOPS** in 2002, the first alpha capture system: collect ideas from many
brokers, measure who has been right, and trust them accordingly. We asked what happens if you
point that idea at a city instead of a market. The "stocks" become possible futures of London,
and the brokers are AI agents with opinions about what happens next. Reality settles every bet.

The "new world" in the brief is the one nobody can see: the London that hasn't happened yet.

## What it does
- **Forks London into 50 parallel worlds** around Oxford Circus, 30 minutes ahead. Each world is
  an agent simulation (commuters, tourists, the lunch crowd, bus arrivals) on the real street
  map, seeded from live TfL data, with different assumptions: who sets off, rain, bus delays,
  pavement capacity.
- [VERIFY: 3D ghost view not built yet] **Draws them in 3D** as ghost cities over the real one. Where the worlds agree, the city looks
  solid. Where they disagree, it shimmers. That shimmer is the city's uncertainty, made visible.
- **Lists every world as a stock**, priced by the probability that it's the one that happens.
- **Reality is the market.** Each new TfL camera frame and bus update reprices every world.
  Contradicted worlds are delisted with a plain reason ("predicted 23 people, cameras saw 86"),
  and survivors spawn new worlds.
- **AI brokers bet too.** Persona agents (a commuter, a tourist, a bus driver, a contrarian)
  submit ideas about the future with a conviction level. Each idea spawns worlds, and reality
  scores every broker's track record, TOPS-style: brokers who keep calling it right get more say.
- [VERIFY: judge betting UI not built yet] **You can bet**, on points only: buy a world and watch the next frame settle it.
- **Place an event:** drop a closure or crowd surge on a street and watch the multiverse fork.

## How we built it
- **Live data:** the TfL Unified API (citywide bus snapshot of about 17,000 stops every minute,
  Tube arrivals, line and road status, disruptions, 796 bike docks, station crowding against
  typical-day profiles, about 80 JamCams with YOLO people/vehicle counts). Our own recorder
  writes it all to a time series. History comes from Open-Meteo, Environment Agency Thames
  gauges, Elexon grid demand and Carbon Intensity.
- **Simulation:** a persona-group agent model on the real OpenStreetMap street graph, with
  Dijkstra routing, pavement capacity and queueing, and interventions. Each world runs in about
  20 ms, so 50 worlds spawn in under a second.
- **The multiverse:** a particle filter. Worlds share **common random numbers**, so a
  difference between two worlds comes from their assumptions, not noise. Likelihood weighting
  against each observation, delisting below a threshold, then systematic resampling with lineage.
- **Brokers:** [VERIFY: being tuned] Claude Haiku 4.5, one batched call per observation for every persona, with
  strict JSON, validation and a rules fallback. Track records use exponentially decayed scoring.
- **Front end:** Next.js, plus [VERIFY] a three.js 3D twin of London built from OpenStreetMap.
- **Reuse (disclosed, approved by the organisers):** the London twin, live-feed plumbing and
  base forecast engine come from our earlier project Mirrorverse (built 17 Oct 2026). New at
  WHACK: the multiverse engine, the exchange, the brokers, the recorder and the ghost-world
  visualisation.

## Challenges we ran into
- [FILL: e.g. stopping the market collapsing onto one world after a surprising frame (tuning the likelihood width)]
- [FILL: e.g. API quirks: station hubs vs NaPTAN ids, Elexon's 7-day range limit, JamCam clips overwritten in place]
- [FILL]

## Accomplishments that we're proud of
- In replay, the market converges on the hidden true world: the rain probability goes from
  0.26 to 1.0, and the bus-delay estimate goes from 3.0 to 4.2 min (true value 4.0).
  [FILL/UPDATE with live-London numbers]
- [FILL: the 3D shatter moment / a live demo result]
- [FILL]

## What we learned
- [FILL]

## What's next
- **London is our first market. The world is the exchange.** The engine only needs a street graph
  and live observations, and public CCTV and transit feeds exist for dozens of cities (NYC, Hong
  Kong, Norway, Helsinki…). After cities: ports, power grids, supply chains.
- A null-model check (shuffle reality and show no world wins), to prove the pricing isn't luck.
- More cities and more alternative data: aircraft holding patterns over Heathrow, grid demand,
  Wikipedia attention as a leading indicator.
- [FILL]

## Built with
python · typescript · next.js · react · three.js · openstreetmap · tfl-unified-api · yolo ·
claude-haiku-4-5 · open-meteo · environment-agency · elexon · [FILL]

## Links
- Repo: https://github.com/chocoliticekreem/whack
- Demo video: [FILL]
- Prior work (disclosed): https://github.com/chocoliticekreem/mirrorverse

## Media checklist (files in `marketing/assets/`)
- [ ] Cover image / thumbnail (3:2): the 3D shimmering twin
- [ ] Screenshot: the exchange board mid-reprice
- [ ] Screenshot: the broker leaderboard
- [ ] GIF: a frame lands and worlds shatter
- [ ] Architecture diagram: feeds → worlds → market → brokers
- [ ] Demo video (≤ 2–3 min), as a backup to the live demo
