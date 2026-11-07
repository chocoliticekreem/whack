import * as THREE from "three";
import { GEOMETRY_BOUNDS, type TwinGeometry } from "../../config";
import {
  BITUMEN,
  GLASS_PLINTH,
  MAST,
  METAL_TRIM,
  PLANT,
  PLINTH_STONE,
  STONE_TRIM,
  WALL_STYLE,
  chooseMaterial,
  roofColour,
  seededRandom,
  wallColour,
} from "./facades";
import type { RoadIndex } from "./clearance";
import { offsetRing, pointInRing, ringArea, type Footprint, type P2 } from "./footprint";
import { MeshBuilder, type Surface, type V3 } from "./meshBuilder";
import { resolveBuildings, type PlacedBuilding } from "./overlaps";

type Building = TwinGeometry["buildings"][number];

/** Shared with the facade shader: storey height and window bay width in metres, per style. */
export const STOREY = { masonry: 3.3, glass: 3.9 };
export const BAY = { masonry: 2.7, glass: 1.8 };
/** Shopfront bays are this much wider than the storeys above. */
export const SHOP_BAY_SCALE = 4 / 3;

const CHUNKS_PER_SIDE = 4;
const TALL_RIM_HEIGHT = 90;
/** Baked ambient occlusion multipliers. */
const GROUND_AO = 0.6;
const SETBACK_AO = 0.82;
const OVERHANG_AO = 0.78;
const CONCAVE_AO = 0.84;
const AWNING_COLOURS = ["#7d1f26", "#1f4d3a", "#1d3557", "#b9791a", "#2e3238", "#7a3560"].map((hex) => new THREE.Color(hex));

/** Geometry is split by how close the camera must be to see it: core always, mid within ~450 m, fine within ~220 m. */
export interface Layers {
  core: MeshBuilder;
  mid: MeshBuilder;
  fine: MeshBuilder;
}

/** What a building may ask about its neighbourhood before it sticks something out of its walls. */
interface Site {
  /** True when (x, z) is inside another building's ground footprint. */
  blocked(x: number, z: number, self: PlacedBuilding): boolean;
  /** True when (x, z) is clear of every carriageway. */
  roadClear(x: number, z: number): boolean;
}

function surface(colour: THREE.Color, seed: number, lit: number, style: number, tone = 1): Surface {
  return { r: colour.r * tone, g: colour.g * tone, b: colour.b * tone, seed, lit, style };
}

function outwardNormal(a: P2, b: P2): V3 {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  return [(b[1] - a[1]) / len, 0, -(b[0] - a[0]) / len];
}

interface Look {
  glass: boolean;
  storey: number;
  bay: number;
  minH: number;
  seed: number;
  wall: Surface;
  trim: Surface;
  shop: Surface;
  roof: Surface;
  plant: Surface;
  light: Surface;
  mast: Surface;
  rail: Surface;
}

/** Convex corners are open to the sky; concave ones trap light. One multiplier per ring vertex. */
function cornerAO(ring: P2[]): number[] {
  return ring.map((p, i) => {
    const prev = ring[(i + ring.length - 1) % ring.length];
    const next = ring[(i + 1) % ring.length];
    const cross = (p[0] - prev[0]) * (next[1] - p[1]) - (p[1] - prev[1]) * (next[0] - p[0]);
    return cross < -0.05 ? CONCAVE_AO : 1;
  });
}

/**
 * Walls from y0 to y1; window bays are stretched so every wall holds a whole number of them.
 * Split into bands so vertex colours carry baked AO: dark at the foot (`baseAO`), light above, dark again
 * under a cornice overhang when `eave` is set, and a little darker at concave corners.
 */
