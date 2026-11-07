import { coveredByHeroLandmark } from "../landmarks";
import type { RoadEdge, RoadNode, RoadNetwork, XZ } from "./types";

/**
 * Heights (metres) of every drawn surface. Layers stack: pavement slab, kerb casing, asphalt, paint.
 * Everything is flat, so there is nothing to z-fight and no step at bridges or junctions.
 */
export const PAVEMENT_Y = 0.22;
export const KERB_Y = 0.26;
export const ROAD_Y = 0.3;
export const MARK_Y = 0.34;
export const PATH_Y = 0.2;

export const PAVE_W = 2.8;
export const KERB_W = 0.3;
/** Lateral distance from the road edge to the pedestrian line on a pavement. */
export const WALK_OFF = 1.4;
export const PATH_W = 1.8;

export function polyCum(pts: XZ[]): number[] {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  }
  return cum;
}

/** Index of the segment containing `dist` (binary search over cumulative lengths). */
export function segmentAt(cum: number[], dist: number): number {
  let lo = 0;
  let hi = cum.length - 2;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (cum[mid] <= dist) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

export function pointAt(pts: XZ[], cum: number[], dist: number): XZ {
  const s = segmentAt(cum, dist);
  const len = cum[s + 1] - cum[s] || 1;
  const t = Math.min(1, Math.max(0, (dist - cum[s]) / len));
  return [pts[s][0] + (pts[s + 1][0] - pts[s][0]) * t, pts[s][1] + (pts[s + 1][1] - pts[s][1]) * t];
}

/** Points of `pts` between cumulative distances s0 and s1 (both ends interpolated). */
export function subPolyline(pts: XZ[], cum: number[], s0: number, s1: number): XZ[] {
  const out: XZ[] = [pointAt(pts, cum, s0)];
  for (let i = 1; i < pts.length - 1; i++) if (cum[i] > s0 + 0.05 && cum[i] < s1 - 0.05) out.push(pts[i]);
  out.push(pointAt(pts, cum, s1));
  return out;
}

/**
 * Offset a polyline sideways by `d` metres (positive = left of travel, negative = right). One output
 * point per input point, mitred at the bends with a limited miter so sharp corners don't spike.
 */
export function offsetPolyline(pts: XZ[], d: number): XZ[] {
  const n = pts.length;
  const nx: number[] = [];
  const nz: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = pts[i + 1][0] - pts[i][0];
    const dz = pts[i + 1][1] - pts[i][1];
    const l = Math.hypot(dx, dz) || 1;
    nx.push(dz / l);
    nz.push(-dx / l);
  }
  const out: XZ[] = [];
  for (let i = 0; i < n; i++) {
    let ax: number;
    let az: number;
    let scale = 1;
    if (i === 0) {
      ax = nx[0];
      az = nz[0];
    } else if (i === n - 1) {
      ax = nx[n - 2];
      az = nz[n - 2];
    } else {
      const mx = nx[i - 1] + nx[i];
      const mz = nz[i - 1] + nz[i];
      const ml = Math.hypot(mx, mz);
      if (ml < 1e-6) {
        ax = nx[i];
        az = nz[i];
      } else {
        ax = mx / ml;
        az = mz / ml;
        scale = 1 / Math.max(0.55, ax * nx[i] + az * nz[i]);
      }
    }
    out.push([pts[i][0] + ax * d * scale, pts[i][1] + az * d * scale]);
  }
  return out;
}

/** Drop offset points that fold back against the original direction (inside of a tight bend). Keeps both ends. */
export function despike(off: XZ[], orig: XZ[]): XZ[] {
  const out: XZ[] = [off[0]];
  let last = 0;
  for (let i = 1; i < off.length; i++) {
    const dot =
      (off[i][0] - off[last][0]) * (orig[i][0] - orig[last][0]) +
      (off[i][1] - off[last][1]) * (orig[i][1] - orig[last][1]);
    if (dot > 0 || i === off.length - 1) {
      out.push(off[i]);
      last = i;
    }
  }
  return out;
}

export interface EdgeStrip {
  /** Centre line with the junction mouths cut away (where the zebra crossings sit). */
  pts: XZ[];
  cum: number[];
  length: number;
}

export interface JunctionItem {
  edge: number;
  atStart: boolean;
  /** Direction away from the node, atan2(dz, dx). Increasing = clockwise seen from above. */
  theta: number;
  /** Zebra crossing across this edge's mouth. */
  crossing: boolean;
}

