import { create } from "zustand";
import type { CamFrame } from "../live/project";

import { BANKSIDE_VIEW } from "./Bankside";

export type CameraPreset = "aerial" | "street" | "skyline" | "bankside" | "streamer";

export const PRESETS: Record<CameraPreset, { pos: [number, number, number]; target: [number, number, number]; label: string }> = {
  aerial: { pos: [470, 270, 640], target: [-140, 0, -140], label: "Aerial" },
  street: { pos: [60, 26, 150], target: [-60, 10, -120], label: "Street" },
  skyline: { pos: [240, 70, 320], target: [-420, 110, -500], label: "Skyline" },
  bankside: BANKSIDE_VIEW,
  /** First person at the streamer; the rig follows the live position, so pos/target are placeholders. */
  streamer: { pos: [0, 1.7, 0], target: [0, 1.7, -10], label: "Streamer view" },
};

/** A real CCTV pose the rig reproduces: the same pinhole camera the ground projection uses (live/project.ts). */
export type CctvPose = CamFrame;

interface CameraState {
  preset: CameraPreset;
  /** Increments on every request so re-clicking the active preset still flies. */
  flight: number;
  /** When set, the camera sits at this real CCTV pose and the preset is ignored. */
  cctv: CctvPose | null;
  /** Jump straight to the CCTV pose instead of flying (used while aligning a pose by hand). */
  snap: boolean;
  /** A scripted shot (Predict replay): the rig flies here and ignores the preset until cleared. */
  cinematic: { pos: [number, number, number]; target: [number, number, number] } | null;
  fly: (preset: CameraPreset) => void;
  setCctv: (pose: CctvPose | null, snap?: boolean) => void;
  setCinematic: (shot: CameraState["cinematic"]) => void;
}

export const useCameraStore = create<CameraState>((set) => ({
  preset: "streamer",
  flight: 0,
  cctv: null,
  snap: false,
  cinematic: null,
  fly: (preset) => set((s) => ({ preset, flight: s.flight + 1, cctv: null, snap: false, cinematic: null })),
  setCctv: (cctv, snap = false) => set((s) => ({ cctv, snap, flight: s.flight + 1, cinematic: null })),
  setCinematic: (cinematic) => set((s) => ({ cinematic, flight: s.flight + 1 })),
}));
