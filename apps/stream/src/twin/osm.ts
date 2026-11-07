import { VectorTile } from "@mapbox/vector-tile";
import { PbfReader } from "pbf";
import { cached, TTL } from "@/lib/server/cache";
import { coveredByHeroLandmark } from "@/twin/landmarks";
import { GEOMETRY_BOUNDS as B, insideGeometryBounds, originKey, projection, snapOrigin, TOWER_HILL, type LatLon, type Projection, type Ring, type TwinGeometry, type TwinPoi } from "@/twin/config";

/** Server-side loader for the real OSM geometry around the origin (OpenFreeMap z14 vector tiles). */

const TILEJSON = "https://tiles.openfreemap.org/planet";
const ZOOM = 14;
const FETCH_TIMEOUT_MS = 15_000;
const MAX_BUILDING_HEIGHT_M = 330;
const DEFAULT_BUILDING_HEIGHT_M = 8;
const WATERWAY_WIDTH_M: Record<string, number> = { river: 14, canal: 8, stream: 3, ditch: 2, drain: 2 };
const PARK_CLASSES = new Set(["park", "nature_reserve", "protected_area", "national_park"]);
const GREEN_LANDCOVER = new Set(["grass", "wood"]);
const OSM_COLOURS: Record<string, string> = {
  light_brown: "#c4a484",
  darkbrown: "#5c4033",
  "yellow-brown": "#b8a060",
  brick_red: "#cb4154",
  cream: "#fffdd0",
};

/** Bump when the extraction changes shape so a stale `.cache/` file is not served. */
const GEOMETRY_VERSION = 3;
const cacheKey = (origin: LatLon) => `twin-osm-v${GEOMETRY_VERSION}-z${ZOOM}-${originKey(origin)}-b${B.xMin}_${B.xMax}_${B.zMin}_${B.zMax}`;
/** Polygons whose courtyard hole we dropped: they must not be used to hide buildings inside them. */
const holed = new WeakSet<Ring>();

type Rect = { x0: number; x1: number; z0: number; z1: number };
type Collected = TwinGeometry & { waterways: Ring[] };

function tileXY(lat: number, lon: number, z: number) {
  const n = 2 ** z;
  const x = Math.floor(((lon + 180) / 360) * n);
  const rad = (lat * Math.PI) / 180;
  const y = Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n);
  return { x, y };
}

function tilesCovering({ toLatLon }: Projection) {
  const nw = toLatLon(B.xMin, B.zMin);
  const se = toLatLon(B.xMax, B.zMax);
  const a = tileXY(nw.lat, nw.lon, ZOOM);
  const b = tileXY(se.lat, se.lon, ZOOM);
  const out: { x: number; y: number }[] = [];
  for (let x = a.x; x <= b.x; x++) for (let y = a.y; y <= b.y; y++) out.push({ x, y });
  return out;
}

/** The tile's exact footprint in local metres. Tiles carry a ~24 m buffer, so everything is clipped to this to avoid overlapping duplicates. */
function tileRect(x: number, y: number, { toLocal }: Projection): Rect {
  const n = 2 ** ZOOM;
  const lat = (ty: number) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * ty) / n))) * 180) / Math.PI;
  const [x0, z0] = toLocal(lat(y), (x / n) * 360 - 180);
  const [x1, z1] = toLocal(lat(y + 1), ((x + 1) / n) * 360 - 180);
  return { x0, x1, z0, z1 };
}

const bboxOf = (ring: Ring) => {
  let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
  for (const [x, z] of ring) {
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (z < z0) z0 = z;
    if (z > z1) z1 = z;
  }
  return { x0, x1, z0, z1 };
};

const inRange = (ring: Ring) => {
  const b = bboxOf(ring);
  return b.x1 >= B.xMin && b.x0 <= B.xMax && b.z1 >= B.zMin && b.z0 <= B.zMax;
};

const ringArea = (ring: Ring) => {
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  return Math.abs(a) / 2;
};

