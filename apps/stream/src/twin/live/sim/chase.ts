import type { RoadNetwork, XZ } from "../../network/types";
import { wrapAngle } from "./rng";

const WORLD_EDGE_M = 560;

/** A vehicle (or walker) travelling along directed lanes of the road graph. */
export class Driver {
  lane = -1;
  s = 0;
  v = 0;
  next = -1;
  x = 0;
  z = 0;
  yaw = 0;
  /** Extra yaw added by the renderer (sliding / spinning out). */
  slip = 0;
  fresh = true;
  arrived = false;
  vMax = 10;
  accel = 3;
  brake = 7;
  /** Speed the driver is willing to carry into a 90° turn. */
  cornerV = 6;
  /** Metres to the left of the lane's travel direction. */
  latMag = 1.6;
  /** Position smoothing rate (1/s). Low = corners are cut and cars drift. */
  smooth = 12;
  delay = 0;
  avoidDead = false;
  route: number[] = [];
  ri = 0;
  follow: Driver | null = null;
}

interface HeapState {
  k: Float64Array;
  v: Int32Array;
  n: number;
}

/**
 * Directed view of a RoadNetwork. A lane is one direction of one edge: `edge * 2` runs from→to and
 * `edge * 2 + 1` runs to→from (not allowed on one-way edges). Holds the A* router and the lane
 * stepping shared by chases, callouts and everyday London traffic.
 */
export class RoadGraph {
  readonly lanes: number;
  readonly empty: boolean;
  readonly edgeId: Int32Array;
  readonly len: Float32Array;
  readonly valid: Uint8Array;
  readonly major: Uint8Array;
  readonly from: Int32Array;
  readonly to: Int32Array;
  readonly yawStart: Float32Array;
  readonly yawEnd: Float32Array;
  readonly width: Float32Array;
  readonly midX: Float32Array;
  readonly midZ: Float32Array;
  readonly departing: Int32Array[];
  readonly main: Uint8Array;
  /** Valid lanes of the largest connected piece of the network. */
  readonly mainLanes: Int32Array;
  private readonly g: Float32Array;
  private readonly prevLane: Int32Array;
  private readonly seen: Uint32Array;
  private readonly closed: Uint32Array;
  private stamp = 0;
  private readonly heap: HeapState;
  private readonly out: XZ = [0, 0];

  constructor(private readonly net: RoadNetwork) {
    const edges = net.edges;
    const nodes = net.nodes;
    const lanes = edges.length * 2;
    this.lanes = lanes;
    this.empty = lanes === 0;
    this.edgeId = new Int32Array(lanes);
    this.len = new Float32Array(lanes);
    this.valid = new Uint8Array(lanes);
    this.major = new Uint8Array(lanes);
    this.from = new Int32Array(lanes);
    this.to = new Int32Array(lanes);
    this.yawStart = new Float32Array(lanes);
    this.yawEnd = new Float32Array(lanes);
    this.width = new Float32Array(lanes);
    this.midX = new Float32Array(lanes);
    this.midZ = new Float32Array(lanes);
    this.main = new Uint8Array(nodes.length);
    this.g = new Float32Array(nodes.length);
    this.prevLane = new Int32Array(nodes.length);
    this.seen = new Uint32Array(nodes.length);
    this.closed = new Uint32Array(nodes.length);
    this.heap = { k: new Float64Array(lanes + 8), v: new Int32Array(lanes + 8), n: 0 };

    const nodeIndex = new Map(nodes.map((n, i) => [n.id, i] as const));
    const dep: number[][] = nodes.map(() => []);
    const parent = nodes.map((_, i) => i);
    const find = (a: number) => {
      while (parent[a] !== a) a = parent[a] = parent[parent[a]];
      return a;
    };
    const out = this.out;
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const a = nodeIndex.get(e.from);
      const b = nodeIndex.get(e.to);
      const fwd = i * 2;
      const rev = fwd + 1;
      this.edgeId[fwd] = this.edgeId[rev] = e.id;
      this.len[fwd] = this.len[rev] = e.length;
      this.major[fwd] = this.major[rev] = e.kind === "major" ? 1 : 0;
      this.width[fwd] = this.width[rev] = e.width;
      if (a === undefined || b === undefined || e.length < 1) continue;
      this.from[fwd] = this.to[rev] = a;
      this.to[fwd] = this.from[rev] = b;
      const eps = Math.min(0.05, e.length * 0.4);
      const h0 = net.sample(e.id, eps, 0, out);
      const h1 = net.sample(e.id, Math.max(e.length - eps, eps), 0, out);
      this.yawStart[fwd] = h0;
      this.yawEnd[fwd] = h1;
      this.yawStart[rev] = wrapAngle(h1 + Math.PI);
      this.yawEnd[rev] = wrapAngle(h0 + Math.PI);
      net.sample(e.id, e.length / 2, 0, out);
      this.midX[fwd] = this.midX[rev] = out[0];
      this.midZ[fwd] = this.midZ[rev] = out[1];
      this.valid[fwd] = 1;
      dep[a].push(fwd);
      if (!e.oneway) {
        this.valid[rev] = 1;
        dep[b].push(rev);
      }
      parent[find(a)] = find(b);
    }
    this.departing = dep.map((l) => Int32Array.from(l));