function addWalls(
  b: MeshBuilder,
  ring: P2[],
  y0: number,
  y1: number,
  look: Look,
  s: Surface,
  shop: boolean,
  rnd: () => number,
  baseAO = 1,
  eave = false,
) {
  const bay = look.bay * (shop ? SHOP_BAY_SCALE : 1);
  const rise = y1 - y0;
  const foot = baseAO < 1 ? Math.min(3.4, rise * 0.55) : 0;
  const lip = eave ? Math.min(1.5, rise * 0.3) : 0;
  const ys = [y0];
  const aos = [baseAO];
  if (foot > 0) {
    ys.push(y0 + foot);
    aos.push(1);
  }
  if (lip > 0 && y1 - lip > ys[ys.length - 1] + 0.3) {
    ys.push(y1 - lip);
    aos.push(1);
  }
  ys.push(y1);
  aos.push(eave ? OVERHANG_AO : 1);
  const corner = cornerAO(ring);
  for (let i = 0; i < ring.length; i++) {
    const j = (i + 1) % ring.length;
    const a = ring[i];
    const c = ring[j];
    const len = Math.hypot(c[0] - a[0], c[1] - a[1]);
    if (len < 0.3) continue;
    const bays = Math.max(1, Math.round(len / bay));
    const u0 = Math.floor(rnd() * 64) * bay;
    const u1 = u0 + bays * bay;
    const n = outwardNormal(a, c);
    for (let k = 0; k < ys.length - 1; k++) {
      const lo = ys[k];
      const hi = ys[k + 1];
      b.quad(
        [a[0], lo, a[1]],
        [a[0], hi, a[1]],
        [c[0], hi, c[1]],
        [c[0], lo, c[1]],
        s,
        n,
        [
          [u0, lo - look.minH],
          [u0, hi - look.minH],
          [u1, hi - look.minH],
          [u1, lo - look.minH],
        ],
        [aos[k] * corner[i], aos[k + 1] * corner[i], aos[k + 1] * corner[j], aos[k] * corner[j]],
      );
    }
  }
}

/** Horizontal slab lip that sticks out of the facade between `inner` and `outer`, from y0 to y1. */
function addLedge(b: MeshBuilder, inner: P2[], outer: P2[], y0: number, y1: number, s: Surface, underside: boolean) {
  for (let i = 0; i < inner.length; i++) {
    const j = (i + 1) % inner.length;
    const n = outwardNormal(inner[i], inner[j]);
    b.quad([outer[i][0], y0, outer[i][1]], [outer[i][0], y1, outer[i][1]], [outer[j][0], y1, outer[j][1]], [outer[j][0], y0, outer[j][1]], s, n, undefined, [0.8, 1, 1, 0.8]);
    b.quad([inner[i][0], y1, inner[i][1]], [outer[i][0], y1, outer[i][1]], [outer[j][0], y1, outer[j][1]], [inner[j][0], y1, inner[j][1]], s, [0, 1, 0]);
    if (underside) {
      b.quad([inner[i][0], y0, inner[i][1]], [outer[i][0], y0, outer[i][1]], [outer[j][0], y0, outer[j][1]], [inner[j][0], y0, inner[j][1]], s, [0, -1, 0], undefined, [0.55, 0.55, 0.55, 0.55]);
    }
  }
}

/** Vertical fins that physically protrude from the facade at bay boundaries. */
function addPilasters(
  b: MeshBuilder,
  ring: P2[],
  y0: number,
  y1: number,
  bay: number,
  step: number,
  halfWidth: number,
  depth: number,
  s: Surface,
) {
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i];
    const c = ring[(i + 1) % ring.length];
    const len = Math.hypot(c[0] - a[0], c[1] - a[1]);
    if (len < 4.5) continue;
    const bays = Math.max(1, Math.round(len / bay));
    const dx = (c[0] - a[0]) / len;
    const dz = (c[1] - a[1]) / len;
    const n = outwardNormal(a, c);
    for (let k = step; k < bays; k += step) {
      const along = (k / bays) * len;
      const px = a[0] + dx * along;
      const pz = a[1] + dz * along;
      const lx = px - dx * halfWidth;
      const lz = pz - dz * halfWidth;
      const rx = px + dx * halfWidth;
      const rz = pz + dz * halfWidth;
      const ox = n[0] * depth;
      const oz = n[2] * depth;
      b.quad([lx + ox, y0, lz + oz], [lx + ox, y1, lz + oz], [rx + ox, y1, rz + oz], [rx + ox, y0, rz + oz], s, n);
      b.quad([lx, y0, lz], [lx, y1, lz], [lx + ox, y1, lz + oz], [lx + ox, y0, lz + oz], s, [-dx, 0, -dz]);
      b.quad([rx, y0, rz], [rx, y1, rz], [rx + ox, y1, rz + oz], [rx + ox, y0, rz + oz], s, [dx, 0, dz]);
    }
  }
}

