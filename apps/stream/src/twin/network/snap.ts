import { useMemo } from "react";
import { useGeometryLoader } from "../scene/useGeometry";
import { layoutOf, polyCum, segmentAt } from "./layout";
import { getNetwork } from "./useNetwork";
import type { RoadNetwork, XZ } from "./types";

/**
 * Grid hash over the network's lane centre-lines and footways so real (YOLO / TfL) entities can be
 * pulled onto the roads and pavements the traffic sim already drives on. Built once per network;
 * queries write into scratch outputs and allocate nothing.
 *
 * Lanes follow the traffic sim's convention: lane id = edge * 2 (from→to) or edge * 2 + 1 (to→from),
 * offset `laneLateral(width)` metres to the LEFT of travel (UK).
 */

const CELL = 16;
export const VEHICLE_SNAP_M = 8;
/** Furthest a vehicle is pulled onto the carriageway: the road grid is padded by this much. */
export const VEHICLE_REACH_M = 24;
export const PERSON_SNAP_M = 6;
export const ON_LANE_M = 1.5;

export const laneLateral = (width: number) => Math.min(2, Math.max(1.1, width * 0.25));

export interface SnapIndex {
  net: RoadNetwork;
  /** Road centre-line segments: [ax, az, bx, bz] per segment. */
  seg: Float32Array;
  segEdge: Int32Array;
  /** Cumulative distance at the segment start along its edge. */
  segS0: Float32Array;
  /** Footway segments. */
  fseg: Float32Array;
  fsegFoot: Int32Array;
  fsegS0: Float32Array;
  cells: Map<number, number[]>;
  fcells: Map<number, number[]>;
}

export interface VehicleSnap {
  x: number;
  z: number;
  /** atan2(dx, dz), same convention as the agents' yaw. */
  heading: number;
  laneId: number;
  /** Distance along the edge (from its `from` node) of the projected point. */
  s: number;
  /** Distance from the input to the lane line, metres. */
  dist: number;
}

const indices = new WeakMap<RoadNetwork, SnapIndex>();

const key = (cx: number, cz: number) => (cx + 4096) * 8192 + (cz + 4096);

function addSeg(cells: Map<number, number[]>, ref: number, ax: number, az: number, bx: number, bz: number, pad: number) {
  const x0 = Math.floor((Math.min(ax, bx) - pad) / CELL);
  const x1 = Math.floor((Math.max(ax, bx) + pad) / CELL);
  const z0 = Math.floor((Math.min(az, bz) - pad) / CELL);
  const z1 = Math.floor((Math.max(az, bz) + pad) / CELL);
  for (let cx = x0; cx <= x1; cx++) {
    for (let cz = z0; cz <= z1; cz++) {
      const k = key(cx, cz);
      const list = cells.get(k);
      if (list) list.push(ref);
      else cells.set(k, [ref]);
    }
  }
}

function build(net: RoadNetwork): SnapIndex {
  const seg: number[] = [];
  const segEdge: number[] = [];
  const segS0: number[] = [];
  const cells = new Map<number, number[]>();
  for (const e of net.edges) {
    for (let i = 0; i < e.pts.length - 1; i++) {
      const ref = segEdge.length;
      seg.push(e.pts[i][0], e.pts[i][1], e.pts[i + 1][0], e.pts[i + 1][1]);
      segEdge.push(e.id);
      segS0.push(e.cum[i]);
      addSeg(cells, ref, e.pts[i][0], e.pts[i][1], e.pts[i + 1][0], e.pts[i + 1][1], VEHICLE_REACH_M);
    }
  }
  const fseg: number[] = [];
  const fsegFoot: number[] = [];
  const fsegS0: number[] = [];
  const fcells = new Map<number, number[]>();
  for (const f of net.footways) {
    for (let i = 0; i < f.pts.length - 1; i++) {
      const ref = fsegFoot.length;
      fseg.push(f.pts[i][0], f.pts[i][1], f.pts[i + 1][0], f.pts[i + 1][1]);
      fsegFoot.push(f.id);
      fsegS0.push(f.cum[i]);
      addSeg(fcells, ref, f.pts[i][0], f.pts[i][1], f.pts[i + 1][0], f.pts[i + 1][1], PERSON_SNAP_M);
    }
  }
  return {
    net,
    seg: Float32Array.from(seg),
    segEdge: Int32Array.from(segEdge),
    segS0: Float32Array.from(segS0),
    fseg: Float32Array.from(fseg),
    fsegFoot: Int32Array.from(fsegFoot),
    fsegS0: Float32Array.from(fsegS0),
    cells,
    fcells,
  };
}

export function snapIndexOf(net: RoadNetwork): SnapIndex {
  let idx = indices.get(net);
  if (!idx) {
    idx = build(net);
    indices.set(net, idx);
  }
  return idx;
}

/**
 * Nearest lane within `maxM` whose direction agrees with `heading` (NaN = unknown: the lane on the
 * point's own side wins, which is the keep-left lane for a vehicle already on the road). Writes the
 * pose on the lane line into `out`; returns false when nothing is close enough. `prefer` is a lane id
 * that wins ties against a lane up to ~3x further away (stops a track flickering between lanes);
 * `majorOnly` skips minor streets (buses).
 */