    const total = new Float64Array(nodes.length);
    for (let L = 0; L < lanes; L += 2) if (this.valid[L]) total[find(this.from[L])] += this.len[L];
    let best = 0;
    for (let i = 1; i < total.length; i++) if (total[i] > total[best]) best = i;
    const mainLanes: number[] = [];
    for (let i = 0; i < nodes.length; i++) this.main[i] = total[best] > 0 && find(i) === find(best) ? 1 : 0;
    for (let L = 0; L < lanes; L++) if (this.valid[L] && this.main[this.from[L]]) mainLanes.push(L);
    this.mainLanes = Int32Array.from(mainLanes);
  }

  /** Position at `s` metres along `lane`, shifted `mag` metres to the left of travel. Returns the travel yaw. */
  pose(lane: number, s: number, mag: number, out: XZ): number {
    const fwd = (lane & 1) === 0;
    const h = this.net.sample(this.edgeId[lane], fwd ? s : this.len[lane] - s, fwd ? mag : -mag, out);
    return fwd ? h : wrapAngle(h + Math.PI);
  }

  nodeX(n: number) {
    return this.net.nodes[n].x;
  }

  nodeZ(n: number) {
    return this.net.nodes[n].z;
  }

  nearestNode(x: number, z: number, mainOnly = true): number {
    let best = -1;
    let bd = Infinity;
    const nodes = this.net.nodes;
    for (let i = 0; i < nodes.length; i++) {
      if (mainOnly && !this.main[i]) continue;
      const d = (nodes[i].x - x) ** 2 + (nodes[i].z - z) ** 2;
      if (d < bd) {
        bd = d;
        best = i;
      }
    }
    return best;
  }

  /** Forward lane of the edge nearest to (x, z), with the distance along it and the gap to it. */
  nearestLane(x: number, z: number, out: { lane: number; s: number; dist: number }) {
    const edges = this.net.edges;
    let bd = Infinity;
    for (let i = 0; i < edges.length; i++) {
      if (!this.valid[i * 2] || !this.main[this.from[i * 2]]) continue;
      const e = edges[i];
      for (let k = 0; k < e.pts.length - 1; k++) {
        const ax = e.pts[k][0];
        const az = e.pts[k][1];
        const vx = e.pts[k + 1][0] - ax;
        const vz = e.pts[k + 1][1] - az;
        const l2 = vx * vx + vz * vz || 1;
        const t = Math.min(1, Math.max(0, ((x - ax) * vx + (z - az) * vz) / l2));
        const d = Math.hypot(ax + vx * t - x, az + vz * t - z);
        if (d < bd) {
          bd = d;
          out.lane = i * 2;
          out.s = e.cum[k] + Math.sqrt(l2) * t;
          out.dist = d;
        }
      }
    }
    return bd < Infinity;
  }

  /** A* over lanes. `ban` is a lane that may not be taken as the first step (stops instant U-turns). */
  path(from: number, to: number, ban = -1): number[] | null {
    if (from === to) return [];
    const { g, seen, closed, prevLane, heap } = this;
    const stamp = ++this.stamp;
    const tx = this.nodeX(to);
    const tz = this.nodeZ(to);
    heap.n = 0;
    g[from] = 0;
    seen[from] = stamp;
    this.push(Math.hypot(this.nodeX(from) - tx, this.nodeZ(from) - tz), from);
    while (heap.n > 0) {
      const u = this.pop();
      if (closed[u] === stamp) continue;
      closed[u] = stamp;
      if (u === to) {
        const lanes: number[] = [];
        let n = to;
        while (n !== from) {
          const l = prevLane[n];
          lanes.push(l);
          n = this.from[l];
        }
        return lanes.reverse();
      }
      const dep = this.departing[u];
      for (let i = 0; i < dep.length; i++) {
        const l = dep[i];
        if (u === from && l === ban) continue;
        const v = this.to[l];
        if (closed[v] === stamp) continue;
        const ng = g[u] + this.len[l];
        if (seen[v] !== stamp || ng < g[v]) {
          g[v] = ng;
          seen[v] = stamp;
          prevLane[v] = l;
          this.push(ng + Math.hypot(this.nodeX(v) - tx, this.nodeZ(v) - tz), v);
        }
      }
    }
    return null;
  }

  private push(key: number, node: number) {
    const h = this.heap;
    let i = h.n++;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (h.k[p] <= key) break;
      h.k[i] = h.k[p];
      h.v[i] = h.v[p];
      i = p;
    }
    h.k[i] = key;
    h.v[i] = node;
  }

  private pop(): number {
    const h = this.heap;
    const top = h.v[0];
    const key = h.k[--h.n];
    const node = h.v[h.n];
    let i = 0;
    for (;;) {
      let c = i * 2 + 1;
      if (c >= h.n) break;
      if (c + 1 < h.n && h.k[c + 1] < h.k[c]) c++;
      if (h.k[c] >= key) break;
      h.k[i] = h.k[c];
      h.v[i] = h.v[c];
      i = c;
    }
    h.k[i] = key;
    h.v[i] = node;
    return top;
  }

  /** Random lane out of the end of `lane`, favouring straight ahead and major roads. */
  wander(lane: number, rng: () => number, avoidDead: boolean): number {
    const node = this.to[lane];
    const exits = this.departing[node];
    let total = 0;
    let count = 0;
    for (let i = 0; i < exits.length; i++) {
      if (exits[i] === (lane ^ 1)) continue;
      count++;
      total += this.exitWeight(lane, exits[i], avoidDead);
    }
    if (!count) return this.valid[lane ^ 1] ? lane ^ 1 : -1;
    let r = rng() * total;
    for (let i = 0; i < exits.length; i++) {
      if (exits[i] === (lane ^ 1)) continue;
      r -= this.exitWeight(lane, exits[i], avoidDead);
      if (r <= 0) return exits[i];
    }
    return exits[exits.length - 1];
  }

  private exitWeight(lane: number, c: number, avoidDead: boolean): number {
    const turn = Math.abs(wrapAngle(this.yawStart[c] - this.yawEnd[lane]));
    let w = Math.exp(-((turn / 1.0) ** 2)) + 0.08;
    if (this.major[c]) w *= 1.8;
    const n = this.to[c];
    const out = this.departing[n].length;
    if (out === 0) w *= 0.001;
    else if (avoidDead && out === 1) w *= 0.02;
    if (Math.hypot(this.nodeX(n), this.nodeZ(n)) > WORLD_EDGE_M) w *= 0.25;
    return w;
  }

  place(d: Driver, lane: number, s: number, rng: () => number) {
    d.lane = lane;
    d.s = s;
    d.v = 0;
    d.arrived = false;
    d.fresh = true;
    d.ri = 0;
    d.next = this.pickNext(d, rng);
  }

  private pickNext(d: Driver, rng: () => number): number {
    if (d.ri < d.route.length) return d.route[d.ri++];
    if (d.route.length) return -1;
    const node = this.to[d.lane];
    const f = d.follow;
    if (f && f.lane >= 0) {
      if (this.to[f.lane] === node) {
        if (f.next >= 0 && f.next !== (d.lane ^ 1)) return f.next;
      } else {
        const p = this.path(node, this.to[f.lane], d.lane ^ 1);
        if (p && p.length) return p[0];
      }
    }
    return this.wander(d.lane, rng, d.avoidDead);
  }

  /** Moves `d` along its lanes towards speed `vTarget`, slowing for turns and dead ends. */
  step(d: Driver, dt: number, vTarget: number, rng: () => number) {
    let target = Math.min(vTarget, d.vMax);
    if (d.delay > 0) {
      d.delay -= dt;
      target = 0;
    }
    const remaining = this.len[d.lane] - d.s;
    if (d.next >= 0) {
      const turn = Math.abs(wrapAngle(this.yawStart[d.next] - this.yawEnd[d.lane]));
      const vEnd = Math.max(d.cornerV, target * (1 - 0.8 * Math.min(1, turn / 1.2)));
      target = Math.min(target, Math.sqrt(vEnd * vEnd + 2 * d.brake * Math.max(0, remaining)));
    } else {
      target = Math.min(target, Math.sqrt(2 * d.brake * Math.max(0, remaining - 1.2)));
    }
    const dv = target - d.v;
    d.v = Math.max(0, dv > 0 ? d.v + Math.min(dv, d.accel * dt) : d.v + Math.max(dv, -d.brake * 1.6 * dt));
    d.s += d.v * dt;
    for (let guard = 0; guard < 4 && d.s >= this.len[d.lane]; guard++) {
      if (d.next < 0) {
        d.s = this.len[d.lane];
        d.v = 0;
        d.arrived = true;
        break;
      }
      d.s -= this.len[d.lane];
      d.lane = d.next;
      d.next = this.pickNext(d, rng);
    }
    this.settle(d, dt);
  }

  /** Writes the smoothed world pose of `d` from its lane position. */
  settle(d: Driver, dt: number) {
    const out = this.out;
    const laneYaw = this.pose(d.lane, d.s, d.latMag, out);
    if (d.fresh) {
      d.x = out[0];
      d.z = out[1];
      d.yaw = laneYaw;
      d.fresh = false;
      return;
    }
    const k = 1 - Math.exp(-d.smooth * dt);
    const nx = d.x + (out[0] - d.x) * k;
    const nz = d.z + (out[1] - d.z) * k;
    const moved = Math.hypot(nx - d.x, nz - d.z);
    const want = moved > 0.012 && d.v > 1.5 ? Math.atan2(nx - d.x, nz - d.z) : laneYaw;
    d.yaw += wrapAngle(want - d.yaw) * (1 - Math.exp(-9 * dt));
    d.x = nx;
    d.z = nz;
  }
}

