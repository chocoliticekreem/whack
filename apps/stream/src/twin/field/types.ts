import type { TwinGeometry } from "@/twin/config";

export type SegmentKind = TwinGeometry["roads"][number]["kind"];

/** One ≤40 m piece of road with its current estimated load. Densities are per 100 m of segment. */
export interface FieldSegment {
  id: number;
  a: [number, number];
  b: [number, number];
  kind: SegmentKind;
  peds: number;
  vehicles: number;
  speedKph?: number;
  source: "observed" | "inferred";
  confidence: number;
}

export interface FieldAnchor {
  name: string;
  x: number;
  z: number;
  weight: number;
  kind: "station" | "poi";
}

export interface FieldCam {
  id: string;
  x: number;
  z: number;
  people: number;
  vehicles: number;
  at: number;
}

export interface FieldResponse {
  at: number;
  segments: FieldSegment[];
  anchors: FieldAnchor[];
  cams: FieldCam[];
}
