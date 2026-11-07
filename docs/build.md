---
title: Build — what we reuse, what we record, what we need
date: 2026-11-07
status: not started · scope is final once idea.md is decided
prior_art: https://github.com/chocoliticekreem/mirrorverse (built 2026-10-17 at a VAST hackathon)
---

# Build

## 1. Code reuse: approved, not pulled yet
- **The organisers confirmed (Sat 7 Nov) that we can build on Mirrorverse.** Say so openly in
  the Devpost submission, and get judged on the new layer.
- **Don't pull code until the idea is locked.** Then bring in only what the table below needs.

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

## 5. Starter stack: record now
One recorder process writing timestamped NDJSON/Parquet per source. Fresh code in this repo.

| Source | Poll | TfL calls/min |
|---|---|---|
| Bus snapshot `/Mode/bus/Arrivals?count=1` | 60 s (timeout ≥ 60 s; keep stop, vehicle, line, time to station only) | 1 |
| Tube arrivals + line status + road status | 60 s | 3 |
| BikePoint | 2 min | 0.5 |
| Crowding live, ~20 key stations (+ typical profiles once) | 5 min | 4 |
| JamCam clips for 30–50 cameras in our area, through YOLO counts | 60–90 s | 0 (S3) |
| **Backfill once:** Open-Meteo 7 days at 15 min, EA `since=`, Elexon 2 weeks, adsb.lol 1–2 days | once | — |

The backfill gives **an instant 7–14 day backtest** (e.g. every downpour vs grid demand vs
flights) without waiting.

**Gotchas:**
- About 9 TfL calls/min, under the anonymous ~50/min, but that budget is shared with the app.
  **Get a free TfL app_key now** (500/min) and keep it in env, never in git.
- YouTube cams are embed-only under their terms, so keep them to the demo.
- Attribute adsb.lol (ODbL).

## 6. Open
- [ ] Who owns: engine, UI/3D, data recorder, pitch?
- [ ] West End only, or a selectable area?
- [ ] Where the recorder runs (VM or laptop)?