export const PHASE_RUN = 0;
export const PHASE_STOP = 1;
export const PHASE_HOLD = 2;
export const PHASE_DONE = 3;

export interface Officer {
  live: boolean;
  suspect: boolean;
  x: number;
  z: number;
  yaw: number;
  tx: number;
  tz: number;
  delay: number;
  moving: boolean;
}

export interface Helicopter {
  x: number;
  y: number;
  z: number;
  yaw: number;
  /** Where the searchlight is pointing on the ground. */
  lx: number;
  lz: number;
  angle: number;
  leaving: boolean;
}

const COP_LAT = [1.3, 0.2, 2.5, 0.8];
const HOLD_S = 22;
const FADE_S = 1.6;
const PARK_TRIGGER_M = 26;

/**
 * One police pursuit. A dark hatchback flees along the road graph at 18–24 m/s, taking corners wide
 * and never braking for junctions. 2–4 police cars A* towards the junction it is heading for (so they
 * cut corners it can't) with a speed that rubber-bands to the gap. After 30–60 s the fleeing car is
 * stopped: the cops box it in front/rear/side, officers get out, the suspect steps out with hands up.
 * A helicopter orbits above with a searchlight until the scene is cleared.
 */
export class Chase {
  readonly quarry = new Driver();
  readonly cops: Driver[] = [];
  readonly officers: Officer[] = [];
  readonly heli: Helicopter | null;
  phase = PHASE_RUN;
  t = 0;
  /** 1 while the scene is visible, fading to 0 when cleared. */
  fade = 1;
  alive = true;
  private hold = 0;
  private readonly parked: Uint8Array;
  private readonly duration: number;
  private readonly seekSide: number;

