---
title: Devpost submission draft
event: WHACK 26 (https://warwickhack.co.uk)
track: Marshall Wace — New Worlds
status: draft, checked against main through #46 and the live system on Sat 7 Nov ~19:30. Add every teammate on Devpost and re-check the live link before submitting (Sun 09:00–11:00)
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
OverWatch

## Team
Team name: OverWatch.
Members: add every teammate on Devpost.

## Tagline (≤ 200 chars)
OverWatch: a live stock exchange on London's next five minutes. Every price comes from 50 futures, narrowed by real TfL data, traded against seven AI traders.

## Inspiration
Prediction markets like Polymarket and Kalshi price the questions someone thought to ask. We wanted to price a
city's next five minutes, starting with London.

Marshall Wace built **TOPS** in 2002, the first alpha capture system: collect ideas from many brokers, measure who
has been right, and trust them accordingly. We pointed the first half of that idea at a city. The brokers are AI
traders, each reading one stream of alternative data; reality settles every bet, and each trader builds a public
track record (kept in memory today, so a restart resets it). Weighting their picks into the price by that record is
next.

The "new world" in the brief is the one nobody can see yet: the London of five minutes from now.

## What it does
- **Lists city-wide markets on London every 5 minutes**, on the London clock. Every round has:
  - **Hub surge:** "Which hub surges most by 19:20?" Oxford Circus, King's Cross, Piccadilly Circus, Waterloo and
    Bank, each measured against its usual Saturday.
  - **City Pulse:** over or under on average busyness across 20 TfL stations, with the line on a half point.
  - **The London VIX:** higher or lower, when the engine is warm (it lists only after a 60-minute warm-up). Otherwise
    "planes holding over Heathrow" stands in. In the rounds we watched on Saturday evening, Heathrow stood in every
    time.
  - **Hourly headlines:** which hub ends the hour furthest above its usual, and a "clean sheet" on whether every
    Tube line on Good Service stays Good for the hour.
- **Prices every market from 50 futures.** They are statistical walks fitted to today's data, starting from the
  latest real reading. On the crowding markets a share of them (half by default, set by `SIM_WEIGHT`) is drawn from our
  central-London agent simulation when it is running. Each new reading rules out the futures that disagree with it, up
  to 30 of the 50 per market, and an LMSR market maker prices what's left plus the bets.
- **Seven AI traders bet against you**, and each reads one live feed: Tube Crowd (station crowding), Camera Eye (the
  London VIX), Line Watcher (Tube status), Rain Radar (rain), Plane Spotter (aircraft in the Heathrow holding stacks),
  The Contrarian (where the points are going) and Simulation (the agent simulation's forecast). Each gives a reason with a real number, for example "Bank is at
  84% of a usual Saturday, the furthest below: it catches up".
- **Settles on real data under published rules.** Every market shows its source and rule. Betting closes 60 s before
  settlement. Crowding markets settle on the first TfL reading our recorder polls after bets close (settling on TfL's
  own timestamp is next). The settled reading is frozen and shown, for example "Waterloo rose most: 99% → 104% of a usual
  Saturday". No reading in the window means void and a refund.
- **The London VIX:** an uncertainty gauge for the area the agent simulation covers (Oxford Circus in the default
  setup): the spread of predicted crowds across 50 simulated worlds. It needs a 60-minute warm-up before it lists.
- **A wallet built like an exchange's:** points only, on an append-only double-entry ledger, with a daily loss limit,
  stake confirmation, breaks, and P&L and history.
- **Simulation that has to earn its place.** The data settles the markets. The central-London agent simulation feeds
  part of the crowding futures and trades as the Simulation trader, so the leaderboard scores it against the
  data-only traders. It doesn't beat "nothing changes" yet (see Accomplishments). A scenario mode, where you drop a
  closure anywhere in central London and watch every market reprice, is in progress.

## How we built it
- **Live data:** our own recorder polls the TfL Unified API (station crowding and typical-day profiles, Tube line
  status, road status, disruptions, the citywide bus snapshot, 796 bike docks, 83 JamCams). It also polls Open-Meteo
  rain, aircraft over Heathrow from adsb.lol, Elexon grid demand, GDELT news, Wikipedia attention and London football
  fixtures. YOLO counts people on the 13 market cameras. A bash supervisor (`scripts/run-data.sh`) restarts anything
  that stalls, and a health server reports every feed's age.
- **Futures and pricing (`services/city`):** 50 futures per market. Crowding markets use a log random walk with
  step sizes fitted to today's real changes. The VIX and Heathrow markets bootstrap today's own moves. Clean sheet
  uses a per-line break hazard. On the crowding markets, part of the 50 (half by default) is
  drawn from the central-London simulation's forecast when it's running. Each observation prunes the futures that
  disagree, particle-filter style, up to 30 of the 50 per market; after that a market's futures stop narrowing (in a
  replay of Saturday evening, 67 of 83 markets reached that cap). Prices come from LMSR (b = 1200) on top of the
  futures' probabilities. Lines are set per series: City Pulse at the reading at open plus the median 5-minute move;
  the VIX and Heathrow at the current value; the hourly hub market widens its fit until the favourite opens at 70% or
  less; the clean sheet holds its opening probability to 30–70%. A bracket market whose favourite is still over 85%
  isn't listed.
- **Agent simulation (`services/multiverse`):** persona groups on the real OpenStreetMap street graph around Oxford
  Circus, with Dijkstra routing and pavement capacity. It runs 50 worlds with **common random numbers**, so a
  difference between two worlds comes from their assumptions, not noise. Worlds are weighted against each camera
  count, bus reading, rain, crowding and bike reading, and resampled. The London VIX is the price-weighted spread of
  predicted people at the 5-minute horizon.
- **Market integrity, following Kalshi's rules where we can** (no official-timestamp settlement or surveillance yet):
  - a source and rule on every market;
  - betting closes before settle;
  - settlement on a fixed window, then a grace period, then void and refund;
  - a frozen settlement snapshot;
  - halts on a stale feed or a 3σ jump;
  - a position cap (25% of bankroll);
  - markets close the moment the outcome is decided;
  - a payout never exceeds the market maker's shares, and bets under 1.05x are refused;
  - a house exposure cap.
- **Wallet:** an append-only double-entry SQLite ledger with server-issued player tokens, idempotent bets and
  exactly-once settlement. 129 city, 127 simulation and 16 recorder tests, including a randomised 1000-bet invariant
  test.
- **Front end:** Next.js 16 on Vercel and a MapLibre map with OpenFreeMap vector tiles, styled after RAISE, with
  Kalshi × PostHog market cards. The backend runs on our laptop behind a Cloudflare tunnel.
- **Reuse (disclosed, approved by the organisers):** the London twin, the live-feed plumbing, YOLO counting and the
  base forecast engine come from our earlier project Mirrorverse (built 17 Oct 2026). New at WHACK: the recorder,
  the multiverse engine and London VIX, the city markets, the AI traders, the integrity rules, the wallet and the
  OverWatch UI.

## Challenges we ran into
- **Our first questions were already decided or pure dice.** "Which station is busiest" went to Piccadilly Circus 63
  times out of 66. Rain was 0.0 mm all day. Bus and single-camera counts were noise. We measured candidate questions
  on Saturday's recorded data and kept the ones where reading the data gives an edge. For example, the hub furthest
  below its usual Saturday surged most 35% of the time, against 20% by luck.
- **One-sided markets.** Early fits priced the hourly hub market at 89% when it really came in 56% of the time, and
  the clean sheet at 85% against a real 43%. We now widen the hourly hub fit until its favourite opens at 70% or
  less and hold the clean sheet to 30–70% at open. In a 15:00–18:30 replay the favourite opened at 30–70% in
  68–86% of markets, depending on the series.
- **Frozen YOLO counts.** The recorder asked a 2-worker YOLO server about all 83 cameras, so a full pass took hours
  and every count went stale. We now count only the 13 cameras the markets read.
- **The forecast lost to "nothing changes".** On real Oxford Circus counts the simulation was off by 5.49 people
  per camera against 1.54 for persistence. Anchoring forecasts to the last real count and adding rain, crowding and
  bike likelihoods brought it to a tie (latest run: 2.06 vs 2.04).
- **Our own trader beat the house.** A judge-prep replay of Saturday found that copying Tube Crowd's rule (back the
  usual-Saturday curve) beat our market maker: on City Pulse it won 21 of 23 bets, +48% per point staked, on data
  the rule hadn't been tuned on. The market maker doesn't price the usual-Saturday slope yet. Adding it to the
  futures' drift is in progress; until then the leaderboard shows it openly.