/** Sutherland-Hodgman clip of a closed ring against an axis-aligned rectangle; returns a closed ring or null. */
function clipRing(ring: Ring, r: Rect): Ring | null {
  let pts = ring.slice(0, ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1] ? -1 : undefined);
  const edges: { inside: (p: [number, number]) => boolean; cut: (a: [number, number], b: [number, number]) => [number, number] }[] = [
    { inside: (p) => p[0] >= r.x0, cut: (a, b) => [r.x0, a[1] + ((b[1] - a[1]) * (r.x0 - a[0])) / (b[0] - a[0])] },
    { inside: (p) => p[0] <= r.x1, cut: (a, b) => [r.x1, a[1] + ((b[1] - a[1]) * (r.x1 - a[0])) / (b[0] - a[0])] },
    { inside: (p) => p[1] >= r.z0, cut: (a, b) => [a[0] + ((b[0] - a[0]) * (r.z0 - a[1])) / (b[1] - a[1]), r.z0] },
    { inside: (p) => p[1] <= r.z1, cut: (a, b) => [a[0] + ((b[0] - a[0]) * (r.z1 - a[1])) / (b[1] - a[1]), r.z1] },
  ];
  for (const e of edges) {
    const next: Ring = [];
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i];
      const b = pts[(i + 1) % pts.length];
      if (e.inside(a)) {
        next.push(a);
        if (!e.inside(b)) next.push(e.cut(a, b));
      } else if (e.inside(b)) next.push(e.cut(a, b));
    }
    pts = next;
    if (pts.length < 3) return null;
  }
  const closed: Ring = [...pts, pts[0]];
  return ringArea(closed) >= 1 ? closed : null;
}

/** Liang-Barsky clip of a polyline against a rectangle; a line leaving and re-entering becomes several lines. */
function clipLine(line: Ring, r: Rect): Ring[] {
  const out: Ring[] = [];
  let run: Ring = [];
  for (let i = 0; i < line.length - 1; i++) {
    const [ax, az] = line[i];
    const [bx, bz] = line[i + 1];
    const dx = bx - ax;
    const dz = bz - az;
    let t0 = 0;
    let t1 = 1;
    const tests: [number, number][] = [[-dx, ax - r.x0], [dx, r.x1 - ax], [-dz, az - r.z0], [dz, r.z1 - az]];
    let visible = true;
    for (const [p, q] of tests) {
      if (p === 0) {
        if (q < 0) visible = false;
      } else {
        const t = q / p;
        if (p < 0) t0 = Math.max(t0, t);
        else t1 = Math.min(t1, t);
      }
    }
    if (!visible || t0 > t1) {
      if (run.length > 1) out.push(run);
      run = [];
      continue;
    }
    const start: [number, number] = [ax + dx * t0, az + dz * t0];
    const end: [number, number] = [ax + dx * t1, az + dz * t1];
    const last = run[run.length - 1];
    if (!last || Math.hypot(last[0] - start[0], last[1] - start[1]) > 1e-6) {
      if (run.length > 1) out.push(run);
      run = [start];
    }
    run.push(end);
  }
  if (run.length > 1) out.push(run);
  return out;
}

const insideRect = ([x, z]: [number, number], r: Rect) => x >= r.x0 && x < r.x1 && z >= r.z0 && z < r.z1;

