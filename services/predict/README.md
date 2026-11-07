> Reused from chocoliticekreem/mirrorverse (built 2026-10-17), approved by WHACK organisers.

# Predict: a MiroFish-style forecast of Tower Hill, seeded from live reality

`services/predict` answers "what happens around Tower Hill in the next 30 minutes?" the way
[MiroFish](https://github.com/666ghj/MiroFish) answers questions about a document: seed a parallel
world, let a swarm of personas play it forward, then write a prediction report and score it against
reality once the time arrives.

MiroFish (built on [CAMEL-AI OASIS](https://github.com/camel-ai/oasis)) goes seed document → knowledge
graph → thousands of LLM persona agents with memory → simulation → report. We keep that shape and swap
the seed for the live city:

| MiroFish | Predict |
|---|---|
| Seed document | Live field from the twin: street segments with YOLO people/vehicle counts from TfL JamCams, Open-Meteo weather, TfL disruptions and bus arrivals, the real Europe/London clock |
| Knowledge graph | The real OSM street graph (local metres, +x east, +z south) grouped into 16 zones around real anchors (Tower Hill, Tower Gateway DLR, Fenchurch Street, Tower of London, Tower Bridge, St Katharine Docks, office blocks) |
| Thousands of LLM agents | 30–80 persona **groups**, each standing for N counted people (commuters, tourists, office lunch, residents, delivery riders, cyclists, school groups, bus arrivals). The LLM is called only at decision points, in one batched call each |
| OASIS simulation | Deterministic, seeded movement on the graph: Dijkstra paths, pavement capacity → queueing, crowding → avoidance, closures/incidents/events/weather → re-planning |
| Report + interactive world | `report` (top changes, reasons, confidence, explicit assumptions) + a 60 s timeline the twin can replay; `/score` compares due predictions with the latest camera readings |

Nothing is invented: every number traces to a feed, and anything the model assumes (alighting riders
per bus, pavement capacity, how far a camera sees) is listed under `assumptions`.

## CAMP mapping

| Layer | How Predict uses it |
|---|---|
| **M** model services | Persona generation, decision points and report prose run on **W&B Inference** (`https://api.inference.wandb.ai/v1`, OpenAI-compatible, model `WANDB_MODEL`, default `Qwen/Qwen3.8-27B`), every call wrapped in a `weave.op` and traced in **Weave** at `WANDB_TEAM/WANDB_PROJECT`. Strict JSON with schema validation and repair; a bad group falls back to its rules twin |
| **A** agent runtime | Pure Python 3.12, two pip deps (`openai`, `weave`), stdlib HTTP server. Small enough to ship in the same ConfigMap as `services/agent` on the team's cluster |
| **P** platform | The YOLO counts come through the twin's camera routes; on the cluster the same seed can read the VSS detections instead |

Without `WANDB_API_KEY` everything still runs in **rules** mode (transparent heuristics; the report says
`"mode": "rules"` and lists "LLM off" under assumptions).

## Files

| File | Role |
|---|---|
| `seed.py` | Fetch `GET /api/twin/field`; fall back to `/api/twin/osm` + `/api/around/cams` + `/api/around/counts`. Zones, London time, weather, disruptions, buses, interventions, assumptions |
| `personas.py` | Persona groups: `rules` generator and `llm` generator (one batched call, validated/repaired) |
| `simulate.py` | Street graph, walkers, capacity, interventions, re-planning (rules or batched LLM decision point), 60 s timeline |
| `report.py` | Top 3–5 predicted changes with time, zone, magnitude, drivers, inputs, confidence, assumptions; LLM prose when enabled |
| `llm.py` | W&B Inference client + Weave tracing + JSON parsing/repair |
| `server.py` | HTTP API on `:8048`, CORS for `http://localhost:3047`, runs stored in memory and `runs/*.json` |

## Run

```bash
cd services/predict
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
TWIN_URL=http://localhost:3047 .venv/bin/python server.py
```

Environment (names only): `TWIN_URL` (default `http://localhost:3047`), `PREDICT_PORT` (8048),
`PREDICT_CORS_ORIGIN`, `WANDB_API_KEY`, `WANDB_TEAM`, `WANDB_PROJECT`, `WANDB_MODEL`.

## API

| Route | Body → reply |
|---|---|
| `GET /health` | `{ok, llm, weave, model, runs}` |
| `POST /predict` | `{horizonMin=30, interventions?, useLlm?=true, rngSeed?=47}` → `{runId, at, horizonMin, seed, personas, timeline, report, interventions, camSegments, trace?}` |
| `GET /predict/{runId}` | the stored run |
| `POST /score` | `{runId}` → compares the latest due timeline step with fresh camera readings: per-camera and per-zone predicted vs actual people, error %, `right`/`wrong` (within 30 % or 2 people), mean absolute error |

An intervention is `{kind: "closure"|"incident"|"event"|"weather", where: {x, z} | zoneId | zoneName,
note, atMin}`. Closures remove the street segments within 45 m for pedestrians and vehicles (vehicles
are spread over the zone's open streets); incidents cut capacity to 40 % within 70 m; events add an
attractor that 40 % of tourists, residents and lunch crowds divert to; weather flips the rain reactions.
TfL disruptions marked `Closed` inside the field are applied automatically as observed vehicle closures.

`timeline[i].segments` lists only segments whose load differs from now (≥ 2 people per 100 m or 50 %);
unlisted segments keep their seed value. `timeline[i].zones[*].deltaPeople` is relative to `timeline[0]`.

## Honesty rules baked in

- `source: "observed"` only where a camera reading sits on that segment; everything else is `inferred`
  with confidence ≤ 0.3 and the report's confidence reflects the share of observed segments in a zone.
- Persona counts are the seeded counts; a configurable share (by daypart) sets off within the horizon,
  the rest stay. Bus-arrival groups come from live TfL arrivals with an assumed 8 riders alighting.
- `/score` only compares cameras with a reading newer than the forecast step minus 15 min.
