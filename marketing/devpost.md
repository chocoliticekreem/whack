---
title: Devpost submission draft
event: WHACK 26 (https://warwickhack.co.uk)
track: Marshall Wace — New Worlds
status: final draft, checked against main through #87 (bbfc214), the replay and tests re-run on it and the live trading re-run (analysis/live_trading/RERUN.md). Add every teammate on Devpost and re-check the live link before submitting (Sun 09:00–11:00)
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
OverWatch: a live stock exchange on London's next five minutes. Prices come from 50 futures reweighted by real TfL data; you trade against seven computer traders.

## Inspiration
Prediction markets like Polymarket and Kalshi price the questions someone thought to ask. We wanted to price a
city's next five minutes, starting with London.

Marshall Wace built **TOPS** in 2002, the first alpha capture system: collect ideas from many brokers, measure who
has been right, and trust them accordingly. We pointed the first half of that idea at a city. The brokers are computer
traders, each reading one stream of alternative data; reality settles every bet, and each trader builds a public track
record. Weighting their picks into the price by that record is next.

The "new world" in the brief is the one nobody can see yet: the London of five minutes from now.

## What it does
- **Lists plain-language markets on London every 5 minutes**, on the London clock. Each card is a question, two or
  five answer buttons and what you win ("23% chance · Bet 100 · win 410"). Every round has:
  - **Hub surge:** "Which of these 5 areas will see its crowd grow the most by 21:10?" Oxford Circus, King's Cross,
    Piccadilly Circus, Waterloo and Bank, each against its usual Saturday.
  - **City Pulse:** "Will London's Tube get busier or quieter by 21:10?" Average busyness across 20 TfL stations; the
    line sits at the half point nearest the futures' median, so it opens near 50/50.
  - **The London VIX:** "Will London get more unpredictable or calmer?" An uncertainty gauge for Oxford Circus: the
    spread of predicted crowds across 50 simulated worlds. When it's down, "planes holding over Heathrow" stands in.
  - **Hourly headlines:** which area ends the hour furthest above its usual, and whether every Tube line keeps running
    smoothly.
- **Click anywhere on the map to trade that spot.** If a real sensor is within 400 m (20 TfL stations and about 13
  JamCams with fresh people counts), a local market opens for the round, settled only on that sensor. Elsewhere you
  get a forecast and no bet.
- **Asks "what if?"** Click a street or station, close it or add a crowd, and our central-London agent simulation runs
  on copies of the city and shows how the odds would move.
- **Seven computer traders bet against you**, each reading one live feed: Tube Crowd (station crowding), Camera Eye
  (the London VIX), Line Watcher (Tube status), Rain Radar (rain), Plane Spotter (Heathrow holding stacks), The
  Contrarian (where the points are going) and Simulation (the agent simulation's forecast). Each gives a reason with
  a real number ("Bank is at 93% of a usual Saturday, the furthest below: it catches up") and keeps a public record.
- **Settles on real data under published rules.** Every market shows its source and rule. Station markets settle on
  the first reading **TfL stamped after betting closed**, so nobody polling faster can see the answer; TfL publishes
  late, so the card says "result about 22:00, when TfL publishes". The VIX and Heathrow settle within a minute. The
  settled reading is frozen and shown; no reading means void and a refund.
- **Feels like a trading app:** a scrolling exchange ticker ("Waterloo +5% · Tube 6/11 · Bakerloo severe delays"),
  busyness as change vs normal ("Bank −7%"), a live earnings dropdown, a traders dropdown with sparklines, and a short
  burst of confetti showing your profit when you win.
- **A wallet built like a brokerage account:** points only, on an append-only double-entry ledger, with a daily loss
  limit, stake confirmation and breaks, plus a portfolio page with your equity curve, every bet, P&L by series, your
  record against each computer trader and the raw ledger.

## How we built it
- **Live data:** our own recorder polls the TfL Unified API (station crowding and typical-day profiles, Tube line
  status, road status, disruptions, the citywide bus snapshot, 796 bike docks, 83 JamCams). It also polls Open-Meteo
  rain, aircraft over Heathrow from adsb.lol, Elexon grid demand, GDELT news, Wikipedia attention and London football
  fixtures. YOLO counts people on the 13 market cameras. A bash supervisor restarts anything that stalls, and a health
  server reports every feed's age.
- **Futures and pricing (`services/city`):** 50 futures per market, each starting from the latest real reading at
  TfL's own timestamp. City Pulse walks follow TfL's usual-Saturday curve. The hub race uses a relative
  mean-reversion model: each hub follows its usual-day slope, is pulled toward the five-hub average (rate fitted on
  12:00–17:00) and takes correlated shocks; the first TfL publish inside the round restarts every future from that
  reading. Heathrow stacks are a Poisson count around a level that reverts toward its 30-minute mean, chosen out of
  sample. The VIX bootstraps today's own moves; clean sheet uses a per-line break hazard. Each reading reweights the
  futures by likelihood and rules out the worst, up to 30 of 50.
  An LMSR market maker (b = 1200, 5% fee) prices the result plus the bets.
- **Agent simulations (`services/multiverse`):** persona groups on the real OpenStreetMap street graph, with Dijkstra
  routing and pavement capacity, 50 worlds with **common random numbers**, so a difference between two worlds comes
  from their assumptions, not noise. One covers Oxford Circus and produces the London VIX. A central-London one
  (1,017 streets) takes live events by general rules (no hardcoded venues or lines) and powers scenario mode. It
  doesn't beat persistence, so it stays out of the price and trades as the Simulation trader instead.
- **Market integrity, following Kalshi's rules where we can:**
  - a source and rule on every market; betting closes before settle; a frozen settlement snapshot with TfL's own
    measurement time;
  - settlement on TfL's post-close timestamp; a value measured before the close voids;
  - courtsiding defences: each bet fills a few seconds after you place it (45 s on the single-event clean sheet and on
    local markets), any big feed change pauses the market and cancels pending bets, and stakes are capped at 10% or 25%
    of your points;
  - halts on a stale feed or a 3σ jump; markets close the moment the outcome is decided;
  - a payout never exceeds the market maker's shares, and bets under 1.05x are refused;
  - a house exposure cap that counts the computer traders' stakes.
- **Testing:** 228 city, 132 simulation and 16 recorder tests, including a randomised 1000-bet ledger invariant and an
  8-thread hammer. Then we pressure-tested it with bots: seven strategies (random, favourite, longshot, signal,
  copycat, Kelly and a last-second reader) traded live rounds on a scratch ledger, twice: 327 bets over 75 minutes,
  then 314 more over 65 minutes after our pricing fixes. The bugs they found are fixed with regression tests (40 of
  them), all but one pricing bias we report below.
- **Front end:** Next.js 16 on Vercel; a MapLibre map with OpenFreeMap vector tiles and deck.gl layers (a dusk palette,
  glowing hub columns, arcs between hubs) at 60 fps. Phones work in portrait and landscape (market column left, map
  right). The backend runs on our laptop behind Cloudflare tunnels.
- **Reuse (disclosed, approved by the organisers):** from our earlier project Mirrorverse (built 17 Oct 2026), the
  street-graph agent simulation in `services/multiverse` builds on Mirrorverse's predict engine (`services/predict`,
  which it imports), and the YOLO camera counter runs from the original Mirrorverse service. The committed Oxford
  Circus and central-London street seeds were built once from Mirrorverse's three.js London twin; the twin itself is
  no longer in the repo. New at WHACK: the recorder, both agent simulations and the London VIX, the city and local markets, pricing, the computer traders,
  the integrity rules, the wallet and portfolio, scenario mode and the OverWatch UI.

## Challenges we ran into
We built with a small team and many parallel AI coding agents. The agents wrote code fast; the hard part was
direction. The owner redirected the build 30 times in one day (logged in `marketing/steering-log.md`). Five that
changed the product:
- **"I have no idea what anything means."** Our first screen was a 2D exchange board full of jargon. We rebuilt it as
  a game with one question at a time. Six hours later the owner looked at our market cards and said: "I don't
  understand even a bit of it. Any person should be able to understand how to trade." The cards became a plain
  question, big answer buttons with the chance and the payout, and details behind a tap. We cold-read each version
  until fresh readers could say how to bet from a screenshot.
- **"I'd only feel the urge to bet if I was there in person."** Our first questions were about one camera or one bus
  stop: decided or pure noise at 5 minutes. We measured candidate questions on Saturday's recorded data and kept the
  ones where reading the data gives an edge: for example, the hub furthest below its usual Saturday surged most 35% of
  the time, against 20% by luck.
- **"If someone on the train sees it stop before the API does, can they trade on that?"** Yes, they could. We added a
  bet delay, a pause whenever the feed changes and tighter caps on the one single-event market. A head start of up to
  44 seconds now gains nothing (it gained 305 points before).
- **Fast or fair settlement.** TfL publishes its crowding numbers several minutes after it stamps them (our poller sees a row a median 7 minutes after its stamp). Settling fast
  meant settling on a number someone could already see. The owner chose fair, and to show the wait: station cards say
  "result about HH:MM, when TfL publishes".
- **"What do the twin and the simulation add besides looking cool?"** An honest answer was "nothing measurable yet":
  our central-London simulation loses to "nothing changes". So we took it out of the prices, let it trade as one more
  trader, and gave it the job data can't do: what-ifs. The first what-if was a settings form; the owner said "this
  makes no sense", so it became click a street, pick an action, see the odds move.

The technical challenges:
- **Our own trader beat the house.** A replay found that copying Tube Crowd's rule (back TfL's usual-Saturday curve)
  beat our market maker by 32–48% per bet. We put that curve into the futures and rebuilt the hub race model. On the
  hub race the edge is gone (−3% out of sample). On City Pulse it isn't: as the city empties in the late evening,
  copying Tube Crowd still makes +42% (95% CI +23% to +61%, n=82), because our futures don't carry the evening
  decline. No fix held up in our fit window, so we left it open and say so.
