import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import type { LiveWeather } from "@/platform/demo";
import type { AroundCam, CamReading } from "@/platform/around/types";
import { originKey, type LatLon } from "@/twin/config";
import { estimate } from "@/twin/field/estimate";
import { buildSegments, type GraphSegment } from "@/twin/field/graph";
import { CAM_RADIUS_M, FIELD_CACHE_MS } from "@/twin/field/model";
import type { FieldResponse } from "@/twin/field/types";
import { loadGeometry, originFromParams } from "@/twin/osm";

const segmentsByOrigin = new Map<string, GraphSegment[]>();

async function getJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(6000), cache: "no-store" });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

async function computeField(origin: string, at: LatLon): Promise<FieldResponse> {
  const now = Date.now();
  const geometry = await loadGeometry(at);
  const key = originKey(at);
  let segments = segmentsByOrigin.get(key);
  if (!segments) {
    segments = buildSegments(geometry.roads);
    segmentsByOrigin.set(key, segments);
  }
  const q = `lat=${at.lat}&lon=${at.lon}`;
  const [{ cams }, weather] = await Promise.all([
    getJson<{ cams: AroundCam[] }>(`${origin}/api/around/cams?${q}&radius=${CAM_RADIUS_M}`, { cams: [] }),
    getJson<LiveWeather | null>(`${origin}/api/around/weather?${q}`, null),
  ]);
  const ids = cams.map((c) => c.id).join(",");
  const { readings } = ids
    ? await getJson<{ readings: CamReading[] }>(`${origin}/api/around/counts?ids=${ids}`, { readings: [] })
    : { readings: [] };
  return estimate({
    segments,
    pois: geometry.pois,
    cams,
    readings: readings ?? [],
    weather: weather?.kind ?? "clear",
    now,
  });
}

export async function GET(request: NextRequest) {
  const origin = request.nextUrl.origin;
  const at = originFromParams(request.nextUrl.searchParams);
  try {
    // A field from the last run is served instantly after a restart while a fresh one is computed.
    const body = await cached(`twin-field-${originKey(at)}`, { ttlMs: FIELD_CACHE_MS, maxStaleMs: 10 * TTL.minute }, () =>
      computeField(origin, at),
    );
    return json(body, request, cacheShort(10));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
