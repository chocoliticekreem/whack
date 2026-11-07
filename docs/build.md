---
title: Build — what we reuse, what we record, what we need
date: 2026-11-07
status: in progress · recorder live on main · prototype on local branch build/multiverse (not pushed)
prior_art: https://github.com/chocoliticekreem/mirrorverse (built 2026-10-17 at a VAST hackathon)
---

# Build

## 0. Status (Sat 7 Nov, ~13:00)
| Piece | State | Where |
|---|---|---|
| **Recorder** | **Live since 12:06** on Anson's laptop (TfL key, 300/min cap). Bus, tube, line/road status, disruptions, bikes, crowding, JamCam metadata. No YOLO counts yet (`--yolo` off) | `main`: `recorder/`, data in `data/raw/` (gitignored) |
| **History backfill** | Done: 7 days of rain/temp (721 rows), Thames gauges (1,338), 14 days of grid demand (698) and carbon (698) | `data/backfill/` |
| **Multiverse engine** | Built: spawn, observe, prune, resample; common random numbers; 50 worlds in under 1 s; delist reasons; consensus + implied volatility per zone | `build/multiverse`: `services/multiverse/` |
| **Exchange board** | Built: `/multiverse` page with world prices, sparklines, lineage, a 2D zone map, "place an event" | `build/multiverse`: `apps/stream/src/app/multiverse/` |
| **Persona brokers (TOPS)** | Built: 10 personas, Claude Haiku 4.5 (one batched call per observation), rules fallback, track records, leaderboard. **Being tuned now** with live Haiku | `build/multiverse`: `services/multiverse/brokers.py` |
| **Upstream Mirrorverse #52** (live-data resilience, cleaner 3D twin) | Being pulled in now | `build/multiverse` |
| **3D ghost-world view** | **Not started.** Highest-value presentation task | twin in `apps/stream/src/twin/` |
| Keys | TfL app key and Anthropic key in the main checkout's gitignored `.env` | — |

**Gaps, ranked:**
0. **Engine upgrade, queued for after the broker tuning lands:**
   - 5-minute markets (horizon 5 min, observation every ~60 s);
   - the **London VIX** index;
   - **brokers each read one alternative-data feed**, scored on which data has alpha.
   - **In progress now (separate agent):** alternative-data feeds + scrapers in the recorder (aircraft, grid, rain, GDELT, Wikipedia, theatre/events).
1. The engine runs on a **synthetic fixture**. It needs a real Oxford Circus seed whose camera IDs
   match the recorder's.
2. **No people counts yet:** start `services/yolo` and restart the recorder with `--yolo`.
3. **3D ghost worlds** in the twin (the presentation centrepiece).
4. Optional: null-model check (shuffle reality, no world should win); "follow this person".
5. Known engine bug: bus riders are counted at the station before their bus arrives.
6. **Demo robustness** (lessons from God's Eye View; see data-sources.md §3b):
   - Cache the Oxford Circus OSM extract locally; Overpass refuses old clients.
   - Show stale data as **stale**, never as zero.
   - Have a fallback camera feed (another city's CCTV, or recorded replay).
   - Record a scripted-camera backup video.

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

- About 320 MB/day, mostly the bus snapshot.
- It only runs while the laptop is awake and online (`caffeinate -i`).
- Start it with: `caffeinate -i python3 recorder/record.py` from the main checkout.
- Attribute adsb.lol (ODbL) if we use it. YouTube cams are embed-only.

## 6. Open
- [ ] Who owns: engine, UI/3D, data recorder, pitch?
- [x] Where the recorder runs: Anson's laptop (keep it plugged in and awake).
- [x] Area: Oxford Circus / West End.