export interface Layout {
  strips: (EdgeStrip | null)[];
  /** Per edge: its midpoint lies on the hero Tower Bridge, which draws its own deck, so skip drawing it (the graph keeps the edge). */
  hidden: boolean[];
  /** Per node, incident edges sorted clockwise. */
  items: JunctionItem[][];
  /** Radius of the asphalt disc that rounds each junction, bend and dead end (0 on a straight join). */
  nodeRadius: number[];
  /** Footway ids of OSM pedestrian paths (filled in by buildNetwork). */
  pathIds: number[];
}

const layouts = new WeakMap<object, Layout>();

export function layoutOf(net: RoadNetwork): Layout {
  const found = layouts.get(net);
  if (found) return found;
  const made = computeLayout(net.nodes, net.edges);
  layouts.set(net, made);
  return made;
}

export function registerLayout(net: RoadNetwork, layout: Layout) {
  layouts.set(net, layout);
}

function hash01(a: number, b: number): number {
  let h = Math.imul(a + 374761393, 668265263) ^ Math.imul(b + 2246822519, 3266489917);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** True when the midpoint of the polyline sits inside the hero Tower Bridge footprint. */
export function underHeroLandmark(pts: XZ[], cum: number[]): boolean {
  return coveredByHeroLandmark([pointAt(pts, cum, cum[cum.length - 1] / 2)]);
}

export function computeLayout(nodes: RoadNode[], edges: RoadEdge[]): Layout {
  const lists: { edge: RoadEdge; atStart: boolean; theta: number }[][] = nodes.map(() => []);
  for (const edge of edges) {
    for (const atStart of [true, false]) {
      const node = atStart ? edge.from : edge.to;
      const n = edge.pts.length;
      const origin = atStart ? edge.pts[0] : edge.pts[n - 1];
      let theta = 0;
      for (let k = 1; k < n; k++) {
        const p = atStart ? edge.pts[k] : edge.pts[n - 1 - k];
        const dx = p[0] - origin[0];
        const dz = p[1] - origin[1];
        if (Math.hypot(dx, dz) >= 1.5 || k === n - 1) {
          theta = Math.atan2(dz, dx);
          break;
        }
      }
      lists[node].push({ edge, atStart, theta });
    }
  }

  const nodeRadius = nodes.map((_, i) => {
    const list = lists[i];
    if (!list.length) return 0;
    const half = Math.max(...list.map((l) => l.edge.width)) / 2;
    if (list.length === 1) return half;
    if (list.length >= 3) return half + 0.5;
    const apart = Math.abs(list[0].theta - list[1].theta);
    const deflection = Math.PI - Math.min(apart, 2 * Math.PI - apart);
    return deflection < 0.09 ? 0 : Math.min(...list.map((l) => l.edge.width)) / 2;
  });

  const trimStart = new Array<number>(edges.length).fill(0);
  const trimEnd = new Array<number>(edges.length).fill(0);
  lists.forEach((list, i) => {
    if (list.length < 3) return;
    for (const it of list) (it.atStart ? trimStart : trimEnd)[it.edge.id] = nodeRadius[i] + 2.5;
  });
  edges.forEach((edge, i) => {
    const sum = trimStart[i] + trimEnd[i];
    if (sum > edge.length * 0.85) {
      trimStart[i] *= (edge.length * 0.85) / sum;
      trimEnd[i] *= (edge.length * 0.85) / sum;
    }
  });

  const strips = edges.map((edge, i): EdgeStrip | null => {
    const s0 = trimStart[i];
    const s1 = edge.length - trimEnd[i];
    if (s1 - s0 < 0.3) return null;
    const pts = subPolyline(edge.pts, edge.cum, s0, s1);
    return { pts, cum: polyCum(pts), length: s1 - s0 };
  });

  const items: JunctionItem[][] = lists.map((list, nodeId) => {
    list.sort((a, b) => a.theta - b.theta);
    return list
      .filter((it) => strips[it.edge.id])
      .map((it) => ({
        edge: it.edge.id,
        atStart: it.atStart,
        theta: it.theta,
        crossing:
          list.length >= 3 && it.edge.length > (trimStart[it.edge.id] + trimEnd[it.edge.id]) + 8 && hash01(nodeId, it.edge.id) < 0.55,
      }));
  });

  const hidden = edges.map((edge) => underHeroLandmark(edge.pts, edge.cum));

  return { strips, hidden, items, nodeRadius, pathIds: [] };
}