export function snapVehicle(
  idx: SnapIndex,
  x: number,
  z: number,
  heading: number,
  out: VehicleSnap,
  maxM = VEHICLE_SNAP_M,
  prefer = -1,
  majorOnly = false,
): boolean {
  const list = idx.cells.get(key(Math.floor(x / CELL), Math.floor(z / CELL)));
  if (!list) return false;
  const hx = Number.isNaN(heading) ? 0 : Math.sin(heading);
  const hz = Number.isNaN(heading) ? 0 : Math.cos(heading);
  const known = !Number.isNaN(heading);
  let best = maxM * maxM;
  let found = false;
  const { seg, segEdge, segS0, net } = idx;
  for (let n = 0; n < list.length; n++) {
    const r = list[n];
    const ax = seg[r * 4];
    const az = seg[r * 4 + 1];
    const vx = seg[r * 4 + 2] - ax;
    const vz = seg[r * 4 + 3] - az;
    const len2 = vx * vx + vz * vz;
    if (len2 < 1e-6) continue;
    const len = Math.sqrt(len2);
    const tx = vx / len;
    const tz = vz / len;
    let t = ((x - ax) * tx + (z - az) * tz) / len;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const px = ax + vx * t;
    const pz = az + vz * t;
    // Left of travel (from→to) is (tz, -tx), matching RoadNetwork.sample.
    const side = (x - px) * tz - (z - pz) * tx;
    const edge = net.edges[segEdge[r]];
    if (majorOnly && edge.kind !== "major") continue;
    const lat = laneLateral(edge.width);
    const dot = tx * hx + tz * hz;
    let rev: boolean;
    if (edge.oneway) rev = false;
    else if (known) rev = dot < 0;
    else rev = side < 0;
    if (known && !edge.oneway && Math.abs(dot) < 0.3) continue;
    const lx = px + (rev ? -tz : tz) * lat;
    const lz = pz + (rev ? tx : -tx) * lat;
    const dx = x - lx;
    const dz = z - lz;
    const dist2 = dx * dx + dz * dz;
    const d2 = edge.id * 2 + (rev ? 1 : 0) === prefer ? dist2 * 0.3 : dist2;
    if (d2 >= best || dist2 >= maxM * maxM) continue;
    best = d2;
    found = true;
    out.x = lx;
    out.z = lz;
    out.heading = Math.atan2(rev ? -tx : tx, rev ? -tz : tz);
    out.laneId = edge.id * 2 + (rev ? 1 : 0);
    out.s = segS0[r] + t * len;
    out.dist = Math.sqrt(dist2);
  }
  return found;
}

/**
 * How far inside the drawn asphalt (x, z) is, in metres; negative when it is off the carriageway.
 * Best of every nearby road strip (half width minus distance to its centre line) and junction disc.
 */
export function asphaltMargin(idx: SnapIndex, x: number, z: number): number {
  let best = -Infinity;
  const list = idx.cells.get(key(Math.floor(x / CELL), Math.floor(z / CELL)));
  const { seg, segEdge, net } = idx;
  for (let n = 0; list && n < list.length; n++) {
    const r = list[n];
    const ax = seg[r * 4];
    const az = seg[r * 4 + 1];
    const vx = seg[r * 4 + 2] - ax;
    const vz = seg[r * 4 + 3] - az;
    const len2 = vx * vx + vz * vz;
    if (len2 < 1e-6) continue;
    const t = Math.min(1, Math.max(0, ((x - ax) * vx + (z - az) * vz) / len2));
    const margin = net.edges[segEdge[r]].width / 2 - Math.hypot(x - ax - vx * t, z - az - vz * t);
    if (margin > best) best = margin;
  }
  const radius = layoutOf(net).nodeRadius;
  for (const node of net.nodes) {
    const margin = radius[node.id] - Math.hypot(x - node.x, z - node.z);
    if (margin > best) best = margin;
  }
  return best;
}

export interface PersonSnap {
  x: number;
  z: number;
  footwayId: number;
  s: number;
  dist: number;
}

/** Nearest footway point within `maxM`; writes into `out`, returns false when none is close. */
export function snapPerson(idx: SnapIndex, x: number, z: number, out: PersonSnap, maxM = PERSON_SNAP_M): boolean {
  const list = idx.fcells.get(key(Math.floor(x / CELL), Math.floor(z / CELL)));
  if (!list) return false;
  let best = maxM * maxM;
  let found = false;
  const { fseg, fsegFoot, fsegS0 } = idx;
  for (let n = 0; n < list.length; n++) {
    const r = list[n];
    const ax = fseg[r * 4];
    const az = fseg[r * 4 + 1];
    const vx = fseg[r * 4 + 2] - ax;
    const vz = fseg[r * 4 + 3] - az;
    const len2 = vx * vx + vz * vz;
    if (len2 < 1e-6) continue;
    let t = ((x - ax) * vx + (z - az) * vz) / len2;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const px = ax + vx * t;
    const pz = az + vz * t;
    const dx = x - px;
    const dz = z - pz;
    const d2 = dx * dx + dz * dz;
    if (d2 >= best) continue;
    best = d2;
    found = true;
    out.x = px;
    out.z = pz;
    out.footwayId = fsegFoot[r];
    out.s = fsegS0[r] + t * Math.sqrt(len2);
    out.dist = Math.sqrt(d2);
  }
  return found;
}

