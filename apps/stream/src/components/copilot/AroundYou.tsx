"use client";

import { useEffect, useState } from "react";
import type { AroundAir, AroundDisruption, AroundStation, AroundTide } from "@/platform/around/types";
import { subscribe } from "@/platform/bus";
import { LEVEL } from "./Advisor";

type Level = "calm" | "busy" | "danger";
const REFRESH_MS = 60_000;
const STATION_RADIUS_M = 900;
const DISRUPTION_RADIUS_M = 800;

const AIR_TONE: Record<AroundAir["band"], string> = {
  good: "text-ok",
  fair: "text-ok",
  moderate: "text-warn",
  poor: "text-live",
  "very poor": "text-live",
};

export function useSurroundingsStatus() {
  const [status, setStatus] = useState<{ summary: string; level: Level } | null>(null);
  useEffect(
    () =>
      subscribe((e) => {
        if (e.type === "surroundings.update") setStatus({ summary: e.summary, level: e.level });
      }),
    [],
  );
  return status;
}

async function getJson<T>(url: string, signal: AbortSignal): Promise<T | null> {
  const res = await fetch(url, { signal }).catch(() => null);
  return res?.ok ? ((await res.json()) as T) : null;
}

function useNearby(point: { lat: number; lon: number }, enabled: boolean) {
  const [stations, setStations] = useState<AroundStation[]>([]);
  const [disruptions, setDisruptions] = useState<AroundDisruption[]>([]);
  const [air, setAir] = useState<AroundAir | null>(null);
  const [tide, setTide] = useState<AroundTide | null>(null);
  const { lat, lon } = point;
  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    const q = `lat=${lat}&lon=${lon}`;
    const load = async () => {
      const [s, d, a, t] = await Promise.all([
        getJson<{ stations: AroundStation[] }>(`/api/around/stations?${q}&radius=${STATION_RADIUS_M}`, ctrl.signal),
        getJson<{ items: AroundDisruption[] }>(`/api/around/disruptions?${q}&radius=${DISRUPTION_RADIUS_M}`, ctrl.signal),
        getJson<AroundAir>(`/api/around/air?${q}`, ctrl.signal),
        getJson<AroundTide>(`/api/around/tide?${q}`, ctrl.signal),
      ]);
      if (ctrl.signal.aborted) return;
      if (s) setStations(s.stations.slice(0, 3));
      if (d) setDisruptions(uniqueStreets(d.items).slice(0, 3));
      if (a && "eaqi" in a) setAir(a);
      if (t && "levelM" in t) setTide(t);
    };
    void load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      ctrl.abort();
      clearInterval(id);
    };
  }, [lat, lon, enabled]);
  return { stations, disruptions, air, tide };
}

/** TfL repeats one closure per carriageway and shouts the street in caps with a postcode list. */
function uniqueStreets(items: AroundDisruption[]) {
  const seen = new Set<string>();
  return items
    .map((d) => ({ ...d, street: d.street.replace(/\s*\(.*\)\s*$/, "").toLowerCase() }))
    .filter((d) => !seen.has(d.street) && seen.add(d.street));
}

function crowdingTone(c: number | null) {
  if (c === null) return "text-dim";
  if (c >= 1.6) return "text-live";
  if (c >= 1.2) return "text-warn";
  return "text-ok";
}

/** Surroundings summary from the bus plus the nearest stations, disruptions and air/tide chips. */
export function AroundYou({ location, live }: { location: { lat: number; lon: number }; live: boolean }) {
  const status = useSurroundingsStatus();
  const { stations, disruptions, air, tide } = useNearby(location, live);
  const level = status ? LEVEL[status.level] : null;

  return (
    <div className="flex flex-col gap-3">
      {status && level ? (
        <div className="flex items-start gap-2.5">
          <span className={`mt-[7px] size-2 shrink-0 rounded-full ${level.dot}`} aria-hidden />
          <p className="text-[13px] leading-5 text-text">
            <span className={`font-semibold ${level.text}`}>{level.label}</span>
            <span className="text-muted"> · {status.summary}</span>
          </p>
        </div>
      ) : (
        <p className="text-[13px] leading-5 text-muted">{live ? "Counting people on the nearest cameras…" : "Go live to read the street around you."}</p>
      )}

      {stations.length > 0 && (
        <ul className="flex flex-col divide-y divide-line rounded-lg border border-line bg-surface-2">
          {stations.map((s) => (
            <li key={s.id} className="flex items-center gap-3 px-3 py-2 text-[12px]">
              <span className="min-w-0 flex-1 truncate font-medium text-text">{s.name}</span>
              <span className="shrink-0 text-dim">{Math.round(s.distanceM)} m</span>
              <span className={`shrink-0 font-mono ${crowdingTone(s.crowding)}`}>
                {s.crowding === null ? "no data" : `${Math.round(s.crowding * 100)}%`}
              </span>
            </li>
          ))}
        </ul>
      )}

      {disruptions.length > 0 && (
        <ul className="flex flex-col gap-1 text-[12px] leading-5">
          {disruptions.map((d) => (
            <li key={d.id} className="flex gap-2">
              <span className={`mt-2 size-1 shrink-0 rounded-full ${d.closure === "Closed" ? "bg-live" : "bg-warn"}`} aria-hidden />
              <span className="min-w-0 text-muted">
                <span className="font-medium capitalize text-text">{d.street}</span> · {d.closure === "Closed" ? "closed" : d.severity.toLowerCase()} ·{" "}
                {Math.round(d.distanceM)} m
              </span>
            </li>
          ))}
        </ul>
      )}

      {(air || tide) && (
        <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
          {air && (
            <span className="rounded-md border border-line px-2 py-0.5 text-muted" title={`PM2.5 ${air.pm25} · NO₂ ${air.no2} µg/m³`}>
              Air <span className={AIR_TONE[air.band]}>{air.band}</span> {air.eaqi}
            </span>
          )}
          {tide && (
            <span className="rounded-md border border-line px-2 py-0.5 text-muted" title={`EA gauge at ${tide.station}`}>
              Thames {tide.levelM.toFixed(1)} m {tide.trend}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
