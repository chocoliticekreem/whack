import type { AroundCam, CamClipTracks, TrackedObject } from "./types";

/**
 * Projects YOLO tracks from a camera frame onto the map.
 * Approximate on purpose: TfL publishes no camera calibration, so we assume a 60° field of view,
 * the stated compass heading (or the bearing towards the streamer), and ground distance growing
 * towards the top of the frame. Good enough to show where and how things move near each camera.
 */

const FOV_DEG = 60;
const NEAR_M = 8;
const FAR_M = 85;
/** People closer than this merge into one group dot. */
const GROUP_M = 7;

export interface FlowFrame {
  /** One feature per person or group; `count` > 1 means a group. */
  people: GeoJSON.FeatureCollection<GeoJSON.Point, { count: number }>;
  /** One feature per vehicle; `bearing` is its direction of travel in degrees. */
  vehicles: GeoJSON.FeatureCollection<GeoJSON.Point, { bearing: number }>;
  totals: { people: number; vehicles: number };
}

export function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number) {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
  const x =
    Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
    Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function offset(lat: number, lon: number, bearing: number, metres: number): [number, number] {
  const rad = (bearing * Math.PI) / 180;
  const dLat = (metres * Math.cos(rad)) / 111_320;
  const dLon = (metres * Math.sin(rad)) / (111_320 * Math.cos((lat * Math.PI) / 180));
  return [lon + dLon, lat + dLat];
}

function project(cam: AroundCam, heading: number, u: number, v: number): [number, number] {
  const bearing = heading + (u - 0.5) * FOV_DEG;
  const distance = NEAR_M + (FAR_M - NEAR_M) * Math.pow(1 - Math.min(Math.max(v, 0), 1), 1.6);
  return offset(cam.lat, cam.lon, bearing, distance);
}

export function camHeading(cam: AroundCam, towards: { lat: number; lon: number }) {
  return cam.heading ?? bearingDeg(cam.lat, cam.lon, towards.lat, towards.lon);
}

/** The camera's field of view as a wedge polygon. */
export function viewCone(cam: AroundCam, heading: number): GeoJSON.Feature<GeoJSON.Polygon> {
  const ring: [number, number][] = [[cam.lon, cam.lat]];
  for (let a = -FOV_DEG / 2; a <= FOV_DEG / 2; a += 5) ring.push(offset(cam.lat, cam.lon, heading + a, FAR_M));
  ring.push([cam.lon, cam.lat]);
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

function sampleAt(pts: TrackedObject["pts"], t: number): [number, number] | null {
  if (t < pts[0][0] || t > pts[pts.length - 1][0]) return null;
  for (let i = 1; i < pts.length; i++) {
    const [t1, u1, v1] = pts[i];
    if (t1 >= t) {
      const [t0, u0, v0] = pts[i - 1];
      const k = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
      return [u0 + (u1 - u0) * k, v0 + (v1 - v0) * k];
    }
  }
  return null;
}

function metresBetween(a: [number, number], b: [number, number]) {
  const dx = (a[0] - b[0]) * 111_320 * Math.cos((a[1] * Math.PI) / 180);
  const dy = (a[1] - b[1]) * 111_320;
  return Math.hypot(dx, dy);
}

/** Greedy grouping: each person joins the first group within GROUP_M, else starts one. */
function groupPeople(points: [number, number][]): FlowFrame["people"]["features"] {
  const groups: { centre: [number, number]; count: number }[] = [];
  for (const p of points) {
    const g = groups.find((x) => metresBetween(x.centre, p) < GROUP_M);
    if (g) {
      g.centre = [(g.centre[0] * g.count + p[0]) / (g.count + 1), (g.centre[1] * g.count + p[1]) / (g.count + 1)];
      g.count += 1;
    } else {
      groups.push({ centre: p, count: 1 });
    }
  }
  return groups.map((g) => ({
    type: "Feature",
    properties: { count: g.count },
    geometry: { type: "Point", coordinates: g.centre },
  }));
}

/** Positions of every tracked object at `nowMs`, each clip looping over its own duration. */
export function flowFrame(
  clips: { cam: AroundCam; heading: number; clip: CamClipTracks }[],
  nowMs: number,
): FlowFrame {
  const people: [number, number][] = [];
  const vehicles: FlowFrame["vehicles"]["features"] = [];
  for (const { cam, heading, clip } of clips) {
    if (clip.duration <= 0) continue;
    const t = (nowMs / 1000) % clip.duration;
    for (const track of clip.tracks) {
      const here = sampleAt(track.pts, t);
      if (!here) continue;
      const pos = project(cam, heading, here[0], here[1]);
      if (track.kind === "person") {
        people.push(pos);
        continue;
      }
      const before = sampleAt(track.pts, t - 0.5);
      const prev = before ? project(cam, heading, before[0], before[1]) : null;
      const moved = prev && metresBetween(prev, pos) > 0.4;
      vehicles.push({
        type: "Feature",
        properties: { bearing: moved ? bearingDeg(prev[1], prev[0], pos[1], pos[0]) : heading },
        geometry: { type: "Point", coordinates: pos },
      });
    }
  }
  return {
    people: { type: "FeatureCollection", features: groupPeople(people) },
    vehicles: { type: "FeatureCollection", features: vehicles },
    totals: { people: people.length, vehicles: vehicles.length },
  };
}
