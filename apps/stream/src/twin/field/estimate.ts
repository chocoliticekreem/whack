import type { TwinPoi } from "@/twin/config";
import type { WeatherKind } from "@/platform/demo";
import type { AroundCam, CamReading } from "@/platform/around/types";
import { camFrame, inViewCone } from "@/twin/live/project";
import type { GraphSegment } from "./graph";
import {
  ANCHOR_BOOST_MAX,
  ANCHOR_SIGMA_M,
  ANCHOR_WEIGHT,
  INFERRED_CONFIDENCE_BASE,
  JAM_VEHICLES,
  JUNCTION_VEHICLE_FACTOR,
  MIN_SPEED_FRACTION,
  MIN_VISIBLE_M,
  OBSERVED_CONFIDENCE,
  OBSERVED_HALF_LIFE_MS,
  OBSERVED_MAX_AGE_MS,
  PEAK_PEDS,
  PEAK_VEHICLES,
  PED_CURVE_WEEKDAY,
  PED_CURVE_WEEKEND,
  RESIDUAL_FLOOR_M,
  RESIDUAL_LOG_CLAMP,
  RESIDUAL_PRIOR_DISTANCE_M,
  SPEED_KPH,
  VEH_CURVE_WEEKDAY,
  VEH_CURVE_WEEKEND,
  WEATHER_PEDS,
  WEATHER_VEHICLES,
} from "./model";
import type { FieldAnchor, FieldCam, FieldResponse, FieldSegment } from "./types";

export interface EstimateInput {
  segments: GraphSegment[];
  pois: TwinPoi[];
  cams: AroundCam[];
  readings: CamReading[];
  weather: WeatherKind;
  now: number;
}

/** London clock → hour fraction and weekend flag, so the curve follows real time not server time. */
export function londonClock(now: number) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    hour: "numeric",
    minute: "numeric",
    weekday: "short",
    hour12: false,
  }).formatToParts(new Date(now));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const hour = Number(get("hour")) % 24;
  const minute = Number(get("minute"));
  const weekday = get("weekday");
  return { hour: hour + minute / 60, weekend: weekday === "Sat" || weekday === "Sun" };
}

function curveAt(curve: number[], hour: number) {
  const h0 = Math.floor(hour) % 24;
  const h1 = (h0 + 1) % 24;
  const k = hour - Math.floor(hour);
  return curve[h0] * (1 - k) + curve[h1] * k;
}

export function anchorsFrom(pois: TwinPoi[]): FieldAnchor[] {
  return pois.map((p) => ({
    name: p.name,
    x: p.x,
    z: p.z,
    weight: ANCHOR_WEIGHT[p.kind],
    kind: p.kind === "station" ? "station" : "poi",
  }));
}

function anchorBoost(x: number, z: number, anchors: FieldAnchor[]) {
  const inv = 1 / (2 * ANCHOR_SIGMA_M * ANCHOR_SIGMA_M);
  let sum = 0;
  for (const a of anchors) {
    const dx = a.x - x;
    const dz = a.z - z;
    const d2 = dx * dx + dz * dz;
    if (d2 > 16 * ANCHOR_SIGMA_M * ANCHOR_SIGMA_M) continue;
    sum += a.weight * Math.exp(-d2 * inv);
  }
  return 1 + Math.min(ANCHOR_BOOST_MAX, sum);
}