- **Bots pressure-tested the exchange, and they found real bugs.** The first live run (75 minutes, 327 bets) caught
  restart, pending-bet, confirm-bypass and settlement-record bugs, which we fixed. We re-ran the bots on our new pricing
  for 65 minutes and measured again: all 30 crowding markets settled on TfL readings stamped after close, City Pulse
  opened at 50–66% instead of up to 97%, no prices froze, and the house lost 177 points to the bots instead of 1,408.
  The re-run and a re-check of our live and replay numbers then exposed three more leaks, which we fixed and
  measured out of sample: fading the Heathrow holding stack went from +47% to −7.6% (CI −28% to +14%), backing the
  hub that had risen most went from +76% to +6% (CI −25% to +42%), and The Contrarian, which had gone to −181 points
  through a stake-sizing bug, can no longer go below 0. The re-run also found that markets measured against the usual
  day voided after the midnight profile refresh until a restart, and that a server stall made a filled bet look
  rejected. The reload and the stall are fixed; a fix for the app's retry is still on a branch.
- **The forecast lost to "nothing changes".** On real Oxford Circus counts the simulation was first off by 5.49 people
  per camera against 1.54 for persistence. Anchoring every world to the last real count and adding rain, crowding and
  bike likelihoods brought it to a tie.
- **The event layer made forecasts worse** because a weekend planned closure read as every line shut at Bank and
  Waterloo. Working out which lines serve a station from the whole day fixed it.
