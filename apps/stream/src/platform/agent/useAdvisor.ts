"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { StreamLocation } from "@/lib/types";
import type { AroundCam, CamReading } from "@/platform/around/types";
import type { Assessment, AssessRequest } from "./types";

const REFRESH_MS = 60_000;
const LOCATION_DEBOUNCE_MS = 1500;
const DEFAULT_QUESTION = "Is it safe to keep streaming here, and should I tell chat where I am?";

async function json<T>(url: string, init?: RequestInit): Promise<T | null> {
  const res = await fetch(url, init).catch(() => null);
  return res?.ok ? ((await res.json()) as T) : null;
}

async function nearestYolo(loc: StreamLocation): Promise<AssessRequest["yolo"]> {
  const cams = await json<{ cams: AroundCam[] }>(`/api/around/cams?lat=${loc.lat}&lon=${loc.lon}&radius=500`);
  const near = (cams?.cams ?? []).slice(0, 3);
  if (near.length === 0) return undefined;
  const counts = await json<{ readings: CamReading[] }>(`/api/around/counts?ids=${near.map((c) => c.id).join(",")}`);
  const readings = counts?.readings ?? [];
  if (readings.length === 0) return undefined;
  const busiest = readings.reduce((a, b) => (b.people > a.people ? b : a));
  const cam = near.find((c) => c.id === busiest.camId);
  return { people: busiest.people, vehicles: busiest.vehicles, camera: cam?.name ?? busiest.camId };
}

export interface AdvisorState {
  assessment: Assessment | null;
  error: string | null;
  loading: boolean;
  question: string;
}

/** Asks the CAMP agent about the streamer's spot every minute while live, and on demand. */
export function useAdvisor(location: StreamLocation, live: boolean) {
  const [state, setState] = useState<AdvisorState>({ assessment: null, error: null, loading: false, question: "" });
  const inflight = useRef<AbortController | null>(null);
  // Read through a ref: a new location object every relay tick must not restart (and abort) a 15-20 s assessment.
  const locationRef = useRef(location);
  useEffect(() => {
    locationRef.current = location;
  }, [location]);

  const assess = useCallback(
    async (question = DEFAULT_QUESTION, background = false) => {
      if (background && inflight.current) return;
      inflight.current?.abort();
      const ctrl = new AbortController();
      inflight.current = ctrl;
      const location = locationRef.current;
      setState((s) => ({ ...s, loading: true, question: question === DEFAULT_QUESTION ? "" : question }));
      const yolo = await nearestYolo(location);
      const body: AssessRequest = { question, location: { name: location.name, lat: location.lat, lon: location.lon }, yolo };
      const res = await fetch("/api/agent/assess", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      }).catch(() => null);
      if (ctrl.signal.aborted) return;
      inflight.current = null;
      if (!res) {
        setState((s) => ({ ...s, loading: false, error: "Advisor offline" }));
        return;
      }
      const data = (await res.json().catch(() => null)) as Assessment | { error: string } | null;
      if (!res.ok || !data || "error" in data) {
        setState((s) => ({ ...s, loading: false, error: data && "error" in data ? data.error : "Advisor offline" }));
        return;
      }
      setState({ assessment: data, error: null, loading: false, question: question === DEFAULT_QUESTION ? "" : question });
    },
    [],
  );

  // Re-assess only when the streamer has really moved (~100 m grid) or the place name changes.
  const placeKey = `${location.name}|${location.lat.toFixed(3)}|${location.lon.toFixed(3)}`;
  useEffect(() => {
    if (!live) return;
    const first = setTimeout(() => void assess(DEFAULT_QUESTION, true), LOCATION_DEBOUNCE_MS);
    const id = setInterval(() => void assess(DEFAULT_QUESTION, true), REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [live, assess, placeKey]);

  useEffect(() => () => inflight.current?.abort(), []);

  return { ...state, assess };
}
