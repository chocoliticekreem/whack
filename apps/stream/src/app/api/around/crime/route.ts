import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { distanceM, parsePoint } from "@/platform/around/geo";
import type { AroundCrime, CrimeStreet } from "@/platform/around/types";

interface PoliceCrime {
  category: string;
  month: string;
  location: { latitude: string; longitude: string; street: { id: number; name: string } };
}

interface Compact {
  category: string;
  month: string;
  lat: number;
  lon: number;
  street: string;
}

/** police.uk returns everything within a mile; cache per ~1 km cell for a day (data changes monthly). */
const crimesNear = (lat: number, lon: number) =>
  cached(`police-${lat.toFixed(2)},${lon.toFixed(2)}`, { ttlMs: TTL.day, maxStaleMs: TTL.week }, async () => {
    const res = await fetch(`https://data.police.uk/api/crimes-street/all-crime?lat=${lat}&lng=${lon}`, {
      signal: AbortSignal.timeout(15_000),
    });
    if (!res.ok) throw new Error(`data.police.uk failed: HTTP ${res.status}`);
    const rows = (await res.json()) as PoliceCrime[];
    return rows.map<Compact>((r) => ({
      category: r.category,
      month: r.month,
      lat: Number(r.location.latitude),
      lon: Number(r.location.longitude),
      street: r.location.street.name.replace(/^On or near /, ""),
    }));
  });

function tally(keys: string[]) {
  const counts = new Map<string, number>();
  for (const k of keys) counts.set(k, (counts.get(k) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 600);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  try {
    const near = (await crimesNear(point.lat, point.lon)).filter(
      (c) => distanceM(point.lat, point.lon, c.lat, c.lon) <= point.radius,
    );
    const byStreet = new Map<string, CrimeStreet>();
    for (const c of near) {
      const s = byStreet.get(c.street) ?? { name: c.street, lat: c.lat, lon: c.lon, count: 0 };
      s.count += 1;
      byStreet.set(c.street, s);
    }
    const body: AroundCrime = {
      month: near[0]?.month ?? "",
      total: near.length,
      byCategory: tally(near.map((c) => c.category)).map(([category, count]) => ({ category, count })),
      streets: [...byStreet.values()].sort((a, b) => b.count - a.count).slice(0, 8),
      points: near.map((c) => [c.lon, c.lat]),
    };
    return json(body, request, cacheShort(3600));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