- **Frozen counts, frozen prices.** YOLO was asked to count 83 cameras on 2 workers, so counts were hours old; we count
  only the 13 the markets read. Prices also stopped moving once 30 of 50 futures were ruled out; weighting the
  survivors cut the stalled markets from 96 to 42, and on current code none stall in the full-day replay.
- **Review and a red team found real holes:** anyone could claim any player id, and a price near certainty could pay
  more than the market maker's shares. Both fixed, with tests that fail on the old code.

## Accomplishments that we're proud of
- It runs live on real London data at overwatchcapital.vercel.app/multiverse: rounds list, computer traders bet with
  real reasons, and markets settle on real TfL, adsb.lol and simulation readings.
- A full-day replay of Saturday (91 rounds, 275 markets) settled every market on a real reading: 0 voids.
- A money path that held up under two rounds of live bot trading: 0 fills after close in either (0 of 229 in the
  re-run), every bet on a halted market refused (46 of 46, then 26 of 26), wallets reconciled exactly (120 of 120, then
  8 of 8 at the end of the re-run), and a clean ledger audit.
- Settlement nobody can front-run: in the re-run, 30 of 30 crowding markets settled on readings TfL stamped after
  betting closed, and our recorder saw none of them before the close.
- We used the bots to find our own leaks, fixed three, and re-measured each out of sample instead of declaring it
  fixed. The one we couldn't fix yet, City Pulse in a falling evening, we report.