- **The three.js twin was too heavy** for a city-wide view. We switched to MapLibre with vector tiles, and the first
  map frame now lands in about 410 ms.
- **Review and a red team found real holes:** anyone could claim any player id, and a price near certainty could
  pay more than it should. We fixed both with server-issued tokens, an odds floor and tests.

## Accomplishments that we're proud of
- It runs live on real London data: rounds list, AI traders bet with real reasons, and markets settle on real TfL
  and adsb.lol readings.
- In a 15:00–18:30 replay of Saturday: 42 rounds and 0 voids. Over the whole day, 308 of 309 markets of the earlier
  lineup settled on a real reading.
- At 18:00 a final whistle led Tube Crowd to back King's Cross, which won at 132% of its usual Saturday.
- A money path that holds up: append-only double-entry ledger, exactly-once settlement, and 129 city tests including a
  randomised 1000-bet invariant test and an 8-thread hammer test.
- We report accuracy honestly. On the latest 30 real minutes of Oxford Circus (48 camera counts) the people forecast
  ties persistence (MAE 2.06 vs 2.04, 79% of readings inside the 80% range), and persistence still wins on bus delay
  (0.34 vs 0.04 min). Our central-London simulation loses to persistence by 15% on station busyness (MAE 4.14 vs
  3.61, 919 readings over seven 60-minute windows, 12:36–19:36 UTC).

