import type { TwinGeometry } from "@/twin/config";
import { SEGMENT_MAX_M, SNAP_M } from "./model";
import type { SegmentKind } from "./types";

export interface GraphSegment {
  id: number;
  a: [number, number];
  b: [number, number];
  kind: SegmentKind;
  length: number;
}

/** Splits OSM polylines into ≤SEGMENT_MAX_M pieces and snaps endpoints within SNAP_M to one point. */
export function buildSegments(roads: TwinGeometry["roads"]): GraphSegment[] {
  const grid = new Map<string, [number, number]>();
  const cell = SNAP_M;
  const snap = (p: [number, number]): [number, number] => {
    const cx = Math.floor(p[0] / cell);
    const cz = Math.floor(p[1] / cell);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        const hit = grid.get(`${cx + dx},${cz + dz}`);
        if (hit && Math.hypot(hit[0] - p[0], hit[1] - p[1]) <= SNAP_M) return hit;
      }
    }
    grid.set(`${cx},${cz}`, p);
    return p;
  };

  const out: GraphSegment[] = [];
  for (const road of roads) {
    if (road.kind === "rail") continue;
    const line = road.line;
    for (let i = 1; i < line.length; i++) {
      const p0 = line[i - 1];
      const p1 = line[i];
      const len = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
      if (len < 0.5) continue;
      const parts = Math.max(1, Math.ceil(len / SEGMENT_MAX_M));
      let prev = snap([p0[0], p0[1]]);
      for (let k = 1; k <= parts; k++) {
        const t = k / parts;
        const raw: [number, number] = [p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t];
        const next = k === parts ? snap(raw) : raw;
        const l = Math.hypot(next[0] - prev[0], next[1] - prev[1]);
        if (l > 0.5) out.push({ id: out.length, a: prev, b: next, kind: road.kind, length: l });
        prev = next;
      }
    }
  }
  return out;
}