/** Cornice, parapet and flat roof for the top of a tier. Classic (masonry) cornices project further and step on a corbel course. Returns the walkable roof ring. */
function addFlatRoof(b: MeshBuilder, ring: P2[], y: number, look: Look, small: boolean, rim: boolean): P2[] {
  const classic = !small && !look.glass;
  const outer = offsetRing(ring, small ? 0.15 : classic ? 0.42 : 0.28);
  const inner = offsetRing(ring, -0.35);
  const corbel = classic ? offsetRing(ring, 0.16) : null;
  const jitter = look.seed * 0.04;
  const top = y + (small ? 0.4 : 0.55) + jitter;
  const lipLow = y - (small ? 0.2 : 0.5);
  const lightLow = top - 0.3;
  for (let i = 0; i < ring.length; i++) {
    const j = (i + 1) % ring.length;
    const n = outwardNormal(ring[i], ring[j]);
    const oa: V3 = [outer[i][0], 0, outer[i][1]];
    const ob: V3 = [outer[j][0], 0, outer[j][1]];
    b.quad([oa[0], lipLow, oa[2]], [oa[0], lightLow, oa[2]], [ob[0], lightLow, ob[2]], [ob[0], lipLow, ob[2]], look.trim, n, undefined, [0.85, 1, 1, 0.85]);
    b.quad([oa[0], lightLow, oa[2]], [oa[0], top, oa[2]], [ob[0], top, ob[2]], [ob[0], lightLow, ob[2]], rim ? look.light : look.trim, n);
    b.quad([outer[i][0], top, outer[i][1]], [inner[i][0], top, inner[i][1]], [inner[j][0], top, inner[j][1]], [outer[j][0], top, outer[j][1]], look.trim, [0, 1, 0]);
    b.quad([inner[i][0], y, inner[i][1]], [inner[i][0], top, inner[i][1]], [inner[j][0], top, inner[j][1]], [inner[j][0], y, inner[j][1]], look.roof, [-n[0], 0, -n[2]], undefined, [0.7, 1, 1, 0.7]);
    if (!small) {
      b.quad([ring[i][0], lipLow, ring[i][1]], [outer[i][0], lipLow, outer[i][1]], [outer[j][0], lipLow, outer[j][1]], [ring[j][0], lipLow, ring[j][1]], look.trim, [0, -1, 0], undefined, [0.5, 0.5, 0.5, 0.5]);
    }
    if (corbel) {
      const c0 = corbel[i];
      const c1 = corbel[j];
      const low = lipLow - 0.38;
      b.quad([c0[0], low, c0[1]], [c0[0], lipLow, c0[1]], [c1[0], lipLow, c1[1]], [c1[0], low, c1[1]], look.trim, n, undefined, [0.7, 0.95, 0.95, 0.7]);
      b.quad([ring[i][0], low, ring[i][1]], [c0[0], low, c0[1]], [c1[0], low, c1[1]], [ring[j][0], low, ring[j][1]], look.trim, [0, -1, 0], undefined, [0.5, 0.5, 0.5, 0.5]);
    }
  }
  b.cap(inner, y, look.roof);
  return inner;
}

/** Truncated hip roof: eaves overhang the wall, slopes climb to a flat (or ridge-thin) top. Null when the footprint can't take it. */
function addHippedRoof(b: MeshBuilder, ring: P2[], y: number, run: number, rise: number, look: Look, slope: Surface): P2[] | null {
  const eave = offsetRing(ring, 0.35);
  const top = offsetRing(ring, -run);
  if (ringArea(top) < 0.5) return null;
  const yRidge = y + rise;
  for (let i = 0; i < ring.length; i++) {
    const j = (i + 1) % ring.length;
    const n = outwardNormal(ring[i], ring[j]);
    b.quad(
      [eave[i][0], y - 0.15, eave[i][1]],
      [top[i][0], yRidge, top[i][1]],
      [top[j][0], yRidge, top[j][1]],
      [eave[j][0], y - 0.15, eave[j][1]],
      slope,
      [n[0], 1, n[2]],
    );
  }
  b.cap(top, yRidge, slope);
  return top;
}

