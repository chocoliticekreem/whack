import type { YoloStatus } from "@/platform/around/types";
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

/**
 * How much of the field comes from cameras. `status` is YOLO's: "unavailable" means the camera
 * counts are missing (service down), so every segment is inferred, not that the streets are empty.
 */
export interface FieldCameras {
  inRange: number;
  observed: number;
  status: YoloStatus;
  /** Newest reading used, epoch ms; 0 when none. */
  newestAt: number;
}

export interface FieldResponse {
  at: number;
  segments: FieldSegment[];
  anchors: FieldAnchor[];
  cams: FieldCam[];
  cameras?: FieldCameras;
}
