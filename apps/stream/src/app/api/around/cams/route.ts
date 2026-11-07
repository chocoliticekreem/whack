import type { NextRequest } from "next/server";
import { cached, TTL } from "@/lib/server/cache";
import { cacheShort, json } from "@/lib/server/json";
import { tfl } from "@/lib/server/tfl";
import { virtualCams } from "@/lib/vcams";
import { distanceM, parsePoint } from "@/platform/around/geo";
import type { AroundCam } from "@/platform/around/types";

const VIRTUAL_RADIUS_M = 1500;

interface TflPlace {
  id: string;
  commonName: string;
  lat: number;
  lon: number;
  additionalProperties: { key: string; value: string }[];
}

const COMPASS: Record<string, number> = { N: 0, NORTH: 0, E: 90, EAST: 90, S: 180, SOUTH: 180, W: 270, WEST: 270 };

// TfL's "view" field often starts with the direction the camera faces, e.g. "WEST-twds Blackfriars".
function headingFromView(view: string | undefined): number | null {
  const word = view?.trim().toUpperCase().match(/^(NORTH|SOUTH|EAST|WEST|[NSEW])\b/)?.[1];
  return word ? COMPASS[word] : null;
}

async function fetchCams(): Promise<Omit<AroundCam, "distanceM">[]> {
  const places = await tfl<TflPlace[]>("/Place/Type/JamCam", 20_000);
  const prop = (p: TflPlace, key: string) => p.additionalProperties.find((a) => a.key === key)?.value;
  return places
    .filter((p) => prop(p, "available") !== "false" && prop(p, "videoUrl"))
    .map((p) => ({
      id: p.id,
      name: p.commonName,
      lat: p.lat,
      lon: p.lon,
      videoUrl: prop(p, "videoUrl")!,
      imageUrl: prop(p, "imageUrl") ?? "",
      view: prop(p, "view") ?? "",
      heading: headingFromView(prop(p, "view")),
    }));
}

// Cameras are added or retired over weeks; an hour-old list is as good as a live one.
const allCams = () => cached("tfl-jamcams", { ttlMs: TTL.hour }, fetchCams);

export async function GET(request: NextRequest) {
  const point = parsePoint(request.nextUrl.searchParams, 800);
  if (!point) return Response.json({ error: "lat and lon query params are required" }, { status: 400 });
  try {
    const withDistance = (c: Omit<AroundCam, "distanceM">) => ({ ...c, distanceM: distanceM(point.lat, point.lon, c.lat, c.lon) });
    // Virtual cams reach a little further than TfL's: there are only a few, and Bank sits just past the twin's 700 m.
    const virtual = virtualCams().map(withDistance).filter((c) => c.distanceM <= Math.max(point.radius, VIRTUAL_RADIUS_M));
    const cams: AroundCam[] = [...virtual, ...(await allCams()).map(withDistance).filter((c) => c.distanceM <= point.radius)].sort(
      (a, b) => a.distanceM - b.distanceM,
    );
    return json({ cams }, request, cacheShort(300));
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
