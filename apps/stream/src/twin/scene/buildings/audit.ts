import type { TwinGeometry } from "../../config";
import { ringRoadDepth, type RoadIndex } from "./clearance";
import { onHeroPlot } from "./overlaps";
import { pointInRing, prepareFootprint, type Footprint } from "./footprint";

type Building = TwinGeometry["buildings"][number];

export interface AuditItem {
  fp: Footprint;
  minHeight: number;
  height: number;
}

export interface AuditReport {
  buildings: number;
  /** Pairs whose footprints overlap by at least a fifth of the smaller one while their height ranges overlap by more than 1.5 m. */
  overlappingPairs: number;
  /** Subset of those where the smaller footprint is almost entirely inside the other (walls inside walls). */
  containedPairs: number;
  /** Buildings with a footprint inside a carriageway by more than 1.5 m. */
  roadIntrusions: number;
  worstRoadMetres: number;
  /** Buildings with a vertex or centre inside a Bankside hero-model plot. */
  heroPlotOverlaps: number;
}

const GRID = 60;
const SAMPLES = 7;

function samplePoints(fp: Footprint): [number, number][] {
  const xs = fp.pts.map((p) => p[0]);
  const zs = fp.pts.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const z0 = Math.min(...zs);
  const z1 = Math.max(...zs);
  const out: [number, number][] = [];
  for (let i = 0; i < SAMPLES; i++) {
    for (let j = 0; j < SAMPLES; j++) {
      const x = x0 + ((i + 0.5) / SAMPLES) * (x1 - x0);
      const z = z0 + ((j + 0.5) / SAMPLES) * (z1 - z0);
      if (pointInRing(x, z, fp.pts)) out.push([x, z]);
    }
  }
  return out.length ? out : [[fp.cx, fp.cz]];
}

export function auditItems(items: AuditItem[], roads: RoadIndex | null): AuditReport {
  const samples = items.map((it) => samplePoints(it.fp));
  const boxes = items.map((it) => {
    const xs = it.fp.pts.map((p) => p[0]);
    const zs = it.fp.pts.map((p) => p[1]);
    return [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  });
  const cells = new Map<string, number[]>();
  boxes.forEach((b, i) => {
    for (let cx = Math.floor(b[0] / GRID); cx <= Math.floor(b[1] / GRID); cx++) {
      for (let cz = Math.floor(b[2] / GRID); cz <= Math.floor(b[3] / GRID); cz++) {
        const key = `${cx},${cz}`;
        const list = cells.get(key);
        if (list) list.push(i);
        else cells.set(key, [i]);
      }
    }
  });
  const seen = new Set<number>();
  let overlappingPairs = 0;
  let containedPairs = 0;
  const share = (a: number, b: number) => samples[a].filter(([x, z]) => pointInRing(x, z, items[b].fp.pts)).length / samples[a].length;
  for (const list of cells.values()) {
    for (const i of list) {
      for (const j of list) {
        if (j <= i) continue;
        const key = i * items.length + j;
        if (seen.has(key)) continue;
        seen.add(key);
        const a = items[i];
        const b = items[j];
        if (Math.min(a.height, b.height) - Math.max(a.minHeight, b.minHeight) <= 1.5) continue;
        if (boxes[i][0] > boxes[j][1] || boxes[i][1] < boxes[j][0] || boxes[i][2] > boxes[j][3] || boxes[i][3] < boxes[j][2]) continue;
        const f = Math.max(share(i, j), share(j, i));
        if (f >= 0.2) overlappingPairs++;
        if (f >= 0.8) containedPairs++;
      }
    }
  }

  let roadIntrusions = 0;
  let worstRoadMetres = 0;
  if (roads) {
    for (const it of items) {
      const depth = ringRoadDepth(it.fp.pts, roads);
      if (depth > 1.5) roadIntrusions++;
      worstRoadMetres = Math.max(worstRoadMetres, depth);
    }
  }
  const heroPlotOverlaps = items.filter((it) => it.fp.pts.some(([x, z]) => onHeroPlot(x, z)) || onHeroPlot(it.fp.cx, it.fp.cz)).length;
  return { buildings: items.length, overlappingPairs, containedPairs, roadIntrusions, worstRoadMetres: +worstRoadMetres.toFixed(1), heroPlotOverlaps };
}

/** Audit of raw OSM footprints, exactly as they arrive (no dedupe, no clearance). */
export function auditRaw(buildings: Building[], roads: RoadIndex | null): AuditReport {
  const items: AuditItem[] = [];
  for (const b of buildings) {
    const fp = prepareFootprint(b.ring);
    if (fp) items.push({ fp, minHeight: Math.max(0, b.minHeight), height: Math.max(b.height, b.minHeight + 3) });
  }
  return auditItems(items, roads);
}