## What we learned
- A good market question is one you can only win by reading the data. Local questions, where anyone standing there
  knows the answer, kill the urge to bet.
- Uncertainty is the product. A market whose favourite opens over 85% isn't worth listing.
- Measure against the naive baseline before claiming anything. Ours was "nothing changes", and it was hard to beat.
- List only outcomes that emerge from many people's behaviour, never one person's decision. Clean sheet is the
  exception: a line failure is one event that TfL staff see before the feed does. Today the market closes the moment
  a reading shows a break, stakes are capped at 25% per market, and it's points only; a bet delay is next.

## What's next
- **Make the simulation earn its place:** the central-London simulation and the Simulation trader are live, and its
  P&L against the data-only traders shows whether a model of the city beats raw data. Next: make it beat
  persistence, and add a scenario mode where councils, TfL and event organisers can stress-test interventions.
- **Prizes:** sponsored prizes with free entry, paid for by venues and brands.
- **A real-money exchange where licensed:** peer-to-peer event contracts with exchange fees and no house risk.
  Today's house-banked LMSR is bookmaking, not an exchange; an order book is on the roadmap. The ledger is
  real-money grade; the order book, payments, ID checks, anti-money-laundering checks and licensing are the work
  left.
- **Hedging and data:** businesses hedge footfall, weather and disruption risk; index insurance pays out
  automatically; the London VIX and our live probabilities become an alternative-data feed.
- Then more cities. The engine needs a street graph and live public feeds.

## Built with
python · typescript · next.js · react · maplibre-gl · openfreemap · openstreetmap · tfl-unified-api · yolo ·
open-meteo · adsb.lol · sqlite · vercel · cloudflare-tunnel

## Links
- Live: https://overwatchcapital.vercel.app/multiverse
- Repo: https://github.com/chocoliticekreem/whack
- Prior work (disclosed): https://github.com/chocoliticekreem/mirrorverse

## Media checklist (files in `marketing/assets/`)
- [ ] Cover image / thumbnail (3:2): the OverWatch map with this round's markets
- [ ] Screenshot: a market card with the AI traders' bets and reasons
- [ ] Screenshot: a settled round with the frozen reading
- [ ] Screenshot: the wallet panel (P&L and history)
- [ ] Architecture diagram: feeds → recorder → 50 futures → pruning → LMSR → settlement → ledger
- [ ] Demo video (≤ 2–3 min), as a backup to the live demo
