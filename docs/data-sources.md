---
title: Data sources — what exists, what's live, what has history
date: 2026-11-07
method: research agent; every row marked V was fetched on Sat 7 Nov ~11:40 GMT, R = from docs
---

# Data sources

**Verdict: London.** It has the most live, free, keyless open data of any city checked. **The
gap is history:** almost no live London feed is archived (the exceptions are TfL's typical
crowding profiles and EA river readings), **so we record from now on** (see
[build.md](build.md)). NYC and Singapore have more history but less live data, so use them as
a side panel at most.

V = fetched today. R = reported in docs, not fetched.

## 1. What Mirrorverse already uses (all keyless)
| Source | Endpoint | Refresh in code |
|---|---|---|
| TfL bus arrivals | `/StopPoint?lat,lon,…` → `/StopPoint/{id}/Arrivals` | 25 s |
| TfL JamCams | `/Place/Type/JamCam` (889 cams, V); clips on `s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/{id}.mp4`, run through local YOLO (`services/yolo`) | clip ~1 min old, **overwritten in place** |
| TfL road disruptions | `/Road/all/Street/Disruption` (213 segments today, V) | 5 min |
| TfL stations, line status, live crowding | `/StopPoint?stopTypes=…`, `/Line/Mode/{modes}/Status`, `/crowding/{naptan}/Live` | 1–3 min |
| Police street crime | `data.police.uk/api/crimes-street/all-crime` | monthly, ~2 months behind (V) |
| EA tide gauges | `environment.data.gov.uk/flood-monitoring/…` | 15 min |
| Open-Meteo weather / air quality | `api.open-meteo.com`, `air-quality-api.open-meteo.com` | 5–15 min |
| YouTube live cams | `services/vision/cameras/london_live.json` (7 cams, yt-dlp) | live; **embed-only under YouTube's terms** |
| Virtual cams | `apps/stream/src/lib/vcams.json` (4K walking videos, YOLO-tracked offline) | one-off |
| OSM street graph, twin field | `api/twin/osm`, `api/twin/field` | on demand |

**It records nothing.** Every feed is cached and thrown away, so there's no time series yet.

## 2. Mobility and city state
| Source | Endpoint | Measures | Fresh | History | Auth | V/R |
|---|---|---|---|---|---|---|
| **TfL citywide bus snapshot** | `/Mode/bus/Arrivals?count=1` | next arrival at **every stop**: 17,314 stops, 4,442 buses, 537 lines in one call (15.6 MB, 10 s) | ~30 s | none | none | V |
| TfL Tube/DLR/Overground | `/Mode/tube/Arrivals?count=1` | every platform's predictions | ~30 s | none | none | V |
| **TfL BikePoint** | `/BikePoint` | bikes, e-bikes, empty docks at 796 docks | per change | journey CSVs on `cycling.data.tfl.gov.uk` up to May 2026 (~5 months behind) | none | V |
| **TfL crowding** | `/crowding/{naptan}` + `/Live` | live load as % of baseline + **a typical 15-min profile per weekday** | ~5 min | the profiles are a built-in prior | none | V |
| TfL road corridor status | `/Road/all/Status` | severity for ~25 A-road corridors | minutes | none | none | V |

## 3. Alternative data (pointed at a city, nobody does this)
| Source | Endpoint | Measures | Fresh | History | Auth | Fit | V/R |
|---|---|---|---|---|---|---|---|
| **adsb.lol** | `api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}` | every aircraft: position, altitude, squawk | 1 s | **daily global archives**, github `adsblol/globe_history_2026` | none (ODbL licence, attribute) | Heathrow stacks/holding as a city stress signal | V |
| **Elexon BMRS** | `data.elexon.co.uk/bmrs/api/v1/demand/outturn/summary`, `/datasets/FUELINST` | GB demand + generation by fuel | 5 min | **years**, by date range | none | a macro "pulse"; big events move demand within minutes | V |
| Carbon Intensity | `api.carbonintensity.org.uk/regional/regionid/13` | London grid carbon + mix | 30 min | any range | none | slow regime variable | V |
| **Wikipedia pageviews** | hourly dumps `dumps.wikimedia.org/other/pageviews/` (REST is daily only) | attention per place article | hourly, ~3 h behind | years | none | does attention lead footfall? | V |
| Wikipedia EventStreams | `stream.wikimedia.org/v2/stream/recentchange` (SSE) | live edits | seconds | ~7 days replay (R) | none | edit bursts flag incidents | V |
| **GDELT 2.0** | `data.gdeltproject.org/gdeltv2/lastupdate.txt` → 15-min zips | geocoded news events + tone | 15 min | since 2015 | none (the DOC API returns 429, use the files) | news as an observation that reprices worlds | V |
| Open-Meteo history | `?minutely_15=precipitation&past_days=7`, `archive-api.open-meteo.com` | rain at 15-min steps | 15 min | 7–92 days at 15 min; hourly since 1940 | none (~10k/day) | event studies on downpours | V |
| EA flood monitoring | `…/readings?since=` | Thames river/tide levels, ~50 gauges | 15 min | ~28 days in the API | none | add `since=` to backfill | V |
| Sensor.Community | `data.sensor.community/airrohr/v1/filter/area=51.51,-0.12,10` | citizen PM2.5, temperature | ~5 min | daily CSVs (R) | none | dense, noisy local field | V |
| London Air (LAQN) | `api.erg.ic.ac.uk/AirQuality/Hourly/MonitoringIndex/GroupName=London/Json` | official air quality | hourly | yes | none | | V |