function addPrism(b: MeshBuilder, cx: number, cz: number, r0: number, y0: number, r1: number, y1: number, segments: number, s: Surface) {
  for (let i = 0; i < segments; i++) {
    const a0 = (i / segments) * Math.PI * 2;
    const a1 = ((i + 1) / segments) * Math.PI * 2;
    const mid = (a0 + a1) / 2;
    b.quad(
      [cx + Math.cos(a0) * r0, y0, cz + Math.sin(a0) * r0],
      [cx + Math.cos(a0) * r1, y1, cz + Math.sin(a0) * r1],
      [cx + Math.cos(a1) * r1, y1, cz + Math.sin(a1) * r1],
      [cx + Math.cos(a1) * r0, y0, cz + Math.sin(a1) * r0],
      s,
      [Math.cos(mid), 0, Math.sin(mid)],
    );
  }
}

function addRoofClutter(layers: Layers, roof: P2[], y: number, fp: Footprint, height: number, mast: boolean, look: Look, rnd: () => number) {
  const area = ringArea(roof);
  if (area < 12) return;
  const safe = offsetRing(roof, -0.6);
  if (ringArea(safe) < 4) return;
  const tall = height > 60;
  const count = tall ? 1 + Math.floor(rnd() * 2) : height > 12 ? Math.floor(rnd() * (1 + Math.min(3, area / 200))) : rnd() < 0.3 ? 1 : 0;
  const bbox = safe.reduce(
    (m, [x, z]) => [Math.min(m[0], x), Math.max(m[1], x), Math.min(m[2], z), Math.max(m[3], z)],
    [Infinity, -Infinity, Infinity, -Infinity],
  );
  for (let n = 0; n < count; n++) {
    for (let attempt = 0; attempt < 6; attempt++) {
      const hw = (1.6 + rnd() * (tall ? 3.5 : 3)) / 2;
      const hd = (1.4 + rnd() * 3) / 2;
      const x = bbox[0] + rnd() * (bbox[1] - bbox[0]);
      const z = bbox[2] + rnd() * (bbox[3] - bbox[2]);
      const bx = -fp.az;
      const bz = fp.ax;
      const fits = [
        [1, 1],
        [1, -1],
        [-1, 1],
        [-1, -1],
        [0, 0],
      ].every(([sx, sz]) => pointInRing(x + fp.ax * hw * sx + bx * hd * sz, z + fp.az * hw * sx + bz * hd * sz, safe));
      if (!fits) continue;
      const top = y + 1.1 + rnd() * (tall ? 1.8 : 1.5);
      layers.fine.box(x, z, fp.ax, fp.az, hw, hd, y, top, look.plant);
      if (hw > 1.2 && rnd() < 0.5) {
        // fan housing on the plant box
        addPrism(layers.fine, x, z, 0.55, top, 0.55, top + 0.35, 8, look.trim);
      }
      break;
    }
  }
  if (height > 12 && height < 70 && rnd() < 0.15) {
    const x = (bbox[0] + bbox[1]) / 2;
    const z = (bbox[2] + bbox[3]) / 2;
    if (pointInRing(x, z, safe) && pointInRing(x + 1.6, z, safe) && pointInRing(x - 1.6, z, safe) && pointInRing(x, z + 1.6, safe) && pointInRing(x, z - 1.6, safe)) {
      layers.fine.box(x, z, fp.ax, fp.az, 0.6, 0.6, y, y + 0.9, look.plant);
      addPrism(layers.fine, x, z, 1.3, y + 0.9, 1.3, y + 3.3, 8, look.plant);
      const lid: P2[] = Array.from({ length: 8 }, (_, i) => [x + Math.cos((i / 8) * Math.PI * 2) * 1.3, z + Math.sin((i / 8) * Math.PI * 2) * 1.3]);
      layers.fine.cap(lid, y + 3.3, look.plant);
    }
  }
  if (mast && height >= 120 && rnd() < (height >= 200 ? 0.8 : 0.3) && pointInRing(fp.cx, fp.cz, safe)) {
    const mastHeight = 7 + rnd() * 9;
    addPrism(layers.core, fp.cx, fp.cz, 0.5, y, 0.25, y + mastHeight * 0.4, 6, look.mast);
    addPrism(layers.core, fp.cx, fp.cz, 0.25, y + mastHeight * 0.4, 0.08, y + mastHeight, 6, look.mast);
    if (height >= 150) {
      layers.core.box(fp.cx, fp.cz, 1, 0, 0.3, 0.3, y + mastHeight, y + mastHeight + 0.6, { ...look.light, style: 5 });
    }
  }
}

