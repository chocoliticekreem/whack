"use client";

import { create } from "zustand";
import type { AroundCam, CamClipTracks } from "@/platform/around/types";
import { frameFromPose, projectInto, type CamCalibration } from "../live/project";
import { inBuilding, type BuildingIndex } from "../network/buildings";
import { snapVehicle, type SnapIndex, type VehicleSnap } from "../network/snap";

/**
 * Fits a camera's pose to the road network using its own YOLO vehicle tracks: real cars drive on lanes,
 * in the lane's direction (UK, keep left), so the pose that lands their ground points on lanes moving the
 * right way is the one that lines the twin's roads up with the real view. Heading is searched over the
 * full circle because the vision model sometimes reads a camera back to front; the rest stays near the
 * model's pose (a prior), so a clip with few vehicles cannot drag the camera somewhere absurd.
 */

const MISS_M = 9;
/** A real car inside a building footprint is impossible: costs several misses. */
const IN_BUILDING = 4 * MISS_M * MISS_M;
const SAMPLES_PER_TRACK = 8;
const MAX_SAMPLES = 640;

interface Sample {
  u: number;
  v: number;
  /** The same track a moment later, for its direction of travel. */
  u2: number;
  v2: number;
}

export interface FitResult {
  pose: CamCalibration;
  beforeM: number;
  afterM: number;
  samples: number;
}

export const useAutofit = create<{ byCam: Record<string, FitResult> }>(() => ({ byCam: {} }));

function samplesOf(clip: CamClipTracks): Sample[] {
  const out: Sample[] = [];
  for (const tr of clip.tracks) {
    if (tr.kind !== "vehicle" || tr.pts.length < 3) continue;
    const n = tr.pts.length;
    const step = Math.max(1, Math.floor(n / SAMPLES_PER_TRACK));
    for (let i = 0; i < n - 1; i += step) {
      const a = tr.pts[i];
      const b = tr.pts[Math.min(n - 1, i + step)];
      out.push({ u: a[1], v: a[2], u2: b[1], v2: b[2] });
    }
  }
  if (out.length <= MAX_SAMPLES) return out;
  const stride = out.length / MAX_SAMPLES;
  return Array.from({ length: MAX_SAMPLES }, (_, i) => out[Math.floor(i * stride)]);
}

const _a = [0, 0];
const _b = [0, 0];
const _s: VehicleSnap = { x: 0, z: 0, heading: 0, laneId: 0, s: 0, dist: 0 };

/** Mean squared distance (capped) from each sample to a lane running its way, seen through `pose`. */
function laneCost(cam: AroundCam, pose: CamCalibration, samples: Sample[], idx: SnapIndex, blds: BuildingIndex) {
  const frame = frameFromPose(cam, pose);
  let sum = 0;
  for (const s of samples) {
    projectInto(frame, s.u, s.v, _a, 0, 120);
    projectInto(frame, s.u2, s.v2, _b, 0, 120);
    const dx = _b[0] - _a[0];
    const dz = _b[1] - _a[1];
    const heading = dx * dx + dz * dz > 0.25 ? Math.atan2(dx, dz) : NaN;
    const d = snapVehicle(idx, _a[0], _a[1], heading, _s, MISS_M) ? _s.dist : MISS_M;
    sum += inBuilding(blds, _a[0], _a[1]) ? IN_BUILDING : d * d;
  }
  return sum / Math.max(1, samples.length);
}

function priorCost(p: CamCalibration, base: CamCalibration) {
  const dh = ((p.headingDeg - base.headingDeg + 540) % 360) - 180;
  const dp = p.pitchDeg - base.pitchDeg;
  const dH = Math.log(p.heightM / base.heightM);
  const dl = p.hfovDeg - base.hfovDeg;
  const dt = Math.hypot((p.dxM ?? 0) - (base.dxM ?? 0), (p.dzM ?? 0) - (base.dzM ?? 0));
  return (dh / 120) ** 2 + (dp / 8) ** 2 + (dH / 0.5) ** 2 + (dl / 15) ** 2 + (dt / 15) ** 2;
}

type Key = "headingDeg" | "pitchDeg" | "heightM" | "hfovDeg" | "dxM" | "dzM";

function clamp(p: CamCalibration): CamCalibration {
  return {
    ...p,
    headingDeg: ((p.headingDeg % 360) + 360) % 360,
    pitchDeg: Math.min(75, Math.max(-5, p.pitchDeg)),
    heightM: Math.min(40, Math.max(1, p.heightM)),
    hfovDeg: Math.min(100, Math.max(25, p.hfovDeg)),
    dxM: Math.min(25, Math.max(-25, p.dxM ?? 0)),
    dzM: Math.min(25, Math.max(-25, p.dzM ?? 0)),
  };
}

/** Full-circle heading sweep, then coordinate descent. Returns null when the clip has too few vehicles. */
export function fitPose(cam: AroundCam, clip: CamClipTracks, idx: SnapIndex, blds: BuildingIndex, base: CamCalibration): FitResult | null {
  const samples = samplesOf(clip);
  if (samples.length < 24) return null;
  const start: CamCalibration = { dxM: 0, dzM: 0, ...base };
  const cost = (p: CamCalibration) => laneCost(cam, p, samples, idx, blds) + 4 * priorCost(p, start);
  const beforeM = Math.sqrt(laneCost(cam, start, samples, idx, blds));

  let best = start;
  let bestC = cost(start);
  for (let h = 0; h < 360; h += 10) {
    for (const dp of [-5, 0, 5]) {
      for (const kH of [0.7, 1, 1.4]) {
        const p = clamp({ ...start, headingDeg: h, pitchDeg: start.pitchDeg + dp, heightM: start.heightM * kH });
        const c = cost(p);
        if (c < bestC) [best, bestC] = [p, c];
      }
    }
  }

  const step: Record<Key, number> = { headingDeg: 6, pitchDeg: 2, heightM: 0.2, hfovDeg: 5, dxM: 4, dzM: 4 };
  const keys = Object.keys(step) as Key[];
  for (let round = 0; round < 7; round++) {
    let improved = true;
    for (let guard = 0; improved && guard < 12; guard++) {
      improved = false;
      for (const k of keys) {
        for (const dir of [-1, 1]) {
          const cur = best[k] ?? 0;
          // Height moves multiplicatively so a step means the same at 2 m and 20 m.
          const next = k === "heightM" ? cur * Math.exp(dir * step[k]) : cur + dir * step[k];
          const p = clamp({ ...best, [k]: next });
          const c = cost(p);
          if (c < bestC - 1e-6) {
            [best, bestC] = [p, c];
            improved = true;
          }
        }
      }
    }
    for (const k of keys) step[k] /= 2;
  }

  const afterM = Math.sqrt(laneCost(cam, best, samples, idx, blds));
  if (afterM >= beforeM) return { pose: start, beforeM, afterM: beforeM, samples: samples.length };
  const q = (x: number, by: number) => Math.round(x / by) * by;
  return {
    pose: {
      ...best,
      headingDeg: q(best.headingDeg, 0.1),
      pitchDeg: q(best.pitchDeg, 0.1),
      heightM: q(best.heightM, 0.1),
      hfovDeg: q(best.hfovDeg, 0.5),
      dxM: q(best.dxM ?? 0, 0.1),
      dzM: q(best.dzM ?? 0, 0.1),
      source: `auto-fit to ${samples.length} vehicle points on the road network, from: ${base.source ?? "the default pose"}`,
    },
    beforeM,
    afterM,
    samples: samples.length,
  };
}
