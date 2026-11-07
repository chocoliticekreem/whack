import type { Ring, TwinGeometry } from "../config";

/** Grid hash over building footprints, for "is this ground point inside a building?" in O(1)-ish. */

const CELL = 16;
const key = (cx: number, cz: number) => (cx + 4096) * 8192 + (cz + 4096);

export interface BuildingIndex {
  rings: Ring[];
  cells: Map<number, number[]>;
}

const indices = new WeakMap<TwinGeometry, BuildingIndex>();

export function buildingIndexOf(geometry: TwinGeometry): BuildingIndex {
  let idx = indices.get(geometry);
  if (idx) return idx;
  const rings: Ring[] = [];
  const cells = new Map<number, number[]>();
  for (const b of geometry.buildings) {
    if (b.minHeight > 3 || b.ring.length < 3) continue; // bridges and canopies overhang the road
    const ref = rings.length;
    rings.push(b.ring);
    let x0 = Infinity;
    let x1 = -Infinity;
    let z0 = Infinity;
    let z1 = -Infinity;
    for (const [x, z] of b.ring) {
      x0 = Math.min(x0, x);
      x1 = Math.max(x1, x);
      z0 = Math.min(z0, z);
      z1 = Math.max(z1, z);
    }
    for (let cx = Math.floor(x0 / CELL); cx <= Math.floor(x1 / CELL); cx++) {
      for (let cz = Math.floor(z0 / CELL); cz <= Math.floor(z1 / CELL); cz++) {
        const k = key(cx, cz);
        const list = cells.get(k);
        if (list) list.push(ref);
        else cells.set(k, [ref]);
      }
    }
  }
  idx = { rings, cells };
  indices.set(geometry, idx);
  return idx;
}

function inside(ring: Ring, x: number, z: number) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, zi] = ring[i];
    const [xj, zj] = ring[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) hit = !hit;
  }
  return hit;
}

export function inBuilding(idx: BuildingIndex, x: number, z: number) {
  const list = idx.cells.get(key(Math.floor(x / CELL), Math.floor(z / CELL)));
  if (!list) return false;
  for (const r of list) if (inside(idx.rings[r], x, z)) return true;
  return false;
}
