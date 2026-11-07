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

## 4. Record the live feeds now (needed for ①, ③ and ④ in idea.md)
Every hour we don't record is an incident we can never replay or test against. **Start this
before ideating finishes.** It's a fresh, small script in this repo, with nothing pulled from
mirrorverse.

| Feed | What to keep | Estimated size for 24h |
|---|---|---|
| TfL JamCams (~150 central cameras, ~5 min refresh) | YOLO people/vehicle counts per snapshot, **no video** | ~45k rows, ~10 MB |
| TfL road disruptions (every 5 min) | Only snapshots that changed (hash and skip repeats) | tens of MB at most |
| Bus arrivals (~50 stops, every 1–2 min) | Predicted arrival times; delays worked out later | ~50–100 MB |
| Optional: still frames near flagged incidents | Demo evidence | under 100 MB |

- Total **well under 1 GB**, in one SQLite file. These are estimates, so check after the first
  hour.
- Run it on a cloud VM, or on a laptop that stays plugged in with `caffeinate -i`, ideally off
  venue wifi.
- Get a **free TfL API key**; anonymous use is rate-limited hard.

## 5. Data sources (all public)
- **TfL Unified API:** JamCams `/Place/Type/JamCam`, disruptions `/Road/all/Disruption`, bus
  arrivals `/StopPoint/{id}/Arrivals`, routes `/Line/{id}/Route/Sequence`, line status,
  station crowding `/crowding/{naptan}`.
- **OpenStreetMap / Overpass:** roads, buildings, ambulance stations, hospitals.
- **Open-Meteo:** weather.
- **Unconventional extras (optional):** aircraft tracking (adsb.lol), ship tracking, Wikipedia
  edit streams.

## 6. Open
- [ ] Who owns: engine, UI/3D, data recorder, pitch?
- [ ] West End only, or a selectable area?
- [ ] Where the recorder runs (VM or laptop)?
