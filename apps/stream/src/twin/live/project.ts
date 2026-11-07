import { create } from "zustand";
import type { AroundCam, TrackedObject } from "@/platform/around/types";
import { bearingDeg } from "@/platform/around/flows";
import { currentOrigin, toLocal } from "../config";
import CALIBRATION from "./calibration.json";

/**
 * One pinhole camera model shared by the ground projection (YOLO u,v → street) and the twin's virtual
 * CCTV camera (CameraRig). Because both use the same pose, a simulated person or car lands on the same
 * pixel as the real one in the compare view. Per-camera poses come from calibration.json, written by
 * services/vision/calibrate.py (Nemotron 3.5 Super VL); cameras without one use a typical JamCam pose.
 */

export interface CamCalibration {
  headingDeg: number;
  pitchDeg: number;
  heightM: number;
  hfovDeg: number;
  /** Width / height of the real camera's frame. */
  aspect: number;
  source?: string;
  confidence?: number;
  reason?: string;
  street?: string;
  /** Correction to the TfL-published camera position, metres east / south in local space. */
  dxM?: number;
  dzM?: number;
}

const CALIBRATIONS = CALIBRATION as Record<string, CamCalibration>;

/** Typical TfL JamCam: ~7 m pole, tilted ~15° down, 352×288 frame. */
export const DEFAULT_POSE = { pitchDeg: 15, heightM: 7, hfovDeg: 60, aspect: 352 / 288 } as const;
/** Rays that meet the ground further than this (or never) are clamped here along their bearing. */
export const MAX_GROUND_M = 150;
/** Far edge of the drawn view cone. */
export const CONE_M = 90;

/** Poses being adjusted live in the compare view; they win over calibration.json until saved. */
const overrides: Record<string, CamCalibration> = {};

/** Bumped whenever a live pose changes, so memoised geometry (agents, view cones) recomputes. */
export const usePoseVersion = create<{ v: number }>(() => ({ v: 0 }));

export function setPoseOverride(camId: string, pose: CamCalibration) {
  overrides[camId] = pose;
  usePoseVersion.setState((s) => ({ v: s.v + 1 }));
}

export function calibrationFor(camId: string): CamCalibration | null {
  return overrides[camId] ?? CALIBRATIONS[camId] ?? null;
}

/** The pose a camera is using right now, as a full calibration (defaults filled in). */
export function currentPose(cam: AroundCam): CamCalibration {
  return calibrationFor(cam.id) ?? { headingDeg: camHeadingDeg(cam), ...DEFAULT_POSE };
}

export function camHeadingDeg(cam: AroundCam) {
  return calibrationFor(cam.id)?.headingDeg ?? cam.heading ?? bearingDeg(cam.lat, cam.lon, currentOrigin().lat, currentOrigin().lon);
}

export interface CamFrame {
  x: number;
  z: number;
  /** Compass heading, 0 = north (-z), clockwise. */
  headingRad: number;
  /** Downward tilt. */
  pitchRad: number;
  heightM: number;
  /** tan(half horizontal FOV) and tan(half vertical FOV) of the real frame. */
  tanH: number;
  tanV: number;
  aspect: number;
  calibrated: boolean;
}

export function camFrame(cam: AroundCam): CamFrame {
  const cal = calibrationFor(cam.id);
  return { ...frameFromPose(cam, { ...DEFAULT_POSE, ...cal, headingDeg: camHeadingDeg(cam) }), calibrated: cal !== null };
}

/** The frame a camera would have at `pose`, without registering it. */
export function frameFromPose(cam: AroundCam, pose: CamCalibration): CamFrame {
  const [x, z] = toLocal(cam.lat, cam.lon);
  const tanH = Math.tan((pose.hfovDeg * Math.PI) / 360);
  return {
    x: x + (pose.dxM ?? 0),
    z: z + (pose.dzM ?? 0),
    headingRad: (pose.headingDeg * Math.PI) / 180,
    pitchRad: (pose.pitchDeg * Math.PI) / 180,
    heightM: pose.heightM,
    tanH,
    tanV: tanH / pose.aspect,
    aspect: pose.aspect,
    calibrated: true,
  };
}

/**
 * True when ground point (x, z) is inside the camera's calibrated ground footprint: within the
 * horizontal field of view, beyond where the bottom edge of the frame meets the ground, and
 * within CONE_M. Shared by the crowd field and the sim so "observed" matches what YOLO can see.
 */