  constructor(
    private readonly graph: RoadGraph,
    private readonly rng: () => number,
    focus: XZ,
    copCount: number,
    withHeli: boolean,
  ) {
    const rn = rng;
    this.duration = 30 + rn() * 30;
    this.seekSide = rn() < 0.5 ? 1 : -1;
    this.parked = new Uint8Array(4);
    for (let i = 0; i < 9; i++) {
      this.officers.push({ live: false, suspect: false, x: 0, z: 0, yaw: 0, tx: 0, tz: 0, delay: 0, moving: false });
    }

    const lane = this.pickStartLane(focus);
    const q = this.quarry;
    q.vMax = 18 + rn() * 6;
    q.accel = 6;
    q.brake = 11;
    q.cornerV = 9 + rn() * 3;
    q.latMag = 0.4;
    q.smooth = 5;
    q.avoidDead = true;
    if (lane < 0) {
      this.alive = false;
      this.heli = null;
      return;
    }
    const len = graph.len[lane];
    const s0 = Math.min(len - 8, Math.max(50, len * 0.7));
    graph.place(q, lane, s0, rn);
    q.v = q.vMax * 0.5;
    graph.settle(q, 0.016);

    for (let i = 0; i < copCount; i++) {
      const c = new Driver();
      c.vMax = q.vMax * (0.94 + rn() * 0.1);
      c.accel = 5;
      c.brake = 10;
      c.cornerV = 8;
      c.latMag = COP_LAT[i % COP_LAT.length];
      c.smooth = 6;
      c.follow = q;
      graph.place(c, lane, Math.max(0.5, s0 - 20 - i * 13), rn);
      c.v = q.v;
      graph.settle(c, 0.016);
      this.cops.push(c);
    }

    if (withHeli) {
      const a = rn() * Math.PI * 2;
      this.heli = { x: q.x + Math.cos(a) * 520, y: 95, z: q.z + Math.sin(a) * 520, yaw: 0, lx: q.x, lz: q.z, angle: a, leaving: false };
    } else this.heli = null;
  }

