import type { RoadNetwork, XZ } from "../../network/types";
import { emergency } from "./london";
import { mulberry32, pickCumulative, wrapAngle } from "./rng";

export const KIND_CAR = 0;
export const KIND_VAN = 1;
export const KIND_CAB = 2;
const KIND_LENGTH = [4.3, 5.3, 4.6];
const KIND_HALF_WIDTH = [0.9, 1.0, 0.9];
/** Per-kind odds that a vehicle stops mid-street (delivery, breakdown, pick-up) on entering a lane. */
const HOLD_CHANCE = [0.002, 0.015, 0.006];

const IDM_ACCEL = 2.6;
const IDM_COMFORT = 2.6;
const IDM_HEADWAY_S = 1.1;
const IDM_MIN_GAP = 2;
const MAX_DECEL = 9;
const BRAKE = 2.8;
const HARD_GAP_M = 0.3;
/** The move guard shrinks footprints by this; the audit shrinks more, so a guarded move never shows up as an overlap. */
const MOVE_SHRINK_M = 0.05;
const BORN_CAP = 64;
/** Samples along a junction movement used for swept-footprint tests, and the clearance added around each body. */
const PATH_SAMPLES = 8;
const SWEPT_MARGIN_M = 0.35;
const ARC_M = 7;
/** How far the turn arc's control point swings toward the lane-line crossing (1) rather than the node (0); less keeps wide turns on the carriageway. */
const ARC_SWING = 0.4;
const ARC_MIN_TURN = 0.17;
const LOOKAHEAD_M = 45;
const MIN_SPAWN_LANE_M = 12;
const SPAWN_CLEARANCE_M = 12;
const JUNCTION_SPEED = 8.5;
const STOP_LINE_M = 4.5;
const APPROACH_M = 40;
const CLAIM_M = 10;
/** Reserve the box this many seconds of travel before the stop line, so two cars can't both be committed to it. */
const CLAIM_LEAD_S = 2;
const FIFO_M = 20;
const BOX_TIMEOUT_S = 20;
const RELAX_CONFLICT_S = 12;
const RELAX_ROOM_S = 45;
const RECYCLE_WAIT_S = 30;
const STUCK_S = 45;
const NODE_CAP = 12;
/** Junction nodes joined by an edge this short share one junction box (split carriageways, offset crossings). */
const CLUSTER_EDGE_M = 18;
const MAJOR_SPEED = 12;
const MINOR_SPEED = 8.9;
const YAW_RATE = 9;
const LANE_KEY = 1e5;
const PARKED_KEY = -1e12;
const COUNT_RATE = 0.25;
const COUNT_MIN_RATE = 1.5;
const RESPONDER_PACE = 2.1;
const FOCUS_SIGMA_M = 260;
const RECYCLE_FOCUS_M = 380;
const RECYCLE_CHANCE = 0.35;
/** A car may only be removed or re-seeded when it is outside the camera frustum (plus this padding) and at least SAFE_DIST_M away. */
const SEEN_PAD_M = 14;
const SAFE_DIST_M = 60;
/** Beyond this distance a car is a sub-pixel dot and counts as unseen. With no frustum supplied, nothing past FALLBACK_SIGHT_M is seen. */
const VISIBLE_FAR_M = 3000;
const FALLBACK_SIGHT_M = 150;
const VIEW_SLACK = 0.04;
/** Waiting this long at the head of a queue, try another exit; no more than REROUTE_MAX times. */
const REROUTE_S = 10;
const REROUTE_MAX = 3;
/** Cars stay this far inside the carriageway edge: half the widest body plus a little. */
const KERB_CLEARANCE_M = 1.1;
const ENTRY_RUN_M = 4;
const SIREN_RANGE_M = 60;
const SIREN_MIN_SPEED = 3;
const SIREN_CORRIDOR_M = 9;
const YIELD_HOLD_S = 1.5;
const YIELD_DECEL = 2.5;
const NO_STOP_NEAR_END_M = 14;
const OVERTAKE_RATE = 0.6;
const OVERTAKE_START_GAP_M = 22;
const MAX_SIRENS = 40;
const HASH_SIZE = 4096;
const HASH_CELL_M = 20;

export const ROLE_NONE = 0;
export const ROLE_QUARRY = 1;
export const ROLE_POLICE = 2;
export const ROLE_AMBULANCE = 3;

export class Vehicle {
  idx = 0;
  slot = 0;
  len: number;
  hw: number;
  pace: number;
  lane = 0;
  s = 0;
  v = 0;
  next = -1;
  next2 = -1;
  /** Lane just left (-1 when freshly placed), so the turn arc through the junction can be drawn from both sides. */
  prev = -1;
  turnIn = 0;
  turnOut = 0;
  /** Junction node this vehicle has claimed (-1 = none), with the lanes it will use through it. */
  box = -1;
  boxIn = -1;
  boxOut = -1;
  boxAt = 0;
  /** Sim time it came within FIFO range of its junction (0 = not yet). */
  arrive = 0;
  gridWait = 0;
  stuck = 0;
  /** Mid-lane stop: distance along the lane to stop at, and seconds left standing there. */
  holdAt = 0;
  hold = 0;
  /** 0 = in lane, 1 = fully in the oncoming lane; `passing` is the vehicle being overtaken. */
  ovt = 0;
  passing = -1;
  yieldT = 0;
  pull = 0;
  braking = false;
  x = 0;
  z = 0;
  yaw = 0;
  fresh = true;
  /** Surplus car heading for the nearest exit; it is only removed once nobody can see it (or at a dead end out of frame). */
  retiring = false;
  /** Inside the camera frustum (padded) as of this frame's update. */
  seen = false;
  /** Times this car has swapped its next exit while queued, and the `gridWait` reading at the last swap. */
  reroutes = 0;
  rerouteAt = 0;
  /** True once a retiring car has replanned its next exit toward the nearest sink. */
  replanned = false;
  /** Parked vehicles are off the road (lane -1) and drawn at zero scale until the field asks for them. */
  parked = true;
  role = ROLE_NONE;

  constructor(
    readonly kind: number,
    pace: number,
  ) {
    this.len = KIND_LENGTH[kind];
    this.hw = KIND_HALF_WIDTH[kind];
    this.pace = pace;
  }
}

/** Separating-axis test on two oriented rectangles (yaw measured from +z toward +x; `h` half width, `l` half length). */
function rectsOverlap(
  ax: number, az: number, ayaw: number, ah: number, al: number,
  bx: number, bz: number, byaw: number, bh: number, bl: number,
): boolean {
  const asn = Math.sin(ayaw);
  const acs = Math.cos(ayaw);
  const bsn = Math.sin(byaw);
  const bcs = Math.cos(byaw);
  const dx = bx - ax;
  const dz = bz - az;
  for (let t = 0; t < 4; t++) {
    const ux = t === 0 ? asn : t === 1 ? acs : t === 2 ? bsn : bcs;
    const uz = t === 0 ? acs : t === 1 ? -asn : t === 2 ? bcs : -bsn;
    const ra = al * Math.abs(asn * ux + acs * uz) + ah * Math.abs(acs * ux - asn * uz);
    const rb = bl * Math.abs(bsn * ux + bcs * uz) + bh * Math.abs(bcs * ux - bsn * uz);
    if (Math.abs(dx * ux + dz * uz) > ra + rb) return false;
  }
  return true;
}

function hashCell(cx: number, cz: number): number {
  return (Math.imul(cx, 73856093) ^ Math.imul(cz, 19349663)) & (HASH_SIZE - 1);
}

/**
 * Ambient traffic that lives on the road graph. A lane is one direction of one edge (`edge * 2` is
 * from→to, `edge * 2 + 1` is to→from); each vehicle holds (lane, s) where s is metres travelled along
 * the lane. Vehicles follow leaders with the Intelligent Driver Model (same lane, the next two lanes
 * on their route, and any body ahead found through a spatial hash), give way at junctions (priority
 * to major roads, first come first served, left-hand turns), only enter a junction when the exit
 * has room, overtake stopped vehicles when the oncoming lane is clear, and pull over for sirens.
 */
