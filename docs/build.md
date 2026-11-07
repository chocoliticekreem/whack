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

## 4. Data sources (researched and fetched Sat 7 Nov ~11:40)
**Verdict: stay in London.** It has the most live, free, keyless data of any city checked. The one
gap is history: almost nothing live is archived, **so we must record from now on.** NYC and
Singapore have more history but less live data. A history slice from one of them can be a side
panel, not a reason to switch cities.

**Mirrorverse already uses** (all keyless): the TfL API (bus arrivals, 889 JamCams + YOLO counts,
road disruptions, stations, line status, live crowding), police crime (monthly, about 2 months
behind), EA tide gauges, Open-Meteo weather and air quality, 7 YouTube live cams, and an OSM
street graph. **It records nothing**, so there is no time series to backtest on.

### Mobility and city state (the observation stream)
| Source | Endpoint | What / how fresh | History | Auth |
|---|---|---|---|---|
| **TfL citywide bus snapshot** | `/Mode/bus/Arrivals?count=1` | 17,314 stops, 4,442 buses **in one call** (15.6 MB, 10 s), about 30 s fresh | none, record it | none |
| TfL Tube/DLR/Overground arrivals | `/Mode/tube/Arrivals?count=1` | every platform, about 30 s | none | none |
| **TfL BikePoint (Santander)** | `/BikePoint` | 796 docks, bikes/e-bikes/empty | journey CSVs (about 5 months behind) | none |
| **TfL crowding** | `/crowding/{naptan}` + `/Live` | live load vs **a typical 15-min profile per weekday** (a built-in prior) | the profiles are the prior | none |
| TfL road corridor status | `/Road/all/Status` | ~25 A-road corridors | none | none |
| JamCam clips | `s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/{id}.mp4` | about 1 min fresh, **overwritten in place** | none | none |

### Alternative data (the "satellite images" angle: data nobody points at a city)
| Source | Endpoint | What / how fresh | History | Auth |
|---|---|---|---|---|
| **adsb.lol aircraft** | `api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}` | every aircraft, 1 s (Heathrow stacks/holding as a city stress signal) | **daily global archives** (github `adsblol/globe_history_2026`) | none (ODbL licence, attribute) |
| **Elexon BMRS grid** | `data.elexon.co.uk/bmrs/api/v1/...` | GB demand and generation by fuel, 5 min | **years**, by date range | none |
| Carbon Intensity (London) | `api.carbonintensity.org.uk/regional/regionid/13` | 30 min | any range | none |
| **Wikipedia pageviews** | hourly dumps `dumps.wikimedia.org/other/pageviews/` (REST is daily only) | attention per place, hourly, about 3 h behind | years | none |
| Wikipedia live edits | `stream.wikimedia.org/v2/stream/recentchange` | seconds (edit bursts flag incidents) | about 7 days, replayable | none |
| **GDELT news** | `data.gdeltproject.org/gdeltv2/lastupdate.txt` (15-min files) | geocoded news events + tone | since 2015 | none (the DOC API returns 429, use the files) |
| Open-Meteo history | `?minutely_15=precipitation&past_days=7`, archive API | 15-min rain | 7–92 days at 15 min; hourly since 1940 | none |
| EA river/tide gauges | `.../readings?since=` | 15 min | ~28 days in the API | none |
| Sensor.Community | `data.sensor.community/airrohr/v1/filter/area=...` | citizen PM2.5/temperature, ~5 min | daily CSVs | none |
| Needs a free key | AISstream (Thames ships), Realtime Trains, Ticketmaster, Windy webcams, Breathe London | | | free signup |
| Unreliable / skip | OpenSky (no anonymous history), Raspberry Shake (502), Bluesky (few geotags), DfT counts (annual) | | | |

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
