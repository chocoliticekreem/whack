import type { TwinGeometry } from "../../config";
import { KERB_W, layoutOf } from "../../network/layout";
import type { RoadNetwork, XZ } from "../../network/types";
import type { P2 } from "./footprint";

type Building = TwinGeometry["buildings"][number];

interface Segment {
  ax: number;
  az: number;
  bx: number;
  bz: number;
  /** Half the carriageway plus the kerb: nothing solid may stand inside this. */
  half: number;
}

export interface RoadHit {
  /** How far the query point sits inside the carriageway (metres, > 0 = inside). */
  depth: number;
  /** Closest point on the road centre line. */
  px: number;
  pz: number;
  distance: number;
  half: number;
  nx: number;
  nz: number;
}

const CELL = 32;
/** Penetration tolerated before a footprint is moved; the pavement slab is low enough to share. */
export const ROAD_TOLERANCE_M = 0.4;
const ROAD_GAP_M = 0.35;
const SAMPLE_STEP_M = 3;

/** Grid of drivable road segments (bridges and the hero-bridge deck excluded) for point queries. */
export class RoadIndex {
  private cells = new Map<string, Segment[]>();

  constructor(net: RoadNetwork) {
    const layout = layoutOf(net);
    for (const edge of net.edges) {
      if (edge.bridge || layout.hidden[edge.id]) continue;
      const half = edge.width / 2 + KERB_W;
      for (let i = 0; i < edge.pts.length - 1; i++) this.add(edge.pts[i], edge.pts[i + 1], half);
    }
  }

  private add(a: XZ, b: XZ, half: number) {
    const seg: Segment = { ax: a[0], az: a[1], bx: b[0], bz: b[1], half };
    const x0 = Math.floor((Math.min(a[0], b[0]) - half) / CELL);
    const x1 = Math.floor((Math.max(a[0], b[0]) + half) / CELL);
    const z0 = Math.floor((Math.min(a[1], b[1]) - half) / CELL);
    const z1 = Math.floor((Math.max(a[1], b[1]) + half) / CELL);
    for (let cx = x0; cx <= x1; cx++) {
      for (let cz = z0; cz <= z1; cz++) {
        const key = `${cx},${cz}`;
        const list = this.cells.get(key);
        if (list) list.push(seg);
        else this.cells.set(key, [seg]);
      }
    }
  }

  /** The road the point sits deepest inside, or null when it is clear of all of them. */
  deepest(x: number, z: number): RoadHit | null {
    const list = this.cells.get(`${Math.floor(x / CELL)},${Math.floor(z / CELL)}`);
    if (!list) return null;
    let best: RoadHit | null = null;
    for (const s of list) {
      const dx = s.bx - s.ax;
      const dz = s.bz - s.az;
      const len2 = dx * dx + dz * dz || 1;
      const t = Math.min(1, Math.max(0, ((x - s.ax) * dx + (z - s.az) * dz) / len2));
      const px = s.ax + dx * t;
      const pz = s.az + dz * t;
      const distance = Math.hypot(x - px, z - pz);
      const depth = s.half - distance;
      if (depth > 0 && (!best || depth > best.depth)) {
        const len = Math.sqrt(len2);
        best = { depth, px, pz, distance, half: s.half, nx: -dz / len, nz: dx / len };
      }
    }
    return best;
  }
}

function area(pts: P2[]): number {
  let a2 = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x0, z0] = pts[i];
    const [x1, z1] = pts[(i + 1) % pts.length];
    a2 += x0 * z1 - x1 * z0;
  }
  return Math.abs(a2) / 2;
}