- We report accuracy honestly. On the latest 30 real minutes of Oxford Circus (48 camera counts), the people forecast
  ties persistence: MAE 1.37 vs 1.35, with 79% of readings inside the 80% range; persistence wins on bus delay. Out of
  sample (17:00–00:05), the hub race's opening price beats a uniform guess (Brier 0.753 vs 0.800, n=83, t = 2.7); the
  other series don't, significantly.
- Anyone can play: in cold-read tests, fresh readers given only a screenshot could say how to bet ("tap an answer,
  choose your points, then confirm"), at desktop and phone widths, and you can turn your phone sideways and keep
  trading.

## What we learned
- **Measure before you claim.** Our naive baseline, "nothing changes", was hard to beat, and a quant will ask. The
  honest number is more defensible than a flattering one.
- **The leaderboard is the product.** When our own Tube Crowd trader beat the house, that was the system working: it
  told us which data the market maker was ignoring. That's the TOPS idea.
- **A good market question is one you can only win by reading the data.** Local questions, where anyone standing there
  knows the answer, kill the urge to bet; obvious ones (a favourite over 85%) aren't worth listing.
- **Information is good; riskless information isn't.** List outcomes that emerge from many people's behaviour. Clean
  sheet is the exception, a single event TfL staff see first, so it gets a 45-second bet delay, a pause on any line
  change, a 10% cap and a 5-minute betting window. Points only.
- **Fair beats fast.** Waiting 14 minutes for a number nobody could have seen is better than settling on one somebody
  could.
- **Agents give speed; direction stays human.** Every turn in our steering log was a person looking at what had been
  built and saying what was wrong.

## What's next
- **The second half of TOPS:** weight each trader's picks into the price by its track record, so the market learns
  which data to trust.
- **Close the remaining leak:** City Pulse in a falling evening, by fitting the evening decline on more than one
  day.
- **Make the simulation earn a place in the price:** beat persistence on held-out days, then blend it back in.
- **A real exchange:** a Kalshi-style order book with AI market makers and no house (designed in
  `docs/exchange-design.md`, not built). The ledger is real-money grade; the order book, payments, ID checks,
  anti-money-laundering checks and licensing are the work left.
- **Prizes, hedging and data:** sponsored free-entry prizes; footfall and disruption cover for businesses; the London
  VIX and live probabilities as an alternative-data feed.
- Then more cities. The engine needs a street graph and live public feeds.

## Built with
python · typescript · next.js · react · maplibre-gl · deck.gl · openfreemap · openstreetmap · tfl-unified-api · yolo ·
open-meteo · adsb.lol · sqlite · vercel · cloudflare-tunnel

## Links
- Live: https://overwatchcapital.vercel.app/multiverse
- Repo: https://github.com/chocoliticekreem/whack
- Prior work (disclosed): https://github.com/chocoliticekreem/mirrorverse

## Media checklist (files in `marketing/assets/`)
- [ ] Cover image / thumbnail (3:2): the OverWatch map with this round's markets
- [ ] Screenshot: plain market cards ("Bet 100 · win 400") and the ticker
- [ ] Screenshot: a station card with "result about HH:MM, when TfL publishes"
- [ ] Screenshot: scenario mode ("What if Regent Street closes")
- [ ] Screenshot: the earnings dropdown and the portfolio page
- [ ] Architecture diagram: feeds → recorder → 50 futures → reweighting → LMSR → settlement → ledger
- [ ] Demo video (≤ 2–3 min), as a backup to the live demo
