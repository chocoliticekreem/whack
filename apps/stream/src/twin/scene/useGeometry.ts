"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { cachedFetch, prefetch } from "@/lib/client/cachedFetch";
import { originKey, snapOrigin, type LatLon, type TwinGeometry } from "../config";
import { useLive } from "../live/store";
import { commitOrigin, needsRecentre, sameOrigin, useOrigin } from "../origin";

export type GeometryState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: TwinGeometry };

const geometries = new Map<string, TwinGeometry>();
const loads = new Map<string, Promise<TwinGeometry>>();

export const geometryUrl = (o: LatLon) => `/api/twin/osm?lat=${o.lat}&lon=${o.lon}`;
export const fieldUrl = (o: LatLon) => `/api/twin/field?lat=${o.lat}&lon=${o.lon}`;

/** Geometry for a snapped origin; one in-flight fetch per area shared by every caller, kept for the session. */
export function loadGeometryFor(origin: LatLon): Promise<TwinGeometry> {
  const key = originKey(origin);
  const hit = geometries.get(key);
  if (hit) return Promise.resolve(hit);
  let p = loads.get(key);
  if (!p) {
    p = cachedFetch(geometryUrl(origin))
      .then(async (res) => {
        if (!res.ok) throw new Error(`/api/twin/osm responded ${res.status}`);
        return (await res.json()) as TwinGeometry;
      })
      .then((data) => {
        geometries.set(key, data);
        return data;
      })
      .finally(() => loads.delete(key));
    loads.set(key, p);
  }
  return p;
}

export function useGeometryLoader(): GeometryState {
  const origin = useOrigin((s) => s.origin);
  const key = originKey(origin);
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  const [, bump] = useState(0);

  useEffect(() => {
    if (geometries.has(key)) return;
    let alive = true;
    loadGeometryFor(origin)
      .then(() => alive && bump((n) => n + 1))
      .catch((err: unknown) => {
        if (alive) setFailed({ key, message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      alive = false;
    };
  }, [origin, key]);

  const hit = geometries.get(key);
  if (hit) return { status: "ready", data: hit };
  if (failed?.key === key) return { status: "error", message: failed.message };
  return { status: "loading" };
}

/**
 * Follows the streamer: once they are more than RECENTRE_M from the origin, the next area's geometry
 * and field are fetched in the background, then the origin swaps behind a short fade.
 */
export function useRecentre() {
  useEffect(() => {
    let active = true;
    const check = () => {
      const { streamer } = useLive.getState();
      const { pending } = useOrigin.getState();
      if (pending || !needsRecentre(streamer)) return;
      const next = snapOrigin(streamer.lat, streamer.lon);
      if (sameOrigin(next, useOrigin.getState().origin)) return;
      useOrigin.setState({ pending: next });
      void prefetch(fieldUrl(next));
      loadGeometryFor(next)
        .then(() => {
          if (active) commitOrigin(next);
        })
        .catch(() => useOrigin.setState({ pending: null }));
    };
    check();
    const unsub = useLive.subscribe((s, prev) => {
      if (s.streamer !== prev.streamer) check();
    });
    return () => {
      active = false;
      unsub();
    };
  }, []);
}

export const GeometryContext = createContext<TwinGeometry | null>(null);

export function useGeometry(): TwinGeometry {
  const g = useContext(GeometryContext);
  if (!g) throw new Error("useGeometry must be used inside GeometryContext");
  return g;
}
