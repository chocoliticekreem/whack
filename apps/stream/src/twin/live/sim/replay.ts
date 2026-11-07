import type { TrackedObject } from "@/platform/around/types";
import { layoutOf } from "../../network/layout";
import {
  VEHICLE_REACH_M,
  pathAt,
  snapVehicle,
  walkPath,
  type Path,
  type SnapIndex,
  type VehicleSnap,
} from "../../network/snap";
import type { XZ } from "../../network/types";
import { bodyCentreInto, projectInto, sampleInto, type CamFrame } from "../project";

/**
 * Replays tracked (YOLO) vehicles on the road network. Each track becomes a lane route: its ground
 * points pulled onto the nearest asphalt lane, extended upstream and downstream along the network so a
 * vehicle drives in from, and out past, the edge of what the camera saw. A time-stepped pass then
 * moves every vehicle along its route at the tracked speed but never closer than a bumper gap to
 * anything else: followers are held back, a spawn waits until its spot is clear. The result is a pose
 * table per vehicle that is cheap to sample every frame.
 */

export const REPLAY_DT = 0.1;
/** Routes run until this far from the camera that tracked them (it never removes a car inside 150 m). */
const CULL_M = 170;
const EXT_M = 450;
const MIN_GAP_M = 2;
/** Gap grows with the pair's combined length: a bus needs more room than a bike. */
const GAP_PER_LENGTH = 0.04;
const SIDE_PAD_M = 0.15;
const JUNCTION_PAD_M = 6;
const APPROACH_MPS = 8;
const DEPART_MPS = 6;
/** How far behind its desired spot a vehicle may spawn to clear whatever stands there. */
const SPAWN_BACK_M = [0, 1, 2, 3, 4.5, 6, 8, 11, 15, 20];

export interface ReplayInput {
  key: string;
  frame: CamFrame;
  pts: TrackedObject["pts"];
  /** Body footprint in metres. */
  length: number;
  width: number;
  /** Buses stay on major roads when there is one in reach. */
  bus: boolean;
}

/** Pose table of one vehicle, one entry per REPLAY_DT starting at step `j0`. */
export interface Traj {
  j0: number;
  n: number;
  x: Float32Array;
  z: Float32Array;
  yaw: Float32Array;
  length: number;
  width: number;
  camX: number;
  camZ: number;
}

export interface ReplaySet {
  /** Clip time of the first pose of any vehicle, and the time until the last one has left. */
  tMin: number;
  cycle: number;
  byKey: Map<string, Traj>;
}

interface Plan {
  inp: ReplayInput;
  path: Path;
  /** First step and desired odometer (distance along the path) per step. */
  jStart: number;
  ud: Float32Array;
  hl: number;
  hw: number;
}

const _uv = new Float32Array(2);
const _g = new Float32Array(2);
const _c = new Float32Array(2);
const _vs: VehicleSnap = { x: 0, z: 0, heading: 0, laneId: -1, s: 0, dist: 0 };
const _p: XZ = [0, 0];