export interface Path {
  pts: XZ[];
  cum: number[];
  length: number;
}

/** Metres over which a stopping bus eases from its lane line to the kerb-side edge of the carriageway. */
const KERB_BLEND_M = 30;
/** Gap kept between a stopped bus's near side and the kerb. */
const KERB_INSET_M = 1.4;

/**
 * Lane polyline walked from (laneId, s) through the network keep-left. `forward` follows the lane
 * downstream (at each node the exit with the smallest turn); otherwise it traces upstream (the
 * straightest incoming lane). Points come out in the direction of travel, so a backward walk ends at
 * the anchor. With `kerb` the anchor end sits on the kerb-side lane edge instead of the lane line
 * (a bus at its stop), easing to the lane line over KERB_BLEND_M. Null when it yields fewer than two points.
 */
export function walkPath(idx: SnapIndex, laneId: number, s0: number, metres: number, forward: boolean, kerb = false): Path | null {
  const net = idx.net;
  const out: XZ[] = [];
  let edge = net.edges[laneId >> 1];
  let rev = (laneId & 1) === 1;
  let s = s0;
  let left = metres;
  const p: XZ = [0, 0];
  for (let guard = 0; guard < 24 && left > 0; guard++) {
    const lat = laneLateral(edge.width);
    const kerbLat = Math.max(lat, edge.width / 2 - KERB_INSET_M);
    // Direction of increasing s along the walk: downstream follows the lane's travel, upstream opposes it.
    const dir = forward !== rev ? 1 : -1;
    const end = dir > 0 ? edge.length : 0;
    const span = Math.min(left, Math.abs(end - s));
    const steps = Math.max(1, Math.ceil(span / 4));
    let tangent = 0;
    for (let i = 0; i <= steps; i++) {
      const along = (span * i) / steps;
      const k = kerb ? Math.min(1, (metres - left + along) / KERB_BLEND_M) : 1;
      const mag = kerbLat + (lat - kerbLat) * (k * k * (3 - 2 * k));
      tangent = net.sample(edge.id, s + dir * along, rev ? -mag : mag, p);
      out.push([p[0], p[1]]);
    }
    left -= span;
    if (left <= 0) break;
    const travel = rev ? tangent + Math.PI : tangent;
    const node = net.nodes[dir > 0 ? edge.to : edge.from];
    let next = edge;
    let nextRev = rev;
    let bestScore = -0.5;
    for (const eid of node.edges) {
      const cand = net.edges[eid];
      if (cand.id === edge.id) continue;
      // Downstream we leave along cand (to→from when it ends here); upstream we arrive along it (to→from when it starts here).
      const candRev = forward ? cand.to === node.id : cand.from === node.id;
      if (candRev && cand.oneway) continue;
      const eps = Math.min(0.5, cand.length * 0.4);
      const h = net.sample(cand.id, (forward ? candRev : !candRev) ? cand.length - eps : eps, 0, p);
      const score = Math.cos((candRev ? h + Math.PI : h) - travel);
      if (score > bestScore) {
        bestScore = score;
        next = cand;
        nextRev = candRev;
      }
    }
    if (next === edge) break;
    edge = next;
    rev = nextRev;
    s = forward === rev ? edge.length : 0;
  }
  if (out.length < 2) return null;
  if (!forward) out.reverse();
  const cum = polyCum(out);
  return { pts: out, cum, length: cum[cum.length - 1] };
}

/** Point and heading at `dist` along a path, written into `out`. */
export function pathAt(path: Path, dist: number, out: XZ): number {
  const d = Math.min(Math.max(dist, 0), path.length);
  const s = segmentAt(path.cum, d);
  const dx = path.pts[s + 1][0] - path.pts[s][0];
  const dz = path.pts[s + 1][1] - path.pts[s][1];
  const t = (d - path.cum[s]) / (path.cum[s + 1] - path.cum[s] || 1);
  out[0] = path.pts[s][0] + dx * t;
  out[1] = path.pts[s][1] + dz * t;
  return Math.atan2(dx, dz);
}

/** `?snap=0` turns snapping off for side-by-side comparison. */
export const SNAP_ENABLED = typeof window === "undefined" || new URLSearchParams(window.location.search).get("snap") !== "0";
export const SNAP_STATS = typeof window !== "undefined" && new URLSearchParams(window.location.search).has("snapstats");

/** Snap index for the current origin's network, or null while its geometry is still loading. */
export function useSnapIndex(): SnapIndex | null {
  const geometry = useGeometryLoader();
  return useMemo(() => (geometry.status === "ready" ? snapIndexOf(getNetwork(geometry.data)) : null), [geometry]);
}
