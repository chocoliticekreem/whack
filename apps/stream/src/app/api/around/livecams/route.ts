import type { NextRequest } from "next/server";
import { cacheShort, json } from "@/lib/server/json";
import { distanceM, parsePoint } from "@/platform/around/geo";
import type { AroundLiveCam } from "@/platform/around/types";
import CATALOGUE from "@/data/london_live.json";

interface CatalogueCam {
  id: string;
  name: string;
  area: string;
  lat: number;
  lon: number;
  location_precision: "exact" | "approx";
  subjects: string[];
  page: string;
  resolve: { video_id: string };
}

// Bundled copy of services/vision/cameras/london_live.json so it ships with the deployed app;
// refresh with `cp services/vision/cameras/london_live.json apps/stream/src/data/`. Embed-only (YouTube ToS).
const catalogue = async () => (CATALOGUE as unknown as { cameras: CatalogueCam[] }).cameras;

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 5000);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  try {
    const cams: AroundLiveCam[] = (await catalogue())
      .map((c) => ({
        id: c.id,
        name: c.name,
        area: c.area,
        lat: c.lat,
        lon: c.lon,
        precision: c.location_precision,
        subjects: c.subjects,
        embedUrl: `https://www.youtube.com/embed/${c.resolve.video_id}?autoplay=1&mute=1&playsinline=1`,
        page: c.page,
        distanceM: distanceM(point.lat, point.lon, c.lat, c.lon),
      }))
      .filter((c) => c.distanceM <= point.radius)
      .sort((a, b) => a.distanceM - b.distanceM);
    return json({ cams }, request, cacheShort(600));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