  private pickStartLane(focus: XZ): number {
    const g = this.graph;
    let best = -1;
    let total = 0;
    const weight = (L: number) => {
      if (g.len[L] < 90 || g.departing[g.to[L]].length < 2) return 0;
      const d2 = (g.midX[L] - focus[0]) ** 2 + (g.midZ[L] - focus[1]) ** 2;
      return Math.exp(-d2 / (2 * 260 * 260)) * (g.major[L] ? 3 : 1);
    };
    for (let i = 0; i < g.mainLanes.length; i++) total += weight(g.mainLanes[i]);
    if (total <= 0) return -1;
    let r = this.rng() * total;
    for (let i = 0; i < g.mainLanes.length; i++) {
      const L = g.mainLanes[i];
      r -= weight(L);
      if (r <= 0) {
        best = L;
        break;
      }
    }
    return best;
  }

  private laneSpeed(d: Driver, boost: number) {
    return d.vMax * (this.graph.major[d.lane] ? 1 : 0.72) * boost;
  }

  update(dt: number) {
    if (!this.alive) return;
    const g = this.graph;
    const rn = this.rng;
    this.t += dt;
    const q = this.quarry;

    if (this.phase === PHASE_RUN) {
      if (this.t > this.duration || (q.arrived && q.next < 0)) this.phase = PHASE_STOP;
      g.step(q, dt, this.laneSpeed(q, 1), rn);
    } else if (this.phase !== PHASE_DONE) {
      g.step(q, dt, 0, rn);
      q.slip += (this.seekSide * 0.55 - q.slip) * Math.min(1, dt * 1.5);
      if (this.phase === PHASE_STOP && q.v < 0.3) {
        this.phase = PHASE_HOLD;
        this.hold = 0;
      }
    }

    let allParked = this.phase >= PHASE_STOP;
    for (let i = 0; i < this.cops.length; i++) {
      const c = this.cops[i];
      const d = Math.hypot(q.x - c.x, q.z - c.z);
      if (this.parked[i] === 0 && this.phase >= PHASE_STOP && q.v < 8 && d < PARK_TRIGGER_M) this.parked[i] = 1;
      if (this.parked[i] === 0) {
        allParked = false;
        if (this.phase !== PHASE_DONE) {
          const boost = 1 + Math.min(0.25, Math.max(-0.3, (d - 24) / 80));
          const cruise = this.laneSpeed(c, boost);
          g.step(c, dt, d < 9 && q.v > 2 ? Math.min(cruise, q.v * 0.92 + 0.5) : cruise, rn);
        }
      } else if (this.parked[i] === 1) {
        if (!this.parkAt(c, i, dt)) allParked = false;
        else this.parked[i] = 2;
      }
    }

    if (this.phase === PHASE_HOLD) {
      if (allParked && this.hold === 0) this.releaseOfficers();
      if (allParked) this.hold += dt;
      if (this.hold > HOLD_S) this.phase = PHASE_DONE;
    }
    this.updateOfficers(dt);
    if (this.phase === PHASE_DONE) {
      this.fade = Math.max(0, this.fade - dt / FADE_S);
      if (this.fade === 0 && !this.heli) this.alive = false;
    }
    if (this.heli) this.updateHeli(dt);
    if (this.phase === PHASE_DONE && this.fade === 0 && this.heli && this.heli.leaving) {
      if (Math.hypot(this.heli.x - q.x, this.heli.z - q.z) > 700) this.alive = false;
    }
  }

