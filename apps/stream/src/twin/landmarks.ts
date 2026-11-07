/**
 * Real London landmarks as pure data. Positions are lat/lon (use toLocal for metres); headings are
 * compass bearings of the long axis (0 = north, clockwise). Heights are metres.
 */
import { currentOrigin, insideGeometryBounds, toLocal, TOWER_HILL, type Ring } from "./config";
import { distanceM } from "@/platform/around/geo";

export type LandmarkKind =
  | "suspension-bridge"
  | "girder-bridge"
  | "castle"
  | "tower"
  | "dome"
  | "ship"
  | "wheel"
  | "palace";

export interface Landmark {
  id: string;
  name: string;
  lat: number;
  lon: number;
  kind: LandmarkKind;
  headingDeg: number;
  length: number;
  width: number;
  height: number;
}

export const LANDMARKS = {
  towerBridge: { id: "tower-bridge", name: "Tower Bridge", lat: 51.50552, lon: -0.07537, kind: "suspension-bridge", headingDeg: 23, length: 327, width: 24, height: 66 },
  londonBridge: { id: "london-bridge", name: "London Bridge", lat: 51.50772, lon: -0.0876, kind: "girder-bridge", headingDeg: 3, length: 262, width: 34, height: 9 },
  towerOfLondon: { id: "tower-of-london", name: "Tower of London", lat: 51.5081, lon: -0.0759, kind: "castle", headingDeg: 0, length: 220, width: 190, height: 27 },
  shard: { id: "shard", name: "The Shard", lat: 51.5045, lon: -0.0865, kind: "tower", headingDeg: 0, length: 55, width: 55, height: 310 },
  gherkin: { id: "gherkin", name: "30 St Mary Axe", lat: 51.5145, lon: -0.0803, kind: "tower", headingDeg: 0, length: 56, width: 56, height: 180 },
  walkieTalkie: { id: "walkie-talkie", name: "20 Fenchurch Street", lat: 51.51129, lon: -0.08354, kind: "tower", headingDeg: 0, length: 64, width: 56, height: 160 },
  cheesegrater: { id: "cheesegrater", name: "122 Leadenhall Street", lat: 51.513, lon: -0.07996, kind: "tower", headingDeg: 0, length: 68, width: 52, height: 225 },
  tower42: { id: "tower-42", name: "Tower 42", lat: 51.5152, lon: -0.08392, kind: "tower", headingDeg: 0, length: 56, width: 60, height: 183 },
  stPauls: { id: "st-pauls", name: "St Paul's Cathedral", lat: 51.5138, lon: -0.0984, kind: "dome", headingDeg: 90, length: 158, width: 75, height: 111 },
  hmsBelfast: { id: "hms-belfast", name: "HMS Belfast", lat: 51.5066, lon: -0.0813, kind: "ship", headingDeg: 107, length: 187, width: 20, height: 36 },
  cityHall: { id: "city-hall", name: "City Hall", lat: 51.5048, lon: -0.0786, kind: "tower", headingDeg: 0, length: 52, width: 56, height: 45 },
  elizabethTower: { id: "elizabeth-tower", name: "Elizabeth Tower (Big Ben)", lat: 51.5007, lon: -0.1246, kind: "palace", headingDeg: 190, length: 300, width: 40, height: 96 },
  londonEye: { id: "london-eye", name: "London Eye", lat: 51.5033, lon: -0.1196, kind: "wheel", headingDeg: 12, length: 135, width: 20, height: 135 },
} as const satisfies Record<string, Landmark>;

export function landmarkPos(l: Landmark): [number, number] {
  return toLocal(l.lat, l.lon);
}

/** Beyond this the fog has swallowed even the towers, so the meshes are not built. */
export const LANDMARK_RANGE_M = 3000;

export function landmarkInRange(l: { lat: number; lon: number }, range = LANDMARK_RANGE_M): boolean {
  const o = currentOrigin();
  return distanceM(o.lat, o.lon, l.lat, l.lon) <= range;
}

/**
 * Where Tower Hill sits in the current frame: hand-placed Tower Hill-local metres (Tower of London
 * walls, viaducts, Bankside plots) are rendered under a group at this offset. [0, 0] at Tower Hill.
 */
export function towerHillOffset(): [number, number] {
  return toLocal(TOWER_HILL.lat, TOWER_HILL.lon);
}

/** True when the Tower Hill-local hand-placed models are within the loaded area. */
export function towerHillInRange(): boolean {
  const [x, z] = towerHillOffset();
  return insideGeometryBounds(x, z);
}

/** Compass bearing of a long axis → Three.js yaw for geometry whose long axis is local z. */
export function bearingToYaw(deg: number): number {
  return (-deg * Math.PI) / 180;
}

/**
 * The Tower of London in local metres. The OSM footprints already give an unadorned ring of
 * 5 m walls; this data drives the battlements, towers and the taller White Tower on top of them.
 */
