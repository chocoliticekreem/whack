# Crowd & traffic field

`GET /api/twin/field` returns, for every ≤40 m road segment around Tower Hill, how many people and
vehicles are on it right now (per 100 m), where that number came from, and how much to trust it.
The client sim (`src/twin/sim`) spawns agents until each segment holds its target count.

## Observed vs inferred

- **Observed** — the segment's midpoint lies inside a TfL JamCam view cone (60°, 8–85 m, heading
  from TfL's view text) and that cam has a YOLO reading younger than 30 min. Density = peak count
  in the clip ÷ visible segment length. Confidence 0.8, halving every 10 min of age. People are
  spread over every footway in the cone; vehicles over major/minor roads only.
- **Inferred** — everything else. `prior × exp(residual)` where
  - prior = peak density by road kind (`model.ts`) × London hour-of-day curve (weekday/weekend from
    the Europe/London clock) × anchor boost (1 + Σ gaussian σ=120 m around stations, stops,
    attractions, shops, food — stations weigh 1.0, a café 0.05) × weather (rain 0.55 for people);
  - residual = inverse-distance² blend of each cam's log(observed ÷ prior) with a virtual prior
    observation 350 m away, clamped to ±ln 3. Busy cams lift nearby streets; far from cams the
    estimate relaxes to the prior. Confidence 0.35 at most.

Speeds on major/minor roads: free-flow 30/20 km/h scaled by `1 - vehicles/25`, floor 25 %.

## Calibration

All constants are in `model.ts` with the reasoning next to them. The sanity check is the five cams
within 700 m: the inferred density on the streets next to a cam should land within ~2× of what the
cam itself reads at the same hour. Totals are capped in the sim (2500 people, 600 vehicles) and
LOD-culled beyond 1.1 km from the camera; `?field=1` colours segments by load (observed solid,
inferred faint) to eyeball the field against the real cams.