/** Slab-and-rail balconies on a share of residential bays, where nothing stands or drives in front of them. */
function addBalconies(b: MeshBuilder, ring: P2[], from: number, to: number, look: Look, site: Site, placed: PlacedBuilding, rnd: () => number) {
  const slabDepth = 0.95;
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i];
    const c = ring[(i + 1) % ring.length];
    const len = Math.hypot(c[0] - a[0], c[1] - a[1]);
    if (len < 6 || rnd() < 0.4) continue;
    const bays = Math.max(1, Math.round(len / look.bay));
    const dx = (c[0] - a[0]) / len;
    const dz = (c[1] - a[1]) / len;
    const n = outwardNormal(a, c);
    const phase = Math.floor(rnd() * 3);
    for (let k = 0; k < bays; k++) {
      if ((k + phase) % 3 !== 0) continue;
      const along = ((k + 0.5) / bays) * len;
      const px = a[0] + dx * along;
      const pz = a[1] + dz * along;
      if (site.blocked(px + n[0] * 1.4, pz + n[2] * 1.4, placed) || !site.roadClear(px + n[0] * 1.5, pz + n[2] * 1.5)) continue;
      const hw = Math.min(look.bay * 0.36, 1);
      for (let f = from + 1; f < to; f += 2) {
        const y = look.minH + f * look.storey + 0.02;
        b.box(px + n[0] * slabDepth * 0.5, pz + n[2] * slabDepth * 0.5, dx, dz, hw, slabDepth * 0.5, y, y + 0.14, look.trim);
        b.box(px + n[0] * (slabDepth - 0.03), pz + n[2] * (slabDepth - 0.03), dx, dz, hw, 0.025, y + 0.14, y + 1.0, look.rail);
        b.box(px - dx * (hw - 0.025) + n[0] * slabDepth * 0.5, pz - dz * (hw - 0.025) + n[2] * slabDepth * 0.5, dx, dz, 0.025, slabDepth * 0.5, y + 0.14, y + 1.0, look.rail);
        b.box(px + dx * (hw - 0.025) + n[0] * slabDepth * 0.5, pz + dz * (hw - 0.025) + n[2] * slabDepth * 0.5, dx, dz, 0.025, slabDepth * 0.5, y + 0.14, y + 1.0, look.rail);
      }
    }
  }
}

/** Striped canvas awnings over shopfront bays, only where the pavement in front is free. */
function addAwnings(b: MeshBuilder, ring: P2[], yBase: number, look: Look, site: Site, placed: PlacedBuilding, rnd: () => number) {
  const reach = 1.1;
  const bayWidth = look.bay * SHOP_BAY_SCALE;
  const colour = AWNING_COLOURS[Math.floor(rnd() * AWNING_COLOURS.length)];
  const surf = surface(colour, look.seed, 0, 7);
  const hang = surface(colour, look.seed, 0, 7, 0.7);
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i];
    const c = ring[(i + 1) % ring.length];
    const len = Math.hypot(c[0] - a[0], c[1] - a[1]);
    if (len < 4) continue;
    const bays = Math.max(1, Math.round(len / bayWidth));
    const dx = (c[0] - a[0]) / len;
    const dz = (c[1] - a[1]) / len;
    const n = outwardNormal(a, c);
    for (let k = 0; k < bays; k++) {
      if (rnd() < 0.35) continue;
      const w0 = ((k + 0.08) / bays) * len;
      const w1 = ((k + 0.92) / bays) * len;
      const mx = a[0] + dx * ((w0 + w1) / 2) + n[0] * reach;
      const mz = a[1] + dz * ((w0 + w1) / 2) + n[2] * reach;
      if (site.blocked(mx, mz, placed) || !site.roadClear(a[0] + dx * ((w0 + w1) / 2) + n[0] * (reach + 0.4), a[1] + dz * ((w0 + w1) / 2) + n[2] * (reach + 0.4))) continue;
      const x0 = a[0] + dx * w0;
      const z0 = a[1] + dz * w0;
      const x1 = a[0] + dx * w1;
      const z1 = a[1] + dz * w1;
      const yTop = yBase + 2.78;
      const yLow = yBase + 2.3;
      const uvs: [number, number][] = [
        [w0, 0],
        [w0, 1],
        [w1, 1],
        [w1, 0],
      ];
      b.quad([x0, yTop, z0], [x0 + n[0] * reach, yLow, z0 + n[2] * reach], [x1 + n[0] * reach, yLow, z1 + n[2] * reach], [x1, yTop, z1], surf, [n[0] * 0.5, 0.8, n[2] * 0.5], uvs, [1, 1, 1, 1]);
      b.quad(
        [x0 + n[0] * reach, yLow, z0 + n[2] * reach],
        [x0 + n[0] * reach, yLow - 0.26, z0 + n[2] * reach],
        [x1 + n[0] * reach, yLow - 0.26, z1 + n[2] * reach],
        [x1 + n[0] * reach, yLow, z1 + n[2] * reach],
        hang,
        n,
        uvs,
        [0.9, 0.7, 0.7, 0.9],
      );
      b.quad([x0, yTop, z0], [x0 + n[0] * reach, yLow, z0 + n[2] * reach], [x1 + n[0] * reach, yLow, z1 + n[2] * reach], [x1, yTop, z1], hang, [0, -1, 0], uvs, [0.35, 0.5, 0.5, 0.35]);
    }
  }
}