export const TOWER_OF_LONDON = {
  whiteTower: { x: 36.5, z: 191.5, yaw: -5, w: 38, d: 38, h: 27 },
  outerWall: [
    [-43, 100], [60, 83], [114, 106], [125, 126], [122, 290], [64, 288], [-9, 262], [-70, 236], [-95, 222], [-43, 100],
  ] as Ring,
  innerWall: [
    [-32, 115], [7, 112], [47, 108], [78, 114], [110, 127], [107, 150], [107, 265], [56, 271], [-6, 247], [-69, 222], [-53, 167], [-32, 115],
  ] as Ring,
  /** Round towers: r metres, h body height, cone = conical roof. */
  towers: [
    { x: -32, z: 115, r: 5.9, h: 16, cone: true },
    { x: 7, z: 112, r: 4.2, h: 14, cone: true },
    { x: 47, z: 108, r: 4.2, h: 14, cone: true },
    { x: 78, z: 114, r: 4.2, h: 14, cone: true },
    { x: 110, z: 127, r: 5.8, h: 16, cone: true },
    { x: 106, z: 168, r: 3.8, h: 13, cone: true },
    { x: 106, z: 209, r: 3.8, h: 13, cone: true },
    { x: 107, z: 265, r: 4.6, h: 15, cone: true },
    { x: 55, z: 271, r: 5.2, h: 15, cone: true },
    { x: -6.5, z: 247, r: 7.5, h: 17, cone: true },
    { x: -69, z: 222, r: 5.4, h: 15, cone: true },
    { x: -53, z: 167, r: 5.7, h: 15, cone: true },
    { x: -43, z: 100, r: 8, h: 11, cone: false },
    { x: 134, z: 115.5, r: 11.5, h: 11, cone: false },
    { x: 122, z: 290, r: 6, h: 10, cone: false },
    { x: -95, z: 222, r: 5.5, h: 10, cone: false },
    { x: 58, z: 83, r: 5.5, h: 10, cone: false },
  ],
} as const;

export type StationNetwork = "tube" | "dlr" | "rail" | "overground";

export interface StationSeed {
  name: string;
  lat: number;
  lon: number;
  network: StationNetwork;
}

/** Used when the OSM station list is empty or misses a landmark station. */
export const STATION_FALLBACKS: StationSeed[] = [
  { name: "Tower Hill", lat: 51.5098, lon: -0.0766, network: "tube" },
  { name: "Fenchurch Street", lat: 51.5115, lon: -0.0785, network: "rail" },
  { name: "Tower Gateway", lat: 51.5106, lon: -0.0742, network: "dlr" },
  { name: "London Bridge", lat: 51.505, lon: -0.0864, network: "rail" },
];

/**
 * Rail that is carried on brick viaducts rather than in cuttings or tunnels. The data has no
 * bridge/tunnel flag for rail, so the elevated stretches are named by hand. Height ramps up over
 * `rampX` metres from the x edges and `rampZ` metres from the z edges of each zone.
 */
export const ELEVATED_RAIL = [
  { name: "c2c viaduct into Fenchurch Street", x0: -140, x1: 900, z0: -205, z1: -150, h: 8, rampX: 70, rampZ: 6 },
  { name: "DLR viaduct from Tower Gateway", x0: 100, x1: 900, z0: -165, z1: -85, h: 7, rampX: 40, rampZ: 6 },
  { name: "London Bridge approaches", x0: -900, x1: 900, z0: 430, z1: 1500, h: 8, rampX: 40, rampZ: 24 },
] as const;

function smooth(edge: number, v: number): number {
  const t = Math.min(1, Math.max(0, v / edge));
  return t * t * (3 - 2 * t);
}

/** Height of the viaduct deck above ground at (x, z) in the current frame; 0 where rail runs at ground level. */
export function railHeight(x: number, z: number): number {
  const [ox, oz] = towerHillOffset();
  x -= ox;
  z -= oz;
  let best = 0;
  for (const zone of ELEVATED_RAIL) {
    const dx = Math.min(x - zone.x0, zone.x1 - x);
    const dz = Math.min(z - zone.z0, zone.z1 - z);
    if (dx <= 0 || dz <= 0) continue;
    best = Math.max(best, zone.h * smooth(zone.rampX, dx) * smooth(zone.rampZ, dz));
  }
  return best;
}

/** True for OSM building footprints the hero Tower Bridge replaces (towers, piers, walkways, decks). */
export function coveredByHeroLandmark(ring: Ring): boolean {
  if (ring.length === 0) return false;
  let cx = 0;
  let cz = 0;
  for (const [x, z] of ring) {
    cx += x;
    cz += z;
  }
  cx /= ring.length;
  cz /= ring.length;
  const [bx, bz] = landmarkPos(LANDMARKS.towerBridge);
  const yaw = bearingToYaw(LANDMARKS.towerBridge.headingDeg);
  const dx = cx - bx;
  const dz = cz - bz;
  const across = dx * Math.cos(yaw) - dz * Math.sin(yaw);
  const along = dx * Math.sin(yaw) + dz * Math.cos(yaw);
  return Math.abs(across) < 34 && Math.abs(along) < 170;
}
