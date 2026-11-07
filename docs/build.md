---
title: Build — what we reuse, what we record, what we need
date: 2026-11-07
status: MVP live on main (OverWatch) · see §0
prior_art: https://github.com/chocoliticekreem/mirrorverse (built 2026-10-17 at a VAST hackathon)
---

# Build

## 0. Status (Sat 7 Nov, ~18:15)
**MVP is live:** OverWatch at `/multiverse`. It runs city-wide markets on real TfL data, priced by
simulated futures and settled by real readings, against 6 AI traders. Verified end to end: a 100-point
bet on "Over 12 people on the Oxford Circus camera at 17:51" settled on JamCam 00001.07451 (13 people)
and paid out.

| Piece | State | Where (all on `main`) |
|---|---|---|
| **Recorder** | Live under a supervisor that restarts it if a key feed stalls for 3 min. YOLO counts the 13 market cameras, which stay fresh | `recorder/`, `scripts/run-data.sh` (#27, #30) |
| **Data health** | Per-feed age and status, YOLO and TfL key checks, `GET /health` on :8052 | `services/health/` (#30) |
| **City markets backend** | 5 hotspots. Futures-priced, LMSR, strike selection (no favourite above 85%), Kalshi-style integrity rules (source, close 60 s before settle, settlement window, fallback, void and refund, halts, position cap). 6 AI traders, one feed each. :8051 | `services/city/` (#33) |
| **OverWatch UI** | 3D London with hotspots, market cards, stakes and multipliers, settle flow, AI traders panel, leaderboard, data-health dot, phone layout | `apps/stream/src/app/multiverse/` (#32) |
| **Oxford Circus multiverse** | 50 worlds, 5-min horizon, real seed and real data by default. Rain, crowding and bike likelihood terms. London VIX | `services/multiverse/` (#26, #31); game at `/multiverse/oxford`, board at `/multiverse/board` |
| **Accuracy check** | On 30 real minutes, the people forecast ties "nothing changes" (MAE 2.4 vs 2.4), 80% range covers 77%. Persistence still wins on bus delay | `services/multiverse/eval_real.py` (#29, #31) |

**In progress:**
1. 5-minute rounds with recurring series and bracket ladders. Bus markets dropped. Fixes "too random, too quick".
2. UI restyle: no emojis, Kalshi × PostHog, less "vibe-coded".
3. A teammate is designing better questions (series).

**Gaps, ranked:**
1. Market questions feel random until rounds and series land.
2. "Place an event" exists only on the Oxford Circus board. It doesn't move city prices.
3. The city markets don't use the agent simulation; each station has its own statistical model.
4. All state is in memory and lost on restart.
5. Demo backup: a scripted-camera video (owned by the team).

**Run it:**
```sh
scripts/run-data.sh &                      # recorder + YOLO + health (:8052)
python3 services/city/server.py &          # city markets (:8051)
python3 services/multiverse/server.py &    # Oxford Circus engine (:8049)
cd apps/stream && npx next dev -H 0.0.0.0  # open /multiverse
```

## 1. Code reuse: approved
- **The organisers confirmed (Sat 7 Nov) that we can build on Mirrorverse.** It's disclosed in
  the Devpost draft and in the reused READMEs. We get judged on the new layer.
- Brought in on the local `build/multiverse` branch: `services/predict`, `services/yolo`,
  `apps/stream`.

## 2. What Mirrorverse already has
| Need | Status | Where in `chocoliticekreem/mirrorverse` |
|---|---|---|
| 3D London twin from OpenStreetMap | Built, centred on **Tower Hill**. Needs moving to the West End | `apps/stream/src/twin/` (`origin.ts`, `osm.ts`, `network/`) |
| Live feeds: TfL cameras, buses, disruptions, weather, air, crime | Built | `apps/stream/src/app/api/around/*` |
| People/vehicle counts on cameras | Built (YOLO11 + ByteTrack) | `services/yolo/server.py` |
| Counts per street segment ("field") | Built | `apps/stream/src/twin/field/`, `/api/twin/field` |
| **Forecast / what-if engine** | Built: persona groups on the real street graph, Dijkstra re-planning, interventions `closure`, `incident`, `event`, `weather` | `services/predict` (`POST /predict`) |
| Scoring a forecast against the cameras | Built | `services/predict` `/score` |
| Forecast "ghost" layer in 3D | Built | `apps/stream/src/twin/predict/` |

## 3. New for WHACK (depends on idea.md)
- **Many worlds instead of one forecast:** run N forked Predict runs with varied assumptions.
- **Reprice or prune** the worlds against each new data snapshot (likelihood weighting).
- **UI:** a world cloud or exchange view, an uncertainty map, and the "place an event" button.
- **Optional:** the lead-lag network over recorded counts; the phone trading page (points only).

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

Every source can be switched on or off with `--only` / `--skip`. The alternative-data sources are the
streams the AI brokers read, one each.

- About 320 MB/day, mostly the bus snapshot.
- It only runs while the laptop is awake and online (`caffeinate -i`).
- Start it with: `caffeinate -i python3 recorder/record.py` from the main checkout, plus
  `caffeinate -i python3 recorder/scrape/run.py` for fixtures.
- Attribute adsb.lol (ODbL) if we use it. YouTube cams are embed-only.

## 6. Open
- [ ] Who owns: engine, UI/3D, data recorder, pitch?
- [x] Where the recorder runs: Anson's laptop (keep it plugged in and awake).
- [x] Area: Oxford Circus / West End.
