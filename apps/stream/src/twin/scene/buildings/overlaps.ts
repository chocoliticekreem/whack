import type { TwinGeometry } from "../../config";
import { bearingToYaw, LANDMARKS, landmarkPos, type Landmark } from "../../landmarks";
import { clearRoads, type RoadIndex } from "./clearance";
import { pointInRing, prepareFootprint, type Footprint } from "./footprint";

type Building = TwinGeometry["buildings"][number];

/** How much of a part's roof is hidden by something stacked on top of it. */
export type RoofState = "open" | "partial" | "hidden";

export interface PlacedBuilding {
  fp: Footprint;
  minHeight: number;
  height: number;
  colour?: string;
  /** Stacked slices of one tower share this so they get one material, colour and window rhythm. */
  lookSeed: number;
  lookHeight: number;
  /** A slice of a bigger structure (OSM building:part, or footprint stacked with others). */
  part: boolean;
  roof: RoofState;
  /** Only one slice per structure carries masts. */
  carriesMast: boolean;
}

interface Entry {
  i: number;
  data: Building;
  fp: Footprint;
  minH: number;
  height: number;
  x0: number;
  x1: number;
  z0: number;
  z1: number;
  samples: [number, number][];
  dropped: boolean;
  clipped: boolean;
  roof: RoofState;
}

const GRID = 60;
/** The hero skyline models (Gherkin, Walkie-Talkie...) replace OSM buildings taller than this inside their box. */
const LANDMARK_MARGIN_M = 20;
const LANDMARK_MIN_HEIGHT_M = 15;

const MODELLED = Object.values(LANDMARKS as Record<string, Landmark>)
  .filter((l) => l.kind === "tower" || l.kind === "dome")
  .map((l) => ({ l, pos: landmarkPos(l), yaw: bearingToYaw(l.headingDeg) }));

/** Bankside hero models (scene/Bankside.tsx): plot centre, yaw, and half extents of the model box in its own frame, metres. */
const HERO_PLOTS = [
  { at: [-196, 508], yaw: -0.5, cx: 0, cz: 22, hx: 44, hz: 26 },
  { at: [-240, 543], yaw: -0.8, cx: 0, cz: 0, hx: 26, hz: 15 },
];

export function onHeroPlot(x: number, z: number): boolean {
  return HERO_PLOTS.some((p) => {
    const dx = x - p.at[0];
    const dz = z - p.at[1];
    const lx = dx * Math.cos(p.yaw) - dz * Math.sin(p.yaw);
    const lz = dx * Math.sin(p.yaw) + dz * Math.cos(p.yaw);
    return Math.abs(lx - p.cx) < p.hx && Math.abs(lz - p.cz) < p.hz;
  });
}

/** True when a hand-built model already draws this OSM building, or stands where a hero model does. */
function replacedByLandmark(fp: Footprint, height: number): boolean {
  if (fp.pts.some(([x, z]) => onHeroPlot(x, z)) || onHeroPlot(fp.cx, fp.cz)) return true;
  if (height < LANDMARK_MIN_HEIGHT_M) return false;
  return MODELLED.some(({ l, pos, yaw }) => {
    const dx = fp.cx - pos[0];
    const dz = fp.cz - pos[1];
    const across = dx * Math.cos(yaw) - dz * Math.sin(yaw);
    const along = dx * Math.sin(yaw) + dz * Math.cos(yaw);
    return Math.abs(across) < l.width / 2 + LANDMARK_MARGIN_M && Math.abs(along) < l.length / 2 + LANDMARK_MARGIN_M;
  });
}
const SAMPLES = 9;
const INSIDE = 0.8;

function sampleFootprint(e: Entry) {
  for (let i = 0; i < SAMPLES; i++) {
    for (let j = 0; j < SAMPLES; j++) {
      const x = e.x0 + ((i + 0.5) / SAMPLES) * (e.x1 - e.x0);
      const z = e.z0 + ((j + 0.5) / SAMPLES) * (e.z1 - e.z0);
      if (pointInRing(x, z, e.fp.pts)) e.samples.push([x, z]);
    }
  }
  if (!e.samples.length) e.samples.push([e.fp.cx, e.fp.cz]);
}

/** Fraction of `b`'s footprint that lies inside `a`'s. */
function inside(b: Entry, a: Entry): number {
  let n = 0;
  for (const [x, z] of b.samples) if (pointInRing(x, z, a.fp.pts)) n++;
  return n / b.samples.length;
}

function find(parent: number[], i: number): number {
  while (parent[i] !== i) {
    parent[i] = parent[parent[i]];
    i = parent[i];
  }
  return i;
}

/**
 * OSM stacks building outlines and building:parts on the same site. Extruding all of them puts walls
 * inside walls. This drops outlines that parts replace, trims what pokes through, and tags the rest so
 * the massing knows which slices are parts and whose roof is hidden.
 */
