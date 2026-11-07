"use client";

import { create } from "zustand";
import { useCameraStore, type CameraPreset } from "../scene/cameraStore";
import { getLastField } from "../sim/useField";
import { shownRun, usePredict } from "./store";
import type { PredictRun, ReportItem } from "./types";

/** Forecast minutes per real second at 1×. At the default 4× the 30 min play in ~60 s plus ~3 s per hotspot dwell. */
export const MIN_PER_S = 0.125;
export const DWELL_S = 12;
export const DEFAULT_SPEED = 4;
/** Fly to a hotspot this many forecast minutes before its change lands so the camera is there in time. */
const LEAD_MIN = 3;
const SHOT_DIST = 170;
const SHOT_HEIGHT = 95;

export interface Hotspot {
  item: ReportItem;
  x: number;
  z: number;
  caption: string;
  /** Forecast minute at which the camera starts flying here. */
  flyAt: number;
}

interface ReplayState {
  active: boolean;
  paused: boolean;
  speed: 1 | 2 | 4 | 8;
  /** Current hotspot being dwelt on, or -1 while travelling. */
  dwellIndex: number;
  hotspots: Hotspot[];
  /** Preset to return the camera to on exit. */
  returnTo: CameraPreset;
}

export const useReplay = create<ReplayState>(() => ({
  active: false,
  paused: false,
  speed: DEFAULT_SPEED,
  dwellIndex: -1,
  hotspots: [],
  returnTo: "streamer",
}));

function zoneCentroid(run: PredictRun, zoneId: string): { x: number; z: number } | null {
  const key = run.keyZoneSegments?.[zoneId];
  if (key) return { x: key.x, z: key.z };
  let n = 0;
  let x = 0;
  let z = 0;
  for (const p of run.personas) {
    if (p.zone !== zoneId || !p.origin) continue;
    x += p.origin.x;
    z += p.origin.z;
    n++;
  }
  if (n > 0) return { x: x / n, z: z / n };
  const segs = getLastField()?.segments;
  if (!segs) return null;
  for (const cs of run.camSegments ?? []) {
    if (cs.zoneId !== zoneId) continue;
    const f = segs.find((s) => s.id === cs.segmentId);
    if (f) return { x: (f.a[0] + f.b[0]) / 2, z: (f.a[1] + f.b[1]) / 2 };
  }
  return null;
}

function captionFor(it: ReportItem) {
  const sign = it.delta > 0 ? "+" : "−";
  const verb = it.delta > 0 ? "arriving" : "leaving";
  const why = it.reasons.find((r) => r.startsWith(verb)) ?? "";
  const groups = why
    .replace(/^\w+:\s*/, "")
    .replace(/_/g, " ")
    .split(",")
    .slice(0, 2)
    .map((g) => g.trim())
    .join(", ");
  return `${it.zone} ${sign}${Math.abs(Math.round(it.delta))} people${groups ? ` — ${groups} ${verb}` : ""}`;
}

export function buildHotspots(run: PredictRun): Hotspot[] {
  const ranked = run.report.items.slice().sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  const headline = ranked[0];
  // Why: the headline zone opens the replay; the rest follow in forecast order.
  const items = headline ? [headline, ...ranked.slice(1, 5).sort((a, b) => a.atMin - b.atMin)] : [];
  const out: Hotspot[] = [];
  let lastFly = -Infinity;
  for (const item of items) {
    const c = zoneCentroid(run, item.zoneId);
    if (!c) continue;
    // Why: two hotspots landing in the same minute would otherwise race the camera; space the flights out.
    const flyAt = item === headline ? 0 : Math.max(lastFly + 1, item.atMin - LEAD_MIN);
    lastFly = flyAt;
    out.push({ item, x: c.x, z: c.z, caption: captionFor(item), flyAt });
  }
  return out;
}

export function shotFor(h: Hotspot, index: number): { pos: [number, number, number]; target: [number, number, number] } {
  const ang = 0.6 + index * 1.9;
  return {
    pos: [h.x + Math.sin(ang) * SHOT_DIST, SHOT_HEIGHT, h.z + Math.cos(ang) * SHOT_DIST],
    target: [h.x, 6, h.z],
  };
}

export function overviewShot(hotspots: Hotspot[]): { pos: [number, number, number]; target: [number, number, number] } {
  let x = 0;
  let z = 0;
  for (const h of hotspots) {
    x += h.x;
    z += h.z;
  }
  const n = hotspots.length || 1;
  x /= n;
  z /= n;
  return { pos: [x + 260, 320, z + 520], target: [x, 0, z] };
}

export function startReplay() {
  const run = shownRun(usePredict.getState());
  if (!run) return;
  const hotspots = buildHotspots(run);
  const cam = useCameraStore.getState();
  const returnTo = cam.cinematic ? useReplay.getState().returnTo : cam.preset;
  usePredict.setState({ playing: false, minute: 0, reportOpen: false, ghosts: true });
  useReplay.setState({ active: true, paused: false, dwellIndex: -1, hotspots, returnTo });
  cam.setCinematic(hotspots.length ? shotFor(hotspots[0], 0) : overviewShot(hotspots));
}

export function stopReplay() {
  const { returnTo, active } = useReplay.getState();
  if (!active) return;
  useReplay.setState({ active: false, paused: false, dwellIndex: -1 });
  usePredict.setState({ minute: 30, playing: false });
  useCameraStore.getState().fly(returnTo);
}

export function skipHotspot() {
  const r = useReplay.getState();
  if (!r.active) return;
  const minute = usePredict.getState().minute;
  const next = r.hotspots.find((h) => h.flyAt > minute + 0.01);
  if (!next) {
    stopReplay();
    return;
  }
  usePredict.setState({ minute: Math.max(0, next.item.atMin - 0.1) });
  useReplay.setState({ dwellIndex: -1 });
}