interface Tier {
  ring: P2[];
  from: number;
  to: number;
}

function planTiers(fp: Footprint, ring: P2[], floors: number, height: number, rnd: () => number): Tier[] {
  const single: Tier[] = [{ ring, from: 0, to: floors }];
  if (height < 45 || fp.width < 14 || floors < 8) return single;
  const step = Math.min(2.4, fp.width * 0.09);
  const cuts: number[] = [];
  const insets: number[] = [];
  if (height >= TALL_RIM_HEIGHT && fp.width >= 22 && rnd() < 0.7) {
    cuts.push(Math.round(floors * 0.62), Math.round(floors * 0.86));
    insets.push(step, step * 2);
  } else if (rnd() < 0.8) {
    cuts.push(Math.round(floors * 0.78));
    insets.push(step);
  }
  const tiers: Tier[] = [];
  let from = 0;
  let current = ring;
  const bounds = [...cuts, floors];
  bounds.forEach((to, i) => {
    if (to - from < 2) return;
    tiers.push({ ring: current, from, to });
    from = to;
    if (i < insets.length) {
      const next = offsetRing(ring, -insets[i]);
      current = ringArea(next) > fp.area * 0.45 ? next : current;
    }
  });
  return tiers.length ? tiers : single;
}

function addBuilding(layers: Layers, placed: PlacedBuilding, site: Site) {
  const { fp, minHeight: minH, height, part } = placed;
  const { core, mid, fine } = layers;
  const rnd = seededRandom(placed.lookSeed);
  const seed = rnd();
  const lookHeight = placed.lookHeight;
  const material = chooseMaterial(lookHeight, rnd());
  const glass = material === "glass";
  const storey = glass ? STOREY.glass : STOREY.masonry;
  const bay = glass ? BAY.glass : BAY.masonry;
  const floors = Math.max(1, part ? Math.ceil((height - minH) / storey - 0.2) : Math.round((height - minH) / storey));

  const lit = (glass ? 0.38 : lookHeight > 60 ? 0.26 : lookHeight > 25 ? 0.17 : 0.11) * (0.55 + rnd() * 0.9);
  const style = WALL_STYLE[material];
  const wallTone = wallColour(material, placed.colour, rnd, new THREE.Color());
  const trimTone = glass ? METAL_TRIM : new THREE.Color().copy(STONE_TRIM).lerp(wallTone, material === "stone" ? 0.6 : 0.25);
  const shopTone = glass ? GLASS_PLINTH : new THREE.Color().copy(PLINTH_STONE).lerp(wallTone, 0.35);
  const pitched =
    !part &&
    !glass &&
    height <= 14 &&
    floors <= 3 &&
    fp.convex &&
    fp.pts.length <= 8 &&
    fp.area < 600 &&
    fp.area / (fp.length * fp.width) > 0.8 &&
    rnd() < (height <= 9 ? 0.6 : 0.3);
  const mansard = !part && !pitched && !glass && floors >= 4 && floors <= 9 && fp.pts.length <= 12 && fp.width >= 9 && rnd() < 0.18;
  const roofTone = roofColour(pitched || mansard, lookHeight, rnd, new THREE.Color());

  const look: Look = {
    glass,
    storey,
    bay,
    minH,
    seed,
    wall: surface(wallTone, seed, lit, style),
    trim: surface(trimTone, seed, 0, 0, 0.9 + rnd() * 0.2),
    shop: surface(shopTone, seed, 0.55 + rnd() * 0.3, style + 0.5),
    roof: surface(roofTone, seed, 0, 3),
    plant: surface(PLANT, seed, 0, 0, 0.8 + rnd() * 0.4),
    light: surface(BITUMEN, seed, 0, 4),
    mast: surface(MAST, seed, 0, 0),
    rail: surface(METAL_TRIM, seed, 0, 0, 0.7),
  };
  const slope = surface(roofTone, seed, 0, 3, 1.1);
  // Roof-top plant rooms and lift cores arrive as tiny floating parts: plain grey boxes, no windows or trim.
  const solid = part && minH >= 15 && height - minH < 20 && fp.area < 400;
  if (solid) look.wall = look.plant;

  const base = offsetRing(fp.pts, 0.01 + Math.floor(seed * 6) * 0.025);
  const tiers = part ? [{ ring: base, from: 0, to: floors }] : planTiers(fp, base, floors, height, rnd);
  const plinth = !part && minH < 1 && floors >= 2 && (height > 40 || (height > 8 && rnd() < 0.6));
  const ledgeEvery = glass ? 4 : floors <= 6 ? 1 : floors <= 12 ? 2 : 3;
  const pilasterStep = glass ? (lookHeight > 25 ? 2 : 0) : rnd() < 0.55 && floors >= 3 ? 1 + Math.floor(rnd() * 2) : 0;
  const rim = lookHeight >= TALL_RIM_HEIGHT;
  const residential = !glass && !part && !solid && floors >= 3 && floors <= 14 && lookHeight < 45 && rnd() < 0.5;
  const footAO = minH < 1 ? GROUND_AO : 0.9;

  let roofRing: P2[] | null = null;
  let roofY = minH;
  for (const [index, tier] of tiers.entries()) {
    const yBase = minH + tier.from * storey;
    const last = index === tiers.length - 1;
    // Anything stacked on this slice, and every part, needs the wall to end exactly at its stated height.
    const yTop = last && part ? height : minH + tier.to * storey;
    const first = index === 0;
    let yWall = yBase;
    if (first && plinth) {
      const plinthRing = offsetRing(tier.ring, 0.22);
      const lipRing = offsetRing(tier.ring, 0.3);
      yWall = yBase + storey;
      addWalls(core, plinthRing, yBase, yWall, look, look.shop, true, rnd, footAO);
      addLedge(mid, tier.ring, lipRing, yWall - 0.05, yWall + 0.12, look.trim, true);
      if (!glass) addAwnings(fine, plinthRing, yBase, look, site, placed, rnd);
    }
    addWalls(core, tier.ring, yWall, yTop, look, look.wall, false, rnd, first && !plinth ? footAO : SETBACK_AO, last && !solid);
    if (solid) {
      core.cap(tier.ring, yTop, look.roof);
      continue;
    }

    const ledgeRing = offsetRing(tier.ring, glass ? 0.12 : 0.15);
    for (let f = tier.from + 1; f < tier.to; f++) {
      if (f % ledgeEvery !== 0 || (first && plinth && f === 1)) continue;
      const y = minH + f * storey;
      if (y > yTop - 1) continue;
      addLedge(mid, tier.ring, ledgeRing, y - 0.11, y + 0.11, look.trim, true);
    }
    if (pilasterStep > 0) {
      addPilasters(mid, tier.ring, yWall, yTop - 0.4, bay, pilasterStep, glass ? 0.07 : 0.2, glass ? 0.3 : 0.26, look.trim);
    }
    if (residential && first) addBalconies(fine, tier.ring, plinth ? tier.from + 1 : tier.from, tier.to, look, site, placed, rnd);

    if (last && (pitched || mansard)) {
      const run = pitched ? Math.min(0.44 * fp.width, 5) : Math.min(1.8, fp.width * 0.3);
      const top = addHippedRoof(core, tier.ring, yTop, run, run * (pitched ? 0.62 : 1.35), look, slope);
      if (top) {
        const rise = run * (pitched ? 0.62 : 1.35);
        roofRing = top;
        roofY = yTop + rise;
        if (pitched && rnd() < 0.7 && pointInRing(fp.cx, fp.cz, top)) {
          const stackTop = yTop + rise + 1.3;
          mid.box(fp.cx, fp.cz, fp.ax, fp.az, 0.35, 0.35, yTop + rise * 0.5, stackTop, look.plant);
          for (const dz of [-0.2, 0, 0.2]) {
            const px = fp.cx - fp.az * dz;
            const pz = fp.cz + fp.ax * dz;
            addPrism(fine, px, pz, 0.09, stackTop, 0.08, stackTop + 0.4, 6, look.rail);
          }
        }
        continue;
      }
    }
    if (last && placed.roof !== "open") {
      if (placed.roof === "partial") core.cap(tier.ring, yTop, look.roof);
      continue;
    }
    const small = height < 12;
    const inner = addFlatRoof(core, tier.ring, yTop, look, small, rim);
    if (last) {
      roofRing = inner;
      roofY = yTop;
    }
  }

  if (roofRing) addRoofClutter(layers, roofRing, roofY, fp, lookHeight, placed.carriesMast, look, rnd);
}

