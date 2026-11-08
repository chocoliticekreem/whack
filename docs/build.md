---
title: Build — what we reuse, what we record, what we need
date: 2026-11-07
status: OverWatch live (Vercel + tunnels) · see §0 · refreshed through #87
prior_art: https://github.com/chocoliticekreem/mirrorverse (built 2026-10-17 at a VAST hackathon)
---

# Build

## 0. Status (Sun 8 Nov, ~00:55; main at bbfc214, #87)
**Live:** OverWatch at https://overwatchcapital.vercel.app/multiverse. The front end is on Vercel (#41); `/api/city/*`
and `/api/sim/*` reach the laptop backend through Cloudflare quick tunnels. City-wide markets run in 5-minute rounds on
the London clock, priced from 50 futures, settled by real TfL and adsb.lol readings, against 7 AI traders. Points
only. No PRs are open.

Example settled round (23:45, from `GET /round`): "London VIX 5.57 vs 5.70 → Lower". Station markets for the same
round show "Betting closed · result about 00:00, when TfL publishes".

| Piece | State | Where (all on `main`) |
|---|---|---|
| **Recorder** | Live under `scripts/run-data.sh`, which restarts it if a key feed stalls for 3 min. Polls 83 JamCams; YOLO counts only the 13 market cameras, so counts stay fresh | `recorder/`, `scripts/run-data.sh` (#11, #22, #27, #30) |
| **Data health** | Per-feed age and status, YOLO check, `GET /health` on :8052; 16 sources | `services/health/` (#30) |
| **City markets** | Every round: hub_surge (5 hubs vs a usual Saturday), city_pulse (20 stations; line = the half point nearest the futures' median), london_vix (higher/lower) or heathrow_stack when the VIX is down. Hourly: hub_hour, clean_sheet (closes with its first round). Station markets settle on the first reading TfL stamped after close, about 14 min later. :8051 | `services/city/` (#33, #40, #42, #72, #77, #81, #86, #87) |
| **Pricing** | 50 futures per market. City Pulse walks follow TfL's usual-Saturday curve (#72); hub_surge uses `RelativeRaceSim` (mean reversion toward the five-hub average, correlated shocks, Platt softening, #77), re-simulated untempered from the first in-round TfL publish (#86). heathrow_stack is a Poisson count around a level reverting toward its 30-min mean, line on the futures' median (`CountSim`, #86). Weighted updating keeps prices moving after the 60% ruled-out cap (#72). LMSR b = 1200, fee 5%. `SIM_WEIGHT=0` | `services/city/pricing.py` |
| **Local markets** | Click anywhere: a pin drops and, within 400 m of one of 33 real sensors (20 TfL Crowding stations, about 13 JamCams with fresh counts), a local market opens for the round, settled only on that sensor. Otherwise a forecast only, no bet | `services/city/local.py` (#71) |
| **Integrity** | Source and rule on every market, close before settle, void and refund, frozen reading with `measuredAt` (values measured at or before close void). Courtsiding: bet delay per series (45 s clean sheet and local, 5 s hubs and pulse, 2 s VIX and Heathrow), suspend on a feed change, caps 10%/25% | `integrity.py` (#61, #81) |
| **AI traders** | 7, each reading one feed (Tube Crowd, Camera Eye, Line Watcher, Rain Radar, Plane Spotter, The Contrarian, Simulation). Rule-based, not LLMs. Stake min(100, 10% of points not already staked on open markets), so a trader can't go below 0 (#86); records persist in the ledger DB | `services/city/traders.py` (#33, #55, #72, #86) |
| **Wallet** | Append-only double-entry SQLite ledger, server-issued tokens, daily gross-loss limit, stake confirmation (counts pending stakes), breaks, house exposure cap (counts AI stakes), odds floor. Restart rebuilds open hourly markets and reads caps from the ledger | `ledger.py`, `Wallet.tsx` (#45, #70, #81) |
| **Portfolio** | `/multiverse/portfolio`: cash, equity, P&L, equity curve, open positions, bet history with CSV export, P&L by series, record vs each AI trader, raw ledger | (#65) |
| **OverWatch UI** | MapLibre dusk-palette 3D map with deck.gl hub columns and arcs at 60 fps (#68); plain-language cards: a question, two buttons, "Bet 100 · win 400" (#69, #73, #78); scrolling exchange ticker (#75); "result about HH:MM" on station cards (#74); traders dropdown, live earnings dropdown, win confetti showing profit, busyness as change vs normal ("Bank −19%") (#78, #80); first-visit "How it works"; phone portrait and landscape (market column left, map right, #83) | `apps/stream/src/app/multiverse/` |
| **Scenario mode** | "What if…": click a street or station, pick Close this street / Crowd here / Shut station; runs on copies of the central sim (live state unchanged) and shows the implied odds on the map and cards | (#66) |
| **Oxford Circus multiverse** | Agent simulation on the real street graph: 50 worlds, common random numbers, 5-min horizon. Produces the London VIX; listed in 14 of 15 rounds in the live re-run; since #87 it lists in round 1 after a restart | `services/multiverse/` (#24, #26, #31) |
| **Central-London sim** | 1,017 streets, 50 worlds, live forecasts, safe what-ifs; general event layer (no hardcoded venues) | `services/multiverse/` (#49, #54, #60) |
| **Accuracy check** | Oxford Circus (`--windows 6`, 30 min to 23:38 UTC, 48 counts): MAE 1.37 vs persistence 1.35, 79% inside the 80% range; persistence wins on bus delay (0.65 vs 0.99 min). Central London: station MAE 3.94 vs persistence 3.61 (#60). Out-of-sample replay (17:00–00:05): hub_surge Brier 0.753 vs uniform 0.800 (t = 2.7, n=83) | `eval_real.py`, `analysis/judge/replay_score.py` |
| **Pressure test** | Bots traded 75 min of live rounds on a scratch ledger: 0 voids, 0 fills after close, 120/120 reconciliations; bugs fixed in #81. Re-run for 65 min on code with #72 and #77: 30/30 crowding markets settled on TfL readings stamped after close, City Pulse opens 0.50–0.66, 0 frozen prices, 0 of 229 fills after close, house −177 (was −1,408). What it found is fixed in #86 and #87, except the City Pulse evening bias and the UI retry | `analysis/live_trading/` (#79, #84, #85) |
| **Exchange venue (prototype)** | The Kalshi-style design in `docs/exchange-design.md`, built as a separate process beside the live exchange: one order book per binary contract, no house, 12 AI market makers quoting both sides, 184 hedge funds, 2,000 retail, Kalshi's taker fee (makers free), and the same speed bumps, position limits, halts and settlement sources. Books and ledger are in memory and it reads the recorder files read-only. Run: `python3 services/city/venue_server.py` (:8062; `CITY_VENUE_PORT` overrides), or `python3 services/city/venue_demo.py` for an offline walkthrough and health report. API only, no UI page. Demo on 25 recorded minutes (Sat 15:00 UTC): all 9 health checks pass, median quoted spread 4 points, designated market makers quoting 95–96% of the time, closing price no better calibrated than the forecast alone (Brier 0.1256 vs 0.1255, n=16). **The live site still runs the LMSR house.** Pricing against the LMSR is not yet compared | `services/city/venue*.py` (#67 design, #90, #94) |
| **Docs** | Market structure today (#58), the exchange design target (#67), judge prep (#57, refreshed), steering log | `docs/`, `marketing/steering-log.md` |

**Merged since the first MVP (#49–#87, plus the venue):** event layer (#49, #60), futures fan (#50), name and icon (#51, #52), central
sim and Simulation trader (#54, #55), market structure (#58), courtsiding defences (#61), claim corrections (#63),
portfolio (#65), scenario mode (#66), exchange design (#67), 3D map (#68), plain cards (#69, #73), stale-login fix
(#70), click-anywhere markets (#71), judge fixes (#72), result times (#74), ticker (#75), pricing round 2 (#77),
earnings and traders dropdowns and confetti (#78, #80), live pressure test (#79) and its fixes (#81), doc refresh
(#82), phone landscape (#83), live re-run report (#84, #85), Heathrow and mid-round hub pricing and the AI stake cap
(#86), midnight profile reload, rounds built off the lock and VIX in round one (#87); the parallel exchange venue (#90)
and the fixes its demo found: selling at the position limit, market-maker quote uptime, P&L after fees (#94).

**In progress:** the UI makes a new idempotency key when it retries after a client timeout; the fix is on a branch,
not merged.

**Gaps, ranked:**
1. **The laptop is the single host** for the recorder, YOLO, both sims and the markets. If it sleeps or goes offline,
   the site falls back to demo data.
2. **The tunnel URLs change on restart.** Cloudflare quick tunnels, so Vercel's `CITY_URL` must be reset each time.
3. **One Saturday of data** behind every number; samples are small and directional.
4. **The live server must run #87 or later.** One started earlier voids vs-usual markets from the 00:03 profile
   refresh until it is restarted (`analysis/live_trading/RERUN.md` #1).
5. **City Pulse is biased in a falling evening.** Under won 14/15 in the live re-run; copying Tube Crowd on City Pulse
   made +42% after 17:00 in replay (95% CI +23% to +61%, n=82). No stable fix in the fit window (#86).
6. **#86 and #87 are measured offline only.** After 17:00: Heathrow fade −7.6% (CI −28% to +14%), hub top riser +6%
   (CI −25% to +42%); the riser still makes +48% on 12:40–20:10. No live bot run since.
7. **The sims have no measured skill.** Central London loses to persistence; the event layer has no skill. Both stay
   out of the price (`SIM_WEIGHT=0`).
8. **After a restart**, current 5-minute markets rebuild from fresh data (City Pulse may get a new line under the same
   id), and pending bets are cancelled.
9. **`X-Forwarded-For` can be spoofed** past the new-player rate limit (a `debt:` note in `services/city/server.py`).
10. Port defaults disagree: the city server polls the VIX on :8349, the multiverse defaults to :8049. Set
   `MULTIVERSE_PORT=8349` (or `MULTIVERSE_URL`) as in the run commands below.

**Run it:**
```sh
scripts/run-data.sh &                                          # recorder + YOLO (:8047) + health (:8052); set YOLO_DIR
MULTIVERSE_PORT=8349 python3 services/multiverse/server.py &   # Oxford Circus engine + London VIX
python3 services/city/server.py &                              # city markets + wallet (:8051)
python3 services/city/venue_server.py &                        # optional: exchange venue prototype (:8062), not on the site
cd apps/stream && npx next dev -H 0.0.0.0 -p 3349                         # open /multiverse
cloudflared tunnel --url http://localhost:8051                 # public backend; put the URL in Vercel CITY_URL
```
Replay instead of live: `python3 services/city/server.py --replay-from 2026-11-07T14:00:00Z`. Tests:
`python3 -m unittest discover -s services/city` (295), `-s services/multiverse` (132), `-s recorder` (16).

## 1. Code reuse: approved
- **The organisers confirmed (Sat 7 Nov) that we can build on Mirrorverse.** It's disclosed in
  the Devpost draft and in the reused READMEs. We get judged on the new layer.
- Brought in with #24: `services/predict`, `services/yolo`, `apps/stream`.
- **What remains after the OverWatch-only cleanup:**
  - `services/predict`: the street-graph agent simulation. `services/multiverse` imports its seed, personas,
    simulate and LLM modules (`worlds.py`, `brokers.py`), so the Oxford Circus engine and the London VIX build on it.
  - YOLO: the camera counter runs from the original Mirrorverse service outside this repo (`YOLO_DIR` in
    `scripts/run-data.sh`). The in-repo copy of `services/yolo` is removed.
  - Seeds: `services/multiverse/seeds/oxford-circus-field.json` (from the twin's `/api/twin/field`) and
    `central-osm.json.gz` (from the twin's cached OpenFreeMap tiles) were built once from Mirrorverse's twin. They are
    committed; `real_seed.py --offline` and `central_seed.py --offline` rebuild the seeds from them.
  - `apps/stream` holds only the OverWatch pages (`/multiverse`, `/multiverse/portfolio`) and their two proxies
    (`/api/city/*`, `/api/sim/*`). No Mirrorverse code remains in it.
  - Removed: the streaming app (persona chat, phone streaming, "around me", copilot, the `/twin` page) and its
    routes (`/api/agent/*`, `/api/chat/scene`, `/api/route`, `/api/live/state`, `/api/live/signal`, and
    `/api/around/{air,crime,livecams,stations,tide}`); then the superseded `/multiverse/oxford` and
    `/multiverse/board` pages, `/api/multiverse/*`, the three.js twin (`src/twin`, `src/lib`, `src/platform`,
    `src/components`) and its routes (`/api/around/*`, `/api/twin/*`, `/api/predict`, `/api/live/stream`), and the
    `three`, `@react-three/*` and `postprocessing` dependencies.

## 2. What Mirrorverse already has
| Need | Status | Where in `chocoliticekreem/mirrorverse` |
|---|---|---|
| 3D London twin from OpenStreetMap | Built, centred on **Tower Hill**. The Oxford Circus seed (#26) re-centres the simulation; `/multiverse` now draws London with MapLibre instead (#44) | `apps/stream/src/twin/` (`origin.ts`, `osm.ts`, `network/`) |
| Live feeds: TfL cameras, buses, disruptions, weather, air, crime | Built | `apps/stream/src/app/api/around/*` |
| People/vehicle counts on cameras | Built (YOLO11 + ByteTrack) | `services/yolo/server.py` |
| Counts per street segment ("field") | Built | `apps/stream/src/twin/field/`, `/api/twin/field` |
| **Forecast / what-if engine** | Built: persona groups on the real street graph, Dijkstra re-planning, interventions `closure`, `incident`, `event`, `weather` | `services/predict` (`POST /predict`) |
| Scoring a forecast against the cameras | Built | `services/predict` `/score` |
| Forecast "ghost" layer in 3D | Built | `apps/stream/src/twin/predict/` |

## 3. New for WHACK (built)
- **Many worlds instead of one forecast:** 50 forked Predict runs with varied assumptions and common random numbers
  (`services/multiverse`, #24).
- **Pruning** against each new real reading (likelihood weighting, at most 30 of 50 ruled out in total per market), in both the
  multiverse and the city markets (#24, #33).
- **The exchange:** city-wide 5-minute rounds, LMSR pricing, integrity rules, the wallet and the AI traders
  (#33, #40, #42, #45).
- **UI:** the OverWatch map and market sidebar (#32, #38, #44).
- Not built: the lead-lag network, and the 3D ghost-world layer (see idea.md "Superseded ideas").

## 4. Data sources
Full research is in **[data-sources.md](data-sources.md)**: what Mirrorverse uses, mobility feeds,
alternative data, the city comparison and the gotchas. Short version: London, every live TfL feed
is keyless, history is thin, so record now.

## 5. Recorder (built, running)
Polls, per `recorder/README.md`:
| Source | Poll |
|---|---|
| Bus snapshot `/Mode/bus/Arrivals?count=1` (~17,400 stops) | 60 s |
| Tube arrivals, line status, road status | 60 s |
| Disruptions (only written when changed) | 5 min |
| BikePoint (796 docks) | 2 min |
| Crowding live, 20 central stations (+ typical profiles once a day) | 5 min |
| JamCam metadata, ~83 central cams (`--yolo` adds people/vehicle counts) | 60 s (for 5-minute markets) |
| `aircraft`: adsb.lol, 40 nm round Heathrow + holding-stack counts (BNN/LAM/BIG/OCK) | 60 s |
| `grid`: Elexon FUELINST + demand; Carbon Intensity London | 5 min (carbon 30 min) |
| `rain_now`: Open-Meteo current + 15-min now-cast at Oxford Circus | 5 min |
| `news`: GDELT 15-min export, events within 5 km of Oxford Circus | each new file (checked every 5 min) |
| `wiki_views`: daily pageviews, 40 West End/London articles | startup + 6 h |
| `wiki_edits`: Wikimedia EventStreams, edits to those articles | live (SSE) |
| `scrape_fixtures` (`recorder/scrape/run.py`): London home football fixtures today/tomorrow | 30 min |

Every source can be switched on or off with `--only` / `--skip`. Each AI trader (city markets) and broker
(Oxford Circus multiverse) reads one of these streams.

- About 320 MB/day, mostly the bus snapshot.
- It only runs while the laptop is awake and online (`caffeinate -i`).
- Start it with `scripts/run-data.sh`, which runs `caffeinate -i python3 recorder/record.py --yolo` under
  supervision. Fixtures: `caffeinate -i python3 recorder/scrape/run.py`.
- Attribute adsb.lol (ODbL) if we use it. YouTube cams are embed-only.

## 6. Open
- [ ] Who owns: engine, UI/3D, data recorder, pitch?
- [x] Where the recorder runs: Anson's laptop (keep it plugged in and awake).
- [x] Area: central London (5 hubs, 20 stations). The Oxford Circus simulation produces the VIX; the central-London
  simulation (#54) powers scenario mode and the Simulation trader.