export class TrafficSim {
  readonly vehicles: Vehicle[] = [];
  private readonly rng: () => number;
  private readonly out: XZ = [0, 0];
  private readonly edgeId: Int32Array;
  private readonly laneLen: Float32Array;
  private readonly laneLat: Float32Array;
  private readonly lanePull: Float32Array;
  private readonly laneFree: Float32Array;
  private readonly laneYawStart: Float32Array;
  private readonly laneYawEnd: Float32Array;
  private readonly laneNode: Int32Array;
  private readonly laneDep: Int32Array;
  /** 1 when the lane ends at a real junction (3+ edges), where vehicles give way. */
  private readonly laneGive: Uint8Array;
  /** Junction box id per node: nodes linked by short edges map to one representative. */
  private readonly nodeGroup: Int32Array;
  private readonly laneMajor: Uint8Array;
  private readonly laneTwoWay: Uint8Array;
  private readonly laneHead: Int32Array;
  private readonly laneExits: Int32Array[];
  private readonly laneExitCum: Float64Array[];
  private readonly laneExitBase: Float64Array[];
  private readonly laneBase: Float32Array;
  private readonly spawnable: Uint8Array;
  /** 1 on lanes that end at a map edge or dead end; `exitDist` is the metres to the nearest one along the cheapest route. */
  private readonly laneSink: Uint8Array;
  private readonly exitDist: Float32Array;
  /** Spawnable lanes that start at a map-edge node: where new cars drive in from. */
  private entryLanes: Int32Array = new Int32Array(0);
  private planes: Float32Array | null = null;
  private camX = 0;
  private camZ = 0;
  private readonly nodeStamp: Int32Array;
  private readonly nodeOccN: Uint8Array;
  private readonly nodeOcc: Int16Array;
  private readonly nodeApprN: Uint8Array;
  private readonly nodeAppr: Int16Array;
  private readonly hashStart = new Int32Array(HASH_SIZE + 1);
  private readonly hashCursor = new Int32Array(HASH_SIZE);
  private readonly sirenX = new Float64Array(MAX_SIRENS);
  private readonly sirenZ = new Float64Array(MAX_SIRENS);
  private readonly sirenH = new Float64Array(MAX_SIRENS);
  private readonly sirenV = new Float64Array(MAX_SIRENS);
  /** Edge midpoints as x, z pairs (local metres), for snapping onto the field and spawn-distance checks. */
  readonly edgeMid: Float32Array;
  private edgeWeight: Float32Array;
  private edgeSpeed: Float32Array;
  private focus: XZ | null;
  private spawnLanes: Int32Array = new Int32Array(0);
  private spawnCum: Float64Array = new Float64Array(0);
  private order: Int32Array;
  private key: Float64Array;
  private vcell: Int32Array;
  private hashItems: Int32Array;
  private readonly enableOrder: Int32Array;
  /** Vehicles on the road that count toward the target (retiring ones still driving off are not included). */
  active = 0;
  private target = 0;
  private countAcc = 0;
  private clock = 0;
  private tick = 0;
  private v0cur = 1;
  private inter = 0;
  private hardGap = 0;
  /** Counters for the headless check and `?stats=1`: stuck vehicles recycled, overtakes begun, siren yields begun. */
  deadlocks = 0;
  overtakes = 0;
  yields = 0;
  /** Moves refused by the overlap guard (the car held instead). */
  holds = 0;
  /** Cars removed or teleported while inside the frustum. Always 0: removals wait for the car to be out of sight. */
  pops = 0;
  /** Spawns or retirements postponed because every candidate spot was in view. */
  deferred = 0;
  private readonly paths = new Map<number, Float32Array>();
  private readonly crossCache = new Map<number, boolean>();
  private readonly born = new Int16Array(BORN_CAP);
  private bornN = 0;

  constructor(
    private readonly net: RoadNetwork,
    counts: number[],
    seed: number,
    focus: XZ | null = null,
    /** Edge ids cars must not spawn on, e.g. edges hidden under a landmark's own deck. They can still drive through. */
    noSpawn: ArrayLike<boolean> | null = null,
  ) {
    this.rng = mulberry32(seed);
    this.focus = focus;
    const edges = net.edges;
    const lanes = edges.length * 2;
    this.edgeId = new Int32Array(lanes);
    this.laneLen = new Float32Array(lanes);
    this.laneLat = new Float32Array(lanes);
    this.lanePull = new Float32Array(lanes);
    this.laneFree = new Float32Array(lanes);
    this.laneYawStart = new Float32Array(lanes);
    this.laneYawEnd = new Float32Array(lanes);
    this.laneNode = new Int32Array(lanes);
    this.laneDep = new Int32Array(lanes);
    this.laneGive = new Uint8Array(lanes);
    this.nodeGroup = new Int32Array(net.nodes.length).map((_, i) => i);
    this.laneMajor = new Uint8Array(lanes);
    this.laneTwoWay = new Uint8Array(lanes);
    this.laneHead = new Int32Array(lanes).fill(-1);
    this.laneExits = new Array<Int32Array>(lanes);
    this.laneExitCum = new Array<Float64Array>(lanes);
    this.laneExitBase = new Array<Float64Array>(lanes);
    this.laneBase = new Float32Array(lanes);
    this.spawnable = new Uint8Array(lanes);
    this.laneSink = new Uint8Array(lanes);
    this.exitDist = new Float32Array(lanes).fill(Infinity);
    this.edgeMid = new Float32Array(edges.length * 2);
    this.edgeWeight = new Float32Array(edges.length).fill(1);
    this.edgeSpeed = new Float32Array(edges.length).fill(1);
    this.nodeStamp = new Int32Array(net.nodes.length);
    this.nodeOccN = new Uint8Array(net.nodes.length);
    this.nodeOcc = new Int16Array(net.nodes.length * NODE_CAP);
    this.nodeApprN = new Uint8Array(net.nodes.length);
    this.nodeAppr = new Int16Array(net.nodes.length * NODE_CAP);
    const valid = new Uint8Array(lanes);
    const nodeById = new Map(net.nodes.map((n, i) => [n.id, i] as const));
    const departing = new Map<number, number[]>();
    const out = this.out;

    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const fwd = i * 2;
      const rev = fwd + 1;
      this.edgeId[fwd] = this.edgeId[rev] = e.id;
      this.laneLen[fwd] = this.laneLen[rev] = e.length;
      // The left-lane offset and any pull-over or overtake swing stay inside the carriageway, a body half-width from the kerb.
      const room = Math.max(0, e.width / 2 - KERB_CLEARANCE_M);
      this.laneLat[fwd] = Math.min(room, Math.min(2, Math.max(1.1, e.width * 0.25)));
      this.laneLat[rev] = -this.laneLat[fwd];
      this.lanePull[fwd] = this.lanePull[rev] = Math.max(0, Math.min(1.4, room - this.laneLat[fwd]));
      this.laneFree[fwd] = this.laneFree[rev] = e.kind === "major" ? MAJOR_SPEED : MINOR_SPEED;
      this.laneMajor[fwd] = this.laneMajor[rev] = e.kind === "major" ? 1 : 0;
      this.laneBase[fwd] = this.laneBase[rev] = this.laneFree[fwd];
      net.sample(e.id, e.length * 0.5, 0, out);
      this.edgeMid[i * 2] = out[0];
      this.edgeMid[i * 2 + 1] = out[1];
      const eps = Math.min(0.05, e.length * 0.4);
      const h0 = net.sample(e.id, eps, 0, out);
      const h1 = net.sample(e.id, Math.max(e.length - eps, eps), 0, out);
      this.laneYawStart[fwd] = h0;
      this.laneYawEnd[fwd] = h1;
      this.laneYawStart[rev] = wrapAngle(h1 + Math.PI);
      this.laneYawEnd[rev] = wrapAngle(h0 + Math.PI);
      const from = nodeById.get(e.from);
      const to = nodeById.get(e.to);
      if (from === undefined || to === undefined || e.length < 1) continue;
      valid[fwd] = 1;
      valid[rev] = e.oneway ? 0 : 1;
      this.laneNode[fwd] = to;
      this.laneNode[rev] = from;
      this.laneDep[fwd] = from;
      this.laneDep[rev] = to;
      this.laneGive[fwd] = net.nodes[to].junction ? 1 : 0;
      this.laneGive[rev] = net.nodes[from].junction ? 1 : 0;
      if ((net.nodes[from].junction || net.nodes[to].junction) && e.length < CLUSTER_EDGE_M) {
        const g = this.nodeGroup;
        const a = g[from] < g[to] ? g[from] : g[to];
        const b = g[from] < g[to] ? g[to] : g[from];
        g[from] = a;
        g[to] = a;
        for (let k = 0; k < g.length; k++) if (g[k] === b) g[k] = a;
      }
      for (const [lane, node] of [
        [fwd, from],
        [rev, to],
      ] as const) {
        if (!valid[lane]) continue;
        const list = departing.get(node);
        if (list) list.push(lane);
        else departing.set(node, [lane]);
      }
    }
    for (let L = 0; L < lanes; L++) this.laneTwoWay[L] = valid[L] && valid[L ^ 1] ? 1 : 0;
    // A lane gives way when its end node is a junction, or sits in a box with one (a short stub in front of a junction).
    const boxHasJunction = new Uint8Array(net.nodes.length);
    for (let i = 0; i < net.nodes.length; i++) if (net.nodes[i].junction) boxHasJunction[this.nodeGroup[i]] = 1;
    for (let L = 0; L < lanes; L++) if (valid[L] && boxHasJunction[this.nodeGroup[this.laneNode[L]]]) this.laneGive[L] = 1;
    // A plain bend where more lanes arrive than leave (a one-way spur merging into a street) is a merge, so it gives way like a junction.
    const arrivals = new Uint8Array(net.nodes.length);
    for (let L = 0; L < lanes; L++) if (valid[L]) arrivals[this.laneNode[L]]++;
    for (let L = 0; L < lanes; L++) {
      if (valid[L] && arrivals[this.laneNode[L]] > (departing.get(this.laneNode[L])?.length ?? 0)) this.laneGive[L] = 1;
    }

