/**
 * Shared contract for the 3D twin (/twin). Every module imports this; change it rarely.
 * Units are metres, y is up, ground y = 0. +x is east, +z is south (so north is -z).
 */

export interface LatLon {
  lat: number;
  lon: number;
}

/** Default world origin: Tower Hill. The live origin follows the streamer (see `origin.ts`). */
export const TOWER_HILL: LatLon = { lat: 51.5098, lon: -0.0766 };

/** Radius of the core mirrored area around the origin, in metres (live feeds, fog and camera framing are tuned to it). */
export const WORLD_RADIUS_M = 650;

/** Rectangle of real OSM geometry loaded around the origin, in local metres. Reaches south-west past Bankside / Halkin. */
export const GEOMETRY_BOUNDS = { xMin: -1100, xMax: 650, zMin: -650, zMax: 1100 } as const;

export const insideGeometryBounds = (x: number, z: number) =>
  x >= GEOMETRY_BOUNDS.xMin && x <= GEOMETRY_BOUNDS.xMax && z >= GEOMETRY_BOUNDS.zMin && z <= GEOMETRY_BOUNDS.zMax;

const M_PER_DEG_LAT = 111_320;
const mPerDegLon = (lat: number) => M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180);

/** Origins snap to this grid (metres) so nearby fixes share one geometry build and cache entry. */
export const ORIGIN_GRID_M = 250;

/** Snaps a point to the origin grid. The grid is anchored on Tower Hill so Tower Hill snaps to itself. */
export function snapOrigin(lat: number, lon: number): LatLon {
  const dLat = ORIGIN_GRID_M / M_PER_DEG_LAT;
  const dLon = ORIGIN_GRID_M / mPerDegLon(TOWER_HILL.lat);
  return {
    lat: Number((TOWER_HILL.lat + Math.round((lat - TOWER_HILL.lat) / dLat) * dLat).toFixed(6)),
    lon: Number((TOWER_HILL.lon + Math.round((lon - TOWER_HILL.lon) / dLon) * dLon).toFixed(6)),
  };
}

/** Stable cache-key fragment for an origin. */
export const originKey = (o: LatLon) => `${o.lat}_${o.lon}`;

export interface Projection {
  origin: LatLon;
  toLocal(lat: number, lon: number): [number, number];
  toLatLon(x: number, z: number): LatLon;
}

/** Projection around an explicit origin. Server code uses this so concurrent requests never share state. */
export function projection(origin: LatLon): Projection {
  const kLon = mPerDegLon(origin.lat);
  return {
    origin,
    toLocal: (lat, lon) => [(lon - origin.lon) * kLon, -(lat - origin.lat) * M_PER_DEG_LAT],
    toLatLon: (x, z) => ({ lat: origin.lat - z / M_PER_DEG_LAT, lon: origin.lon + x / kLon }),
  };
}

let current: Projection = projection(TOWER_HILL);

/** The origin the client scene is built around right now. */
export const currentOrigin = () => current.origin;

/** Swap the client-side origin. Callers must rebuild everything that cached local coordinates (see `origin.ts`). */
export function setCurrentOrigin(origin: LatLon) {
  current = projection(origin);
}

/** lat/lon → local [x, z] metres around the current origin. */
export function toLocal(lat: number, lon: number): [number, number] {
  return current.toLocal(lat, lon);
}

/** local [x, z] metres → lat/lon around the current origin. */
export function toLatLon(x: number, z: number): LatLon {
  return current.toLatLon(x, z);
}

export type Ring = [number, number][];

/** Real OpenStreetMap geometry around the origin, already in local metres (from /api/twin/osm). */
export interface TwinGeometry {
  buildings: {
    ring: Ring;
    height: number;
    minHeight: number;
    /** OSM/OpenMapTiles building colour, if tagged (CSS colour string). */
    colour?: string;
  }[];
  roads: {
    line: Ring;
    kind: "major" | "minor" | "path" | "rail";
    name?: string;
    bridge?: boolean;
    tunnel?: boolean;
    oneway?: boolean;
  }[];
  water: Ring[];
  parks: Ring[];
  /** Footfall anchors from the OSM poi layer: stations, stops, attractions, shops, food. */
  pois: TwinPoi[];
  /** Rail / tube / DLR / overground stations from the tiles' POI layer, in local metres. */
  stations: { pos: [number, number]; name: string; kind: "rail" | "subway" | "light_rail" }[];
}

export interface TwinPoi {
  name: string;
  x: number;
  z: number;
  kind: "station" | "bus_stop" | "attraction" | "shop" | "food";
}