function plan(idx: SnapIndex, inp: ReplayInput): Plan | null {
  const { frame, pts } = inp;
  const t0 = pts[0][0];
  const t1 = pts[pts.length - 1][0];
  const q0 = Math.round(t0 / REPLAY_DT);
  const K = Math.max(1, Math.round((t1 - t0) / REPLAY_DT));

  const gx = new Float32Array(K + 1);
  const gz = new Float32Array(K + 1);
  for (let k = 0; k <= K; k++) {
    const t = Math.min(t1, Math.max(t0, (q0 + k) * REPLAY_DT));
    if (!sampleInto(pts, t, _uv)) return null;
    projectInto(frame, _uv[0], _uv[1], _g);
    gx[k] = _g[0];
    gz[k] = _g[1];
  }

  // Heading from the ground velocity; a stopped car keeps its last known heading.
  const heading = new Float32Array(K + 1).fill(NaN);
  for (let k = 0; k <= K; k++) {
    const a = Math.max(0, k - 2);
    const b = Math.min(K, k + 2);
    const dx = gx[b] - gx[a];
    const dz = gz[b] - gz[a];
    if (Math.hypot(dx, dz) / ((b - a) * REPLAY_DT) > 1) heading[k] = Math.atan2(dx, dz);
  }
  for (let k = 1; k <= K; k++) if (Number.isNaN(heading[k])) heading[k] = heading[k - 1];
  for (let k = K - 1; k >= 0; k--) if (Number.isNaN(heading[k])) heading[k] = heading[k + 1];

  // Pull every sample onto its nearest lane; the lane it was on last breaks ties so it does not flicker.
  const rp: XZ[] = [];
  const raw = new Float32Array(K + 1);
  let u = 0;
  let prefer = -1;
  let firstLane = -1;
  let firstS = 0;
  let lastLane = -1;
  let lastS = 0;
  let firstHeading = 0;
  for (let k = 0; k <= K; k++) {
    const yaw = Number.isNaN(heading[k]) ? Math.atan2(gx[k] - frame.x, gz[k] - frame.z) : heading[k];
    bodyCentreInto(frame, gx[k], gz[k], yaw, inp.length, inp.width, _c);
    const found =
      snapVehicle(idx, _c[0], _c[1], heading[k], _vs, VEHICLE_REACH_M, prefer, inp.bus) ||
      (inp.bus && snapVehicle(idx, _c[0], _c[1], heading[k], _vs, VEHICLE_REACH_M, prefer)) ||
      snapVehicle(idx, _c[0], _c[1], NaN, _vs, VEHICLE_REACH_M, prefer);
    if (!found) {
      raw[k] = u;
      continue;
    }
    prefer = _vs.laneId;
    if (rp.length === 0) {
      rp.push([_vs.x, _vs.z]);
      firstLane = lastLane = _vs.laneId;
      firstS = lastS = _vs.s;
      firstHeading = _vs.heading;
    } else {
      const last = rp[rp.length - 1];
      const d = Math.hypot(_vs.x - last[0], _vs.z - last[1]);
      const sameLane = _vs.laneId === lastLane;
      const ahead = sameLane ? ((_vs.laneId & 1) === 1 ? _vs.s < lastS : _vs.s > lastS) : true;
      if (ahead && d > 0.02) {
        rp.push([_vs.x, _vs.z]);
        u += d;
        lastLane = _vs.laneId;
        lastS = _vs.s;
      }
    }
    raw[k] = u;
  }
  if (rp.length === 0) return null;

  // Easing the odometer keeps a jittery box bottom from jolting the car.
  const us = new Float32Array(K + 1);
  for (let k = 1; k <= K; k++) us[k] = us[k - 1] + (raw[k] - us[k - 1]) * 0.5;

  const cull = (p: XZ, extra = 0) => Math.hypot(p[0] - frame.x, p[1] - frame.z) > CULL_M + extra;
  const upstream = walkPath(idx, firstLane, firstS, EXT_M, false);
  const pre: XZ[] = [];
  if (upstream) {
    for (let i = upstream.pts.length - 2; i >= 0; i--) {
      pre.unshift(upstream.pts[i]);
      if (cull(upstream.pts[i])) break;
    }
  }
  const downstream = walkPath(idx, lastLane, lastS, EXT_M, true);
  const post: XZ[] = [];
  if (downstream) {
    for (let i = 1; i < downstream.pts.length; i++) {
      post.push(downstream.pts[i]);
      if (cull(downstream.pts[i], 30)) break;
    }
  }
  const poly: XZ[] = [...pre, ...rp, ...post];
  if (poly.length < 2) poly.push([rp[0][0] + Math.sin(firstHeading) * 0.5, rp[0][1] + Math.cos(firstHeading) * 0.5]);
  const cum = [0];
  for (let i = 1; i < poly.length; i++) cum.push(cum[i - 1] + Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]));
  const path: Path = { pts: poly, cum, length: cum[cum.length - 1] };

  const offset = cum[pre.length];
  const uEnd = offset + us[K];
  const k5 = Math.min(K, 5);
  const v0 = (us[k5] - us[0]) / (k5 * REPLAY_DT);
  const kb = Math.max(0, K - 5);
  const vL = (us[K] - us[kb]) / ((K - kb) * REPLAY_DT);

  // Desired odometer per step: constant approach speed easing into the first tracked speed, the track itself, then easing out at the last speed.
  const preU: number[] = [];
  for (let m = 1, acc = 0; acc < offset && m < 400; m++) {
    acc += (v0 + (Math.max(v0, APPROACH_MPS) - v0) * Math.min(1, (m * REPLAY_DT) / 3)) * REPLAY_DT;
    preU.push(Math.max(0, offset - acc));
  }
  const postU: number[] = [];
  for (let m = 1, acc = 0; uEnd + acc < path.length && m < 700; m++) {
    acc += (vL + (Math.max(vL, DEPART_MPS) - vL) * Math.min(1, (m * REPLAY_DT) / 2.5)) * REPLAY_DT;
    postU.push(Math.min(path.length, uEnd + acc));
  }
  const ud = new Float32Array(preU.length + K + 1 + postU.length);
  let w = 0;
  for (let i = preU.length - 1; i >= 0; i--) ud[w++] = preU[i];
  for (let k = 0; k <= K; k++) ud[w++] = offset + us[k];
  for (const v of postU) ud[w++] = v;
  return { inp, path, jStart: q0 - preU.length, ud, hl: inp.length / 2, hw: inp.width / 2 };
}

