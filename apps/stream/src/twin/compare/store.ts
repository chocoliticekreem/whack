"use client";

import { create } from "zustand";
import { camFrame } from "../live/project";
import { useLive } from "../live/store";
import { useCameraStore, type CameraPreset } from "../scene/cameraStore";

/**
 * Per-frame time source for the camera being compared. Agents read `video.currentTime` here instead
 * of the wall clock so the simulated figures cross exactly when the real ones do. Plain object, not
 * zustand: it is read inside useFrame.
 */
export const syncClock: { camId: string | null; video: HTMLVideoElement | null; endedAt: number | null } = {
  camId: null,
  video: null,
  // performance.now() when a TfL clip finished: clips play once (no loop), and the twin's clock runs on past the end.
  endedAt: null,
};

interface CompareState {
  camId: string | null;
  returnTo: CameraPreset;
  open: (camId: string) => void;
  close: () => void;
}

export const useCompare = create<CompareState>((set, get) => ({
  camId: null,
  returnTo: "aerial",
  open: (camId) => {
    const cam = useLive.getState().cams.find((c) => c.id === camId);
    if (!cam) return;
    const cameraStore = useCameraStore.getState();
    const returnTo = get().camId ? get().returnTo : cameraStore.preset;
    syncClock.camId = camId;
    cameraStore.setCctv(camFrame(cam));
    set({ camId, returnTo });
  },
  close: () => {
    syncClock.camId = null;
    syncClock.video = null;
    useCameraStore.getState().fly(get().returnTo);
    set({ camId: null });
  },
}));
