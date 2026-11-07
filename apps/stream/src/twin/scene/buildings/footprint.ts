import type { Ring } from "../../config";

export type P2 = [number, number];

export interface Footprint {
  pts: P2[];
  area: number;
  cx: number;
  cz: number;
  /** Unit direction of the longest edge's oriented box, and the box extents along / across it. */
  ax: number;
  az: number;
  length: number;
  width: number;
  convex: boolean;
}

/**
 * Cleans an OSM ring and orients it so that, for an edge direction d = (dx, dz), the outward
 * wall normal is (dz, 0, -dx). Returns null for degenerate rings.
 */
export function prepareFootprint(ring: Ring): Footprint | null {
  const pts: P2[] = [];
  for (const p of ring) {
    const last = pts[pts.length - 1];
    if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) > 0.25) pts.push([p[0], p[1]]);
  }
  while (pts.length > 3 && Math.hypot(pts[0][0] - pts[pts.length - 1][0], pts[0][1] - pts[pts.length - 1][1]) <= 0.25) {
    pts.pop();
  }
  if (pts.length < 3) return null;

  let a2 = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x0, z0] = pts[i];
    const [x1, z1] = pts[(i + 1) % pts.length];
    a2 += x0 * z1 - x1 * z0;
  }
  if (Math.abs(a2) < 8) return null;
  if (a2 < 0) pts.reverse();

  let cx = 0;
  let cz = 0;
  for (const [x, z] of pts) {
    cx += x;
    cz += z;
  }
  cx /= pts.length;
  cz /= pts.length;

  let ax = 1;
  let az = 0;
  let best = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x0, z0] = pts[i];
    const [x1, z1] = pts[(i + 1) % pts.length];
    const len = Math.hypot(x1 - x0, z1 - z0);
    if (len > best) {
      best = len;
      ax = (x1 - x0) / len;
      az = (z1 - z0) / len;
    }
  }
  let minA = Infinity;
  let maxA = -Infinity;
  let minB = Infinity;
  let maxB = -Infinity;
  for (const [x, z] of pts) {
    const a = x * ax + z * az;
    const b = -x * az + z * ax;
    minA = Math.min(minA, a);
    maxA = Math.max(maxA, a);
    minB = Math.min(minB, b);
    maxB = Math.max(maxB, b);
  }

  let convex = true;
  for (let i = 0; i < pts.length && convex; i++) {
    const [x0, z0] = pts[i];
    const [x1, z1] = pts[(i + 1) % pts.length];
    const [x2, z2] = pts[(i + 2) % pts.length];
    if ((x1 - x0) * (z2 - z1) - (z1 - z0) * (x2 - x1) < -1e-6) convex = false;
  }

  return {
    pts,
    area: Math.abs(a2) / 2,
    cx,
    cz,
    ax,
    az,
    length: maxA - minA,
    width: maxB - minB,
    convex,
  };
}

/** Mitered offset of a ring: positive distance grows it outward, negative insets it. */
export function offsetRing(pts: P2[], distance: number): P2[] {
  const n = pts.length;
  const normals: P2[] = [];
  for (let i = 0; i < n; i++) {
    const [x0, z0] = pts[i];
    const [x1, z1] = pts[(i + 1) % n];
    const len = Math.hypot(x1 - x0, z1 - z0) || 1;
    normals.push([(z1 - z0) / len, -(x1 - x0) / len]);
  }
  return pts.map(([x, z], i) => {
    const a = normals[(i + n - 1) % n];
    const b = normals[i];
    const denom = Math.max(0.35, 1 + a[0] * b[0] + a[1] * b[1]);
    return [x + ((a[0] + b[0]) / denom) * distance, z + ((a[1] + b[1]) / denom) * distance];
  });
}

export function ringArea(pts: P2[]): number {
  let a2 = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x0, z0] = pts[i];
    const [x1, z1] = pts[(i + 1) % pts.length];
    a2 += x0 * z1 - x1 * z0;
  }
  return a2 / 2;
}

export function pointInRing(x: number, z: number, pts: P2[]): boolean {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, zi] = pts[i];
    const [xj, zj] = pts[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}
