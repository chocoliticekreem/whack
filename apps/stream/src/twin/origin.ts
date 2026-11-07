"use client";

import { create } from "zustand";
import { distanceM } from "@/platform/around/geo";
import { currentOrigin, setCurrentOrigin, snapOrigin, TOWER_HILL, type LatLon } from "./config";

/** Demo stops for the dev panel; also name the area the twin is centred on. */
export const PLACES: { id: string; name: string; lat: number; lon: number }[] = [
  { id: "tower-hill", name: "Tower Hill", lat: TOWER_HILL.lat, lon: TOWER_HILL.lon },
  { id: "trafalgar", name: "Trafalgar Square", lat: 51.508, lon: -0.1281 },
  { id: "oxford-circus", name: "Oxford Circus", lat: 51.5152, lon: -0.1419 },
  { id: "waterloo", name: "Southbank / Waterloo", lat: 51.5045, lon: -0.1138 },
  { id: "shoreditch", name: "Shoreditch", lat: 51.5246, lon: -0.0776 },
  { id: "camden", name: "Camden", lat: 51.5392, lon: -0.1426 },
];

/** How far the streamer may drift from the origin before the twin re-centres. */
export const RECENTRE_M = 400;
/** Fade duration around the swap, ms. */
export const SWAP_FADE_MS = 350;

interface OriginState {
  origin: LatLon;
  /** Bumps on every swap; scene subtrees keyed on it rebuild their local-metre caches. */
  version: number;
  /** Area being loaded ahead of a swap, null when settled. */
  pending: LatLon | null;
  /** True while the fade overlay hides the rebuild. */
  swapping: boolean;
}

function initialOrigin(): LatLon {
  if (typeof window === "undefined") return TOWER_HILL;
  const params = new URLSearchParams(window.location.search);
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  if (params.has("lat") && params.has("lon") && Number.isFinite(lat) && Number.isFinite(lon)) return snapOrigin(lat, lon);
  return TOWER_HILL;
}

const first = initialOrigin();
setCurrentOrigin(first);

export const useOrigin = create<OriginState>(() => ({ origin: first, version: 0, pending: null, swapping: false }));

/** Name of the nearest demo place within 300 m, else coordinates. */
export function areaName(o: LatLon = currentOrigin()): string {
  let best: { name: string; d: number } | null = null;
  for (const p of PLACES) {
    const d = distanceM(p.lat, p.lon, o.lat, o.lon);
    if (d < 300 && (!best || d < best.d)) best = { name: p.name, d };
  }
  return best?.name ?? `${o.lat.toFixed(4)}, ${o.lon.toFixed(4)}`;
}

export const sameOrigin = (a: LatLon, b: LatLon) => a.lat === b.lat && a.lon === b.lon;

/** Swap the origin now (geometry should already be prefetched). The fade hides the rebuild. */
export function commitOrigin(next: LatLon) {
  const snapped = snapOrigin(next.lat, next.lon);
  if (sameOrigin(snapped, useOrigin.getState().origin)) {
    useOrigin.setState({ pending: null });
    return;
  }
  useOrigin.setState({ swapping: true });
  window.setTimeout(() => {
    setCurrentOrigin(snapped);
    useOrigin.setState((s) => ({ origin: snapped, version: s.version + 1, pending: null }));
    window.setTimeout(() => useOrigin.setState({ swapping: false }), SWAP_FADE_MS);
  }, SWAP_FADE_MS);
}

/** True when `p` is further than RECENTRE_M from the current origin. */
export function needsRecentre(p: LatLon) {
  const o = useOrigin.getState().origin;
  return distanceM(o.lat, o.lon, p.lat, p.lon) > RECENTRE_M;
}