/** Deepest road penetration over the ring's vertices and points along its edges. */
export function ringRoadDepth(ring: P2[], roads: RoadIndex): number {
  let worst = 0;
  for (let i = 0; i < ring.length; i++) {
    const [x0, z0] = ring[i];
    const [x1, z1] = ring[(i + 1) % ring.length];
    const steps = Math.max(1, Math.ceil(Math.hypot(x1 - x0, z1 - z0) / SAMPLE_STEP_M));
    for (let s = 0; s < steps; s++) {
      const hit = roads.deepest(x0 + ((x1 - x0) * s) / steps, z0 + ((z1 - z0) * s) / steps);
      if (hit && hit.depth > worst) worst = hit.depth;
    }
  }
  return worst;
}


/** One pass: add points along edges that cross a road, then move every point out of the carriageway. */
function pushRound(ring: P2[], roads: RoadIndex, cx: number, cz: number): P2[] {
  const dense: P2[] = [];
  for (let i = 0; i < ring.length; i++) {
    const [x0, z0] = ring[i];
    const [x1, z1] = ring[(i + 1) % ring.length];
    dense.push([x0, z0]);
    const steps = Math.ceil(Math.hypot(x1 - x0, z1 - z0) / SAMPLE_STEP_M);
    for (let s = 1; s < steps; s++) {
      const x = x0 + ((x1 - x0) * s) / steps;
      const z = z0 + ((z1 - z0) * s) / steps;
      const hit = roads.deepest(x, z);
      if (hit && hit.depth > ROAD_TOLERANCE_M * 0.5) dense.push([x, z]);
    }
  }
  return dense.map(([x0, z0]): P2 => {
    let x = x0;
    let z = z0;
    for (let pass = 0; pass < 3; pass++) {
      const hit = roads.deepest(x, z);
      if (!hit || hit.depth <= ROAD_TOLERANCE_M * 0.5) break;
      let dx = x - hit.px;
      let dz = z - hit.pz;
      const d = Math.hypot(dx, dz);
      if (d < 1e-3) {
        const side = (cx - hit.px) * hit.nx + (cz - hit.pz) * hit.nz >= 0 ? 1 : -1;
        dx = hit.nx * side;
        dz = hit.nz * side;
      } else {
        dx /= d;
        dz /= d;
      }
      x = hit.px + dx * (hit.half + ROAD_GAP_M);
      z = hit.pz + dz * (hit.half + ROAD_GAP_M);
    }
    return [x, z];
  });
}

export interface ClearanceStats {
  shrunk: number;
  dropped: number;
}

/**
 * Pushes footprint corners (and points along edges that cross a road) out of the carriageway.
 * A building that would lose most of its footprint, or sits on the road, is dropped.
 */
export function clearRoads(buildings: Building[], roads: RoadIndex): { buildings: Building[]; stats: ClearanceStats } {
  const stats: ClearanceStats = { shrunk: 0, dropped: 0 };
  const out: Building[] = [];
  for (const b of buildings) {
    const open = b.ring.length > 1 && b.ring[0][0] === b.ring[b.ring.length - 1][0] && b.ring[0][1] === b.ring[b.ring.length - 1][1];
    const base = (open ? b.ring.slice(0, -1) : b.ring) as P2[];
    if (base.length < 3 || ringRoadDepth(base, roads) <= ROAD_TOLERANCE_M) {
      out.push(b);
      continue;
    }
    const before = area(base);
    let cx = 0;
    let cz = 0;
    for (const [x, z] of base) {
      cx += x;
      cz += z;
    }
    cx /= base.length;
    cz /= base.length;

    let pushed = base;
    for (let round = 0; round < 4 && ringRoadDepth(pushed, roads) > ROAD_TOLERANCE_M; round++) {
      pushed = pushRound(pushed, roads, cx, cz);
    }
    const centre = roads.deepest(cx, cz);
    if (area(pushed) < before * 0.4 || ringRoadDepth(pushed, roads) > 1.5 || (centre && centre.depth > centre.half * 0.5)) {
      stats.dropped++;
      continue;
    }
    stats.shrunk++;
    out.push({ ...b, ring: [...pushed, pushed[0]] });
  }
  return { buildings: out, stats };
}
