# Recorder

Live London feeds have no history, so we record them from now on. Python 3.10+ stdlib only.
The new sources are for the AI brokers: each broker reads one alternative data stream.

## Run

```sh
python3 recorder/record.py --once        # poll every source once, print counts, exit (~60 s: wiki_edits listens for 60 s)
caffeinate -i python3 recorder/record.py # long-running; caffeinate keeps a Mac awake
python3 recorder/record.py --yolo        # also add JamCam people/vehicle counts
python3 recorder/record.py --only aircraft,grid,rain_now,news,wiki_views,wiki_edits  # just these
python3 recorder/record.py --skip bus    # everything except these
python3 recorder/scrape/run.py [--once]  # scrapers (London football fixtures), refreshed every 30 min
python3 -m unittest discover -s recorder # offline parser tests (fixtures in recorder/tests/fixtures/)
python3 recorder/backfill.py             # one-shot history pull (Open-Meteo, EA, Elexon, Carbon)
python3 recorder/load.py [source]        # records per source + time span
```

Long-term, detached, survives the terminal closing:

```sh
nohup caffeinate -i python3 recorder/record.py >> data/recorder.out 2>&1 &
```

Supervised (what the live demo runs): `scripts/run-data.sh` keeps the recorder, the YOLO server and
the health server (`GET :8052/health`, `services/health/`) up. It restarts a process that dies, restarts the
recorder when jamcams/tube/line_status have had no data for 3 min, and restarts YOLO when health has
called it dead for 3 min. It adopts processes that are already running and logs to `data/logs/`.

```sh
nohup scripts/run-data.sh >/dev/null 2>&1 &
python3 services/health/health.py        # per-feed age, status, YOLO freshness, TfL key ([--json])
```

A status line goes to stdout every minute (`source=<writes>w/<rows>r[/<errors>err]`). Errors are one
line each in `data/recorder.log`. A failed poll is logged and retried on the next tick. The loop never exits on its own.

### Env
| Name | Purpose |
|---|---|
| `TFL_APP_KEY` | TfL key (free at api-portal.tfl.gov.uk). Raises the cap from 40 to 300 calls/min. Read from the environment, or else from `--env-file`, the repo-root `.env`, or (in a git worktree) the main checkout's `.env`. Never logged. |
| `JAMCAM_YOLO_URL` | YOLO server for `--yolo` (default `http://127.0.0.1:8047`, Mirrorverse `services/yolo/server.py`). If it is unreachable, the recorder logs it, writes JamCam rows without counts and retries with backoff (30 s doubling to 120 s), so a restarted server is picked up again. |

`.env` and `data/` are gitignored.

## What is polled
| Source | Endpoint | Every | Kept |
|---|---|---|---|
| `bus` | `/Mode/bus/Arrivals?count=1` (~17k stops, 90 s timeout) | 60 s | naptanId, vehicleId, lineName, timeToStation, expectedArrival |
| `tube` | `/Mode/tube/Arrivals?count=1` | 60 s | naptanId, stationName, vehicleId, lineId, platformName, direction, destinationNaptanId, timeToStation, expectedArrival, currentLocation |
| `line_status` | `/Line/Mode/tube,dlr,overground,elizabeth-line/Status` | 60 s | id, name, mode, statuses[severity, description, reason] |
| `road_status` | `/Road/all/Status` | 60 s | id, displayName, severity, description, bounds |
| `disruptions` | `/Road/all/Street/Disruption?startDate=today&endDate=tomorrow` | 5 min, **written only when the content changes** | full segments, `$type` stripped |
| `bikepoint` | `/BikePoint` | 2 min | id, lat, lon, NbBikes, NbEBikes, NbEmptyDocks, modified |
| `crowding` | `/crowding/{naptan}/Live` for 20 central stations | 30 s (40 TfL calls/min; TfL publishes each station every 5 min, a few minutes after its stamp, so a fast poll keeps the exchange as fresh as TfL. Needs `TFL_APP_KEY`: the keyless cap is 40/min) | naptan, name, dataAvailable, percentageOfBaseline, timeUtc |
| `crowding_profile` | `/crowding/{naptan}` (typical 15-min profile per weekday) | once per UTC day | one record per station |
| `jamcams` | `/Place/Type/JamCam` (list cached 1 h), filtered to lat 51.495–51.530, lon -0.175 to -0.100 (~85 cams); HEAD on each S3 clip | 90 s | id, name, lat, lon, view, imageUrl, videoUrl, lastModified, etag, contentLength, `yolo` {people, vehicles, at} with `--yolo` |