export function inViewCone(frame: CamFrame, x: number, z: number) {
  const dx = x - frame.x;
  const dz = z - frame.z;
  const d = Math.hypot(dx, dz);
  const lowest = frame.pitchRad + Math.atan(frame.tanV);
  const near = lowest >= Math.PI / 2 ? 0 : frame.heightM / Math.tan(lowest);
  if (d < near || d > CONE_M) return false;
  let diff = Math.atan2(dx, -dz) - frame.headingRad;
  while (diff > Math.PI) diff -= 2 * Math.PI;
  while (diff < -Math.PI) diff += 2 * Math.PI;
  return Math.abs(diff) <= Math.atan(frame.tanH);
}

/** Unit-free forward direction of the camera as [x, y, z]. */
export function forwardOf(frame: CamFrame): [number, number, number] {
  const sh = Math.sin(frame.headingRad);
  const ch = Math.cos(frame.headingRad);
  const sp = Math.sin(frame.pitchRad);
  const cp = Math.cos(frame.pitchRad);
  return [sh * cp, -sp, -ch * cp];
}

/**
 * Writes the ground point seen at frame coords (u, v) into `out` as [x, z]: the ray through that pixel
 * meets the road (y = 0). Same maths as a three.js PerspectiveCamera at the frame's pose. Allocation-free.
 */
export function projectInto(frame: CamFrame, u: number, v: number, out: Float32Array | number[], o = 0, maxM = MAX_GROUND_M) {
  const sh = Math.sin(frame.headingRad);
  const ch = Math.cos(frame.headingRad);
  const sp = Math.sin(frame.pitchRad);
  const cp = Math.cos(frame.pitchRad);
  const xn = (2 * u - 1) * frame.tanH;
  const yn = (1 - 2 * v) * frame.tanV;
  // ray = forward + xn·right + yn·up, with right = (cos h, 0, sin h) and up tilted with the pitch
  const dx = sh * cp + xn * ch + yn * sh * sp;
  const dy = -sp + yn * cp;
  const dz = -ch * cp + xn * sh - yn * ch * sp;
  const flat = Math.hypot(dx, dz) || 1e-9;
  let reach = dy < -1e-6 ? (frame.heightM / -dy) * flat : maxM;
  if (reach > maxM) reach = maxM;
  out[o] = frame.x + (dx / flat) * reach;
  out[o + 1] = frame.z + (dz / flat) * reach;
}

/**
 * YOLO's point is the bottom of the box: the vehicle's near edge. Writes the body centre into `out`
 * as [x, z]: the ground point pushed away from the camera by half the footprint seen along the ray,
 * so the far side lands where the real one ends. `yaw` is the vehicle's heading (atan2(dx, dz)).
 */
export function bodyCentreInto(frame: CamFrame, x: number, z: number, yaw: number, length: number, width: number, out: Float32Array | number[]) {
  const rx = x - frame.x;
  const rz = z - frame.z;
  const rl = Math.hypot(rx, rz) || 1;
  const along = Math.abs((rx / rl) * Math.sin(yaw) + (rz / rl) * Math.cos(yaw));
  const push = 0.5 * (along * length + Math.sqrt(1 - Math.min(1, along * along)) * width);
  out[0] = x + (rx / rl) * push;
  out[1] = z + (rz / rl) * push;
}

/** Interpolates a track at clip time t into out[o], out[o+1] = (u, v). Returns false outside the track. */
export function sampleInto(pts: TrackedObject["pts"], t: number, out: Float32Array | number[], o = 0) {
  const n = pts.length;
  if (n === 0 || t < pts[0][0] || t > pts[n - 1][0]) return false;
  for (let i = 1; i < n; i++) {
    const p1 = pts[i];
    if (p1[0] >= t) {
      const p0 = pts[i - 1];
      const k = p1[0] === p0[0] ? 0 : (t - p0[0]) / (p1[0] - p0[0]);
      out[o] = p0[1] + (p1[1] - p0[1]) * k;
      out[o + 1] = p0[2] + (p1[2] - p0[2]) * k;
      return true;
    }
  }
  return false;
}

/** View cone as a flat triangle fan in local metres: [apex, then the ground seen along the top of the frame]. */
export function viewConePoints(frame: CamFrame): [number, number][] {
  const pts: [number, number][] = [[frame.x, frame.z]];
  const out = [0, 0];
  for (let i = 0; i <= 12; i++) {
    projectInto(frame, i / 12, 0, out, 0, CONE_M);
    pts.push([out[0], out[1]]);
  }
  return pts;
}