  /** Eases a cop car into its box-in slot around the stopped quarry. Returns true once settled. */
  private parkAt(c: Driver, i: number, dt: number): boolean {
    const q = this.quarry;
    const psi = q.yaw + q.slip;
    const fx = Math.sin(psi);
    const fz = Math.cos(psi);
    const lx = Math.cos(psi);
    const lz = -Math.sin(psi);
    let sx = q.x;
    let sz = q.z;
    let yaw = psi;
    if (i === 0) {
      sx -= fx * 5.6;
      sz -= fz * 5.6;
    } else if (i === 1) {
      sx += fx * 6.4;
      sz += fz * 6.4;
      yaw = psi + Math.PI;
    } else {
      const side = i === 2 ? 1 : -1;
      sx += lx * 3.7 * side + fx * 0.8;
      sz += lz * 3.7 * side + fz * 0.8;
      yaw = Math.atan2(q.x - sx, q.z - sz) + side * 0.2;
    }
    const dx = sx - c.x;
    const dz = sz - c.z;
    const dist = Math.hypot(dx, dz);
    const want = Math.min(14, dist * 1.7 + 0.4);
    c.v += Math.max(-13 * dt, Math.min(6 * dt, want - c.v));
    const step = Math.min(dist, c.v * dt);
    if (dist > 0.05) {
      c.x += (dx / dist) * step;
      c.z += (dz / dist) * step;
    }
    const heading = dist > 1.5 && c.v > 1.5 ? Math.atan2(dx, dz) : yaw;
    c.yaw += wrapAngle(heading - c.yaw) * (1 - Math.exp(-6 * dt));
    if (dist < 0.25) {
      c.v = 0;
      c.yaw += wrapAngle(yaw - c.yaw) * Math.min(1, dt * 4);
      return true;
    }
    return false;
  }