/** Pure estimator: observed densities inside cam cones, inferred prior × residual elsewhere. */
export function estimate(input: EstimateInput): FieldResponse {
  const { segments, cams, readings, weather, now } = input;
  const anchors = anchorsFrom(input.pois);
  const clock = londonClock(now);
  const pedTime = curveAt(clock.weekend ? PED_CURVE_WEEKEND : PED_CURVE_WEEKDAY, clock.hour);
  const vehTime = curveAt(clock.weekend ? VEH_CURVE_WEEKEND : VEH_CURVE_WEEKDAY, clock.hour);
  const pedWeather = WEATHER_PEDS[weather];
  const vehWeather = WEATHER_VEHICLES[weather];

  const n = segments.length;
  const mid = new Float64Array(n * 2);
  const priorPeds = new Float64Array(n);
  const priorVeh = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const s = segments[i];
    const mx = (s.a[0] + s.b[0]) / 2;
    const mz = (s.a[1] + s.b[1]) / 2;
    mid[i * 2] = mx;
    mid[i * 2 + 1] = mz;
    const boost = anchorBoost(mx, mz, anchors);
    priorPeds[i] = PEAK_PEDS[s.kind] * pedTime * pedWeather * boost;
    priorVeh[i] = PEAK_VEHICLES[s.kind] * vehTime * vehWeather * (1 + (boost - 1) * 0.15);
  }

  const readingById = new Map(readings.map((r) => [r.camId, r]));
  const observedPeds = new Float64Array(n).fill(-1);
  const observedVeh = new Float64Array(n).fill(-1);
  const observedConf = new Float64Array(n);
  const fieldCams: FieldCam[] = [];
  const residuals: { x: number; z: number; peds: number; veh: number; w: number }[] = [];

  for (const cam of cams) {
    const frame = camFrame(cam);
    const reading = readingById.get(cam.id);
    if (reading) fieldCams.push({ id: cam.id, x: frame.x, z: frame.z, people: reading.people, vehicles: reading.vehicles, at: reading.at });
    if (!reading || now - reading.at > OBSERVED_MAX_AGE_MS) continue;
    const age = Math.max(0, now - reading.at);
    const conf = OBSERVED_CONFIDENCE * Math.pow(0.5, age / OBSERVED_HALF_LIFE_MS);

    let footLen = 0;
    let roadLen = 0;
    let priorFoot = 0;
    let priorRoad = 0;
    const inside: number[] = [];
    for (let i = 0; i < n; i++) {
      if (!inViewCone(frame, mid[i * 2], mid[i * 2 + 1])) continue;
      inside.push(i);
      const s = segments[i];
      footLen += s.length;
      priorFoot += (priorPeds[i] * s.length) / 100;
      if (s.kind !== "path") {
        roadLen += s.length;
        priorRoad += (priorVeh[i] * s.length) / 100;
      }
    }
    if (inside.length === 0) continue;
    const peds = (reading.people / Math.max(MIN_VISIBLE_M, footLen)) * 100;
    const veh = roadLen > 0 ? (reading.vehicles / Math.max(MIN_VISIBLE_M, roadLen)) * 100 : 0;
    for (const i of inside) {
      if (observedConf[i] >= conf) continue;
      observedConf[i] = conf;
      observedPeds[i] = peds;
      observedVeh[i] = segments[i].kind === "path" ? 0 : veh;
    }
    const clampLog = (v: number) => Math.max(-RESIDUAL_LOG_CLAMP, Math.min(RESIDUAL_LOG_CLAMP, v));
    residuals.push({
      x: frame.x,
      z: frame.z,
      peds: clampLog(Math.log((reading.people + 0.5) / (priorFoot + 0.5))),
      veh:
        roadLen > 0
          ? clampLog(Math.log(((reading.vehicles + 0.5) * JUNCTION_VEHICLE_FACTOR) / (priorRoad + 0.5)))
          : 0,
      w: conf / OBSERVED_CONFIDENCE,
    });
  }

  const priorW = 1 / (RESIDUAL_PRIOR_DISTANCE_M * RESIDUAL_PRIOR_DISTANCE_M);
  const out: FieldSegment[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const s = segments[i];
    let peds: number;
    let veh: number;
    let source: FieldSegment["source"];
    let confidence: number;
    if (observedPeds[i] >= 0) {
      peds = observedPeds[i];
      veh = observedVeh[i];
      source = "observed";
      confidence = observedConf[i];
    } else {
      let wSum = priorW;
      let pedLog = 0;
      let vehLog = 0;
      for (const r of residuals) {
        const dx = r.x - mid[i * 2];
        const dz = r.z - mid[i * 2 + 1];
        const w = r.w / Math.max(RESIDUAL_FLOOR_M * RESIDUAL_FLOOR_M, dx * dx + dz * dz);
        wSum += w;
        pedLog += w * r.peds;
        vehLog += w * r.veh;
      }
      peds = priorPeds[i] * Math.exp(pedLog / wSum);
      veh = priorVeh[i] * Math.exp(vehLog / wSum);
      source = "inferred";
      confidence = INFERRED_CONFIDENCE_BASE * Math.min(1, 1 - priorW / wSum + 0.5);
    }
    const seg: FieldSegment = {
      id: s.id,
      a: [round(s.a[0]), round(s.a[1])],
      b: [round(s.b[0]), round(s.b[1])],
      kind: s.kind,
      peds: round(peds),
      vehicles: round(veh),
      source,
      confidence: round(confidence),
    };
    if (s.kind === "major" || s.kind === "minor") {
      const flow = Math.max(MIN_SPEED_FRACTION, 1 - veh / JAM_VEHICLES);
      seg.speedKph = round(SPEED_KPH[s.kind] * flow);
    }
    out[i] = seg;
  }

  return { at: now, segments: out, anchors, cams: fieldCams };
}

const round = (v: number) => Math.round(v * 100) / 100;
