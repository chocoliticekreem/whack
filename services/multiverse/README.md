# Multiverse: reality prunes the parallel Londons

N worlds, each one run of the reused Predict engine (`services/predict`, rules mode, no LLM, no keys)
under its own assumptions. Every new observation reprices them; the ones reality disproves get delisted
and the survivors spawn jittered children so N stay live.

| Piece | Where |
|---|---|
| Assumptions per world (moving share, tourist mix, pavement capacity, riders per bus, bus delay, walking pace, rain onset, random incident) | `worlds.py` `sample_assumptions` / `jitter_assumptions` |
| Common random numbers: every world draws personas, departures and destinations from one `crnSeed`; assumptions come from a separate per-world stream | `worlds.run_world` |
| Likelihood: camera counts compared on the sqrt scale (Poisson sd 0.5 + model sd 1.0); bus delay Gaussian (sd 2 min) | `engine.log_likelihood_*` |
| Price = normalised posterior; delist below `0.1 / N`; systematic resampling refills to N; a parent splits its price with its children | `engine.Multiverse.observe` |
| Explanation trail: per-observation log-likelihood and the worst camera; delist reason in words | `world.trail`, `world.delistReason` |
| Place an event: every live world forks a child with the Predict intervention (`closure`/`incident`/`event`/`weather`), prior 50 % | `engine.Multiverse.intervene` |
| Persona brokers (TOPS clone, on by default): 10 personas each submit an idea (set-off share, tourists, pavement capacity, bus delay, rain onset, optional event) + conviction + rationale; worlds are centred on it and spread by (1 - conviction); P&L = log(posterior mass / prior mass); track record = decayed sum (0.7); slots = softmax of track records, at least 1 world per broker | `brokers.py`, `engine.Multiverse.rebalance` |
| Past the horizon: re-seed from the latest counts and re-run live worlds (prices and lineage kept) | `engine.reseed`, `Multiverse.roll` |

## Run

```bash
python3 services/multiverse/server.py            # :8049, replay on the fixture by default
python3 services/multiverse/demo.py 3 50         # CLI: spawn 50 -> 3 observations -> board
python3 services/multiverse/demo.py 3 50 --brokers  # same, with the broker leaderboard each frame
python3 -m unittest discover -s services/multiverse
python3 services/multiverse/fixture.py           # regenerate the synthetic fixture
```

UI: `apps/stream` → `/multiverse` (proxied through `/api/multiverse/*`).

Env (names only): `MULTIVERSE_PORT` (8049), `MULTIVERSE_CORS_ORIGIN`, `TWIN_URL` (live mode, default
`http://localhost:3047`), `MULTIVERSE_SEED` (a seed JSON for replaying recorded data), `MULTIVERSE_RAW`
(recorder root, default `data/raw` when `MULTIVERSE_SEED` is set), `MULTIVERSE_CAM_SOURCES`,
`MULTIVERSE_BUS_SOURCES` (comma lists of recorder source names), `ANTHROPIC_API_KEY` (broker ideas
from Claude Haiku 4.5, one batched call per observation; read from the env or the repo / main-checkout
`.env`; without it the rules brokers answer and nothing touches the network).

## Modes

- **replay**: frames from recorder NDJSON `data/raw/<source>/YYYY-MM-DD/HH.ndjson(.gz)`, lines
  `{"ts","source","payload"}`. Camera payloads carry `readings: [{camId, people}]` (the YOLO `/counts`
  shape); the bus payload carries `meanDelayMin`. Without `MULTIVERSE_SEED` it uses `fixtures/`: a
  synthetic 80 m grid with real Oxford Circus anchors, and six frames from a hidden true world
  (`fixture.TRUE_ASSUMPTIONS`: rain from +12 min, tourists x1.5, buses 4 min late) plus Poisson noise.
- **live**: seed from the twin (`/api/twin/field`, Oxford Circus), then each `POST /observe {}` polls
  `/api/around/counts` (YOLO on JamCams, needs `services/yolo` running).

## API

`POST /spawn {n, horizonMin, mode, crnSeed, rngSeed, brokers: "on"|"off"}` · `GET /brokers` · `POST /observe {next:true} | {ts, readings, busDelayMin} | {}`
· `GET /state` · `POST /intervene {kind, zoneId, atMin, note?, prior?}` · `GET /replay?from&to` · `GET /geometry`