export interface BuildingChunk {
  core: THREE.BufferGeometry;
  /** Ledges, pilasters, chimneys: seen within a few hundred metres. */
  mid: THREE.BufferGeometry | null;
  /** Balconies, awnings, rooftop plant, chimney pots: seen up close only. */
  fine: THREE.BufferGeometry | null;
  triangles: number;
}

/** Grid of ground footprints, so attachments can check what stands in front of a wall. */
function makeSite(placed: PlacedBuilding[], roads: RoadIndex | null): Site {
  const CELL = 40;
  const cells = new Map<string, PlacedBuilding[]>();
  for (const p of placed) {
    if (p.minHeight > 6) continue;
    const xs = p.fp.pts.map((q) => q[0]);
    const zs = p.fp.pts.map((q) => q[1]);
    for (let cx = Math.floor(Math.min(...xs) / CELL); cx <= Math.floor(Math.max(...xs) / CELL); cx++) {
      for (let cz = Math.floor(Math.min(...zs) / CELL); cz <= Math.floor(Math.max(...zs) / CELL); cz++) {
        const key = `${cx},${cz}`;
        const list = cells.get(key);
        if (list) list.push(p);
        else cells.set(key, [p]);
      }
    }
  }
  return {
    blocked: (x, z, self) => (cells.get(`${Math.floor(x / CELL)},${Math.floor(z / CELL)}`) ?? []).some((o) => o !== self && pointInRing(x, z, o.fp.pts)),
    roadClear: (x, z) => !roads || !roads.deepest(x, z),
  };
}

