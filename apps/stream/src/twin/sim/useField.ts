"use client";

import { useEffect } from "react";
import { create } from "zustand";
import { useDemoSettings } from "@/platform/demo";
import type { FieldResponse } from "@/twin/field/types";
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
  return { at: f.at, observed, inferred: f.segments.length - observed, people, vehicles, crowd };
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
  let ctrl: AbortController | null = null;
  let stopped = false;
  const tick = async () => {
    ctrl?.abort();
    ctrl = new AbortController();
    try {
      const o = currentOrigin();
      const res = await fetch(`/api/twin/field?lat=${o.lat}&lon=${o.lon}`, { signal: ctrl.signal, cache: "no-store" });
      if (!res.ok) return;
      const f = (await res.json()) as FieldResponse;
      if (stopped || !Array.isArray(f.segments)) return;
      lastField = f;
      useFieldStore.setState(summarise(f, crowdNow));
    } catch {
      // aborted or offline; next tick retries
    }
  };
  void tick();
  const timer = setInterval(() => void tick(), FIELD_MS);
  const unsub = useOrigin.subscribe((s, prev) => {
    if (s.version === prev.version) return;
    lastField = null;
    useFieldStore.setState(null);
    void tick();
  });
  return () => {
    stopped = true;
    ctrl?.abort();
    clearInterval(timer);
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
