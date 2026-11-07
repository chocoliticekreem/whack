---
title: Build — what we reuse, what we record, what we need
date: 2026-11-07
status: OverWatch live (Vercel + tunnels) · see §0 · refreshed through #81
prior_art: https://github.com/chocoliticekreem/mirrorverse (built 2026-10-17 at a VAST hackathon)
---

# Build

## 0. Status (Sat 7 Nov, ~23:50; main at e59773b, #81)
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
| **City markets** | Every round: hub_surge (5 hubs vs a usual Saturday), city_pulse (20 stations; line = the half point nearest the futures' median), london_vix (higher/lower) or heathrow_stack when the VIX is down. Hourly: hub_hour, clean_sheet (closes with its first round). Station markets settle on the first reading TfL stamped after close, about 14 min later. :8051 | `services/city/` (#33, #40, #42, #72, #77, #81) |
| **Pricing** | 50 futures per market. City Pulse walks follow TfL's usual-Saturday curve (#72); hub_surge uses `RelativeRaceSim` (mean reversion toward the five-hub average, correlated shocks, Platt softening, #77). Weighted updating keeps prices moving after the 60% ruled-out cap (#72). LMSR b = 1200, fee 5%. `SIM_WEIGHT=0` | `services/city/pricing.py` |
| **Local markets** | Click anywhere: a pin drops and, within 400 m of one of 33 real sensors (20 TfL Crowding stations, about 13 JamCams with fresh counts), a local market opens for the round, settled only on that sensor. Otherwise a forecast only, no bet | `services/city/local.py` (#71) |
| **Integrity** | Source and rule on every market, close before settle, void and refund, frozen reading with `measuredAt` (values measured at or before close void). Courtsiding: bet delay per series (45 s clean sheet and local, 5 s hubs and pulse, 2 s VIX and Heathrow), suspend on a feed change, caps 10%/25% | `integrity.py` (#61, #81) |
| **AI traders** | 7, each reading one feed (Tube Crowd, Camera Eye, Line Watcher, Rain Radar, Plane Spotter, The Contrarian, Simulation). Rule-based, not LLMs. Stake min(100, 10% of points); records persist in the ledger DB | `services/city/traders.py` (#33, #55, #72) |
| **Wallet** | Append-only double-entry SQLite ledger, server-issued tokens, daily gross-loss limit, stake confirmation (counts pending stakes), breaks, house exposure cap (counts AI stakes), odds floor. Restart rebuilds open hourly markets and reads caps from the ledger | `ledger.py`, `Wallet.tsx` (#45, #70, #81) |
| **Portfolio** | `/multiverse/portfolio`: cash, equity, P&L, equity curve, open positions, bet history with CSV export, P&L by series, record vs each AI trader, raw ledger | (#65) |
| **OverWatch UI** | MapLibre dusk-palette 3D map with deck.gl hub columns and arcs at 60 fps (#68); plain-language cards: a question, two buttons, "Bet 100 · win 400" (#69, #73, #78); scrolling exchange ticker (#75); "result about HH:MM" on station cards (#74); traders dropdown, live earnings dropdown, win confetti showing profit, busyness as change vs normal ("Bank −19%") (#78, #80); first-visit "How it works" | `apps/stream/src/app/multiverse/` |
| **Scenario mode** | "What if…": click a street or station, pick Close this street / Crowd here / Shut station; runs on copies of the central sim (live state unchanged) and shows the implied odds on the map and cards | (#66) |
| **Oxford Circus multiverse** | Agent simulation on the real street graph: 50 worlds, common random numbers, 5-min horizon. Produces the London VIX; listed in 9 of 9 rounds since the last restart | `services/multiverse/` (#24, #26, #31) |
| **Central-London sim** | 1,017 streets, 50 worlds, live forecasts, safe what-ifs; general event layer (no hardcoded venues) | `services/multiverse/` (#49, #54, #60) |
| **Accuracy check** | Oxford Circus (`--windows 6`, 30 min to 23:38 UTC, 48 counts): MAE 1.37 vs persistence 1.35, 79% inside the 80% range; persistence wins on bus delay (0.65 vs 0.99 min). Central London: station MAE 3.94 vs persistence 3.61 (#60). Out-of-sample replay (17:00–21:30): hub_surge Brier 0.755 vs uniform 0.800 | `eval_real.py`, `analysis/judge/replay_score.py` |
| **Pressure test** | Bots traded 75 min of live rounds on a scratch ledger: 0 voids, 0 fills after close, 120/120 reconciliations; bugs fixed in #81 | `analysis/live_trading/` (#79) |
| **Docs** | Market structure today (#58), the exchange design target (#67), judge prep (#57, refreshed), steering log | `docs/`, `marketing/steering-log.md` |

**Merged since the first MVP (#49–#81):** event layer (#49, #60), futures fan (#50), name and icon (#51, #52), central
sim and Simulation trader (#54, #55), market structure (#58), courtsiding defences (#61), claim corrections (#63),
portfolio (#65), scenario mode (#66), exchange design (#67), 3D map (#68), plain cards (#69, #73), stale-login fix
(#70), click-anywhere markets (#71), judge fixes (#72), result times (#74), ticker (#75), pricing round 2 (#77),
earnings and traders dropdowns and confetti (#78, #80), live pressure test (#79) and its fixes (#81).

**In progress:** nothing open. Next, if time allows: re-run the live trading test on main (its pricing findings
predate #72 and #77).

**Gaps, ranked:**
1. **The laptop is the single host** for the recorder, YOLO, both sims and the markets. If it sleeps or goes offline,
   the site falls back to demo data.
2. **The tunnel URLs change on restart.** Cloudflare quick tunnels, so Vercel's `CITY_URL` must be reset each time.
3. **One Saturday of data** behind every number; samples are small and directional.
4. **The hub copycat result has a wide CI.** Copying Tube Crowd on hubs: +13%, 95% CI −27% to +55% (#77); not proven
   gone. Fading the Heathrow stack still beats the house out of sample (+66%, n=34).
5. **The sims have no measured skill.** Central London loses to persistence; the event layer has no skill. Both stay
   out of the price (`SIM_WEIGHT=0`).
6. **After a restart**, current 5-minute markets rebuild from fresh data (City Pulse may get a new line under the same
   id), and pending bets are cancelled.
7. **`X-Forwarded-For` can be spoofed** past the new-player rate limit (a `debt:` note in `services/city/server.py`).
8. Port defaults disagree: the city server polls the VIX on :8349, the multiverse defaults to :8049. Set
   `MULTIVERSE_PORT=8349` (or `MULTIVERSE_URL`) as in the run commands below.

**Run it:**
```sh
scripts/run-data.sh &                                          # recorder + YOLO (:8047) + health (:8052); set YOLO_DIR
MULTIVERSE_PORT=8349 python3 services/multiverse/server.py &   # Oxford Circus engine + London VIX
python3 services/city/server.py &                              # city markets + wallet (:8051)
cd apps/stream && MULTIVERSE_URL=http://127.0.0.1:8349 npx next dev -H 0.0.0.0 -p 3349   # open /multiverse
cloudflared tunnel --url http://localhost:8051                 # public backend; put the URL in Vercel CITY_URL
```
Replay instead of live: `python3 services/city/server.py --replay-from 2026-11-07T14:00:00Z`. Tests:
`python3 -m unittest discover -s services/city` (205), `-s services/multiverse` (132), `-s recorder` (16).

## 1. Code reuse: approved
- **The organisers confirmed (Sat 7 Nov) that we can build on Mirrorverse.** It's disclosed in
  the Devpost draft and in the reused READMEs. We get judged on the new layer.
- Brought in with #24: `services/predict`, `services/yolo`, `apps/stream`.

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
