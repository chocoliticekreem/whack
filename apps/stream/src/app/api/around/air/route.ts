import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { parsePoint } from "@/platform/around/geo";
import type { AroundAir } from "@/platform/around/types";

interface OpenMeteoAir {
  current: { time: string; european_aqi: number; pm2_5: number; pm10: number; nitrogen_dioxide: number };
}

// European AQI bands as published by the EEA.
function bandFor(eaqi: number): AroundAir["band"] {
  if (eaqi <= 20) return "good";
  if (eaqi <= 40) return "fair";
  if (eaqi <= 60) return "moderate";
  if (eaqi <= 80) return "poor";
  return "very poor";
}

async function fetchAir(lat: number, lon: number): Promise<AroundAir> {
  const url =
    `https://air-quality-api.open-meteo.com/v1/air-quality?latitude=${lat}&longitude=${lon}` +
    "&current=european_aqi,pm2_5,pm10,nitrogen_dioxide&timezone=Europe%2FLondon";
  const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`Open-Meteo air quality failed: HTTP ${res.status}`);
  const { current } = (await res.json()) as OpenMeteoAir;
  return {
    eaqi: current.european_aqi,
    band: bandFor(current.european_aqi),
    pm25: current.pm2_5,
    pm10: current.pm10,
    no2: current.nitrogen_dioxide,
    at: Date.now(),
  };
}

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 0);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  const key = `${point.lat.toFixed(2)},${point.lon.toFixed(2)}`;
  try {
    const air = await cached(`air-${key}`, { ttlMs: 15 * TTL.minute, maxStaleMs: TTL.hour }, () =>
      fetchAir(point.lat, point.lon),
    );
    return json(air, request, cacheShort(300));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