function poseAt(path: Path, u: number, out: Float32Array) {
  pathAt(path, u, _p);
  out[0] = _p[0];
  out[1] = _p[1];
  pathAt(path, u - 1.2, _p);
  const ax = _p[0];
  const az = _p[1];
  const yaw = pathAt(path, u + 1.2, _p);
  out[2] = Math.hypot(_p[0] - ax, _p[1] - az) > 0.2 ? Math.atan2(_p[0] - ax, _p[1] - az) : yaw;
}

/** Separating-axis test on two oriented rectangles (yaw from +z toward +x; `h` half width, `l` half length). */
function overlap(
  ax: number, az: number, ayaw: number, ah: number, al: number,
  bx: number, bz: number, byaw: number, bh: number, bl: number,
): boolean {
  const asn = Math.sin(ayaw);
  const acs = Math.cos(ayaw);
  const bsn = Math.sin(byaw);
  const bcs = Math.cos(byaw);
  const dx = bx - ax;
  const dz = bz - az;
  for (let t = 0; t < 4; t++) {
    const ux = t === 0 ? asn : t === 1 ? acs : t === 2 ? bsn : bcs;
    const uz = t === 0 ? acs : t === 1 ? -asn : t === 2 ? bcs : -bsn;
    const ra = al * Math.abs(asn * ux + acs * uz) + ah * Math.abs(acs * ux - asn * uz);
    const rb = bl * Math.abs(bsn * ux + bcs * uz) + bh * Math.abs(bcs * ux - bsn * uz);
    if (Math.abs(dx * ux + dz * uz) > ra + rb) return false;
  }
  return true;
}

/**
 * True when two bodies (full length and width, yaw from +z toward +x) sit closer than the minimum
 * bumper gap, or, at a junction, when their centres are not clear of each other by half their lengths plus 1 m.
 */
export function bodiesClash(
  ax: number, az: number, ayaw: number, al: number, aw: number,
  bx: number, bz: number, byaw: number, bl: number, bw: number,
  atJunction: boolean,
): boolean {
  const gap = MIN_GAP_M + GAP_PER_LENGTH * (al + bl);
  const dx = bx - ax;
  const dz = bz - az;
  const reach = (al + bl) / 2 + gap + 2;
  if (dx * dx + dz * dz > reach * reach) return false;
  if (overlap(ax, az, ayaw, aw / 2 + SIDE_PAD_M, al / 2 + gap / 2, bx, bz, byaw, bw / 2 + SIDE_PAD_M, bl / 2 + gap / 2)) return true;
  return atJunction && Math.hypot(dx, dz) < (al + bl) / 2 + 1;
}

