"use client";

import { useEffect } from "react";
import { publish } from "@/platform/bus";
import type { AroundCam, AroundDisruption, AroundStation, CamReading } from "./types";

const REFRESH_MS = 30_000;
const NEAR_CAMS = 3;
const NEAR_DISRUPTION_M = 400;
const NEAR_STATION_M = 600;
/** TfL crowding share of a normal day above which a station counts as busy. */
const BUSY_STATION = 1.2;

type Level = "calm" | "busy" | "danger";

function levelFor(people: number, closures: number, busyStations: number): Level {
  if (people >= 20) return "danger";
  if (people >= 8 || closures > 0 || busyStations > 0) return "busy";
  return "calm";
}

async function json<T>(url: string, signal: AbortSignal): Promise<T | null> {
  const res = await fetch(url, { signal }).catch(() => null);
  return res?.ok ? ((await res.json()) as T) : null;
}

/**
 * Publishes `surroundings.update` from real data: YOLO counts on the nearest TfL cameras plus
 * road closures close by. Consumers: the stream's SurroundingsCard and the 3D twin's HUD.
 */
export function useSurroundings(point: { lat: number; lon: number }, enabled: boolean) {
  const { lat, lon } = point;
  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    const run = async () => {
      const cams = await json<{ cams: AroundCam[] }>(`/api/around/cams?lat=${lat}&lon=${lon}&radius=500`, ctrl.signal);
      const near = (cams?.cams ?? []).slice(0, NEAR_CAMS);
      if (near.length === 0) return;
      const [counts, disruptions, stations] = await Promise.all([
        json<{ readings: CamReading[] }>(`/api/around/counts?ids=${near.map((c) => c.id).join(",")}`, ctrl.signal),
        json<{ items: AroundDisruption[] }>(
          `/api/around/disruptions?lat=${lat}&lon=${lon}&radius=${NEAR_DISRUPTION_M}`,
          ctrl.signal,
        ),
        json<{ stations: AroundStation[] }>(
          `/api/around/stations?lat=${lat}&lon=${lon}&radius=${NEAR_STATION_M}`,
          ctrl.signal,
        ),
      ]);
      const readings = counts?.readings ?? [];
      if (readings.length === 0) return;
      const busiest = readings.reduce((a, b) => (b.people > a.people ? b : a));
      const cam = near.find((c) => c.id === busiest.camId);
      const closures = (disruptions?.items ?? []).filter((d) => d.closure === "Closed").length;
      const busyStations = (stations?.stations ?? []).filter((s) => (s.crowding ?? 0) >= BUSY_STATION);
      const parts = [
        `${busiest.people} people and ${busiest.vehicles} vehicles at ${cam?.name ?? "the nearest camera"}`,
        closures > 0 ? `${closures} road closure${closures === 1 ? "" : "s"} within ${NEAR_DISRUPTION_M} m` : null,
        busyStations.length > 0
          ? `${busyStations[0].name} station at ${Math.round(busyStations[0].crowding! * 100)}% of normal`
          : null,
      ].filter(Boolean);
      publish({
        type: "surroundings.update",
        summary: parts.join(" · "),
        level: levelFor(busiest.people, closures, busyStations.length),
      });
    };
    run();
    const id = setInterval(run, REFRESH_MS);
    return () => {
      ctrl.abort();
      clearInterval(id);
    };
  }, [lat, lon, enabled]);
}