function pointInRing([px, pz]: [number, number], ring: Ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, zi] = ring[i];
    const [xj, zj] = ring[j];
    if (zi > pz !== zj > pz && px < ((xj - xi) * (pz - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

function roadKind(props: Record<string, unknown>): TwinGeometry["roads"][number]["kind"] | null {
  const cls = String(props.class ?? "");
  const subclass = String(props.subclass ?? "");
  const service = String(props.service ?? "");
  if (props.indoor) return null;
  if (["motorway", "trunk", "primary", "secondary"].includes(cls)) return "major";
  if (cls === "service") return ["driveway", "parking_aisle"].includes(service) ? null : "minor";
  if (["tertiary", "minor", "street"].includes(cls)) return "minor";
  if (cls === "path") return ["platform", "corridor"].includes(subclass) ? null : "path";
  if (["track", "pedestrian"].includes(cls)) return "path";
  if (cls === "rail" || cls === "transit") return "rail";
  return null;
}

function poiKind(cls: string, subclass: string): TwinPoi["kind"] | null {
  if (cls === "railway") return "station";
  if (cls === "bus" && subclass === "bus_stop") return "bus_stop";
  if (cls === "attraction" || subclass === "castle" || subclass === "museum" || cls === "ferry_terminal") return "attraction";
  if (cls === "shop" || cls === "grocery" || cls === "clothing_store") return "shop";
  if (["cafe", "bar", "beer", "restaurant", "fast_food"].includes(cls) || cls === "pub") return "food";
  return null;
}

function stationKind(subclass: string): TwinGeometry["stations"][number]["kind"] | null {
  if (subclass === "station" || subclass === "halt") return "rail";
  if (subclass === "subway") return "subway";
  if (subclass === "tram_stop") return "light_rail";
  return null;
}

const buildingColour = (raw: unknown) => {
  if (typeof raw !== "string" || !raw) return undefined;
  const c = raw.trim().toLowerCase();
  return OSM_COLOURS[c] ?? c;
};

const vertexKey = ([x, z]: [number, number]) => `${x.toFixed(3)},${z.toFixed(3)}`;

async function loadTile(template: string, x: number, y: number, out: Collected, lightRail: Ring[], P: Projection) {
  const { toLocal } = P;
  const res = await fetch(template.replace("{z}", String(ZOOM)).replace("{x}", String(x)).replace("{y}", String(y)), {
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`tile ${x}/${y} failed: HTTP ${res.status}`);
  const tile = new VectorTile(new PbfReader(new Uint8Array(await res.arrayBuffer())));
  const rect = tileRect(x, y, P);
  const geometry = (layer: string, i: number) => tile.layers[layer].feature(i).toGeoJSON(x, y, ZOOM).geometry;
  const toRings = (g: GeoJSON.Geometry): { ring: Ring; hasHole: boolean }[] => {
    const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    // Outer rings only: courtyards are rare at this scale and holes cost a triangulation pass.
    return polys.flatMap((p) => {
      const clipped = clipRing(p[0].map(([lon, lat]) => toLocal(lat, lon)), rect);
      return clipped && inRange(clipped) ? [{ ring: clipped, hasHole: p.length > 1 }] : [];
    });
  };
  const toLines = (g: GeoJSON.Geometry): Ring[] => {
    const lines = g.type === "LineString" ? [g.coordinates] : g.type === "MultiLineString" ? g.coordinates : [];
    return lines.map((l) => l.map(([lon, lat]) => toLocal(lat, lon)));
  };
  const clippedLines = (g: GeoJSON.Geometry) => toLines(g).flatMap((line) => clipLine(line, rect)).filter(inRange);

  const b = tile.layers.building;
  for (let i = 0; b && i < b.length; i++) {
    const props = b.feature(i).properties;
    if (props.hide_3d) continue;
    const rawHeight = Number(props.render_height);
    const height = Math.min(rawHeight > 0 ? rawHeight : DEFAULT_BUILDING_HEIGHT_M, MAX_BUILDING_HEIGHT_M);
    const minHeight = Math.min(Math.max(Number(props.render_min_height) || 0, 0), height - 1);
    const colour = buildingColour(props.colour);
    for (const { ring, hasHole } of toRings(geometry("building", i))) {
      if (hasHole) holed.add(ring);
      out.buildings.push({ ring, height, minHeight, ...(colour && { colour }) });
    }
  }

  const names = new Map<string, Map<string, number>>();
  const tn = tile.layers.transportation_name;
  for (let i = 0; tn && i < tn.length; i++) {
    const props = tn.feature(i).properties;
    const name = String(props["name:latin"] ?? props.name ?? props.ref ?? "");
    if (!name) continue;
    for (const line of toLines(geometry("transportation_name", i))) {
      for (const v of line) {
        const key = vertexKey(v);
        const votes = names.get(key) ?? new Map<string, number>();
        votes.set(name, (votes.get(name) ?? 0) + 1);
        names.set(key, votes);
      }
    }
  }
  const t = tile.layers.transportation;
  for (let i = 0; t && i < t.length; i++) {
    const props = t.feature(i).properties;
    const kind = roadKind(props);
    if (!kind) continue;
    const bridge = props.brunnel === "bridge";
    const tunnel = props.brunnel === "tunnel";
    const oneway = Number(props.oneway) !== 0 && props.oneway !== undefined;
    for (const line of toLines(geometry("transportation", i))) {
      const tally = new Map<string, number>();
      for (const v of line) for (const [n, c] of names.get(vertexKey(v)) ?? []) tally.set(n, (tally.get(n) ?? 0) + c);
      const name = [...tally].sort((p, q) => q[1] - p[1])[0]?.[0];
      for (const piece of clipLine(line, rect)) {
        if (!inRange(piece)) continue;
        out.roads.push({ line: piece, kind, ...(name && { name }), ...(bridge && { bridge }), ...(tunnel && { tunnel }), ...(oneway && { oneway }) });
        if (props.subclass === "light_rail") lightRail.push(piece);
      }
    }
  }

  const w = tile.layers.water;
  for (let i = 0; w && i < w.length; i++) for (const { ring } of toRings(geometry("water", i))) out.water.push(ring);
  const ww = tile.layers.waterway;
  for (let i = 0; ww && i < ww.length; i++) {
    const props = ww.feature(i).properties;
    const width = WATERWAY_WIDTH_M[String(props.class)];
    if (!width || props.brunnel === "tunnel") continue;
    for (const line of clippedLines(geometry("waterway", i))) {
      for (let s = 0; s < line.length - 1; s++) {
        const [ax, az] = line[s];
        const [bx, bz] = line[s + 1];
        const len = Math.hypot(bx - ax, bz - az);
        if (len < 0.5) continue;
        const nx = (-(bz - az) / len) * (width / 2);
        const nz = ((bx - ax) / len) * (width / 2);
        out.waterways.push([[ax + nx, az + nz], [bx + nx, bz + nz], [bx - nx, bz - nz], [ax - nx, az - nz], [ax + nx, az + nz]]);
      }
    }
  }
  const p = tile.layers.park;
  for (let i = 0; p && i < p.length; i++) {
    if (!PARK_CLASSES.has(String(p.feature(i).properties.class))) continue;
    for (const { ring } of toRings(geometry("park", i))) out.parks.push(ring);
  }
  const lc = tile.layers.landcover;
  for (let i = 0; lc && i < lc.length; i++) {
    if (!GREEN_LANDCOVER.has(String(lc.feature(i).properties.class))) continue;
    for (const { ring } of toRings(geometry("landcover", i))) out.parks.push(ring);
  }

  const poi = tile.layers.poi;
  for (let i = 0; poi && i < poi.length; i++) {
    const props = poi.feature(i).properties;
    const g = geometry("poi", i);
    if (g.type !== "Point") continue;
    const pos = toLocal(g.coordinates[1], g.coordinates[0]);
    if (!insideRect(pos, rect) || !insideGeometryBounds(pos[0], pos[1])) continue;
    const name = String(props["name:latin"] ?? props.name ?? "");
    const stationOf = props.class === "railway" ? stationKind(String(props.subclass)) : null;
    if (stationOf && name) out.stations.push({ pos, name, kind: stationOf });
    const poiOf = poiKind(String(props.class ?? ""), String(props.subclass ?? ""));
    if (poiOf) out.pois.push({ name: name || String(props.subclass ?? poiOf), x: pos[0], z: pos[1], kind: poiOf });
  }
}

/** Thin water only where the river/dock polygons do not already cover it. */
function uncoveredWaterways(waterways: Ring[], water: Ring[]) {
  const boxes = water.map(bboxOf);
  return waterways.filter((q) => {
    const mid: [number, number] = [(q[0][0] + q[1][0] + q[2][0] + q[3][0]) / 4, (q[0][1] + q[1][1] + q[2][1] + q[3][1]) / 4];
    return !water.some((ring, i) => {
      const b = boxes[i];
      return mid[0] >= b.x0 && mid[0] <= b.x1 && mid[1] >= b.z0 && mid[1] <= b.z1 && pointInRing(mid, ring);
    });
  });
}

/** Drops exact duplicates and buildings fully swallowed by a taller/equal one (building:part overlaps z-fight). */
function dropHiddenBuildings(buildings: TwinGeometry["buildings"]) {
  const seen = new Set<string>();
  const unique = buildings.filter((b) => {
    const sig = `${b.height}|${b.minHeight}|${b.ring.map(([x, z]) => `${Math.round(x * 10)},${Math.round(z * 10)}`).sort().join(";")}`;
    if (seen.has(sig)) return false;
    seen.add(sig);
    return true;
  });
  const CELL = 64;
  const boxes = unique.map((b) => bboxOf(b.ring));
  const grid = new Map<string, number[]>();
  boxes.forEach((bb, i) => {
    for (let cx = Math.floor(bb.x0 / CELL); cx <= Math.floor(bb.x1 / CELL); cx++)
      for (let cz = Math.floor(bb.z0 / CELL); cz <= Math.floor(bb.z1 / CELL); cz++) {
        const key = `${cx},${cz}`;
        grid.set(key, [...(grid.get(key) ?? []), i]);
      }
  });
  return unique.filter((a, i) => {
    const ba = boxes[i];
    const cell = grid.get(`${Math.floor(ba.x0 / CELL)},${Math.floor(ba.z0 / CELL)}`) ?? [];
    return !cell.some((j) => {
      if (j === i || holed.has(unique[j].ring)) return false;
      const bj = boxes[j];
      const b = unique[j];
      const covers = bj.x0 <= ba.x0 && bj.x1 >= ba.x1 && bj.z0 <= ba.z0 && bj.z1 >= ba.z1;
      const taller = b.height > a.height || (b.height === a.height && j < i);
      return covers && taller && b.minHeight <= a.minHeight && a.ring.every((pt) => pointInRing(pt, b.ring));
    });
  });
}

/** Local metres to the centimetre: 13 significant digits of float per vertex is a third of the payload. */
const round2 = (v: number) => Math.round(v * 100) / 100;
const roundRing = (ring: Ring): Ring => ring.map(([x, z]) => [round2(x), round2(z)]);

async function fetchGeometry(origin: LatLon): Promise<TwinGeometry> {
  const P = projection(origin);
  const { tiles } = (await (await fetch(TILEJSON, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) })).json()) as { tiles: string[] };
  const out: Collected = { buildings: [], roads: [], water: [], parks: [], pois: [], stations: [], waterways: [] };
  const lightRail: Ring[] = [];
  await Promise.all(tilesCovering(P).map(({ x, y }) => loadTile(tiles[0], x, y, out, lightRail, P)));
  // Hero landmarks are placed in Tower Hill metres; compare each footprint in that frame.
  const TH = projection(TOWER_HILL);
  const inTowerHillFrame = (ring: Ring): Ring =>
    ring.map(([x, z]) => {
      const ll = P.toLatLon(x, z);
      return TH.toLocal(ll.lat, ll.lon);
    });
  out.buildings = dropHiddenBuildings(out.buildings).filter((b) => !coveredByHeroLandmark(inTowerHillFrame(b.ring)));
  out.water.push(...uncoveredWaterways(out.waterways, out.water));
  for (const s of out.stations) {
    if (s.kind === "rail" && lightRail.some((l) => l.some(([x, z]) => Math.hypot(x - s.pos[0], z - s.pos[1]) < 60))) s.kind = "light_rail";
  }
  return {
    buildings: out.buildings.map((b) => ({ ...b, ring: roundRing(b.ring) })),
    roads: out.roads.map((r) => ({ ...r, line: roundRing(r.line) })),
    water: out.water.map(roundRing),
    parks: out.parks.map(roundRing),
    pois: out.pois.map((p) => ({ ...p, x: round2(p.x), z: round2(p.z) })),
    stations: out.stations.map((s) => ({ ...s, pos: [round2(s.pos[0]), round2(s.pos[1])] })),
  };
}

/** Geometry for the process lifetime and, via `.cache/`, across restarts; concurrent callers share one fetch. Pass a snapped origin. */
export function loadGeometry(origin: LatLon = TOWER_HILL): Promise<TwinGeometry> {
  return cached(cacheKey(origin), { ttlMs: TTL.week }, () => fetchGeometry(origin));
}

/** Snapped origin from `lat`/`lon` query params, Tower Hill when absent or malformed. */
export function originFromParams(params: URLSearchParams): LatLon {
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  if (!params.has("lat") || !params.has("lon") || !Number.isFinite(lat) || !Number.isFinite(lon)) return TOWER_HILL;
  return snapOrigin(lat, lon);
}