    const empty = new Int32Array(0);
    for (let L = 0; L < lanes; L++) {
      this.laneExits[L] = empty;
      this.laneExitCum[L] = new Float64Array(0);
      this.laneExitBase[L] = new Float64Array(0);
      if (!valid[L]) continue;
      const cands = (departing.get(this.laneNode[L]) ?? []).filter((c) => c !== (L ^ 1));
      if (!cands.length && valid[L ^ 1]) cands.push(L ^ 1);
      const base = new Float64Array(cands.length);
      for (let k = 0; k < cands.length; k++) {
        const c = cands[k];
        const turn = Math.abs(wrapAngle(this.laneYawStart[c] - this.laneYawEnd[L]));
        const major = edges[c >> 1].kind === "major" ? 1.5 : 1;
        base[k] = (Math.exp(-((turn / 0.9) ** 2)) + 0.04) * major;
      }
      this.laneExits[L] = Int32Array.from(cands);
      this.laneExitBase[L] = base;
      this.laneExitCum[L] = new Float64Array(cands.length);
    }
    // Prefer exits that lead somewhere: a one-way stub with no way on is only taken when nothing else is on offer.
    for (let L = 0; L < lanes; L++) {
      const exits = this.laneExits[L];
      const keep: number[] = [];
      for (let k = 0; k < exits.length; k++) if (this.laneExits[exits[k]].length) keep.push(k);
      if (!keep.length || keep.length === exits.length) continue;
      this.laneExits[L] = Int32Array.from(keep.map((k) => exits[k]));
      this.laneExitBase[L] = Float64Array.from(keep.map((k) => this.laneExitBase[L][k]));
      this.laneExitCum[L] = new Float64Array(keep.length);
    }
    this.buildSinks(valid);
    this.buildSpawnTable(valid, nodeById, noSpawn);
    this.setField(this.edgeWeight, this.edgeSpeed, 0);