const NODE_CELL = 16;
const nodeKey = (cx: number, cz: number) => (cx + 4096) * 8192 + (cz + 4096);

const junctionLookups = new WeakMap<SnapIndex, (x: number, z: number) => boolean>();

/** True within a junction's asphalt disc plus a margin, where bodies from different roads meet. */
export function junctionLookup(idx: SnapIndex) {
  const found = junctionLookups.get(idx);
  if (found) return found;
  const radius = layoutOf(idx.net).nodeRadius;
  const cells = new Map<number, number[]>();
  for (const n of idx.net.nodes) {
    if (!n.junction) continue;
    const k = nodeKey(Math.floor(n.x / NODE_CELL), Math.floor(n.z / NODE_CELL));
    const list = cells.get(k);
    if (list) list.push(n.id);
    else cells.set(k, [n.id]);
  }
  const lookup = (x: number, z: number) => {
    const cx = Math.floor(x / NODE_CELL);
    const cz = Math.floor(z / NODE_CELL);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        const list = cells.get(nodeKey(cx + dx, cz + dz));
        if (!list) continue;
        for (const id of list) {
          const n = idx.net.nodes[id];
          if (Math.hypot(x - n.x, z - n.z) < radius[id] + JUNCTION_PAD_M) return true;
        }
      }
    }
    return false;
  };
  junctionLookups.set(idx, lookup);
  return lookup;
}

