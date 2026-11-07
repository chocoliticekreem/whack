# Recorder

Live London feeds have no history, so we record them from now on. Python 3.10+ stdlib only.

## Run

```sh
python3 recorder/record.py --once        # poll every source once, print counts, exit
caffeinate -i python3 recorder/record.py # long-running; caffeinate keeps a Mac awake
python3 recorder/record.py --yolo        # also add JamCam people/vehicle counts
python3 recorder/backfill.py             # one-shot history pull (Open-Meteo, EA, Elexon, Carbon)
python3 recorder/load.py [source]        # records per source + time span
```

Long-term, detached, survives the terminal closing:

```sh
nohup caffeinate -i python3 recorder/record.py >> data/recorder.out 2>&1 &
```

A status line goes to stdout every minute (`source=<writes>w/<rows>r[/<errors>err]`). Errors are one
line each in `data/recorder.log`. A failed poll is logged and retried on the next tick. The loop never exits on its own.

### Env
| Name | Purpose |
|---|---|
| `TFL_APP_KEY` | TfL key (free at api-portal.tfl.gov.uk). Raises the cap from 40 to 300 calls/min. Read from the environment, or else from `--env-file`, the repo-root `.env`, or (in a git worktree) the main checkout's `.env`. Never logged. |
| `JAMCAM_YOLO_URL` | YOLO server for `--yolo` (default `http://127.0.0.1:8047`, Mirrorverse `services/yolo/server.py`). If it is unreachable, the recorder prints one warning and continues without counts. |

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
| `crowding` | `/crowding/{naptan}/Live` for 20 central stations | 5 min | naptan, name, dataAvailable, percentageOfBaseline, timeUtc |
| `crowding_profile` | `/crowding/{naptan}` (typical 15-min profile per weekday) | once per UTC day | one record per station |
| `jamcams` | `/Place/Type/JamCam` (list cached 1 h), filtered to lat 51.495–51.530, lon -0.175 to -0.100 (~85 cams); HEAD on each S3 clip | 90 s | id, name, lat, lon, view, imageUrl, videoUrl, lastModified, etag, contentLength, `yolo` {people, vehicles, at} with `--yolo` |

Station NaPTAN ids are looked up once via `/StopPoint/Search` (hub ids are resolved to their
`940GZZLU*` child) and cached in `data/naptan_cache.json`. Delete it to re-resolve. That is ~9 TfL calls/min in steady state.
All TfL calls share one sliding-window limiter. A 429 backs off with jitter, honouring `Retry-After`.

## Layout
```
data/raw/<source>/YYYY-MM-DD/HH.ndjson.gz   # UTC fetch hour
data/backfill/<source>.ndjson.gz            # open_meteo, ea_thames, elexon_demand, carbon_london
data/naptan_cache.json  data/recorder.log
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
