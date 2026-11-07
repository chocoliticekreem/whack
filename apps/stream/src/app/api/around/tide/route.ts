import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { distanceM, parsePoint } from "@/platform/around/geo";
import type { AroundTide } from "@/platform/around/types";

const EA = "https://environment.data.gov.uk/flood-monitoring";

interface EaStation {
  label: string;
  lat: number;
  long: number;
  measures?: { "@id": string }[];
}

interface EaReading {
  dateTime: string;
  value: number;
}

async function ea<T>(path: string): Promise<T> {
  const res = await fetch(`${EA}${path}`, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`EA flood-monitoring ${path} failed: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** The nearest EA station with a 15-minute tidal level measure; the station set changes rarely. */
const tidalGauge = (lat: number, lon: number) =>
  cached(`ea-tidal-${lat.toFixed(2)},${lon.toFixed(2)}`, { ttlMs: TTL.week }, async () => {
    const { items } = await ea<{ items: EaStation[] }>(`/id/stations?lat=${lat}&long=${lon}&dist=5`);
    const nearest = items
      .filter((s) => Number.isFinite(s.lat) && Number.isFinite(s.long))
      .sort((a, b) => distanceM(lat, lon, a.lat, a.long) - distanceM(lat, lon, b.lat, b.long));
    for (const s of nearest) {
      const m = s.measures?.find((x) => /tidal_level-i-15_min/.test(x["@id"]));
      if (m) return { station: s.label, lat: s.lat, lon: s.long, measure: m["@id"].split("/").pop()! };
    }
    throw new Error("no tidal gauge within 5 km");
  });

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 0);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  try {
    const gauge = await tidalGauge(point.lat, point.lon);
    const tide = await cached<AroundTide>(
      `ea-tide-${gauge.measure}`,
      { ttlMs: 5 * TTL.minute, maxStaleMs: TTL.hour, persist: false },
      async () => {
        const { items } = await ea<{ items: EaReading[] }>(`/id/measures/${gauge.measure}/readings?_sorted&_limit=2`);
        const [now, prev] = items;
        if (!now) throw new Error(`no readings for ${gauge.station}`);
        const delta = prev ? now.value - prev.value : 0;
        return {
          station: gauge.station,
          lat: gauge.lat,
          lon: gauge.lon,
          levelM: now.value,
          trend: delta > 0.05 ? "rising" : delta < -0.05 ? "falling" : "steady",
          at: Date.parse(now.dateTime),
        };
      },
    );
    return json(tide, request, cacheShort(300));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