export function buildReplay(idx: SnapIndex, inputs: ReplayInput[]): ReplaySet {
  const plans: Plan[] = [];
  for (const inp of inputs) {
    const p = plan(idx, inp);
    if (p) plans.push(p);
  }
  const n = plans.length;
  const nearJunction = junctionLookup(idx);

  const ua = new Float64Array(n);
  /** 0 = waiting to spawn, 1 = driving, 2 = gone. */
  const state = new Uint8Array(n);
  const cx = new Float64Array(n);
  const cz = new Float64Array(n);
  const cyaw = new Float64Array(n);
  const cj = new Uint8Array(n);
  const spawn = new Int32Array(n).fill(0);
  const ox: number[][] = plans.map(() => []);
  const oz: number[][] = plans.map(() => []);
  const oyaw: number[][] = plans.map(() => []);
  const live: number[] = [];
  const pose = new Float32Array(3);

  let jMin = Infinity;
  let jMax = -Infinity;
  for (const p of plans) {
    jMin = Math.min(jMin, p.jStart);
    jMax = Math.max(jMax, p.jStart + p.ud.length);
  }

  const blocked = (i: number, x: number, z: number, yaw: number): boolean => {
    const a = plans[i];
    const jx = nearJunction(x, z);
    for (const o of live) {
      if (o === i) continue;
      const b = plans[o];
      if (bodiesClash(x, z, yaw, a.inp.length, a.inp.width, cx[o], cz[o], cyaw[o], b.inp.length, b.inp.width, jx || cj[o] === 1)) return true;
    }
    return false;
  };

  const record = (i: number) => {
    ox[i].push(cx[i]);
    oz[i].push(cz[i]);
    oyaw[i].push(cyaw[i]);
  };

  const place = (i: number, u: number) => {
    poseAt(plans[i].path, u, pose);
    cx[i] = pose[0];
    cz[i] = pose[1];
    cyaw[i] = pose[2];
    cj[i] = nearJunction(pose[0], pose[1]) ? 1 : 0;
    ua[i] = u;
  };

  for (let j = jMin; j < jMax + 300; j++) {
    for (let i = 0; i < n; i++) {
      const p = plans[i];
      if (state[i] === 2 || j < p.jStart) continue;
      const at = j - p.jStart;
      const target = at < p.ud.length ? p.ud[at] : p.path.length;
      if (state[i] === 0) {
        // Spawn on the desired spot, or queued up behind whatever is in the way; failing that, wait for it to clear.
        const free = SPAWN_BACK_M.find((back) => {
          if (target - back < 0) return false;
          poseAt(p.path, target - back, pose);
          return !blocked(i, pose[0], pose[1], pose[2]);
        });
        if (free !== undefined) {
          place(i, target - free);
          state[i] = 1;
          spawn[i] = j;
          live.push(i);
          record(i);
        } else if (at > p.ud.length + 50) state[i] = 2;
        continue;
      }
      const prevTarget = at > 0 && at - 1 < p.ud.length ? p.ud[at - 1] : target;
      const lag = target - ua[i];
      // Holds catch up faster than the clip, so a car released from a queue does not trail it forever.
      const step = Math.min(lag, Math.max(1.35 * ((target - prevTarget) / REPLAY_DT), 2.5) * REPLAY_DT);
      const ox0 = cx[i];
      const oz0 = cz[i];
      const oyaw0 = cyaw[i];
      const oj0 = cj[i];
      const oua = ua[i];
      let moved = false;
      for (const f of [1, 0.6, 0.3]) {
        if (step * f < 1e-4) break;
        poseAt(p.path, oua + step * f, pose);
        if (!blocked(i, pose[0], pose[1], pose[2])) {
          place(i, oua + step * f);
          moved = true;
          break;
        }
      }
      if (!moved) {
        cx[i] = ox0;
        cz[i] = oz0;
        cyaw[i] = oyaw0;
        cj[i] = oj0;
        ua[i] = oua;
      }
      record(i);
      if (ua[i] >= p.path.length - 0.05) {
        state[i] = 2;
        live.splice(live.indexOf(i), 1);
      }
    }
    if (j > jMax && live.length === 0) break;
  }

  const byKey = new Map<string, Traj>();
  let tMin = Infinity;
  let tMax = -Infinity;
  for (let i = 0; i < n; i++) {
    if (ox[i].length === 0) continue;
    const p = plans[i];
    byKey.set(p.inp.key, {
      j0: spawn[i],
      n: ox[i].length,
      x: Float32Array.from(ox[i]),
      z: Float32Array.from(oz[i]),
      yaw: Float32Array.from(oyaw[i]),
      length: p.inp.length,
      width: p.inp.width,
      camX: p.inp.frame.x,
      camZ: p.inp.frame.z,
    });
    tMin = Math.min(tMin, spawn[i] * REPLAY_DT);
    tMax = Math.max(tMax, (spawn[i] + ox[i].length - 1) * REPLAY_DT);
  }
  return { tMin: Number.isFinite(tMin) ? tMin : 0, cycle: Math.max(1, tMax - tMin + 1), byKey };
}

/** Pose at clip time t, or false before the vehicle has spawned. After its table ends it holds the last pose. */
export function sampleTraj(tr: Traj, t: number, out: Float32Array): boolean {
  const f = t / REPLAY_DT - tr.j0;
  if (f < 0) return false;
  const i = Math.min(tr.n - 1, Math.floor(f));
  const k = Math.min(1, f - i);
  const i1 = Math.min(tr.n - 1, i + 1);
  out[0] = tr.x[i] + (tr.x[i1] - tr.x[i]) * k;
  out[1] = tr.z[i] + (tr.z[i1] - tr.z[i]) * k;
  let d = tr.yaw[i1] - tr.yaw[i];
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d < -Math.PI) d += 2 * Math.PI;
  out[2] = tr.yaw[i] + d * k;
  return true;
}

/** True once the vehicle has reached the end of its route, so it is free to be retired out of sight. */
export function trajEnded(tr: Traj, t: number): boolean {
  return t / REPLAY_DT - tr.j0 > tr.n - 1;
}
