import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { parsePoint } from "@/platform/around/geo";
import type { LiveWeather } from "@/platform/demo";

const TTL_MS = 5 * 60_000;

interface OpenMeteoCurrent {
  temperature_2m: number;
  precipitation: number;
  cloud_cover: number;
  weather_code: number;
  wind_speed_10m: number;
  is_day: number;
  visibility: number;
}

// WMO weather codes → the few looks the twin can render.
function kindFor(c: OpenMeteoCurrent): LiveWeather["kind"] {
  const code = c.weather_code;
  if (code >= 95) return "storm";
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82) || c.precipitation > 0) return "rain";
  if (code === 45 || code === 48 || c.visibility < 2000) return "fog";
  if (c.cloud_cover >= 70) return "cloudy";
  return "clear";
}

async function fetchWeather(point: { lat: number; lon: number }): Promise<LiveWeather> {
  const url =
    `https://api.open-meteo.com/v1/forecast?latitude=${point.lat}&longitude=${point.lon}` +
    "&current=temperature_2m,precipitation,cloud_cover,weather_code,wind_speed_10m,is_day,visibility&timezone=Europe%2FLondon";
  const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`Open-Meteo failed: HTTP ${res.status}`);
  const c = ((await res.json()) as { current: OpenMeteoCurrent }).current;
  return {
    kind: kindFor(c),
    temperatureC: c.temperature_2m,
    cloudCover: c.cloud_cover,
    precipitationMm: c.precipitation,
    windKph: c.wind_speed_10m,
    isDay: c.is_day === 1,
    visibilityM: c.visibility,
    at: Date.now(),
  };
}

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 0);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  const key = `${point.lat.toFixed(2)},${point.lon.toFixed(2)}`;
  try {
    const weather = await cached(`weather-${key}`, { ttlMs: TTL_MS, maxStaleMs: TTL.hour }, () => fetchWeather(point));
    return json(weather, request, cacheShort(60));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
