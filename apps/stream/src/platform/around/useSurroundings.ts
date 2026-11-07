"use client";

import { useEffect } from "react";
import { fetchJson, startPoller } from "@/lib/client/poll";
import { publish, type SurroundingsLevel } from "@/platform/bus";
import type { AroundCam, AroundDisruption, AroundStation, CamReading } from "./types";

const REFRESH_MS = 30_000;
const NEAR_CAMS = 3;
const NEAR_DISRUPTION_M = 400;
const NEAR_STATION_M = 600;
/** TfL crowding share of a normal day above which a station counts as busy. */
const BUSY_STATION = 1.2;
/** Past this a count is dropped entirely (the twin's field drops readings at the same age). */
const READING_MAX_AGE_MS = 30 * 60_000;
/** Only counts younger than this drive the level; older ones are shown as a stale note. */
const READING_FRESH_MS = 5 * 60_000;

function levelFor(people: number | null, closures: number, busyStations: number): SurroundingsLevel {
  if (people !== null && people >= 20) return "danger";
  if ((people ?? 0) >= 8 || closures > 0 || busyStations > 0) return "busy";
  return people === null ? "unknown" : "calm";
}

const busiestOf = (rs: CamReading[]) => (rs.length ? rs.reduce((a, b) => (b.people > a.people ? b : a)) : null);


/**
 * Publishes `surroundings.update` from real data: YOLO counts on the nearest TfL cameras plus
 * road closures and busy stations close by. Consumers: the stream's SurroundingsCard and the 3D twin's HUD.
 * Each source keeps its last good value, so one failed call never blanks the card. Without a count
 * from the last 5 min the level is "unknown" (or "busy" from closures/stations), never "calm".
 */
export function useSurroundings(point: { lat: number; lon: number }, enabled: boolean) {
  const { lat, lon } = point;
  useEffect(() => {
    if (!enabled) return;
    let near: AroundCam[] = [];
    let readings: CamReading[] = [];
    let closures: number | null = null;
    let busyStations: AroundStation[] = [];
    const poller = startPoller(async (signal) => {
      const cams = await fetchJson<{ cams: AroundCam[] }>(`/api/around/cams?lat=${lat}&lon=${lon}&radius=500`, signal);
      if (cams.ok) near = (cams.data.cams ?? []).slice(0, NEAR_CAMS);
      const [counts, disruptions, stations] = await Promise.all([
        near.length
          ? fetchJson<{ readings: CamReading[] }>(`/api/around/counts?ids=${near.map((c) => c.id).join(",")}`, signal)
          : null,
        fetchJson<{ items: AroundDisruption[] }>(
          `/api/around/disruptions?lat=${lat}&lon=${lon}&radius=${NEAR_DISRUPTION_M}`,
          signal,
        ),
        fetchJson<{ stations: AroundStation[] }>(`/api/around/stations?lat=${lat}&lon=${lon}&radius=${NEAR_STATION_M}`, signal),
      ]);
      if (counts?.ok && counts.data.readings?.length) readings = counts.data.readings;
      readings = readings.filter((r) => Date.now() - r.at < READING_MAX_AGE_MS);
      if (disruptions.ok) closures = (disruptions.data.items ?? []).filter((d) => d.closure === "Closed").length;
      if (stations.ok) busyStations = (stations.data.stations ?? []).filter((s) => (s.crowding ?? 0) >= BUSY_STATION);
      const fresh = busiestOf(readings.filter((r) => Date.now() - r.at < READING_FRESH_MS));
      const stale = fresh ? null : busiestOf(readings);
      const parts = [
        fresh
          ? `${fresh.people} people`
          : stale
            ? `${stale.people} people (old)`
            : near.length || !cams.ok
              ? "cams offline"
              : "no cams nearby",
        closures ? `${closures} closure${closures === 1 ? "" : "s"}` : null,
        busyStations.length > 0 ? `${busyStations[0].name} ${Math.round(busyStations[0].crowding! * 100)}%` : null,
      ].filter(Boolean);
      publish({
        type: "surroundings.update",
        summary: parts.join(" · "),
        level: levelFor(fresh?.people ?? null, closures ?? 0, busyStations.length),
      });
      if (readings.length === 0 && closures === null && busyStations.length === 0) return cams.ok ? false : cams;
      return true;
    }, REFRESH_MS);
    return () => poller.stop();
  }, [lat, lon, enabled]);
}
