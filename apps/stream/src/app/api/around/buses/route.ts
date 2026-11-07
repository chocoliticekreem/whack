import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { tfl } from "@/lib/server/tfl";
import { distanceM, parsePoint } from "@/platform/around/geo";
import type { AroundBusStop } from "@/platform/around/types";

interface TflStop {
  naptanId: string;
  commonName: string;
  indicator?: string;
  stopLetter?: string;
  lat: number;
  lon: number;
}

interface TflArrival {
  lineName: string;
  destinationName: string;
  timeToStation: number;
}

const MAX_STOPS = 6;
const ARRIVALS_TTL_MS = 25_000;
// Stop search takes 7–14s on TfL and stops never move, so the list lives on disk for days.
const STOPS_TTL_MS = 3 * TTL.day;

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 400);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  // ~100m grid key so nearby requests share TfL's 50 req/min anonymous budget.
  const key = `${point.lat.toFixed(3)},${point.lon.toFixed(3)},${point.radius}`;
  try {
    const stops = await cached(`tfl-arrivals-${key}`, { ttlMs: ARRIVALS_TTL_MS, persist: false, maxStaleMs: 2 * TTL.minute }, () =>
      loadStops(point, key),
    );
    return json({ stops }, request, cacheShort(20));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}

async function loadStops(point: { lat: number; lon: number; radius: number }, key: string): Promise<AroundBusStop[]> {
  const stopPoints = await cached(`tfl-stops-${key}`, { ttlMs: STOPS_TTL_MS }, async () => {
    const { stopPoints } = await tfl<{ stopPoints: TflStop[] }>(
      `/StopPoint?lat=${point.lat}&lon=${point.lon}&stopTypes=NaptanPublicBusCoachTram&radius=${point.radius}&modes=bus&returnLines=false`,
      25_000,
    );
    return stopPoints;
  });
  const nearest = stopPoints
    .map((s) => ({ s, d: distanceM(point.lat, point.lon, s.lat, s.lon) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, MAX_STOPS);
  const stops: AroundBusStop[] = await Promise.all(
    nearest.map(async ({ s, d }) => {
      // `at` travels with the arrivals so a stale copy served after a 429 still counts down from when TfL said it.
      const { at, arrivals } = await cached(
        `tfl-stop-arrivals-${s.naptanId}`,
        { ttlMs: ARRIVALS_TTL_MS, persist: false },
        async () => ({ at: Date.now(), arrivals: await tfl<TflArrival[]>(`/StopPoint/${s.naptanId}/Arrivals`) }),
      ).catch(() => ({ at: Date.now(), arrivals: [] as TflArrival[] }));
      return {
        id: s.naptanId,
        name: s.commonName,
        indicator: s.stopLetter ?? s.indicator ?? "",
        lat: s.lat,
        lon: s.lon,
        distanceM: d,
        at,
        arrivals: arrivals
          .sort((a, b) => a.timeToStation - b.timeToStation)
          .slice(0, 4)
          .map((a) => ({ line: a.lineName, destination: a.destinationName, seconds: a.timeToStation })),
      };
    }),
  );
  return stops;
}
