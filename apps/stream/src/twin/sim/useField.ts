"use client";

import { useEffect } from "react";
import { create } from "zustand";
import { fetchJson, startPoller } from "@/lib/client/poll";
import { useDemoSettings } from "@/platform/demo";
import type { FieldCameras, FieldResponse } from "@/twin/field/types";
import { camFrame, type CamFrame } from "@/twin/live/project";
import { useLive } from "@/twin/live/store";
import { currentOrigin } from "@/twin/config";
import { useOrigin } from "@/twin/origin";

const FIELD_MS = 30_000;

export interface FieldSummary {
  at: number;
  observed: number;
  inferred: number;
  /** Σ density × length over every segment: the city-wide estimate before the agent cap. */
  people: number;
  vehicles: number;
  /** Crowd multiplier applied on top of the estimate (1 in live mode). */
  crowd: number;
  /** Camera coverage behind `observed`; null from a server that predates it. */
  cameras: FieldCameras | null;
}

export const useFieldStore = create<FieldSummary | null>(() => null);

/** Calibrated camera footprints that already carry YOLO-tracked agents; field-driven agents stay out. */
export const useCoveredFrames = create<{ frames: CamFrame[] }>(() => ({ frames: [] }));

function summarise(f: FieldResponse, crowd: number): FieldSummary {
  let observed = 0;
  let people = 0;
  let vehicles = 0;
  for (const s of f.segments) {
    const len = Math.hypot(s.b[0] - s.a[0], s.b[1] - s.a[1]) / 100;
    people += s.peds * len;
    vehicles += s.vehicles * len;
    if (s.source === "observed") observed++;
  }
  return { at: f.at, observed, inferred: f.segments.length - observed, people, vehicles, crowd, cameras: f.cameras ?? null };
}

let refs = 0;
let stop: (() => void) | null = null;
let lastField: FieldResponse | null = null;
let crowdNow = 1;

/** The most recent field payload; changes whenever useFieldStore's `at` does. */
export function getLastField() {
  return lastField;
}

function start() {
  // A failed poll keeps the last field on screen; only a new origin clears it.
  const poller = startPoller(async (signal) => {
    const o = currentOrigin();
    const r = await fetchJson<FieldResponse>(`/api/twin/field?lat=${o.lat}&lon=${o.lon}`, signal, 30_000);
    if (!r.ok) return r;
    if (!Array.isArray(r.data.segments)) return false;
    // The server caches the field; an unchanged one must not re-seed the movers.
    if (lastField?.at === r.data.at) return true;
    lastField = r.data;
    useFieldStore.setState(summarise(r.data, crowdNow));
    return true;
  }, FIELD_MS);
  const unsub = useOrigin.subscribe((s, prev) => {
    if (s.version === prev.version) return;
    lastField = null;
    useFieldStore.setState(null);
    poller.tick();
  });
  return () => {
    poller.stop();
    unsub();
  };
}

/** Polls /api/twin/field and keeps the crowd multiplier and covered cams in sync for the movers. */
export function useField() {
  const crowd = useDemoSettings().crowd;
  const cams = useLive((s) => s.cams);
  const clips = useLive((s) => s.clips);
  useEffect(() => {
    if (refs++ === 0) stop = start();
    return () => {
      if (--refs === 0) {
        stop?.();
        stop = null;
      }
    };
  }, []);
  useEffect(() => {
    crowdNow = crowd;
    if (lastField) useFieldStore.setState(summarise(lastField, crowd));
  }, [crowd]);
  useEffect(() => {
    useCoveredFrames.setState({ frames: cams.filter((c) => clips[c.id]?.tracks?.length).map(camFrame) });
  }, [cams, clips]);
}