/** Builds merged geometry for every building, bucketed into a grid so frustum culling still works. */
export function buildBuildingChunks(buildings: Building[], roads: RoadIndex | null = null): BuildingChunk[] {
  const count = CHUNKS_PER_SIDE * CHUNKS_PER_SIDE;
  const layers: Layers[] = Array.from({ length: count }, () => ({ core: new MeshBuilder(), mid: new MeshBuilder(), fine: new MeshBuilder() }));
  const cellX = (GEOMETRY_BOUNDS.xMax - GEOMETRY_BOUNDS.xMin) / CHUNKS_PER_SIDE;
  const cellZ = (GEOMETRY_BOUNDS.zMax - GEOMETRY_BOUNDS.zMin) / CHUNKS_PER_SIDE;
  const placed = resolveBuildings(buildings, roads);
  const site = makeSite(placed, roads);
  for (const p of placed) {
    const { fp } = p;
    const cx = Math.min(CHUNKS_PER_SIDE - 1, Math.max(0, Math.floor((fp.cx - GEOMETRY_BOUNDS.xMin) / cellX)));
    const cz = Math.min(CHUNKS_PER_SIDE - 1, Math.max(0, Math.floor((fp.cz - GEOMETRY_BOUNDS.zMin) / cellZ)));
    addBuilding(layers[cz * CHUNKS_PER_SIDE + cx], p, site);
  }
  return layers
    .filter((l) => l.core.vertexCount > 0)
    .map((l) => ({
      core: l.core.build(),
      mid: l.mid.vertexCount > 0 ? l.mid.build() : null,
      fine: l.fine.vertexCount > 0 ? l.fine.build() : null,
      triangles: l.core.triangles + l.mid.triangles + l.fine.triangles,
    }));
}
