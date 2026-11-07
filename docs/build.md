---
title: Build — what we reuse, what we record, what we need
date: 2026-11-07
status: OverWatch live (Vercel + tunnel) · see §0
prior_art: https://github.com/chocoliticekreem/mirrorverse (built 2026-10-17 at a VAST hackathon)
---

# Build

## 0. Status (Sat 7 Nov, ~19:30)
**Live:** OverWatch at https://overwatchcapital.vercel.app/multiverse. The front end is on Vercel (#41); `/api/city/*`
reaches the city backend on the laptop through a Cloudflare quick tunnel. City-wide markets run in 5-minute rounds on
the London clock, priced from 50 futures, settled by real TfL and adsb.lol readings, against 7 AI traders.
Points only.

Example settled round (19:15, from `GET /round`): "Waterloo rose most: 99% → 104% of a usual Saturday";
"City Pulse 29.77 vs line 31.50 → Under 31.5"; "3 holding (BNN 0 · LAM 0 · BIG 1 · OCK 2) vs 0.5 → Over".

| Piece | State | Where (all on `main`) |
|---|---|---|
| **Recorder** | Live under `scripts/run-data.sh`, which restarts it if a key feed stalls for 3 min. Polls 83 JamCams; YOLO counts only the 13 market cameras, so counts stay fresh (#27, #33) | `recorder/`, `scripts/run-data.sh` (#11, #22, #27, #30) |
| **Data health** | Per-feed age and status, YOLO check, `GET /health` on :8052; 16 sources | `services/health/` (#30) |
| **City markets** | 5-minute rounds. Every round: hub_surge (5 hubs vs a usual Saturday), city_pulse (20 stations, line on a .5), london_vix (higher/lower) or heathrow_stack in its place. Hourly: hub_hour, clean_sheet. 50 futures per market, particle-filter pruning, LMSR (b = 1200). Kalshi-style integrity rules. :8051 | `services/city/` (#33, #40, #42, #43) |
| **AI traders** | 7, each reading one feed (Tube Crowd, Camera Eye, Line Watcher, Rain Radar, Plane Spotter, The Contrarian, Simulation). Rule-based strategies, not LLMs; they bet through the same LMSR and give a reason citing a real number | `services/city/traders.py` (#33, #42) |
| **Wallet** | Append-only double-entry SQLite ledger, server-issued player tokens, daily gross-loss limit, stake confirmation, 1 h / 24 h breaks, house exposure cap, odds floor. Wallet panel with P&L and history. 129 city tests | `services/city/ledger.py`, `Wallet.tsx` (#45) |
| **OverWatch UI** | MapLibre map (OpenFreeMap tiles, extruded buildings) in the style of RAISE, Kalshi × PostHog styling, no emojis, phone layout | `apps/stream/src/app/multiverse/` (#32, #38, #44, #46) |
| **Oxford Circus multiverse** | Agent simulation on the real street graph: 50 worlds, common random numbers, 5-min horizon, real seed. Produces the London VIX | `services/multiverse/` (#24, #26, #31); also `/multiverse/oxford`, `/multiverse/board` |
| **Accuracy check** | People forecast ties persistence: latest run (`--windows 6`, 30 real minutes to 20:44 UTC, 48 counts) MAE 2.06 vs 2.04, 79% inside the 80% range. Persistence wins on bus delay (0.34 vs 0.04 min). Central London (`--central --windows 7 --minutes 60`): station MAE 4.14 vs persistence 3.61 on 919 readings, so the sim loses by 15% | `services/multiverse/eval_real.py` (#29, #31) |
| **Market questions** | Lineup measured on Saturday's data | `docs/questions.md`, `analysis/questions/` (#39) |

**In progress** (not shipped; see [idea.md](idea.md) "Why simulate, not just read data"):
1. Scaling the agent simulation from the Oxford Circus area to central London.
2. A general event layer: TfL line disruptions, road closures, fixtures ending and crowding surges become simulation
   inputs by one rule, so markets reprice before sensors move.
3. The Simulation trader: a seventh AI trader that bets from the simulation's forecast, scored against the
   data-only traders.
4. Scenario mode: drop a closure or event anywhere in central London and see every open market reprice.
5. Market structure proposal (open PR #36, a teammate's).

**Gaps, ranked:**
1. **The tunnel URL changes on restart.** It's a Cloudflare quick tunnel, so Vercel's `CITY_URL` must be reset each
   time.
2. **The laptop is the single host** for the recorder, YOLO, the multiverse and the markets. If it sleeps or goes
   offline, the site falls back to demo data.
3. **The 50 futures aren't visible in the UI yet.** The card only says "Prices come from 50 simulated futures".
4. **The VIX market lists only after a 1 h warm-up** of a live multiverse engine. Until then heathrow_stack stands in.
   At 19:20 the running engine was on a replay spawn, so every round listed heathrow_stack.
5. **One dry Saturday of data** behind the question design; samples are small and directional.
6. **`X-Forwarded-For` can be spoofed** past the new-player rate limit (a `debt:` note in `services/city/server.py`).
7. City futures are statistical walks fitted to today's data; only the VIX comes from the agent simulation.
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
`python3 -m unittest discover -s services/city` (129), `-s services/multiverse` (127), `-s recorder` (16).

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
- **Pruning** against each new real reading (likelihood weighting, at most 60% ruled out per step), in both the
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
- [x] Area: central London (5 hubs, 20 stations). The agent simulation covers only the Oxford Circus area so far;
  scaling it to central London is in progress.