    if (this.spawnLanes.length) {
      for (let kind = 0; kind < counts.length; kind++) {
        for (let i = 0; i < counts[kind]; i++) {
          const v = new Vehicle(kind, 0.82 + this.rng() * 0.33);
          v.idx = this.vehicles.length;
          v.slot = i;
          v.lane = -1;
          this.vehicles.push(v);
        }
      }
    }
    const n = this.vehicles.length;
    this.order = new Int32Array(n);
    for (let i = 0; i < n; i++) this.order[i] = i;
    this.key = new Float64Array(n);
    this.vcell = new Int32Array(n);
    this.hashItems = new Int32Array(n);
    this.enableOrder = Int32Array.from(
      this.vehicles.map((v, i) => i).sort((a, b) => {
        const va = this.vehicles[a];
        const vb = this.vehicles[b];
        return (va.slot + 0.5) / counts[va.kind] - (vb.slot + 0.5) / counts[vb.kind];
      }),
    );
  }

  /**
   * Vehicles per edge (any non-negative scale; 0 = no spawning and routing avoids it), a speed factor
   * per edge (1 = free flow, 0 = closed: nothing enters, anything still on it crawls out) and the
   * total vehicle count to ease toward. Called whenever the live field or TfL disruptions refresh.
   */
  setField(edgeWeight: Float32Array, edgeSpeed: Float32Array, target: number) {
    this.edgeWeight = edgeWeight;
    this.edgeSpeed = edgeSpeed;
    this.target = Math.min(this.vehicles.length, Math.max(0, Math.round(target)));
    for (let L = 0; L < this.laneFree.length; L++) this.laneFree[L] = this.laneBase[L] * Math.max(0.3, edgeSpeed[L >> 1]);
    this.rebuildSpawnTable();
    for (let L = 0; L < this.laneExits.length; L++) {
      const exits = this.laneExits[L];
      const base = this.laneExitBase[L];
      const out = this.laneExitCum[L];
      let acc = 0;
      for (let k = 0; k < exits.length; k++) {
        const e = exits[k] >> 1;
        acc += base[k] * (edgeSpeed[e] <= 0 ? 0.02 : 0.1 + edgeWeight[e]);
        out[k] = acc;
      }
    }
  }

  /** Biases where vehicles (re)spawn towards a point of interest (local metres), e.g. the streamer. */
  setFocus(focus: XZ | null) {
    this.focus = focus;
    this.rebuildSpawnTable();
  }

  /** Spawn odds per lane = field volume × focus bias; closed or empty streets never spawn. */
  private rebuildSpawnTable() {
    const { edgeWeight, edgeSpeed, focus } = this;
    const lanes: number[] = [];
    const cum: number[] = [];
    let total = 0;
    for (let L = 0; L < this.spawnable.length; L++) {
      const e = L >> 1;
      if (!this.spawnable[L] || edgeWeight[e] <= 0 || edgeSpeed[e] <= 0) continue;
      let density = edgeWeight[e];
      if (focus) {
        const d2 = (this.edgeMid[e * 2] - focus[0]) ** 2 + (this.edgeMid[e * 2 + 1] - focus[1]) ** 2;
        density *= 0.15 + 2 * Math.exp(-d2 / (2 * FOCUS_SIGMA_M ** 2));
      }
      total += this.laneLen[L] * density;
      lanes.push(L);
      cum.push(total);
    }
    if (lanes.length) {
      this.spawnLanes = Int32Array.from(lanes);
      this.spawnCum = Float64Array.from(cum);
    }
  }

  /** Puts a few active vehicles under blue lights: a fleeing car with two pursuers, or one ambulance. */
  dispatch(kind: "chase" | "ambulance") {
    this.clearDispatch();
    const want = kind === "chase" ? [ROLE_QUARRY, ROLE_POLICE, ROLE_POLICE] : [ROLE_AMBULANCE];
    const needKind = kind === "chase" ? KIND_CAR : KIND_VAN;
    let k = 0;
    for (let i = 0; i < this.enableOrder.length && k < want.length; i++) {
      const v = this.vehicles[this.enableOrder[i]];
      if (v.parked || v.retiring || v.kind !== needKind) continue;
      v.role = want[k++];
      v.pace = RESPONDER_PACE;
      v.holdAt = 0;
      v.hold = 0;
    }
  }

  clearDispatch() {
    for (const v of this.vehicles) {
      if (v.role === ROLE_NONE) continue;
      v.role = ROLE_NONE;
      v.pace = 0.82 + this.rng() * 0.33;
    }
  }

  private easeCount(dt: number) {
    const gap = this.target - this.active;
    if (gap === 0) return;
    this.countAcc += Math.max(COUNT_MIN_RATE, Math.abs(gap) * COUNT_RATE) * dt;
    const n = Math.min(Math.abs(gap), Math.floor(this.countAcc));
    if (n === 0) return;
    this.countAcc -= n;
    const order = this.enableOrder;
    if (gap > 0) {
      // First call back cars that were still driving off, then bring new ones in out of sight.
      let need = n;
      for (let i = 0; i < order.length && need > 0; i++) {
        const v = this.vehicles[order[i]];
        if (v.retiring && !v.parked) {
          v.retiring = false;
          v.replanned = false;
          this.active++;
          need--;
        }
      }
      for (let i = 0; i < order.length && need > 0; i++) {
        const v = this.vehicles[order[i]];
        if (!v.parked) continue;
        if (!this.place(v)) {
          this.deferred++;
          break;
        }
        v.parked = false;
        v.v = this.laneFree[v.lane] * v.pace * 0.6;
        this.active++;
        need--;
      }
      this.countAcc += need;
    } else {
      // Unseen cars go first (they just vanish); cars in view are told to drive on to the nearest exit.
      let need = n;
      for (let pass = 0; pass < 2 && need > 0; pass++) {
        for (let i = order.length - 1; i >= 0 && need > 0; i--) {
          const v = this.vehicles[order[i]];
          if (v.parked || v.retiring || v.role !== ROLE_NONE) continue;
          if (pass === 0 && v.seen) continue;
          v.retiring = true;
          v.replanned = false;
          this.active--;
          need--;
        }
      }
    }
  }

  /** Marks lanes ending at a map edge or dead end and finds each lane's distance to the nearest one (queue-based relaxation over the lane graph). */
  private buildSinks(valid: Uint8Array) {
    const lanes = valid.length;
    const preds: number[][] = Array.from({ length: lanes }, () => []);
    for (let L = 0; L < lanes; L++) for (const c of this.laneExits[L]) preds[c].push(L);
    const queue: number[] = [];
    for (let L = 0; L < lanes; L++) {
      if (!valid[L]) continue;
      if (!this.laneExits[L].length || this.net.nodes[this.laneNode[L]].edges.length === 1) {
        this.laneSink[L] = 1;
        this.exitDist[L] = this.laneLen[L];
        queue.push(L);
      }
    }
    for (let q = 0; q < queue.length; q++) {
      const c = queue[q];
      for (const L of preds[c]) {
        const d = this.laneLen[L] + this.exitDist[c];
        if (d < this.exitDist[L]) {
          this.exitDist[L] = d;
          queue.push(L);
        }
      }
    }
  }

  /** Only lanes in the largest connected piece of the network are used for spawning, so cars never get stuck in tiny islands. */
  private buildSpawnTable(valid: Uint8Array, nodeById: Map<number, number>, noSpawn: ArrayLike<boolean> | null) {
    const edges = this.net.edges;
    const parent = new Int32Array(this.net.nodes.length);
    for (let i = 0; i < parent.length; i++) parent[i] = i;
    const find = (a: number) => {
      while (parent[a] !== a) a = parent[a] = parent[parent[a]];
      return a;
    };
    for (const e of edges) {
      const a = nodeById.get(e.from);
      const b = nodeById.get(e.to);
      if (a !== undefined && b !== undefined) parent[find(a)] = find(b);
    }
    const compLen = new Float64Array(parent.length);
    for (const e of edges) {
      const a = nodeById.get(e.from);
      if (a !== undefined) compLen[find(a)] += e.length;
    }
    let best = 0;
    for (let i = 1; i < compLen.length; i++) if (compLen[i] > compLen[best]) best = i;
    for (let L = 0; L < valid.length; L++) {
      if (!valid[L] || this.laneLen[L] < MIN_SPAWN_LANE_M || !this.laneExits[L].length || noSpawn?.[edges[L >> 1].id]) continue;
      const a = nodeById.get(edges[L >> 1].from);
      if (a === undefined || find(a) !== best) continue;
      this.spawnable[L] = 1;
    }
    const entries: number[] = [];
    for (let L = 0; L < valid.length; L++) {
      if (this.spawnable[L] && this.net.nodes[this.laneDep[L]].edges.length === 1) entries.push(L);
    }
    this.entryLanes = Int32Array.from(entries);
  }

  /** Next lane for a route: weighted by the field, or the way to the nearest exit for a retiring car; `avoid` is skipped when anything else is open. */
  private pickNext(lane: number, retiring = false, avoid = -1): number {
    const exits = this.laneExits[lane];
    if (!exits.length) return -1;
    if (exits.length === 1) return exits[0];
    if (retiring) {
      let best = exits[0];
      for (const c of exits) if (c !== avoid && this.exitDist[c] < this.exitDist[best]) best = c;
      return best;
    }
    if (avoid >= 0) {
      const rest = exits.filter((c) => c !== avoid);
      if (rest.length) return rest[(this.rng() * rest.length) | 0];
    }
    return exits[pickCumulative(this.laneExitCum[lane], this.rng())];
  }

  /** Moves a vehicle onto `lane` at `s`; `keepRoute` continues along the route it already planned. */
  private enter(v: Vehicle, lane: number, s: number, keepRoute: boolean) {
    v.turnIn = v.turnOut;
    v.prev = keepRoute ? v.lane : -1;
    v.lane = lane;
    v.s = s;
    v.next = keepRoute ? v.next2 : this.pickNext(lane, v.retiring);
    v.next2 = v.next >= 0 ? this.pickNext(v.next, v.retiring) : -1;
    v.turnOut =
      v.next < 0
        ? 0
        : Math.min(1, Math.abs(wrapAngle(this.laneYawStart[v.next] - this.laneYawEnd[lane])) / 0.7);
    v.arrive = 0;
    v.gridWait = 0;
    v.passing = -1;
    const len = this.laneLen[lane];
    const stops = v.role === ROLE_NONE && this.laneTwoWay[lane] === 1 && len > 45 && this.rng() < HOLD_CHANCE[v.kind];
    v.holdAt = stops ? len * (0.35 + 0.3 * this.rng()) : 0;
    v.hold = 0;
  }

  /** Whether a point on the road (local metres) is inside the camera frustum grown by `pad` metres. */
  private sees(x: number, z: number, pad: number): boolean {
    const dx = x - this.camX;
    const dz = z - this.camZ;
    const d2 = dx * dx + dz * dz;
    const p = this.planes;
    if (!p) return d2 < FALLBACK_SIGHT_M * FALLBACK_SIGHT_M;
    if (d2 > VISIBLE_FAR_M * VISIBLE_FAR_M) return false;
    // Padding grows with distance (about 2.3 degrees), so a camera that swings a little after this frame's planes were read cannot catch a car popping.
    const slack = pad > 0 ? pad + Math.sqrt(d2) * VIEW_SLACK : 0;
    for (let k = 0; k < 24; k += 4) {
      if (p[k] * x + p[k + 1] * 0.5 + p[k + 2] * z + p[k + 3] < -slack) return false;
    }
    return true;
  }

  /** A car may be removed or re-seeded only when nobody can see it and it is well away from the camera. */
  private hidden(v: Vehicle): boolean {
    const dx = v.x - this.camX;
    const dz = v.z - this.camZ;
    return dx * dx + dz * dz > SAFE_DIST_M * SAFE_DIST_M && !this.sees(v.x, v.z, SEEN_PAD_M);
  }

  /** Takes a car off the road. Anything that was in view counts as a pop. */
  private park(v: Vehicle) {
    if (!v.fresh && this.sees(v.x, v.z, 0)) this.pops++;
    v.parked = true;
    v.retiring = false;
    v.lane = -1;
    v.box = -1;
    v.role = ROLE_NONE;
    v.seen = false;
  }

  /** Puts the car on a spawnable lane out of the camera's sight (or at a map-edge entry); false when no such spot is free right now. */
  private place(v: Vehicle): boolean {
    const rng = this.rng;
    if (!this.spawnLanes.length) return false;
    const out = this.out;
    let lane = -1;
    let s = 0;
    for (let attempt = 0; attempt < 48 && lane < 0; attempt++) {
      const entry = attempt >= 24 && this.entryLanes.length > 0;
      const L = entry ? this.entryLanes[(rng() * this.entryLanes.length) | 0] : this.spawnLanes[pickCumulative(this.spawnCum, rng())];
      const sTry = entry ? rng() * ENTRY_RUN_M : rng() * this.laneLen[L];
      let clear = true;
      for (const o of this.vehicles) {
        if (o !== v && !o.parked && o.lane === L && Math.abs(o.s - sTry) < SPAWN_CLEARANCE_M) {
          clear = false;
          break;
        }
      }
      if (!clear) continue;
      const forward = (L & 1) === 0;
      this.net.sample(this.edgeId[L], forward ? sTry : this.laneLen[L] - sTry, this.laneLat[L], out);
      const dx = out[0] - this.camX;
      const dz = out[1] - this.camZ;
      if (dx * dx + dz * dz < SAFE_DIST_M * SAFE_DIST_M || this.sees(out[0], out[1], SEEN_PAD_M)) continue;
      lane = L;
      s = sTry;
    }
    if (lane < 0) return false;
    v.turnOut = 0;
    v.retiring = false;
    v.replanned = false;
    v.reroutes = 0;
    v.rerouteAt = 0;
    this.enter(v, lane, s, false);
    if (v.holdAt - v.s < 15) v.holdAt = 0;
    v.turnIn = 0;
    v.v = 0;
    v.box = -1;
    v.ovt = 0;
    v.yieldT = 0;
    v.pull = 0;
    v.stuck = 0;
    v.gridWait = 0;
    v.fresh = true;
    v.seen = false;
    const forward = (lane & 1) === 0;
    const h = this.net.sample(this.edgeId[lane], forward ? s : this.laneLen[lane] - s, this.laneLat[lane], out);
    v.x = out[0];
    v.z = out[1];
    v.yaw = forward ? h : h + Math.PI;
    return true;
  }

  /** Footprints of the movement `inLane` → `outLane` through its junction: PATH_SAMPLES (x, z, yaw) triples along the turn arc, cached. */
  private path(inLane: number, outLane: number): Float32Array {
    const key = inLane * this.laneLen.length + outLane;
    let p = this.paths.get(key);
    if (!p) {
      p = new Float32Array(PATH_SAMPLES * 3);
      for (let k = 0; k < PATH_SAMPLES; k++) {
        p[k * 3 + 2] = this.arcPose(inLane, outLane, k / (PATH_SAMPLES - 1), this.out);
        p[k * 3] = this.out[0];
        p[k * 3 + 1] = this.out[1];
      }
      this.paths.set(key, p);
    }
    return p;
  }

  /** How far `w` has got along its claimed movement, 0 (before the arc) to 1 (clear of it). */
  private progress(w: Vehicle): number {
    const a = w.boxIn;
    if (w.lane === a) return Math.max(0, 0.5 * (1 - (this.laneLen[a] - w.s) / Math.min(ARC_M, this.laneLen[a] * 0.45)));
    if (w.lane === w.boxOut) return Math.min(1, 0.5 + (0.5 * w.s) / Math.min(ARC_M, this.laneLen[w.lane] * 0.45));
    return 0;
  }

  /**
   * True when `v`'s route through its junction box and `w`'s (inB → outB, then outB2 if that lane is still
   * inside the box; from sample `from` on) sweep overlapping footprints or merge into one lane. Routes are
   * followed through short internal lanes, so a box is reserved for the whole way across.
   */
  private crosses(v: Vehicle, w: Vehicle, inB: number, outB: number, outB2: number, from: number): boolean {
    if (v.lane === inB || inB < 0 || outB < 0) return false;
    const box = this.nodeGroup[this.laneNode[v.lane]];
    const va2 = v.next2 >= 0 && this.nodeGroup[this.laneNode[v.next]] === box ? v.next2 : -1;
    const wb2 = outB2 >= 0 && this.nodeGroup[this.laneNode[outB]] === box ? outB2 : -1;
    if (this.hopsCross(v, v.lane, v.next, w, inB, outB, from)) return true;
    if (wb2 >= 0 && this.hopsCross(v, v.lane, v.next, w, outB, wb2, 0)) return true;
    if (va2 < 0) return false;
    return this.hopsCross(v, v.next, va2, w, inB, outB, from) || (wb2 >= 0 && this.hopsCross(v, v.next, va2, w, outB, wb2, 0));
  }

  private hopsCross(v: Vehicle, a: number, b: number, w: Vehicle, c: number, d: number, from: number): boolean {
    if (a === c) return false;
    if (b === d) return true;
    const key = (((a * this.laneLen.length + b) * this.laneLen.length + c) * this.laneLen.length + d) * 72 + from * 9 + v.kind * 3 + w.kind;
    const cached = this.crossCache.get(key);
    if (cached !== undefined) return cached;
    const pa = this.path(a, b);
    const pb = this.path(c, d);
    const ah = v.hw + SWEPT_MARGIN_M;
    const al = v.len * 0.5 + SWEPT_MARGIN_M;
    const bh = w.hw + SWEPT_MARGIN_M;
    const bl = w.len * 0.5 + SWEPT_MARGIN_M;
    let hit = false;
    for (let i = 0; i < PATH_SAMPLES && !hit; i++) {
      for (let j = from; j < PATH_SAMPLES; j++) {
        if (rectsOverlap(pa[i * 3], pa[i * 3 + 1], pa[i * 3 + 2], ah, al, pb[j * 3], pb[j * 3 + 1], pb[j * 3 + 2], bh, bl)) {
          hit = true;
          break;
        }
      }
    }
    this.crossCache.set(key, hit);
    return hit;
  }

  /** Is any other body (parked at a stop line, queued in the exit, mid-junction) standing on `v`'s whole swept arc? */
  private arcBlocked(v: Vehicle): boolean {
    const vs = this.vehicles;
    const pa = this.path(v.lane, v.next);
    const ah = v.hw + SWEPT_MARGIN_M;
    const al = v.len * 0.5 + SWEPT_MARGIN_M;
    const cx = Math.floor(v.x / HASH_CELL_M);
    const cz = Math.floor(v.z / HASH_CELL_M);
    for (let ox = -1; ox <= 1; ox++) {
      for (let oz = -1; oz <= 1; oz++) {
        const b = hashCell(cx + ox, cz + oz);
        for (let p = this.hashStart[b]; p < this.hashStart[b + 1]; p++) {
          const o = vs[this.hashItems[p]];
          if (o === v || o.lane === v.lane || o.fresh || o.parked) continue;
          const bh = o.hw - MOVE_SHRINK_M;
          const bl = o.len * 0.5 - MOVE_SHRINK_M;
          for (let i = 0; i < PATH_SAMPLES; i++) {
            if (rectsOverlap(pa[i * 3], pa[i * 3 + 1], pa[i * 3 + 2], ah, al, o.x, o.z, o.yaw, bh, bl)) return true;
          }
        }
      }
    }
    return false;
  }

  /** Right of way: sirens, then the major road, with straight and left movements ahead of right turns. */
  private priority(inLane: number, outLane: number, role: number): number {
    const base = role !== ROLE_NONE ? 3 : this.laneMajor[inLane] ? 2 : 1;
    return base + (wrapAngle(this.laneYawStart[outLane] - this.laneYawEnd[inLane]) > -0.5 ? 0.5 : 0);
  }

  /** May this head-of-queue vehicle enter its junction now? Needs room in the exit and no conflicting movement with the right of way. */
  private canGo(v: Vehicle): boolean {
    const vs = this.vehicles;
    const L = v.lane;
    const node = this.nodeGroup[this.laneNode[L]];
    const out = v.next;
    if (v.gridWait < RELAX_ROOM_S) {
      // Room to clear the box: free road along the route, through a short exit lane into the one after it.
      let room = Infinity;
      let free = 0;
      let lane = out;
      for (let hop = 0; hop < 2 && lane >= 0; hop++) {
        const h = this.laneHead[lane];
        const o = h >= 0 ? vs[this.order[h]] : null;
        if (o && o.lane === lane) {
          room = free + o.s - o.len * 0.5;
          break;
        }
        free += this.laneLen[lane];
        lane = hop === 0 ? v.next2 : -1;
      }
      if (room < v.len + 3) return false;
    }
    const base = node * NODE_CAP;
    for (let j = 0; j < this.nodeOccN[node]; j++) {
      const w = vs[this.nodeOcc[base + j]];
      // Cars in the box that are queued behind us (on our lane or feeding it) can't be waited for.
      if (w === v || w.lane === L || w.next === L || w.boxOut === L) continue;
      if (this.crosses(v, w, w.boxIn, w.boxOut, w.lane === w.boxIn ? w.next2 : -1, Math.max(0, Math.floor(this.progress(w) * (PATH_SAMPLES - 1)) - 1))) return false;
    }
    if (this.arcBlocked(v)) return false;
    if (v.gridWait >= RELAX_CONFLICT_S) return true;
    const mine = this.priority(L, out, v.role);
    for (let j = 0; j < this.nodeApprN[node]; j++) {
      const w = vs[this.nodeAppr[base + j]];
      if (w === v || w.box >= 0 || w.lane === L || !this.crosses(v, w, w.lane, w.next, w.next2, 0)) continue;
      const theirs = this.priority(w.lane, w.next, w.role);
      const wr = this.laneLen[w.lane] - w.s;
      if (theirs > mine) {
        if (wr / Math.max(w.v, 1.5) < 4) return false;
      } else if (theirs === mine && w.arrive > 0 && wr < FIFO_M) {
        if (v.arrive === 0 || w.arrive < v.arrive || (w.arrive === v.arrive && w.idx < v.idx)) return false;
      }
    }
    return true;
  }

  private claim(v: Vehicle) {
    const node = this.nodeGroup[this.laneNode[v.lane]];
    v.box = node;
    v.boxIn = v.lane;
    v.boxOut = v.next;
    v.boxAt = this.clock;
    v.gridWait = 0;
    const n = this.nodeOccN[node];
    if (n < NODE_CAP) {
      this.nodeOcc[node * NODE_CAP + n] = v.idx;
      this.nodeOccN[node] = n + 1;
    }
  }

  /** IDM interaction with one obstacle `gap` metres ahead (bumper to bumper) moving at `lv` along our heading. */
  private follow(v: Vehicle, gap: number, lv: number, body: boolean) {
    const dv = v.v - lv;
    const sStar = IDM_MIN_GAP + Math.max(0, v.v * IDM_HEADWAY_S + (v.v * dv) / (2 * Math.sqrt(IDM_ACCEL * IDM_COMFORT)));
    const r = sStar / Math.max(gap, 0.05);
    const inter = IDM_ACCEL * r * r;
    if (inter > this.inter) this.inter = inter;
    if (body && gap < this.hardGap) this.hardGap = gap;
  }

  /** Can `v` pull out around the stopped `o` ahead? Needs a clear oncoming lane for the whole pass and room to return. */
  private overtakeOk(v: Vehicle, o: Vehicle, o2: Vehicle | null): boolean {
    const L = v.lane;
    const len = this.laneLen[L];
    if (emergency.active || !this.laneTwoWay[L] || v.s < 5) return false;
    if (o.hold <= 0 && !(v.role !== ROLE_NONE && o.v < 0.6 * this.laneFree[L])) return false;
    const gap = o.s - v.s - (o.len + v.len) * 0.5;
    if (gap > OVERTAKE_START_GAP_M) return false;
    const span = o.s - v.s + o.len + v.len + 8;
    if (len - v.s < span + 40) return false;
    if (o2 && o2.s - v.s < span + 12) return false;
    const vs = this.vehicles;
    const opp = L ^ 1;
    for (let h = this.laneHead[opp]; h >= 0 && h < vs.length; h++) {
      const w = vs[this.order[h]];
      if (w.parked || w.lane !== opp) break;
      const sMe = len - w.s;
      if (sMe < v.s - 10) break;
      if (sMe < v.s + span + w.v * 4 + 10) return false;
    }
    return true;
  }

  /**
   * Point on the turn from lane `a` into lane `b` at parameter u (0 = ARC_M before the node on `a`, 1 = ARC_M
   * after it on `b`): a quadratic Bézier between the two lane positions whose control point is where the
   * lane offsets cross, so left turns hug the kerb and right turns swing across. Writes [x, z], returns yaw.
   */
  private arcPose(a: number, b: number, u: number, out: XZ): number {
    const lenA = this.laneLen[a];
    const lenB = this.laneLen[b];
    const ra = Math.min(ARC_M, lenA * 0.45);
    const rb = Math.min(ARC_M, lenB * 0.45);
    const fa = (a & 1) === 0;
    const fb = (b & 1) === 0;
    this.net.sample(this.edgeId[a], fa ? lenA - ra : ra, this.laneLat[a], out);
    const p0x = out[0];
    const p0z = out[1];
    this.net.sample(this.edgeId[b], fb ? rb : lenB - rb, this.laneLat[b], out);
    const p1x = out[0];
    const p1z = out[1];
    const node = this.net.nodes[this.laneNode[a]];
    const ya = this.laneYawEnd[a];
    const yb = this.laneYawStart[b];
    const la = Math.abs(this.laneLat[a]);
    const lb = Math.abs(this.laneLat[b]);
    const nax = Math.cos(ya);
    const naz = -Math.sin(ya);
    const nbx = Math.cos(yb);
    const nbz = -Math.sin(yb);
    const k = 1 / Math.max(0.6, 1 + nax * nbx + naz * nbz);
    const swing = ARC_SWING * Math.min(1, 1 / k);
    const cx = node.x + (la * nax + lb * nbx) * k * swing;
    const cz = node.z + (la * naz + lb * nbz) * k * swing;
    const w0 = (1 - u) * (1 - u);
    const w1 = 2 * u * (1 - u);
    const w2 = u * u;
    out[0] = w0 * p0x + w1 * cx + w2 * p1x;
    out[1] = w0 * p0z + w1 * cz + w2 * p1z;
    return Math.atan2((1 - u) * (cx - p0x) + u * (p1x - cx), (1 - u) * (cz - p0z) + u * (p1z - cz));
  }

  /**
   * Steps every car along its route, wherever it is. `planes` is the camera frustum as six
   * (nx, ny, nz, constant) planes with inward normals (Three's `Frustum`); cars are only removed or
   * re-seeded outside it, and `Vehicle.seen` tells the renderer which ones to draw.
   */
  update(dtRaw: number, camX: number, camZ: number, planes: Float32Array | null = null) {
    const vs = this.vehicles;
    const n = vs.length;
    if (!n) return;
    const dt = Math.min(dtRaw, 0.05);
    this.clock += dt;
    this.tick++;
    this.camX = camX;
    this.camZ = camZ;
    this.planes = planes;
    this.easeCount(dt);
    const { order, key, laneHead, laneLen, laneFree, laneNode, laneGive, nodeStamp } = this;
    const tick = this.tick;
    for (let i = 0; i < n; i++) {
      const v = vs[i];
      if (v.parked) {
        key[i] = PARKED_KEY;
        continue;
      }
      key[i] = v.lane * LANE_KEY + v.s;
      laneHead[v.lane] = -1;
    }
    for (let i = 1; i < n; i++) {
      const o = order[i];
      const k = key[o];
      let j = i - 1;
      while (j >= 0 && key[order[j]] > k) {
        order[j + 1] = order[j];
        j--;
      }
      order[j + 1] = o;
    }
    for (let k = n - 1; k >= 0; k--) {
      const v = vs[order[k]];
      if (!v.parked) laneHead[v.lane] = k;
    }

    // Per-frame indexes: spatial hash of bodies, junction occupancy/approach lists and siren sources.
    const hashStart = this.hashStart;
    const hashCursor = this.hashCursor;
    hashStart.fill(0);
    let ns = 0;
    if (emergency.active) {
      for (const e of emergency.vehicles) {
        if (ns >= MAX_SIRENS) break;
        this.sirenX[ns] = e.x;
        this.sirenZ[ns] = e.z;
        this.sirenH[ns] = e.heading;
        this.sirenV[ns++] = e.speed;
      }
    }
    for (let i = 0; i < n; i++) {
      const v = vs[i];
      if (v.parked) continue;
      const L = v.lane;
      if (!v.fresh) {
        const h = hashCell(Math.floor(v.x / HASH_CELL_M), Math.floor(v.z / HASH_CELL_M));
        this.vcell[i] = h;
        hashStart[h + 1]++;
        if ((v.role === ROLE_POLICE || v.role === ROLE_AMBULANCE) && v.v > SIREN_MIN_SPEED && ns < MAX_SIRENS) {
          this.sirenX[ns] = v.x;
          this.sirenZ[ns] = v.z;
          this.sirenH[ns] = v.yaw;
          this.sirenV[ns++] = v.v;
        }
      } else this.vcell[i] = -1;
      const registerBox = v.box >= 0 ? v.box : -1;
      const approaching = laneGive[L] === 1 && v.next >= 0 && v.box < 0 && laneLen[L] - v.s < APPROACH_M;
      const node = registerBox >= 0 ? registerBox : approaching ? this.nodeGroup[laneNode[L]] : -1;
      if (node < 0) continue;
      if (nodeStamp[node] !== tick) {
        nodeStamp[node] = tick;
        this.nodeOccN[node] = 0;
        this.nodeApprN[node] = 0;
      }
      if (registerBox >= 0) {
        const c = this.nodeOccN[node];
        if (c < NODE_CAP) {
          this.nodeOcc[node * NODE_CAP + c] = i;
          this.nodeOccN[node] = c + 1;
        }
      } else {
        const c = this.nodeApprN[node];
        if (c < NODE_CAP) {
          this.nodeAppr[node * NODE_CAP + c] = i;
          this.nodeApprN[node] = c + 1;
        }
      }
    }
    for (let h = 0; h < HASH_SIZE; h++) {
      hashStart[h + 1] += hashStart[h];
      hashCursor[h] = hashStart[h];
    }
    for (let i = 0; i < n; i++) if (!vs[i].parked && this.vcell[i] >= 0) this.hashItems[hashCursor[this.vcell[i]]++] = i;
    this.bornN = 0;
    const yawBlend = 1 - Math.exp(-YAW_RATE * dt);
    const out = this.out;

    for (let k = 0; k < n; k++) {
      const v = vs[order[k]];
      if (v.parked) continue;
      v.seen = this.sees(v.x, v.z, SEEN_PAD_M * 0.5);
      if (v.retiring) {
        const L0 = v.lane;
        const atSink = this.laneSink[L0] === 1 && laneLen[L0] - v.s < 3 && !this.sees(v.x, v.z, SEEN_PAD_M * 0.5);
        if (atSink || this.hidden(v)) {
          this.park(v);
          continue;
        }
        if (!v.replanned && v.box < 0 && laneLen[L0] - v.s > 15) {
          v.replanned = true;
          v.next = this.pickNext(L0, true);
          v.next2 = v.next >= 0 ? this.pickNext(v.next, true) : -1;
          v.turnOut =
            v.next < 0 ? 0 : Math.min(1, Math.abs(wrapAngle(this.laneYawStart[v.next] - this.laneYawEnd[L0])) / 0.7);
        }
      }
      const L = v.lane;
      const len = laneLen[L];
      const remaining = len - v.s;
      const half = v.len * 0.5;
      const fresh = v.fresh;
      // Where the car stands now, so a move the overlap guard refuses can be undone.
      const lane0 = L;
      const s0 = v.s;
      const next0 = v.next;
      const next20 = v.next2;
      const prev0 = v.prev;
      const turnIn0 = v.turnIn;
      const turnOut0 = v.turnOut;
      const arrive0 = v.arrive;
      const gridWait0 = v.gridWait;
      const passing0 = v.passing;
      const holdAt0 = v.holdAt;
      const hold0 = v.hold;
      const ovt0 = v.ovt;
      const pull0 = v.pull;
      if (v.box >= 0) {
        const stillThere =
          this.nodeGroup[laneNode[L]] === v.box || (this.nodeGroup[this.laneDep[L]] === v.box && v.s < v.len + 2);
        if (!stillThere || this.clock - v.boxAt > BOX_TIMEOUT_S) v.box = -1;
      }
      if (v.arrive === 0 && laneGive[L] === 1 && remaining < FIFO_M) v.arrive = this.clock;

      let v0 = laneFree[L] * v.pace;
      if (v.ovt > 0.3) v0 *= 1.15;
      if (v.next >= 0) {
        let vEnd = Math.max(2.2, v0 * (1 - 0.55 * v.turnOut));
        if (laneGive[L]) vEnd = Math.min(vEnd, JUNCTION_SPEED);
        v0 = Math.min(v0, Math.sqrt(vEnd * vEnd + 2 * BRAKE * Math.max(0, remaining - half)));
      }
      v0 = Math.max(v0, 0.5);
      this.v0cur = v0;
      this.inter = 0;
      this.hardGap = 1e9;

      // Leaders in this lane (nearest two, skipping a vehicle we are already alongside) and along the route.
      let lead1: Vehicle | null = null;
      let lead2: Vehicle | null = null;
      let found = 0;
      for (let kk = k + 1; kk < n && found < 2; kk++) {
        const o = vs[order[kk]];
        if (o.parked || o.lane !== L) break;
        if (o.idx === v.passing && v.ovt > 0.8) continue;
        this.follow(v, o.s - v.s - (v.len + o.len) * 0.5, o.v, true);
        if (found === 0) lead1 = o;
        else lead2 = o;
        found++;
      }
      if (found < 2 && v.next >= 0 && remaining < LOOKAHEAD_M) {
        const h = laneHead[v.next];
        const o = h >= 0 ? vs[order[h]] : null;
        if (o && o !== v && o.lane === v.next) {
          this.follow(v, remaining + o.s - (v.len + o.len) * 0.5, o.v, true);
        } else if (v.next2 >= 0 && remaining + laneLen[v.next] < LOOKAHEAD_M) {
          const h2 = laneHead[v.next2];
          const o2 = h2 >= 0 ? vs[order[h2]] : null;
          if (o2 && o2 !== v && o2.lane === v.next2) {
            this.follow(v, remaining + laneLen[v.next] + o2.s - (v.len + o2.len) * 0.5, o2.v, true);
          }
        }
      }

      // Overtaking a stopped vehicle through the oncoming lane.
      if (v.passing >= 0) {
        const p = vs[v.passing];
        if (p.parked || p.lane !== L || v.s - half > p.s + p.len * 0.5 + 3) v.passing = -1;
      } else if (lead1 && !fresh && v.ovt < 0.05 && this.overtakeOk(v, lead1, lead2)) {
        v.passing = lead1.idx;
        this.overtakes++;
      }
      const want = v.passing >= 0 ? 1 : 0;
      v.ovt += Math.max(-OVERTAKE_RATE * dt, Math.min(OVERTAKE_RATE * dt, want - v.ovt));

      // Mid-street stops (deliveries, breakdowns, pick-ups).
      let frozen = false;
      if (v.hold > 0) {
        v.hold -= dt;
        if (v.hold <= 0) {
          v.hold = 0;
          v.holdAt = 0;
        } else frozen = true;
      } else if (v.holdAt > 0) {
        const g = v.holdAt - v.s;
        this.follow(v, g, 0, false);
        if (g < 4 && v.v < 0.3) v.hold = 10 + this.rng() * 18;
      }

      // Junction: the head of a queue decides; followers only respect the stop line while the box is closed to them.
      let waiting = false;
      if (!fresh && v.next >= 0 && laneGive[L] === 1 && v.box < 0 && remaining < APPROACH_M) {
        const g = remaining - half - STOP_LINE_M;
        if (lead1) {
          if (remaining < CLAIM_M && lead1.box >= 0) this.claim(v); // Platoon: ride the leader's grant, but still show up in the box.
          else if (!this.canGo(v)) this.follow(v, g, 0, false);
        } else if (this.canGo(v)) {
          if (remaining < CLAIM_M + v.v * CLAIM_LEAD_S) this.claim(v);
        } else if (g > (v.v * v.v) / (2 * MAX_DECEL * 0.7) || v.v < 1.5) {
          waiting = true;
          this.follow(v, g, 0, false);
        } else this.claim(v);
      }
      if (waiting && v.v < 0.3) {
        v.gridWait += dt;
        if (v.role === ROLE_NONE && v.box < 0 && v.gridWait - v.rerouteAt > REROUTE_S && v.reroutes < REROUTE_MAX) {
          // Head of a stuck queue: try a different exit rather than waiting for the others to clear.
          const alt = this.pickNext(L, v.retiring, v.next);
          v.reroutes++;
          v.rerouteAt = v.gridWait;
          if (alt >= 0 && alt !== v.next) {
            v.next = alt;
            v.next2 = this.pickNext(alt, v.retiring);
            v.turnOut = Math.min(1, Math.abs(wrapAngle(this.laneYawStart[alt] - this.laneYawEnd[L])) / 0.7);
          }
        }
        if (v.gridWait > RECYCLE_WAIT_S && v.role === ROLE_NONE && this.hidden(v)) {
          this.deadlocks++;
          this.place(v);
          continue;
        }
      } else if (!waiting) {
        v.gridWait = 0;
        v.rerouteAt = 0;
      }

      // Sirens approaching from behind: pull to the kerb and stop unless that would block a junction.
      let sirenNear = false;
      const fx = Math.sin(v.yaw);
      const fz = Math.cos(v.yaw);
      if (ns > 0 && v.role === ROLE_NONE && !fresh) {
        for (let j = 0; j < ns; j++) {
          if (this.sirenV[j] < SIREN_MIN_SPEED) continue;
          const dx = this.sirenX[j] - v.x;
          const dz = this.sirenZ[j] - v.z;
          const along = dx * fx + dz * fz;
          if (along > 3 || along < -SIREN_RANGE_M || Math.abs(dx * fz - dz * fx) > SIREN_CORRIDOR_M) continue;
          if (Math.abs(wrapAngle(this.sirenH[j] - v.yaw)) > 1.1) continue;
          sirenNear = true;
          break;
        }
      }
      if (sirenNear) {
        if (v.yieldT === 0) this.yields++;
        v.yieldT = YIELD_HOLD_S;
      } else v.yieldT = Math.max(0, v.yieldT - dt);
      let yielding = false;
      if (v.yieldT > 0 && remaining > NO_STOP_NEAR_END_M && v.box < 0 && !frozen) yielding = true;
      v.pull += ((v.yieldT > 0 || v.hold > 0 ? 1 : 0) - v.pull) * Math.min(1, 2.5 * dt);

      // Any body ahead in our path that the lane lists don't know about (oncoming overtakers, crossing cars).
      if (!fresh) {
        const reach = Math.min(22, half + 3 + v.v * 1.6);
        const cx = Math.floor(v.x / HASH_CELL_M);
        const cz = Math.floor(v.z / HASH_CELL_M);
        for (let ox = -1; ox <= 1; ox++) {
          for (let oz = -1; oz <= 1; oz++) {
            const b = hashCell(cx + ox, cz + oz);
            for (let p = hashStart[b]; p < hashStart[b + 1]; p++) {
              const o = vs[this.hashItems[p]];
              if (o === v || o.lane === L || o.fresh || o.parked) continue;
              const dx = o.x - v.x;
              const dz = o.z - v.z;
              const ahead = dx * fx + dz * fz;
              if (ahead <= 0 || ahead > reach + o.len) continue;
              const dyaw = o.yaw - v.yaw;
              const co = Math.cos(dyaw);
              const si = Math.abs(Math.sin(dyaw));
              const side = Math.abs(dx * fz - dz * fx);
              if (side > v.hw + o.hw * Math.abs(co) + o.len * 0.5 * si + 0.3) continue;
              const gap = ahead - half - (o.len * 0.5 * Math.abs(co) + o.hw * si);
              if (v.ovt > 0.6 && co < -0.7 && gap > 1) continue;
              // Two bodies that each see the other ahead (diverging lanes at a node): the lower id goes first.
              if (v.idx < o.idx && (o.v > 0.5 || gap > 2.5 || (v.stuck > 4 && gap > 1)) && -(dx * Math.sin(o.yaw) + dz * Math.cos(o.yaw)) > 0) continue;
              // A car already in the box has the right of way over one waiting at its stop line for it.
              if (v.box >= 0 && o.box < 0 && o.gridWait > 0 && co < 0.7) continue;
              this.follow(v, gap, o.v * co, true);
            }
          }
        }
      }

      let acc: number;
      if (frozen) {
        v.v = 0;
        acc = 0;
      } else {
        const r = v.v / this.v0cur;
        acc = IDM_ACCEL * (1 - r * r * r * r) - this.inter;
        if (yielding) acc = Math.min(acc, v.v > 0.2 ? -YIELD_DECEL : 0);
        acc = Math.max(-MAX_DECEL, Math.min(IDM_ACCEL, acc));
        v.v = Math.max(0, v.v + acc * dt);
        if (yielding && v.v < 0.2) v.v = 0;
        let ds = v.v * dt;
        if (this.hardGap - ds < HARD_GAP_M) {
          ds = Math.max(0, this.hardGap - HARD_GAP_M);
          v.v = Math.min(v.v, ds / dt);
        }
        v.s += ds;
      }
      v.braking = acc < -0.8 || frozen;

      v.stuck = v.v < 0.2 && !frozen && v.yieldT === 0 ? v.stuck + dt : 0;
      if (v.stuck > STUCK_S && v.role === ROLE_NONE && this.hidden(v)) {
        this.deadlocks++;
        this.place(v);
        continue;
      }

      for (let guard = 0; guard < 3 && v.s >= laneLen[v.lane]; guard++) {
        const carry = v.s - laneLen[v.lane];
        if (v.next < 0) {
          // End of a one-way dead end: wait here until no one can see the car, then give it a new place.
          v.s = laneLen[v.lane];
          v.v = 0;
          if (this.hidden(v)) this.place(v);
          break;
        }
        this.enter(v, v.next, carry, true);
        // Still inside a clustered junction: the movement to reserve is now this lane's onward turn.
        if (v.box >= 0 && this.nodeGroup[laneNode[v.lane]] === v.box) {
          v.boxIn = v.lane;
          v.boxOut = v.next;
        }
        const f = this.focus;
        if (
          f &&
          v.role === ROLE_NONE &&
          !v.retiring &&
          this.rng() < RECYCLE_CHANCE &&
          (v.x - f[0]) ** 2 + (v.z - f[1]) ** 2 > RECYCLE_FOCUS_M ** 2 &&
          this.hidden(v) &&
          this.place(v)
        ) {
          break;
        }
      }

      const lane = v.lane;
      const laneLength = laneLen[lane];
      const toEnd = laneLength - v.s;
      let yaw: number;
      if (v.next >= 0 && v.turnOut > ARC_MIN_TURN && toEnd < Math.min(ARC_M, laneLength * 0.45)) {
        yaw = this.arcPose(lane, v.next, 0.5 * (1 - toEnd / Math.min(ARC_M, laneLength * 0.45)), out);
      } else if (v.prev >= 0 && v.turnIn > ARC_MIN_TURN && v.s < Math.min(ARC_M, laneLength * 0.45)) {
        yaw = this.arcPose(v.prev, lane, 0.5 + 0.5 * (v.s / Math.min(ARC_M, laneLength * 0.45)), out);
      } else {
        let lateral = this.laneLat[lane] * (1 - 2 * v.ovt);
        lateral += Math.sign(this.laneLat[lane]) * v.pull * this.lanePull[lane];
        const forward = (lane & 1) === 0;
        const h = this.net.sample(this.edgeId[lane], forward ? v.s : laneLength - v.s, lateral, out);
        yaw = forward ? h : h + Math.PI;
      }
      const nyaw = v.fresh ? yaw : v.yaw + wrapAngle(yaw - v.yaw) * yawBlend;
      if (this.blocked(v, out[0], out[1], nyaw)) {
        if (v.fresh) {
          this.place(v);
          continue;
        }
        v.lane = lane0;
        v.s = s0;
        v.next = next0;
        v.next2 = next20;
        v.prev = prev0;
        v.turnIn = turnIn0;
        v.turnOut = turnOut0;
        v.arrive = arrive0;
        v.gridWait = gridWait0;
        v.passing = passing0;
        v.holdAt = holdAt0;
        v.hold = hold0;
        v.ovt = ovt0;
        v.pull = pull0;
        v.v = 0;
        v.braking = true;
        if (v.stuck === 0) v.stuck = dt;
        this.holds++;
        continue;
      }
      v.x = out[0];
      v.z = out[1];
      v.yaw = nyaw;
      if (v.fresh) {
        v.fresh = false;
        if (this.bornN < BORN_CAP) this.born[this.bornN++] = v.idx;
      }
    }
  }

  /** Overlap and gap statistics for the headless check. Pass a Set to accumulate distinct overlapping pairs across calls. */
  audit(seen?: Set<number>) {
    const vs = this.vehicles;
    let overlaps = 0;
    let newOverlaps = 0;
    let minGap = Infinity;
    let speed = 0;
    let moving = 0;
    let active = 0;
    for (let i = 0; i < vs.length; i++) {
      const a = vs[i];
      if (a.parked || a.fresh) continue;
      active++;
      speed += a.v;
      if (a.v > 0.5) moving++;
      for (let j = i + 1; j < vs.length; j++) {
        const b = vs[j];
        if (b.parked || b.fresh) continue;
        const dx = b.x - a.x;
        const dz = b.z - a.z;
        if (dx * dx + dz * dz > 36) continue;
        if (this.bodiesOverlap(a, b)) {
          overlaps++;
          const id = i * 65536 + j;
          if (seen && !seen.has(id)) {
            seen.add(id);
            newOverlaps++;
          }
        }
      }
    }
    const live = vs.filter((v) => !v.parked && !v.fresh && v.ovt < 0.3).sort((p, q) => p.lane - q.lane || p.s - q.s);
    for (let k = 0; k + 1 < live.length; k++) {
      if (live[k].lane !== live[k + 1].lane) continue;
      minGap = Math.min(minGap, live[k + 1].s - live[k].s - (live[k].len + live[k + 1].len) * 0.5);
    }
    return { active, overlaps, newOverlaps, minGap, meanSpeed: active ? speed / active : 0, moving };
  }

  /** Overlap test for the audit and the move guard: footprints shrunk a little so touching bumpers don't count. */
  bodiesOverlap(a: Vehicle, b: Vehicle, shrink = 0.15): boolean {
    return rectsOverlap(
      a.x, a.z, a.yaw, a.hw - shrink, a.len * 0.5 - shrink,
      b.x, b.z, b.yaw, b.hw - shrink, b.len * 0.5 - shrink,
    );
  }

  /**
   * Would `v` standing at (nx, nz) facing `nyaw` overlap another body? Bodies that already overlap `v` may
   * only move apart, so nothing is ever pushed deeper. A car that can't move holds instead of crashing.
   */
  private blocked(v: Vehicle, nx: number, nz: number, nyaw: number): boolean {
    const vs = this.vehicles;
    const cx = Math.floor(nx / HASH_CELL_M);
    const cz = Math.floor(nz / HASH_CELL_M);
    const hashStart = this.hashStart;
    const n = this.bornN;
    for (let ox = -1; ox <= 1; ox++) {
      for (let oz = -1; oz <= 1; oz++) {
        const b = hashCell(cx + ox, cz + oz);
        for (let p = hashStart[b]; p < hashStart[b + 1]; p++) if (this.hits(v, vs[this.hashItems[p]], nx, nz, nyaw)) return true;
      }
    }
    for (let p = 0; p < n; p++) if (this.hits(v, vs[this.born[p]], nx, nz, nyaw)) return true;
    return false;
  }

  private hits(v: Vehicle, o: Vehicle, nx: number, nz: number, nyaw: number): boolean {
    if (o === v || o.parked) return false;
    const dx = o.x - nx;
    const dz = o.z - nz;
    const reach = (v.len + o.len) * 0.5 + 0.6;
    if (dx * dx + dz * dz > reach * reach) return false;
    if (!rectsOverlap(nx, nz, nyaw, v.hw - MOVE_SHRINK_M, v.len * 0.5 - MOVE_SHRINK_M, o.x, o.z, o.yaw, o.hw - MOVE_SHRINK_M, o.len * 0.5 - MOVE_SHRINK_M)) return false;
    if (v.fresh) return true;
    const px = o.x - v.x;
    const pz = o.z - v.z;
    if (!rectsOverlap(v.x, v.z, v.yaw, v.hw - MOVE_SHRINK_M, v.len * 0.5 - MOVE_SHRINK_M, o.x, o.z, o.yaw, o.hw - MOVE_SHRINK_M, o.len * 0.5 - MOVE_SHRINK_M)) return true;
    return dx * dx + dz * dz < px * px + pz * pz - 1e-4;
  }
}