### Alternative data (no keys)
| Source | Endpoint | Every | Kept |
|---|---|---|---|
| `aircraft` | adsb.lol `/v2/lat/51.47/lon/-0.45/dist/40` (40 nm round Heathrow, ~130 aircraft) | 60 s | `aircraft[]`: hex, flight, lat, lon, alt_baro, gs, track, squawk. `summary`: count, airborne, ground, below_8000ft, `holding` per stack, holding_total |
| `grid` | Elexon `/datasets/FUELINST` (last 30 min, newest 5-min period kept) + `/demand/outturn/summary` (newest row); Carbon Intensity `/regional/regionid/13` | 5 min (carbon every 30 min) | `fuelinst` {startTime, fuel{type: MW}, total_mw}, `demand` {startTime, demand MW}, `carbon_london` {forecast, index, mix} when refreshed |
| `rain_now` | Open-Meteo forecast at 51.515, -0.14: `current` + `minutely_15` (1 h back, 2 h ahead) | 5 min | current {precipitation, rain, temperature_2m, weather_code, cloud_cover, wind_speed_10m}, minutely_15[] {time, precipitation, rain} |
| `news` | GDELT 2.0 `lastupdate.txt` → the 15-min `.export.CSV.zip` | checks every 5 min, downloads each new file once | one record per file: {file, scanned, events[]} with GLOBALEVENTID, SQLDATE, EventCode, NumMentions, AvgTone, ActionGeo name/country/lat/lon, SOURCEURL; events whose ActionGeo point is within 5 km of Oxford Circus |
| `wiki_views` | Wikimedia pageviews REST, daily, user agent, last 14 days | startup + every 6 h | [{title, views{date: n}}] for the 40-article watchlist in `alt.py` |
| `wiki_edits` | Wikimedia EventStreams `recentchange` (SSE, one long-lived connection) | live | one record per enwiki edit/new page on the watchlist: title, type, dt, timestamp, minor, bot, comment, len_old/new, rev_old/new (no user names) |

Heathrow holding stacks are approximated as 6 nm circles round each stack's VOR, counting aircraft at
or above 7000 ft: BNN Bovingdon 51.7261, -0.5500 · LAM Lambourne 51.6461, 0.1517 · BIG Biggin Hill
51.3311, 0.0347 · OCK Ockham 51.3050, -0.4472.

Every non-TfL host has its own per-minute cap (`HOST_CAPS` in `record.py`). A 429/503 backs off with
jitter. GDELT sometimes announces a file before the CDN has it and then serves a cached 404 for up to
an hour, so a missing file stays queued and is retried for 2 h. On the first tick the recorder also
fetches the previous 15-min file, so there is data straight away. `wiki_edits` reconnects with
`?since=<last event time>` after a drop. The status line shows `wiki_stream_scanned=N` (events read
off the stream). Watchlist edits are rare: a 2-minute run read 3,593 events and matched 0.

### Scrapers (`recorder/scrape/`)
| Source | From | Every | Kept |
|---|---|---|---|
| `scrape_fixtures` | TheSportsDB public API (free key `123`), `eventsnext` + `eventslast` for 8 London clubs | 30 min (each URL cached 10 min in `data/scrape_cache/`, 5 s between calls) | one record per run: London **home** fixtures kicking off today or tomorrow (UTC): league, home, away, kickoff_utc, egress_est_utc (kick-off + 115 min), venue, lat, lon, stations fans use, status, score |

Football kick-off and egress surges hit specific stations and the central interchanges. Errors go to `data/scrape.log`.

Station NaPTAN ids are looked up once via `/StopPoint/Search` (hub ids are resolved to their
`940GZZLU*` child) and cached in `data/naptan_cache.json`. Delete it to re-resolve. That is ~9 TfL calls/min in steady state.
All TfL calls share one sliding-window limiter. A 429 backs off with jitter, honouring `Retry-After`.

## Layout
```
data/raw/<source>/YYYY-MM-DD/HH.ndjson.gz   # UTC fetch hour
data/backfill/<source>.ndjson.gz            # open_meteo, ea_thames, elexon_demand, carbon_london
data/raw/scrape_<name>/YYYY-MM-DD/HH.ndjson.gz
data/naptan_cache.json  data/recorder.log  data/scrape.log  data/scrape_cache/
```
Each raw line is `{"ts": "<ISO UTC fetch time>", "source": "...", "payload": <trimmed JSON>}`. Every
write appends its own gzip member, so plain `gzip`/`zcat` reads the file, and a kill mid-write costs
at most the last line. Bus is the big one: ~220 KB gzipped per snapshot, ~320 MB/day.

Backfill rows are flat (one reading per line) and the file is overwritten each run:
- `open_meteo`: 15-min precipitation + temperature at 51.515, -0.14, the past 7 days up to now
- `ea_thames`: Westminster, Tower Pier and Chelsea gauge readings for the last 7 days
- `elexon_demand`: `/demand/outturn` for 14 days in 7-day chunks (half-hourly INDO/ITSDO). The
  `/summary` endpoint caps ranges at 7 days.
- `carbon_london`: Carbon Intensity region 13 (London), half-hourly, 14 days

## Load
```python
from recorder.load import records   # run from the repo root

for rec in records("bus", "2026-11-07T12:00:00Z", "2026-11-07T13:00:00Z"):
    print(rec["ts"], len(rec["payload"]))
```

## adsb.lol history (not downloaded)
There is one global archive per day, about 3.8 GB, split into 2 GB parts. Releases are at
https://github.com/adsblol/globe_history_2026/releases, tagged `vYYYY.MM.DD-planes-readsb-prod-0`. To fetch one day:

```sh
mkdir -p data/adsb && cd data/adsb
gh release download v2026.11.06-planes-readsb-prod-0 -R adsblol/globe_history_2026
cat v2026.11.06-planes-readsb-prod-0.tar.* | tar -x     # readsb per-aircraft trace JSON
```
Filter to a box around Heathrow/London after extracting. Licence: ODbL, so attribute adsb.lol.