  private releaseOfficers() {
    const q = this.quarry;
    const psi = q.yaw + q.slip;
    const lx = Math.cos(psi);
    const lz = -Math.sin(psi);
    const sus = this.officers[0];
    sus.live = true;
    sus.suspect = true;
    sus.x = q.x + lx * 0.8;
    sus.z = q.z + lz * 0.8;
    sus.tx = q.x + lx * 2.6;
    sus.tz = q.z + lz * 2.6;
    sus.delay = 1.5;
    let k = 1;
    for (let i = 0; i < this.cops.length && k < this.officers.length; i++) {
      const c = this.cops[i];
      const cl = Math.cos(c.yaw);
      const cz = -Math.sin(c.yaw);
      for (let j = 0; j < 2 && k < this.officers.length; j++, k++) {
        const o = this.officers[k];
        const side = j === 0 ? 1 : -1;
        o.live = true;
        o.suspect = false;
        o.x = c.x + cl * 1.2 * side;
        o.z = c.z + cz * 1.2 * side;
        const a = (k / this.officers.length) * Math.PI * 2 + 0.7;
        o.tx = sus.tx + Math.cos(a) * 2.1;
        o.tz = sus.tz + Math.sin(a) * 2.1;
        o.delay = 0.3 + k * 0.25;
      }
    }
  }

  private updateOfficers(dt: number) {
    for (const o of this.officers) {
      if (!o.live) continue;
      if (o.delay > 0) {
        o.delay -= dt;
        o.moving = false;
        continue;
      }
      const dx = o.tx - o.x;
      const dz = o.tz - o.z;
      const d = Math.hypot(dx, dz);
      const sp = o.suspect ? 1.1 : 3.4;
      if (d > 0.15) {
        const step = Math.min(d, sp * dt);
        o.x += (dx / d) * step;
        o.z += (dz / d) * step;
        o.yaw += wrapAngle(Math.atan2(dx, dz) - o.yaw) * Math.min(1, dt * 10);
        o.moving = true;
      } else {
        o.moving = false;
        const q = this.quarry;
        const face = o.suspect ? Math.atan2(this.cops[0] ? this.cops[0].x - o.x : 0, this.cops[0] ? this.cops[0].z - o.z : 1) : Math.atan2(q.x - o.x, q.z - o.z);
        o.yaw += wrapAngle(face - o.yaw) * Math.min(1, dt * 5);
      }
      if (this.phase === PHASE_DONE && this.fade === 0) o.live = false;
    }
  }

  private updateHeli(dt: number) {
    const h = this.heli;
    if (!h) return;
    const q = this.quarry;
    if (this.phase === PHASE_DONE && this.hold > HOLD_S) h.leaving = true;
    h.angle += 0.5 * dt;
    const tx = h.leaving ? h.x + 900 : q.x + Math.cos(h.angle) * 48;
    const tz = h.leaving ? h.z - 400 : q.z + Math.sin(h.angle) * 48;
    const ty = h.leaving ? 140 : 62;
    const dx = tx - h.x;
    const dz = tz - h.z;
    const dist = Math.hypot(dx, dz);
    const sp = h.leaving ? 55 : Math.min(48, 4 + dist * 0.9);
    if (dist > 0.5) {
      const step = Math.min(dist, sp * dt);
      h.x += (dx / dist) * step;
      h.z += (dz / dist) * step;
      if (dist > 4) h.yaw += wrapAngle(Math.atan2(dx, dz) - h.yaw) * Math.min(1, dt * 2);
      else h.yaw += wrapAngle(Math.atan2(q.x - h.x, q.z - h.z) - h.yaw) * Math.min(1, dt);
    }
    h.y += (ty - h.y) * Math.min(1, dt * 0.8);
    const k = Math.min(1, dt * 2.2);
    h.lx += (q.x - h.lx) * k;
    h.lz += (q.z - h.lz) * k;
  }
}