export function resolveBuildings(input: Building[], roads: RoadIndex | null = null): PlacedBuilding[] {
  const buildings = roads ? clearRoads(input, roads).buildings : input;
  const entries: Entry[] = [];
  buildings.forEach((data) => {
    const fp = prepareFootprint(data.ring);
    if (!fp) return;
    if (replacedByLandmark(fp, data.height)) return;
    const minH = Math.max(0, data.minHeight);
    const e: Entry = {
      i: entries.length,
      data,
      fp,
      minH,
      height: Math.max(data.height, minH + 3),
      x0: Infinity,
      x1: -Infinity,
      z0: Infinity,
      z1: -Infinity,
      samples: [],
      dropped: false,
      clipped: false,
      roof: "open",
    };
    for (const [x, z] of fp.pts) {
      e.x0 = Math.min(e.x0, x);
      e.x1 = Math.max(e.x1, x);
      e.z0 = Math.min(e.z0, z);
      e.z1 = Math.max(e.z1, z);
    }
    sampleFootprint(e);
    entries.push(e);
  });

  const cells = new Map<string, number[]>();
  for (const e of entries) {
    for (let cx = Math.floor(e.x0 / GRID); cx <= Math.floor(e.x1 / GRID); cx++) {
      for (let cz = Math.floor(e.z0 / GRID); cz <= Math.floor(e.z1 / GRID); cz++) {
        const key = `${cx},${cz}`;
        const list = cells.get(key);
        if (list) list.push(e.i);
        else cells.set(key, [e.i]);
      }
    }
  }
  const near: number[][] = entries.map((e) => {
    const out = new Set<number>();
    for (let cx = Math.floor(e.x0 / GRID); cx <= Math.floor(e.x1 / GRID); cx++) {
      for (let cz = Math.floor(e.z0 / GRID); cz <= Math.floor(e.z1 / GRID); cz++) {
        for (const j of cells.get(`${cx},${cz}`) ?? []) {
          const o = entries[j];
          if (j !== e.i && o.x0 <= e.x1 && o.x1 >= e.x0 && o.z0 <= e.z1 && o.z1 >= e.z0) out.add(j);
        }
      }
    }
    return [...out];
  });

  const frac = new Map<number, number>();
  const overlap = (b: number, a: number) => {
    const key = b * entries.length + a;
    let f = frac.get(key);
    if (f === undefined) {
      f = inside(entries[b], entries[a]);
      frac.set(key, f);
    }
    return f;
  };
  const strictlyInside = (b: Entry, a: Entry) => b.fp.area < a.fp.area * 0.98 || (b.fp.area <= a.fp.area * 1.02 && b.i > a.i);

  const parent = entries.map((e) => e.i);
  for (const e of entries) {
    for (const j of near[e.i]) {
      const o = entries[j];
      const gap = Math.max(e.minH, o.minH) - Math.min(e.height, o.height);
      if (gap <= 3 && (overlap(e.i, j) >= 0.6 || overlap(j, e.i) >= 0.6)) parent[find(parent, e.i)] = find(parent, j);
    }
  }

  // Outlines that their own parts replace.
  for (const a of entries) {
    const parts = near[a.i].map((j) => entries[j]).filter((b) => strictlyInside(b, a) && b.minH < a.height - 2 && overlap(b.i, a.i) >= INSIDE);
    if (!parts.length || Math.max(...parts.map((b) => b.height)) < a.height * 0.6) continue;
    const covered = a.samples.filter(([x, z]) => parts.some((b) => pointInRing(x, z, b.fp.pts))).length / a.samples.length;
    if (covered >= 0.55) a.dropped = true;
  }

  // What is left inside something else: drop the hidden, raise the ones that stick out above.
  for (const b of entries) {
    if (b.dropped) continue;
    for (const j of near[b.i]) {
      const a = entries[j];
      if (a.dropped || b.dropped || !strictlyInside(b, a) || overlap(b.i, a.i) < INSIDE) continue;
      if (Math.min(b.height, a.height) - Math.max(b.minH, a.minH) <= 0.5) continue;
      if (b.height <= a.height + 1 && b.minH >= a.minH - 1) {
        b.dropped = true;
      } else if (b.height > a.height + 1) {
        b.minH = Math.max(b.minH, a.height);
        b.clipped = true;
        if (b.height - b.minH < 3) b.dropped = true;
      } else {
        b.height = Math.min(b.height, a.minH);
        b.clipped = true;
        if (b.height - b.minH < 3) b.dropped = true;
      }
    }
  }

  // Partial overlaps at the same roof height would z-fight: drop the smaller if it is mostly covered, else step it down.
  for (const a of entries) {
    if (a.dropped) continue;
    for (const j of near[a.i]) {
      const b = entries[j];
      if (b.dropped || a.dropped || !strictlyInside(b, a) || Math.abs(a.height - b.height) > 1 || Math.min(a.height, b.height) - Math.max(a.minH, b.minH) <= 1.5) continue;
      const f = overlap(b.i, a.i);
      if (f < 0.2) continue;
      if (f >= 0.5) b.dropped = true;
      else if (b.height - b.minH > 4) b.height = a.height - 0.3 - (b.i % 5) * 0.05;
    }
  }

  const kept = entries.filter((e) => !e.dropped);
  for (const x of kept) {
    for (const j of near[x.i]) {
      const y = entries[j];
      if (y.dropped || y.minH <= x.minH || y.minH < x.height - 3 || y.minH > x.height + 1.5) continue;
      const f = overlap(x.i, y.i);
      if (f >= 0.85) x.roof = "hidden";
      else if (f >= 0.35 && x.roof === "open") x.roof = "partial";
    }
  }

  const groups = new Map<number, Entry[]>();
  for (const e of kept) {
    const root = find(parent, e.i);
    const list = groups.get(root);
    if (list) list.push(e);
    else groups.set(root, [e]);
  }
  const placed: PlacedBuilding[] = [];
  for (const members of groups.values()) {
    const owner = members.reduce((best, e) => (e.height > best.height ? e : best), members[0]);
    const lookHeight = owner.height;
    const lookSeed = Math.round(owner.fp.cx * 10) * 7919 + Math.round(owner.fp.cz * 10) * 104729 + 13;
    for (const e of members) {
      placed.push({
        fp: e.fp,
        minHeight: e.minH,
        height: e.height,
        colour: owner.data.colour ?? e.data.colour,
        lookSeed,
        lookHeight,
        part: members.length > 1 || e.minH > 0.5 || e.clipped || e.roof !== "open",
        roof: e.roof,
        carriesMast: e === owner,
      });
    }
  }
  return placed;
}
