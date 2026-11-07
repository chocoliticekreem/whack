import type { SegmentKind } from "./types";

/**
 * Every tunable in the crowd/traffic field lives here. Numbers are people or vehicles per 100 m of
 * road at the WEEKDAY PEAK; the time curve scales them down from there. They are hand-calibrated
 * against the five JamCams around Tower Hill (a cam frame covers ~77 m of road, so a reading of
 * 6 people ≈ 8 per 100 m) so that inferred streets look like the observed ones at the same hour.
 */

/** Road segments longer than this are split so density varies at street-corner scale. */
export const SEGMENT_MAX_M = 40;
/** Endpoints closer than this are the same junction (tile seams and OSM rounding). */
export const SNAP_M = 1.5;

/** Peak prior per road kind. Paths are footways; rail carries nobody on foot. */
export const PEAK_PEDS: Record<SegmentKind, number> = { major: 4, minor: 2, path: 3.5, rail: 0 };
export const PEAK_VEHICLES: Record<SegmentKind, number> = { major: 7, minor: 1.5, path: 0, rail: 0 };

/** Free-flow speed by kind: London 20 mph limits on the A100/A1203; side streets slower. */
export const SPEED_KPH: Record<SegmentKind, number> = { major: 30, minor: 20, path: 0, rail: 0 };
/** Vehicles per 100 m at which traffic is effectively stationary (≈ one car every 4 m). */
export const JAM_VEHICLES = 25;
export const MIN_SPEED_FRACTION = 0.25;

/**
 * Hour-of-day shape, 0..1, indexed by London local hour. Weekday: commuter peaks 08–09 and 17–19
 * with a lunchtime bump; the City empties at night. Weekend: no commute, tourists build towards a
 * mid-afternoon plateau (Tower of London / Tower Bridge), evening trade around St Katharine Docks.
 */
export const PED_CURVE_WEEKDAY = [
  0.12, 0.08, 0.05, 0.04, 0.04, 0.08, 0.25, 0.6, 1.0, 0.95, 0.6, 0.55, 0.8, 0.75, 0.55, 0.5, 0.6, 0.95, 1.0, 0.8,
  0.5, 0.4, 0.3, 0.2,
];
export const PED_CURVE_WEEKEND = [
  0.15, 0.1, 0.06, 0.04, 0.04, 0.05, 0.1, 0.18, 0.3, 0.45, 0.65, 0.8, 0.9, 0.95, 0.95, 0.9, 0.85, 0.75, 0.65,
  0.6, 0.55, 0.45, 0.35, 0.25,
];
export const VEH_CURVE_WEEKDAY = [
  0.2, 0.15, 0.1, 0.1, 0.15, 0.35, 0.6, 0.9, 1.0, 0.9, 0.75, 0.7, 0.75, 0.75, 0.75, 0.8, 0.9, 1.0, 0.95, 0.75,
  0.55, 0.45, 0.35, 0.25,
];
export const VEH_CURVE_WEEKEND = [
  0.25, 0.18, 0.12, 0.1, 0.1, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.7, 0.75, 0.75, 0.75, 0.7, 0.7, 0.65, 0.6,
  0.55, 0.5, 0.45, 0.4, 0.3,
];

/** Footfall multiplier near anchors: 1 + Σ weight·exp(-d²/2σ²). σ = a two-minute walk. */
export const ANCHOR_SIGMA_M = 120;
/** Stations dominate; a Tube exit is the single biggest footfall source in the City. */
export const ANCHOR_WEIGHT = { station: 1.0, bus_stop: 0.15, attraction: 0.5, shop: 0.04, food: 0.05 } as const;
/** Cap so a cluster of 20 cafés does not out-pull the station. */
export const ANCHOR_BOOST_MAX = 3;

/** Rain halves pavement footfall; vehicles barely change (people switch to cabs/buses). */
export const WEATHER_PEDS = { clear: 1, cloudy: 0.95, rain: 0.55, fog: 0.85, storm: 0.35 } as const;
export const WEATHER_VEHICLES = { clear: 1, cloudy: 1, rain: 1.05, fog: 0.9, storm: 0.85 } as const;

/** Observed readings: confidence 0.8 fresh, halving every 10 min, dropped after 30 min. */
export const OBSERVED_CONFIDENCE = 0.8;
export const OBSERVED_HALF_LIFE_MS = 10 * 60_000;
export const OBSERVED_MAX_AGE_MS = 30 * 60_000;
/** A fully-visible cam cone in the 8–85 m band; never divide by less than this length. */
export const MIN_VISIBLE_M = 20;

/**
 * Residual spreading: how far an observed-vs-prior ratio from one cam carries into unobserved
 * streets. Inverse-distance² with a 60 m floor; the prior itself has the weight of a virtual cam
 * 350 m away, so far from any cam the estimate returns to the prior.
 */
export const RESIDUAL_FLOOR_M = 60;
export const RESIDUAL_PRIOR_DISTANCE_M = 350;
/** Log-ratio clamp so a single busy frame cannot 10× the whole city. */
export const RESIDUAL_LOG_CLAMP = Math.log(3);
export const INFERRED_CONFIDENCE_BASE = 0.35;
/** JamCams face junctions where vehicles queue at lights; mid-block roads carry about half that. */
export const JUNCTION_VEHICLE_FACTOR = 0.5;

export const FIELD_CACHE_MS = 30_000;
export const CAM_RADIUS_M = 700;
