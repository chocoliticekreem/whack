import type { Ring, TwinGeometry } from "../../config";

/** How far outside the wall the first-person eye is put when the streamer's fix lands inside a footprint. */
const STEP_OUT_M = 1.5;
/** Below this the eye walks under the building (arcades, bridges, canopies). */
const UNDERPASS_MIN_M = 2.5;

let footprints: { ring: Ring; minX: number; maxX: number; minZ: number; maxZ: number }[] = [];

/** Buildings.tsx registers the current area's footprints; CameraRig asks where the eye may stand. */
export function setEyeFootprints(buildings: TwinGeometry["buildings"]) {
  footprints = buildings
    .filter((b) => b.height > 3 && b.minHeight < UNDERPASS_MIN_M && b.ring.length >= 3)
    .map((b) => {
      let minX = Infinity;
      let maxX = -Infinity;
      let minZ = Infinity;
      let maxZ = -Infinity;
      for (const [x, z] of b.ring) {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minZ = Math.min(minZ, z);
        maxZ = Math.max(maxZ, z);
      }
      return { ring: b.ring, minX, maxX, minZ, maxZ };
    });
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

function stepOut(x: number, z: number): [number, number] | null {
  for (const f of footprints) {
    if (x < f.minX || x > f.maxX || z < f.minZ || z > f.maxZ || !inside(f.ring, x, z)) continue;
    let best = Infinity;
    let bx = x;
    let bz = z;
    for (let i = 0, j = f.ring.length - 1; i < f.ring.length; j = i++) {
      const [ax, az] = f.ring[j];
      const [cx, cz] = f.ring[i];
      const ex = cx - ax;
      const ez = cz - az;
      const u = Math.min(1, Math.max(0, ((x - ax) * ex + (z - az) * ez) / (ex * ex + ez * ez || 1)));
      const px = ax + ex * u;
      const pz = az + ez * u;
      const d = Math.hypot(px - x, pz - z);
      if (d < best) {
        best = d;
        bx = px;
        bz = pz;
      }
    }
    const len = Math.max(best, 1e-6);
    return [bx + ((bx - x) / len) * STEP_OUT_M, bz + ((bz - z) / len) * STEP_OUT_M];
  }
  return null;
}

/**
 * GPS fixes and the demo origin often fall inside a footprint (Tower Hill's origin is inside the station), which puts
 * the eye inside a hollow box. Returns the nearest spot just outside the wall instead, or the input when it is clear.
 * A few hops cover terraces, where stepping out of one building lands in the next.
 */
export function clearOfBuildings(x: number, z: number): [number, number] {
  let at: [number, number] = [x, z];
  for (let hop = 0; hop < 4; hop++) {
    const next = stepOut(at[0], at[1]);
    if (!next) break;
    at = next;
  }
  return at;
}
