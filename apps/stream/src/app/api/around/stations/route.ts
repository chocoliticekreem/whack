import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { tfl } from "@/lib/server/tfl";
import { distanceM, parsePoint } from "@/platform/around/geo";
import type { AroundStation, StationLine } from "@/platform/around/types";

interface TflStop {
  naptanId: string;
  commonName: string;
  lat: number;
  lon: number;
  modes: string[];
  lines: { id: string; name: string }[];
}

interface TflLineStatus {
  id: string;
  name: string;
  lineStatuses: { statusSeverityDescription: string }[];
}

interface TflCrowding {
  dataAvailable: boolean;
  percentageOfBaseline: number;
  timeUtc: string | null;
}

const STATUS_MODES = "tube,dlr,overground,elizabeth-line";
// Callers ask for 600 m and 900 m; one search at the larger radius serves both from one TfL call.
const MIN_SEARCH_M = 900;
// TfL's anonymous budget is ~50 requests a minute and every Underground station costs one crowding call.
const CROWDING_TTL_MS = 3 * TTL.minute;

const stopsNear = (lat: number, lon: number, radius: number) =>
  cached(`tfl-stations-${lat.toFixed(3)},${lon.toFixed(3)}-${radius}`, { ttlMs: TTL.day }, async () => {
    const { stopPoints } = await tfl<{ stopPoints: TflStop[] }>(
      `/StopPoint?lat=${lat}&lon=${lon}&stopTypes=NaptanMetroStation,NaptanRailStation&radius=${radius}`,
    );
    return stopPoints.map((s) => ({
      naptanId: s.naptanId,
      commonName: s.commonName.replace(/ (Underground|DLR|Rail) Station$/, ""),
      lat: s.lat,
      lon: s.lon,
      modes: s.modes,
      lines: s.lines.map((l) => ({ id: l.id, name: l.name })),
    }));
  });

const lineStatus = () =>
  cached("tfl-line-status", { ttlMs: TTL.minute, maxStaleMs: TTL.hour }, async () => {
    const lines = await tfl<TflLineStatus[]>(`/Line/Mode/${STATUS_MODES}/Status`);
    return Object.fromEntries(lines.map((l) => [l.id, l.lineStatuses[0]?.statusSeverityDescription ?? "Unknown"]));
  });

const crowding = (naptan: string) =>
  cached(`tfl-crowding-${naptan}`, { ttlMs: CROWDING_TTL_MS, maxStaleMs: 10 * TTL.minute, persist: false }, () =>
    tfl<TflCrowding>(`/crowding/${naptan}/Live`),
  );

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 900);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  try {
    const [found, status] = await Promise.all([
      stopsNear(point.lat, point.lon, Math.max(point.radius, MIN_SEARCH_M)),
      lineStatus(),
    ]);
    const stops = found.filter((s) => distanceM(point.lat, point.lon, s.lat, s.lon) <= point.radius);
    // Only Underground stations have live crowding; DLR/rail return dataAvailable=false.
    const live = await Promise.all(
      stops.map((s) => (s.modes.includes("tube") ? crowding(s.naptanId).catch(() => null) : null)),
    );
    const stations: AroundStation[] = stops
      .map((s, i) => {
        const c = live[i];
        const lines: StationLine[] = s.lines
          .filter((l) => l.id in status)
          .map((l) => ({ id: l.id, name: l.name, status: status[l.id] }));
        return {
          id: s.naptanId,
          name: s.commonName,
          lat: s.lat,
          lon: s.lon,
          distanceM: distanceM(point.lat, point.lon, s.lat, s.lon),
          modes: s.modes,
          lines,
          crowding: c?.dataAvailable ? Math.round(c.percentageOfBaseline * 100) / 100 : null,
          crowdingAt: c?.dataAvailable && c.timeUtc ? Date.parse(c.timeUtc) : null,
        };
      })
      .sort((a, b) => a.distanceM - b.distanceM);
    return json({ stations }, request, cacheShort(30));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
