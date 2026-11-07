import type { FieldResponse } from "../../field/types";
import { inViewCone, type CamFrame } from "../project";

const SNAP_M = 15;
const CELL_M = 40;

/**
 * Snaps points (x, z pairs, local metres) onto the nearest field segment within SNAP_M. Field
 * segments are ≤40 m, so a 40 m hash grid with a one-cell neighbourhood finds every candidate.
 * Returns the segment index per point, or -1 when nothing is near enough.
 */
export function snapToField(points: ArrayLike<number>, field: FieldResponse): Int32Array {
  const segs = field.segments;
  const grid = new Map<number, number[]>();
  const key = (cx: number, cz: number) => cx * 73856093 + cz * 19349663;
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    const cx0 = Math.floor(Math.min(s.a[0], s.b[0]) / CELL_M);
    const cx1 = Math.floor(Math.max(s.a[0], s.b[0]) / CELL_M);
    const cz0 = Math.floor(Math.min(s.a[1], s.b[1]) / CELL_M);
    const cz1 = Math.floor(Math.max(s.a[1], s.b[1]) / CELL_M);
    for (let cx = cx0; cx <= cx1; cx++) {
      for (let cz = cz0; cz <= cz1; cz++) {
        const k = key(cx, cz);
        const list = grid.get(k);
        if (list) list.push(i);
        else grid.set(k, [i]);
      }
    }
  }
  const n = points.length >> 1;
  const out = new Int32Array(n).fill(-1);
  const limit = SNAP_M * SNAP_M;
  for (let p = 0; p < n; p++) {
    const x = points[p * 2];
    const z = points[p * 2 + 1];
    const cx = Math.floor(x / CELL_M);
    const cz = Math.floor(z / CELL_M);
    let best = limit;
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        const list = grid.get(key(cx + dx, cz + dz));
        if (!list) continue;
        for (const i of list) {
          const s = segs[i];
          const ax = s.a[0];
          const az = s.a[1];
          const bx = s.b[0] - ax;
          const bz = s.b[1] - az;
          const l2 = bx * bx + bz * bz;
          const t = l2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * bx + (z - az) * bz) / l2)) : 0;
          const ex = ax + bx * t - x;
          const ez = az + bz * t - z;
          const d2 = ex * ex + ez * ez;
          if (d2 < best) {
            best = d2;
            out[p] = i;
          }
        }
      }
    }
  }
  return out;
}

export function coveredBy(frames: CamFrame[], x: number, z: number): boolean {
  for (const f of frames) if (inViewCone(f, x, z)) return true;
  return false;
}
