# Multiverse: reality prunes the parallel Londons

N worlds, each one run of the reused Predict engine (`services/predict`, rules mode, no LLM, no keys)
under its own assumptions. Every new observation reprices them; the ones reality disproves get delisted
and the survivors spawn jittered children so N stay live.

| Piece | Where |
|---|---|
| Assumptions per world (moving share, tourist mix, pavement capacity, riders per bus, bus delay, walking pace, rain onset, random incident) | `worlds.py` `sample_assumptions` / `jitter_assumptions` |
| Common random numbers: every world draws personas, departures and destinations from one `crnSeed`; assumptions come from a separate per-world stream | `worlds.run_world` |
| Likelihood: camera counts compared on the sqrt scale (Poisson sd 0.5 + model sd 0.4 on real data, 1.0 on the fixture); bus delay Gaussian (sd 2 min); rain, tube crowding and bikes out (real data, below) | `engine.log_likelihood_*`, `engine.feed_parts` |
| Price = normalised posterior; delist below `0.1 / N`; systematic resampling refills to N; a parent splits its price with its children | `engine.Multiverse.observe` |
| Explanation trail: per-observation log-likelihood and the worst camera; delist reason in words | `world.trail`, `world.delistReason` |
| Place an event: every live world forks a child with the Predict intervention (`closure`/`incident`/`event`/`weather`), prior 50 % | `engine.Multiverse.intervene` |
| Data-source brokers (TOPS clone, on by default): 10 brokers, each reading ONE feed (table below) and seeing only that feed's latest summary plus the clock, submit an idea (views only on the knobs their feed informs; null = no view) + conviction + rationale. A broker world is a world drawn from the market nudged toward the idea on the viewed knobs only. P&L = capped log((posterior mass + 0.01) / (prior mass + 0.01)); track record = decayed sum (0.8, one-minute frames); weight = 0.8 x softmax(track / 1.5) + 0.2 uniform; slots: 25 % market maker, the rest by weight with a floor of 2 per broker | `brokers.py`, `engine.Multiverse.rebalance` |
| With brokers on, prices stay pure likelihood: a newborn world is priced on everything seen this epoch (flat prior), so brokers only decide where worlds spawn. The market maker is a plain particle filter (kernel = 0.3 x the market's spread per knob, one-knob recombination with the previous frame). Refills spawn in rounds of 5 so a fresh discovery gets neighbours in the same frame; at most 60 % of worlds go per frame, 20 % of that reserved to recycle the cheapest worlds into neighbours of any world above 10 % of the market. Placed events belong to the market maker and survive their first observation | `engine.Multiverse.rebalance`, `spawn_round`, `history_loglik` |
| Five-minute markets (default `horizonMin` 5), one observation a minute. Past the horizon the market rolls: a new epoch is seeded from the last observed frame's counts, timed assumptions move earlier by the elapsed minutes (rain that was due at +3 is now raining from 0), and every live world re-runs (prices and lineage kept) | `engine.reseed`, `Multiverse.advance`, `Multiverse.roll`, `worlds.shift_assumptions` |
| **London VIX**: per zone, the price-weighted coefficient of variation of predicted people at the horizon, averaged over zones weighted by predicted volume, x100 (= sum of sds / sum of means). 0 when every world agrees. A history point per spawn, frame, roll and placed event, flagged `spike` on a jump of >= 2 points and >= 20 %. The driver is the source that moved it most: each camera and the bus feed alone (reweight the pre-frame market by that source's likelihood), each broker's (and the market maker's) newborn worlds (VIX with vs without them), or "all cameras" / "new worlds" when the group moves it over 2x any single source | `engine.london_vix`, `Multiverse.mark_vix`, `pick_driver` |

## Brokers: which data has alpha

Each broker reads one stream and may only move the knobs that stream can plausibly inform; any other view
is dropped (`brokers.FEED_KNOBS`, `own_knobs_only`). Live summaries come from the recorder's newest record
per source (`feeds.feeds_at`, read-only, `MULTIVERSE_ALT_RAW` or `data/raw` or the main checkout's
`data/raw`); in replay from the same sources in the replay root. Haiku gets, per broker, only that broker's
feed; without a key the rules heuristic below reads the same feed.

| Broker | Feed (recorder source) | Knob | Why | Rules heuristic |
|---|---|---|---|---|
| B-AIR Heathrow plane spotter | aviation (`aircraft`: holding stacks, below 8000 ft) | touristFactor | arrivals stacking up = visitors on the way | 1 + 0.04 x (holding - 8) |
| B-RAIN Radar watcher | weather (`rain_now`: precip now, 15-min steps ahead) | rainAtMin | the 15-min forecast leads the onset | raining now -> 0; first wet step ahead -> its minute; else dry |
| B-GRID Grid engineer | grid (`grid`: demand MW, carbon) | movingShare | demand tracks how active the city is | 0.6 + (MW - 30 000) / 20 000 |
| B-NEWS Newsroom stringer | news (`news`: GDELT events within 5 km, tone) | capacityScale | tense news near the area = cordons, crowd control | >= 3 events and tone < -2 -> 0.8, else 1.05 |
| B-BIKES Bike dock watcher | bikes (`bikepoint`: docks within 800 m emptying) | movingShare | bikes leaving = people setting off | bikes taken per min / 20 |
| B-TUBE Tube supervisor | tube (`crowding` vs `crowding_profile`, Oxford Circus) | touristFactor | crowding over the typical day = extra visitors | live / typical |
| B-WIKI Wikipedia editor | attention (`wiki_views`, `wiki_edits`) | touristFactor | attention on West End articles | latest day / 14-day mean |
| B-BUS Bus driver on the 7 | bus (`bus_delay`) | busDelayMin | it is the measurement | mean of reads |
| B-CAMS CCTV operator | cameras (JamCam counts vs seed) | capacityScale | crowding on the pavement | 1.25 - 0.35 x crowd/seed |
| B-CONTRA Contrarian | cameras, inverted | movingShare, touristFactor | the dissenting view | bets against what the cameras showed |

The fixture writes these feeds in the recorder's payload shapes from the hidden truth: `rain_now`
(forecast shows the rain ~3 min ahead), `bikepoint` (emptying rate = movingShare) and `crowding`
(= touristFactor) are informative; `aircraft`, `grid`, `news` and `wiki_views` are pure noise.

## Run

```bash
python3 services/multiverse/server.py            # :8049; real Oxford Circus replay by default (prints REAL or SYNTHETIC)
python3 services/multiverse/demo.py 3 50         # CLI: spawn 50 -> 3 observations -> board + VIX
python3 services/multiverse/demo.py 3 50 --brokers  # same, with the broker leaderboard each frame (Haiku if a key is found)
ANTHROPIC_API_KEY= python3 services/multiverse/demo.py 8 50 --brokers  # rules brokers, no network
python3 services/multiverse/eval.py 10 50           # quality bar: brokers off vs on over 10 seeds (offline)
python3 -m unittest discover -s services/multiverse
python3 services/multiverse/fixture.py           # regenerate the synthetic fixture
```

UI: no web page calls the Oxford Circus engine; the city service (`services/city`) polls its `GET /vix` for the
London VIX. The central model (`MULTIVERSE_SEED=seeds/central-london.json`, :8449) backs `/multiverse`'s "What if…"
through `apps/stream`'s `/api/sim/*` (`GET /state`, `GET /forecast`, `POST /scenario` only).

**Seed rebuilds need no web app.** The twin routes `real_seed.py` (online) and the live fallback below call
(`/api/twin/field`, `/api/around/{weather,buses,disruptions,counts}`) were removed from `apps/stream` with the three.js
twin. Rebuild from the committed caches instead: `real_seed.py --offline` reads `seeds/oxford-circus-field.json`, and
`central_seed.py --offline` reads `seeds/central-osm.json.gz`. A fresh street field or new OSM tiles need a
Mirrorverse `apps/stream` checkout running at `TWIN_URL`.

Env (names only): `MULTIVERSE_PORT` (8049), `MULTIVERSE_CORS_ORIGIN`, `TWIN_URL` (a Mirrorverse twin, for online
`real_seed.py` and live mode on a non-real seed; default `http://localhost:3047`), `MULTIVERSE_SEED` (a seed JSON,
absolute or relative to this folder; overrides the default seed),
`MULTIVERSE_RAW` (recorder root; default `data/raw`, else the main checkout's `data/raw`), `MULTIVERSE_CAM_SOURCES`,
`MULTIVERSE_BUS_SOURCES` (comma lists of recorder source names), `MULTIVERSE_ALT_RAW` (recorder root for the
brokers' live feeds), `ANTHROPIC_API_KEY` (broker ideas
from Claude Haiku 4.5, two concurrent calls of 5 brokers per observation; read from the env or the repo / main-checkout
`.env`; without it the rules brokers answer and nothing touches the network).

## Real London (Oxford Circus)

**Real is the default.** With no env, the server uses `seeds/oxford-circus.json` on the recorder's data: the
first of `MULTIVERSE_RAW`, the repo's `data/raw`, the main checkout's `data/raw` that holds any recorded
`jamcams` (`feeds.real_root`). Only when none does it falls back to the synthetic fixture. It prints which at
startup (`REAL data: …` or `SYNTHETIC fixture …`). `MULTIVERSE_SEED` still picks another seed.

```bash
python3 services/multiverse/real_seed.py              # rebuild seeds/oxford-circus.json (needs a Mirrorverse twin at TWIN_URL, default :3060)
python3 services/multiverse/real_seed.py --offline    # rebuild from the cached seeds/oxford-circus-field.json
# real replay (the demo's safe mode): recorded frames, default = the 45 min with the most YOLO counts
MULTIVERSE_PORT=8549 python3 services/multiverse/server.py
curl -XPOST localhost:8549/spawn -d '{"n":50,"mode":"replay"}'                    # or add "from"/"to" (ms)
curl -XPOST localhost:8549/observe -d '{"next":true}'                             # one recorded minute
# live: same server; reseeds at the newest recorder snapshot, then reads the newest one every 60 s by itself
curl -XPOST localhost:8549/spawn -d '{"n":50,"mode":"live"}'                      # then GET /state; POST /observe {} = poll now
```

- **Seed** (`real_seed.py`): roads (major + minor, footpaths dropped) from the twin's `/api/twin/field` within
  1150 m of Oxford Circus, largest connected piece, cul-de-sacs pruned, 22 m pieces merged into street edges and
  straightened (Predict's cost grows fast with nodes: 94 s a world on the raw 11k pieces, ~0.1-2 s on the 407 edges).
  Local x/z use the twin's snapped origin (250 m grid), so the seed lines up with the twin's map. Cameras are the
  recorder's `jamcams` rows inside the street box, ids exactly as recorded; a camera with a recorded YOLO count pins
  its street to it. 16 zones on curated real places (Oxford Circus, Regent St, Carnaby, John Lewis, Bond St,
  Selfridges, Marble Arch, Oxford St east toward Tottenham Ct Rd, Soho, Piccadilly Circus, Mayfair, St James's,
  Green Park, Park Lane, Fitzrovia, Marylebone). The six Oxford Circus bus stops keep their NaPTAN ids.
- **Cameras** (`feeds.jamcam_readings` / `fresh_people`): people from each row's nested `yolo`, rows without a
  count skipped, only the seed's cameras, and only counts at most 10 min old (`READING_MAX_AGE_MIN`; a YOLO count
  describes one TfL clip, refreshed every 3-5 min).
- **Bus delay** (`feeds.bus_delay_min`): the arrivals feed has no timetable, so lateness is a proxy: each
  (vehicle, stop) prediction at the seed's stops is followed across the last 15 min of snapshots, its slip is
  latest ETA - first ETA, and the reading is the median slip (floored at 0). Mostly 0-1 min on a normal afternoon.
- **Frames**: one per recorded `jamcams` snapshot (60 s cadence); the brokers' feeds come from the same recorder root.
- **Observation fields** (every observation in `/observe` and `/state`, also `/replay` frames and push frames;
  `null` when the recorder has nothing recent, and then no likelihood term):

  | Key | Source | Meaning | Scores | Term (cap) |
  |---|---|---|---|---|
  | `busDelayMin` | `bus` | lateness proxy above | `busDelayMin` | Gaussian, sd 2 min |
  | `rainMmH` | `rain_now` (≤ 30 min old) | Open-Meteo `current.precipitation` over its 15-min interval, mm/h | `rainAtMin` | raining (≥ 0.1 mm/h) vs "rain by now" (`rainAtMin` ≤ minute): agree 0, disagree -3.0 |
  | `crowding` | `crowding` (≤ 30 min old) | Oxford Circus station live busyness, TfL `percentageOfBaseline` 0..1 | `touristFactor` | log(tourists / (crowding / `crowdingTypical`)), sd 0.3, floor -4.5 |
  | `crowdingTypical` | `crowding_profile` | typical busyness for this weekday and 15-min band | (with `crowding`) | |
  | `bikesOut` | `bikepoint` | share of bikes at docks within 800 m gone since the window start (replay: first frame; live: the spawn), negative = filling | `movingShare` | implied 0.6 + 3 x bikesOut, sd 0.2, floor -4.5 |

  Delist reasons name the feed ("said rain by now at +2 min; radar read 0.0 mm/h"); the VIX driver can be
  "rain radar", "tube crowding" or "bike docks".
- **Likelihood**: a camera costs a world at most `OUTLIER_LL` (-4.5): small, noisy YOLO counts and walking groups
  crossing short segments otherwise delist good worlds on one reading.
- **Anchored camera forecasts** (real seeds only): a world's camera forecast is the camera's last real count
  (the seed's count, a newer count too old to score from the frame's `lastCounts`, or the last observed reading),
  moved by how much more or less this world's count changes than the market's price-weighted average world, at
  most 0.1 per minute on the sqrt scale (`engine.anchored`). Raw world counts spike on short segments
  (9.8 -> 58 a minute apart) and their shared drift has no skill against persistence. A camera with no count
  yet starts from the mean of the counted ones. A new world's bus delay is pulled 80 % of the way to the latest
  reading; before any reading a real seed's 0-8 min prior is scaled to 0-0.8 min. `w.run.rawCams` keeps the raw series.
- **Forecast check**: `eval_real.py --raw data/raw --windows 6` scores the market against persistence on today's
  recorded counts (people MAE, 80 % coverage, bus MAE).
- Recorder root: `MULTIVERSE_RAW`, else `data/raw`, else (in a worktree) the main checkout's `data/raw`. Read-only.

## Central London (any bounding box)

One simulation from King's Cross down to Waterloo and from Marble Arch east to Bank, built by one general seed
builder; no place has its own code path. Oxford Circus is just a smaller bbox.

```bash
python3 services/multiverse/central_seed.py                 # seeds/central-london.json (CENTRAL_BBOX), caches streets
python3 services/multiverse/central_seed.py --offline       # rebuild from seeds/central-osm.json.gz (no twin cache needed)
python3 services/multiverse/central_seed.py --bbox 51.509,-0.152,51.521,-0.131 --out seeds/oxford-bbox.json
MULTIVERSE_PORT=8449 MULTIVERSE_SEED=seeds/central-london.json python3 services/multiverse/server.py
curl -XPOST localhost:8449/spawn -d '{"n":50,"mode":"replay"}'     # or "live"; brokers stay off
python3 services/multiverse/eval_real.py --central --windows 7 --minutes 60 --events-ab
```

| Piece | Rule | Where |
|---|---|---|
| Streets | OSM major + minor roads from the twin's cached OpenFreeMap tiles (`apps/stream/.cache`, or `--tiles DIR`), cut to the bbox; vertices within 2 m share a node; largest connected piece; cul-de-sacs pruned; chains through degree-2 nodes merged (up to 300 m); junction clusters joined by edges under 15 m collapsed (dual carriageways). Central London: 6 062 OSM pieces -> 10 563 vertex pairs -> **1 017 edges on 668 junctions**, 121 km | `central_seed.street_graph` |
| Stations | every station in the recorder's TfL Crowding list placed by name on the OSM stations (else by NaPTAN from `services/city/hotspots.json`) and inside the bbox: live busyness + the typical-day profile | `central_seed.place_stations` |
| Zones and hubs | one zone per station (each street to its nearest station); the hubs of `hotspots.json` inside the bbox group zones into catchments (the partition flows are tallied across; edges touching two catchments are `boundary`) | `assign_zones`, `assign_hubs` |
| Cameras | the recorder's `jamcams` rows inside the bbox, pinned to the nearest street within 60 m, with their newest YOLO count of the last 2 h | `pin_cams` |
| Destinations | stations + POIs pooled on a 150 m grid (attractions x3), top 120 cells | `poi_anchors` |
| Buses | the newest `bus` snapshot at stops with a known position (a cached twin stop list, else a hub's listed stop at the hub) | `bus_stops`, `bus_arrivals` |
| Demand | busyness = typical day (weekday, quarter-hour) x the live/typical ratio at the seed x (1 + `demandTrend` x t/60) x interventions; exits per minute = 120 x `flowScale` x busyness (as many entries); 6 gravity destinations (weight x exp(-d / 600 m)) per station and 5-min slot; bus arrivals put `alightPerBus` on the street; lingering groups walk on (up to 2 hops). The swarm skeleton comes from `crnSeed` alone (common random numbers) | `central.Run` |
| Walk | Dijkstra trees toward each destination, cached per closed-edge set; a group is a stream as long as its departure slot, slowed by pavement load; 20 min warm-up, then 65 min (market horizon + 60) | `central.Run.go` |
| Worlds in parallel | `run_many` ships the graph to a spawn-context process pool once and each world's small dynamic part per run; the engine batches spawn, refills, rolls and re-runs through it | `central.run_many`, `engine.Multiverse.run_batch` |
| Station likelihood | each new TfL reading (repeats skipped) vs the world's forecast anchored on the last reading, log scale, sd `STATION_SD` 0.05, floored at `OUTLIER_LL` per station; cameras, bus delay, rain unchanged | `engine.log_likelihood_station`, `anchored_station` |

Knobs per world (`central.RANGES`): flowScale 0.4-2.5, demandTrend -0.4..0.4 /h, touristFactor, capacityScale,
alightPerBus, busDelayMin, walkPace, lingerScale, plus rain onset and a random incident. Particle filter, delist cap,
systematic resampling, per-term caps and common random numbers are the engine's, unchanged.

**Performance** (this Mac, load average 6-8 while other jobs ran): 50 worlds spawn in 1.2-1.8 s (one world takes
about 35-45 ms on 1 017 edges and about 2 800 groups); a one-minute step takes 0.05-0.3 s, and 1.3 s when the
5-minute market rolls and re-runs every world. Over HTTP, with the /state forecasts, a step takes 0.7-1.4 s. A replay
spawn takes 10-13 s, almost all of it reading the recorder and replaying the event layer.

**Accuracy** (`eval_real.py --central --windows 7 --minutes 60 --events-ab`, 2026-11-07 12:36-19:36 UTC, +5 min):

| | station busyness MAE (pp of usual) | in 80 % | camera MAE (people) | in 80 % |
|---|---|---|---|---|
| market, events on | 4.14 | 84 % | 1.83 | 88 % |
| market, events off | 3.94 | 84 % | | |
| persistence | 3.61 | 86 % | 1.77 | 80 % |
| typical day / camera's mean so far today | 25.09 | 33 % | 1.72 | 83 % |

919 station readings, 297 camera counts. The market beats the typical day by a wide margin on stations. It does not
beat persistence at +5 min on either target, and applying the derived events makes stations slightly worse (4.14 vs 3.94).

### Intervention API

`POST /intervene` (central seeds):

```json
{"kind": "closure|capacity|demand_surge|demand_drop|weather",
 "where": {"segments": ["s12", "s13"]} | {"lat": 51.503, "lon": -0.119, "radiusM": 250} | {"naptan": "940GZZLUWLO"},
 "start": 0, "end": 60, "magnitude": 0.5, "shape": "ramp_up|linear_decay", "note": "...",
 "mode": "all|fork", "weight": 0.3}
```

- `start` / `end`: minutes from now or epoch ms; `end` omitted = open-ended. `radius_m` is accepted for `radiusM`.
- `magnitude`: closure / capacity = remaining capacity (closure 0; at a NaPTAN it scales the station's busyness, on
  streets the pavement capacity); demand_surge / demand_drop = relative change (+/-); weather = rain mm/h (2 mm/h
  sends every tourist to shelter). A user's station closure moves its demand to the nearest open station.
- `where` lat/lon: streets within the radius; stations feel a surge or drop at full strength inside the radius and
  exp(-(d - r) / 400 m) outside it (up to 1.2 km); a surge aimed at a place (not a station) walks to that place.
- `mode: "all"` (default): every world, now and future, carries it; worlds re-run, prices kept. `mode: "fork"`: each
  live world forks a what-if child carrying it, priced at `weight` (default 0.5) of its parent.
- Reply: `{intervention: {id, kind, where, startMs, endMs, magnitude, note, segments, stations}, mode, applied|forked, ...state}`.
- The old `{kind: closure|incident|event|weather, zoneId, atMin, prior}` still works (mapped to closure / capacity 0.4 /
  demand_surge 0.5 / weather around the zone's station, forked).
- In code: `central.normalise_intervention(raw, seed, now_ms)` then `Multiverse.apply(iv, "all"|"fork", weight)` or
  `Multiverse.set_applied(ivs)`.

### Derived events (events.py)

Every tick (replay frame, live poll) the server asks the event layer for the interventions the feeds imply
(`central.EventFeed`: `events.replay` over the replay window, `events.derive` on the newest records live) and resolves
them against the seed (`central.resolve_events`): `{naptan}` to that station, `{lat, lon, radius_m}` to the streets
inside it. A NaPTAN outside the seed's stations resolves to nothing (`inSim: false`). Each resolved event enters half
of the worlds (`EVENT_PRIOR`, a fixed coin per world and event; children inherit) so the readings price it in or out:
a wrong "station closed" call costs every world that carries it as soon as TfL publishes the station busy.
`{"events": "off"}` on `/spawn` turns them off.

Tube line status (`line_disruption`) guards against false closures:
- A station is `closure` only when every line serving it is closed by line status, the station inside the stated
  section. The lines serving a station come from the whole recorded day of arrivals (`tube_day` in replay), so a line
  with no recent train is never read as "all lines shut"; a partly closed station gets `capacity` at its open share.
- A station reading at least 50 % of typical on live crowding (`CLOSURE_MAX_LIVE_RATIO`) is never closed; the
  closure and its displacement are dropped and logged.
- A line with a Planned Closure status and no train seen moving between stations (TfL keeps parked trains listed
  "At Platform") is not scheduled at that time, not disrupted: nothing is emitted for it.
- A delay with no section (whole line, "on the rest of the line") is one `capacity` event per line,
  `where: {line, naptans}`. `central.resolve_events` has no branch for that shape yet, so these neither enter the
  worlds nor show in `/state`.

### Endpoints (central seed)

| Endpoint | Shape |
|---|---|
| `GET /state` | everything the Oxford server returns, plus `forecast: {unit, stations: [{naptan, name, hub, "+5" / "+15" / "+60": {mean, p10, p90}}], hubs: [{hub, name, naptan, "+5"...: {mean, p10, p90, people: {mean, p10, p90}}}]}` (% of usual), `events: [{id, kind, reason, planned, start, end, inSim}]`, `interventions` (the user's), `graph: {edges, nodes, bbox}` |
| `GET /forecast?naptan=&horizon=` | `{naptan, name, horizonMin, at, busyness (TfL percentageOfBaseline), typical, mean, p10, p90}` (% of usual; the interval includes the station noise model, i.e. it is a range for the reading TfL will publish) |
| `GET /forecast?hub=&horizon=` | `{hub, name, horizonMin, at, stations, mean, p10, p90, people: {mean, p10, p90}}`; hub busyness = catchment stations' summed busyness over their summed typical |
| `GET /forecast?horizon=` | the `/state` `forecast` block at that one horizon |
| `GET /segments?horizon=` | `{horizonMin, at, people: [...]}`: price-weighted people per street edge, in `/geometry` order (horizons 0-10, 15-20, 60-65 from the epoch start are kept) |
| `POST /scenario` | body `{interventions: [<the /intervene shape, without mode>], horizons?: [15, 60]}` (default 5, 15, 60). Runs them on copies of the live worlds (each copy priced as its original), never touching live state. Reply `{before: {stations, hubs}, after: {stations, hubs}, ms}`, blocks shaped like `/state` `forecast`: `stations: [{naptan, name, hub, "+15": {mean, p10, p90}, ...}]`, `hubs: [{hub, name, naptan, "+15": {mean, p10, p90, people}}]`. Typically 1-2 s for 50 worlds |
| `GET /cams?horizon=` | `{horizonMin, cams: [{camId, name, mean, p10, p90}]}` |
| `GET /vix` | unchanged: the London VIX over every station zone, history and driver (a camera, a station's crowding, the bus feed, rain, "events", an applied intervention) |
| `GET /geometry` | segments, zones, cams, plus `stations`, `hubs`, `origin` |

## Modes

- **replay**: frames from recorder NDJSON `data/raw/<source>/YYYY-MM-DD/HH.ndjson(.gz)`, lines
  `{"ts","source","payload"}`. Camera payloads carry `readings: [{camId, people}]` (the YOLO `/counts`
  shape); the bus payload carries `meanDelayMin`. When no recorded `jamcams` is found it uses `fixtures/`: a
  synthetic 80 m grid with real Oxford Circus anchors, and ten one-minute frames from a hidden true world
  (`fixture.TRUE_ASSUMPTIONS`: rain from +3 min, tourists x1.5, buses 4 min late) plus Poisson noise,
  rolled into a second five-minute epoch exactly as the engine rolls, plus the synthetic alternative feeds.
- **live**: on the real Oxford Circus seed (the default) or the central seed, reseeds at the newest recorder
  snapshot and reads the newest one every 60 s; no web app involved. Only on the synthetic fixture (no recorded
  `jamcams`) or another non-real seed does it seed from a twin (`/api/twin/field`) and poll `/api/around/counts`.
  Those routes are no longer in this repo, so that path needs a Mirrorverse twin at `TWIN_URL`; without one,
  `POST /spawn {"mode":"live"}` fails. Use replay, or run the recorder.

## API

`POST /spawn {n, horizonMin=5, mode, crnSeed, rngSeed, brokers: "on"|"off"}` · `GET /brokers` · `GET /vix` (`horizon_min`, `vix`, `vix_history` [{t, vix, delta, driver, spike}], `vix_driver`; brokers carry `source` + `glyph`) · `POST /observe {next:true} | {ts, readings, busDelayMin, rainMmH, crowding, crowdingTypical, bikesOut} | {}`
· `GET /state` · `POST /intervene {kind, zoneId, atMin, note?, prior?}` · `GET /replay?from&to` · `GET /geometry`