**Free key needed (not signed up):** AISstream (Thames ships, websocket), Realtime Trains, Ticketmaster
(events), Windy webcams, Breathe London.
**Skip or unreliable:** OpenSky (no anonymous history), Raspberry Shake seismometers (502 today),
Bluesky Jetstream (websocket only, very few geotags), DfT road counts (annual only).

## 3b. More sources, from God's Eye View's catalogue
[bilawalsidhu/gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view) (MIT, 63k stars, a
live OSINT globe) keeps a vetted list in its `DATA_SOURCES.md`. It confirms our picks (TfL
JamCams, adsb.lol, OpenSky, AISStream, GDELT, Open-Meteo) and adds these. Not fetched by us
yet (R).

| Source | Gives | Auth | Use for us |
|---|---|---|---|
| USGS earthquakes | Global quakes, near real-time | none | backup "shock" events / a different-city demo |
| CelesTrak | Satellite orbits (TLEs, SGP4) | none | optional flourish: satellites overhead |
| adsbdb (`api.adsbdb.com`) | Aircraft type/registration by ICAO hex | none | enrich adsb.lol flights |
| Mapillary · Panoramax · KartaView | Street-level imagery + coverage | Mapillary token; the others keyless | "what this street looks like" in a world card |
| NASA GIBS | Recent Sentinel-2/Landsat/VIIRS imagery tiles | none | the satellite-image hero shot |
| TomTom Traffic flow tiles | Live congestion per road | free key | **a second traffic observation stream** to reprice worlds |
| Google News RSS | Locality-matched headlines | none | backup to GDELT for "news reprices worlds" |
| **CCTV from ~20 other networks** | NYC DOT, Hong Kong TD, Norway (HLS video), WSDOT, Caltrans, DriveBC, Ontario 511, Finland, Tallinn, Catalonia, NSW… | mostly none | **robustness:** if TfL JamCams fail, the engine can run on another city's cameras; also a "works anywhere" slide |

**Robustness lessons from their code and README:**
- Public OpenStreetMap Overpass servers now refuse old clients. Cache our OSM extract for
  Oxford Circus locally; don't fetch it live in the demo.
- Every layer is a separate module that fails soft and **labels stale data** ("answers say when
  a feed is stale or on a fallback"). Mirrorverse #52 did the same (last good value, marked
  stale). Keep that rule on the exchange board.
- Terrain/tiles are paused on 429. Back off on rate limits everywhere; the recorder already does.

## 4. Other cities
| City | Live | History | Verdict |
|---|---|---|---|
| **London** | Every bus and train in one keyless API, 889 cameras, bikes, crowding, road status; plus grid and aircraft | thin; record it | **Pick** |
| New York | MTA GTFS-realtime and Citi Bike keyless (V); DOT speeds and 311 lag by hours | **richest**: 311 since 2010, taxi trips, DOT speeds | best for a historical event study |
| Singapore | open endpoint now only 8 cameras; full set needs an LTA key | **historical camera frames** by `date_time` (V, March 2025 images still downloadable) | unique for vision backtests |
| Helsinki | HSL vehicle positions, keyless, every second | no public archive | clean but small |
| Chicago | Socrata congestion, stale (V) | — | not competitive |

## 5. Gotchas
- **TfL rate limit:** ~50 requests/min anonymous, **shared between the recorder and the app**.
  Get a free key at `api-portal.tfl.gov.uk` (500/min) and keep it in env, never in git.
- The bus snapshot is 15.6 MB and takes 10 s: use a timeout of at least 60 s and store only
  stop, vehicle, line and time to station.
- JamCam clips are overwritten, so only what we save exists.
- GDELT DOC API → 429; use the 15-min files. Wikipedia hourly data is only in the dumps
  (50–100 MB each).
- Attribute adsb.lol (ODbL licence).

## Source URLs
TfL https://api.tfl.gov.uk (keys: https://api-portal.tfl.gov.uk) · cycle CSVs https://cycling.data.tfl.gov.uk/ ·
Elexon https://data.elexon.co.uk/bmrs/api/v1/ · Carbon Intensity https://api.carbonintensity.org.uk ·
adsb.lol https://api.adsb.lol (history https://github.com/adsblol/globe_history_2026) · OpenSky https://opensky-network.org/api ·
Open-Meteo https://api.open-meteo.com, https://archive-api.open-meteo.com · EA https://environment.data.gov.uk/flood-monitoring ·
Police https://data.police.uk/api · London Air https://api.erg.ic.ac.uk/AirQuality · Sensor.Community https://data.sensor.community ·
Wikimedia https://dumps.wikimedia.org/other/pageviews/, https://stream.wikimedia.org/v2/stream/recentchange ·
GDELT http://data.gdeltproject.org/gdeltv2/lastupdate.txt · AISstream https://aisstream.io · Realtime Trains https://api.rtt.io ·
Ticketmaster https://developer.ticketmaster.com · Windy https://api.windy.com/webcams · NYC https://data.cityofnewyork.us ·
MTA https://api-endpoint.mta.info · Citi Bike https://gbfs.citibikenyc.com · Singapore https://api.data.gov.sg/v1/transport/traffic-images ·
LTA https://datamall.lta.gov.sg · Helsinki https://realtime.hsl.fi/realtime/vehicle-positions/v2/hsl
