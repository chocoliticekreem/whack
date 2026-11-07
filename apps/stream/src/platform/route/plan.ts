import type { Projection } from "@/twin/config";
import type { FieldAnchor, FieldSegment } from "@/twin/field/types";
import type { RouteMode, RoutePath, RouteResponse } from "./types";

/** Density cost grows with the square of people per 100 m: 5/100 m doubles a street, 10/100 m makes it 5×. */
const PED_WEIGHT = 0.04;
/** Cost per vehicle per 100 m on roads shared with traffic (crossings); paths carry none. */
const VEHICLE_WEIGHT = 0.01;
/** A closed street costs this many times its length; it is avoided but never impassable. */
const CLOSED_PENALTY = 8;
const CLOSED_NEAR_M = 15;
const WALK_MPS = 1.35;
/** Segments at or above this density count as a hotspot the calmer route can claim to avoid. */
const HOT_PEDS = 6;
const HOT_LABEL_M = 140;
const NODE_SNAP_MAX_M = 120;
/** OSM leaves islands (platforms, private yards, station concourses); a start on one this small is moved to the street network. */
const MIN_COMPONENT_NODES = 30;

interface Edge {
  to: number;
  seg: FieldSegment;
  closed: boolean;
}

export interface Graph {
  nodes: [number, number][];
  adj: Edge[][];
}

