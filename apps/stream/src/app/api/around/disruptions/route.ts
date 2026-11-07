import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { tfl } from "@/lib/server/tfl";
import { distanceM, parsePoint } from "@/platform/around/geo";
import type { AroundDisruption } from "@/platform/around/types";

interface TflSegment {
  distruptedStreetId: string;
  streetName: string;
  severity: string;
  category: string;
  closure: string;
  comments: string;
  lineString: string;
  startLat: number;
  startLon: number;
}

const TTL_MS = 5 * 60_000;

const allSegments = () => cached("tfl-disruptions", { ttlMs: TTL_MS, maxStaleMs: TTL.hour }, fetchSegments);

async function fetchSegments(): Promise<TflSegment[]> {
  const day = (d: Date) => d.toISOString().slice(0, 10);
  const now = new Date();
  return tfl<TflSegment[]>(
    `/Road/all/Street/Disruption?startDate=${day(now)}&endDate=${day(new Date(now.getTime() + 86_400_000))}`,
    20_000,
  );
}

function parseLine(s: string): [number, number][] {
  try {
    return JSON.parse(s) as [number, number][];
  } catch {
    return [];
  }
}

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 1500);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  try {
    const items: AroundDisruption[] = (await allSegments())
      .map((s) => ({
        id: s.distruptedStreetId,
        street: s.streetName,
        severity: s.severity,
        category: s.category,
        closure: s.closure,
        comments: s.comments,
        line: parseLine(s.lineString),
        distanceM: distanceM(point.lat, point.lon, s.startLat, s.startLon),
      }))
      .filter((d) => d.distanceM <= point.radius && d.line.length > 0)
      .sort((a, b) => a.distanceM - b.distanceM);
    return json({ items }, request, cacheShort(60));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