const key = (p: [number, number]) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`;

function distToPolyline(p: [number, number], line: [number, number][]) {
  let best = Infinity;
  for (let i = 1; i < line.length; i++) {
    const [ax, az] = line[i - 1];
    const [bx, bz] = line[i];
    const dx = bx - ax;
    const dz = bz - az;
    const l2 = dx * dx + dz * dz;
    const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - az) * dz) / l2));
    best = Math.min(best, Math.hypot(p[0] - (ax + dx * t), p[1] - (az + dz * t)));
  }
  return best;
}

/** Builds an undirected graph from the field segments; `closedLines` are local-metre polylines. */
export function buildGraph(segments: FieldSegment[], closedLines: [number, number][][]): Graph {
  const index = new Map<string, number>();
  const nodes: [number, number][] = [];
  const adj: Edge[][] = [];
  const node = (p: [number, number]) => {
    const k = key(p);
    let id = index.get(k);
    if (id === undefined) {
      id = nodes.length;
      index.set(k, id);
      nodes.push(p);
      adj.push([]);
    }
    return id;
  };
  for (const seg of segments) {
    if (seg.kind === "rail") continue;
    const a = node(seg.a);
    const b = node(seg.b);
    const mid: [number, number] = [(seg.a[0] + seg.b[0]) / 2, (seg.a[1] + seg.b[1]) / 2];
    const closed = closedLines.some((line) => distToPolyline(mid, line) <= CLOSED_NEAR_M);
    adj[a].push({ to: b, seg, closed });
    adj[b].push({ to: a, seg, closed });
  }
  return { nodes, adj };
}

/** Connected-component id per node, plus the size of each component. */
function components(g: Graph) {
  const id = new Int32Array(g.nodes.length).fill(-1);
  const sizes: number[] = [];
  for (let start = 0; start < g.nodes.length; start++) {
    if (id[start] >= 0) continue;
    const c = sizes.length;
    let size = 0;
    const stack = [start];
    id[start] = c;
    while (stack.length) {
      const u = stack.pop()!;
      size++;
      for (const e of g.adj[u]) {
        if (id[e.to] < 0) {
          id[e.to] = c;
          stack.push(e.to);
        }
      }
    }
    sizes.push(size);
  }
  return { id, sizes };
}

export function nearestNode(g: Graph, p: [number, number], accept: (node: number) => boolean = () => true) {
  let best = -1;
  let bestD = Infinity;
  for (let i = 0; i < g.nodes.length; i++) {
    if (!accept(i)) continue;
    const d = Math.hypot(g.nodes[i][0] - p[0], g.nodes[i][1] - p[1]);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return bestD <= NODE_SNAP_MAX_M ? best : -1;
}

const segLength = (s: FieldSegment) => Math.hypot(s.b[0] - s.a[0], s.b[1] - s.a[1]);

function edgeCost(e: Edge, mode: RouteMode) {
  const len = segLength(e.seg);
  if (mode === "fast") return len * (e.closed ? CLOSED_PENALTY : 1);
  const vehicles = e.seg.kind === "path" ? 0 : e.seg.vehicles;
  const crowd = 1 + PED_WEIGHT * e.seg.peds * e.seg.peds + VEHICLE_WEIGHT * vehicles;
  return len * crowd * (e.closed ? CLOSED_PENALTY : 1);
}

/** Dijkstra with a binary heap; returns the edge sequence from `from` to `to` or null. */
export function shortestPath(g: Graph, from: number, to: number, mode: RouteMode): Edge[] | null {
  const dist = new Float64Array(g.nodes.length).fill(Infinity);
  const prev = new Int32Array(g.nodes.length).fill(-1);
  const prevEdge: (Edge | null)[] = new Array(g.nodes.length).fill(null);
  const heap: [number, number][] = [[0, from]];
  dist[from] = 0;
  const push = (item: [number, number]) => {
    heap.push(item);
    let i = heap.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (heap[p][0] <= heap[i][0]) break;
      [heap[p], heap[i]] = [heap[i], heap[p]];
      i = p;
    }
  };
  const pop = () => {
    const top = heap[0];
    const last = heap.pop()!;
    if (heap.length) {
      heap[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
        if (m === i) break;
        [heap[m], heap[i]] = [heap[i], heap[m]];
        i = m;
      }
    }
    return top;
  };
  while (heap.length) {
    const [d, u] = pop();
    if (u === to) break;
    if (d > dist[u]) continue;
    for (const e of g.adj[u]) {
      const nd = d + edgeCost(e, mode);
      if (nd < dist[e.to]) {
        dist[e.to] = nd;
        prev[e.to] = u;
        prevEdge[e.to] = e;
        push([nd, e.to]);
      }
    }
  }
  if (!Number.isFinite(dist[to])) return null;
  const edges: Edge[] = [];
  for (let v = to; v !== from; v = prev[v]) edges.push(prevEdge[v]!);
  return edges.reverse();
}

function describe(g: Graph, from: number, edges: Edge[], mode: RouteMode, project: Projection): RoutePath {
  const xz: [number, number][] = [g.nodes[from]];
  let lengthM = 0;
  let exposure = 0;
  let peakPeds = 0;
  for (const e of edges) {
    xz.push(g.nodes[e.to]);
    const len = segLength(e.seg);
    lengthM += len;
    exposure += (e.seg.peds * len) / 100;
    peakPeds = Math.max(peakPeds, e.seg.peds);
  }
  return {
    mode,
    xz,
    polyline: xz.map(([x, z]) => {
      const { lat, lon } = project.toLatLon(x, z);
      return [Number(lat.toFixed(6)), Number(lon.toFixed(6))];
    }),
    lengthM: Math.round(lengthM),
    etaMin: Number((lengthM / WALK_MPS / 60).toFixed(1)),
    exposure: Number(exposure.toFixed(1)),
    peakPeds: Number(peakPeds.toFixed(1)),
  };
}

function hotspotsAvoided(fast: Edge[], calm: Edge[], anchors: FieldAnchor[]) {
  const used = new Set(calm.map((e) => e.seg.id));
  const names: string[] = [];
  for (const e of fast) {
    if (used.has(e.seg.id) || e.seg.peds < HOT_PEDS) continue;
    const mid: [number, number] = [(e.seg.a[0] + e.seg.b[0]) / 2, (e.seg.a[1] + e.seg.b[1]) / 2];
    let best: FieldAnchor | null = null;
    let bestD = HOT_LABEL_M;
    for (const a of anchors) {
      const d = Math.hypot(a.x - mid[0], a.z - mid[1]);
      if (d < bestD) {
        bestD = d;
        best = a;
      }
    }
    if (best && !names.includes(best.name)) names.push(best.name);
  }
  return names.slice(0, 4);
}

export interface PlanInput {
  graph: Graph;
  anchors: FieldAnchor[];
  from: { lat: number; lon: number; xz: [number, number] };
  to: { name: string; lat: number; lon: number; xz: [number, number] };
  at: number;
  /** Projection the field segments were built in (the twin origin). */
  project: Projection;
}

export function planRoutes(input: PlanInput): RouteResponse | { error: string } {
  const { graph } = input;
  const comp = components(graph);
  const a = nearestNode(graph, input.from.xz, (n) => comp.sizes[comp.id[n]] >= MIN_COMPONENT_NODES);
  // Snap the destination onto the start's network: a station's TfL point often sits on an isolated platform or concourse.
  const b = a < 0 ? -1 : nearestNode(graph, input.to.xz, (n) => comp.id[n] === comp.id[a]);
  if (a < 0) return { error: "Start is outside the twin's street graph (~650 m around the streamer)" };
  if (b < 0) return { error: "Destination is outside the twin's street graph (~650 m around the streamer)" };
  const fast = shortestPath(graph, a, b, "fast");
  const calm = shortestPath(graph, a, b, "calm");
  if (!fast || !calm) return { error: "No walkable path between those points" };
  const fastest = describe(graph, a, fast, "fast", input.project);
  const calmest = describe(graph, a, calm, "calm", input.project);
  return {
    from: { lat: input.from.lat, lon: input.from.lon },
    to: { name: input.to.name, lat: input.to.lat, lon: input.to.lon },
    fastest,
    calmest,
    avoids: hotspotsAvoided(fast, calm, input.anchors),
    extraMin: Number(Math.max(0, calmest.etaMin - fastest.etaMin).toFixed(1)),
    lessCrowd: fastest.exposure > 0 ? Number(Math.max(0, 1 - calmest.exposure / fastest.exposure).toFixed(2)) : 0,
    at: input.at,
  };
}
