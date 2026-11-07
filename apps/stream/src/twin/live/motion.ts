import * as THREE from "three";
import type { AroundCam, CamClipTracks, TrackedClass, TrackedObject } from "@/platform/around/types";
import { WORLD_RADIUS_M } from "../config";
import { laneLateral, snapPerson, snapVehicle, VEHICLE_REACH_M, type PersonSnap, type SnapIndex, type VehicleSnap } from "../network/snap";
import type { RoadNetwork, XZ } from "../network/types";
import { SKIN } from "./models/people";
import { camFrame, projectInto, sampleInto, type CamFrame } from "./project";
import { motionStats } from "./motionStats";
import { bodiesClash, junctionLookup } from "./sim/replay";

/**
 * Live entity simulation over real YOLO tracks. Each TfL clip plays ONCE, in real time, from the moment
 * it arrives; when a track's data ends the entity dead-reckons along its lane / footway at its last
 * smoothed speed, and a track from the next clip that appears where a reckoned entity is takes that
 * entity over instead of spawning a twin. A people figure fades out after RECKON_S; a vehicle keeps
 * driving the road network (it stops at a dead end) and is only retired once `keep` says nobody can
 * see it, or at the map edge. Vehicles are held to a bumper gap on spawn and in motion. Nothing here
 * allocates per frame except when a new track activates.
 */

export type Model = "person" | "car" | "van" | "bus" | "bike";

export const MODEL_OF: Record<TrackedClass, Model> = {
  person: "person",
  car: "car",
  truck: "van",
  bus: "bus",
  motorcycle: "bike",
  bicycle: "bike",
};

/** Real-world footprint per YOLO class: [width, height, length] in metres. */
export const DIMS: Record<Exclude<TrackedClass, "person">, [number, number, number]> = {
  car: [1.85, 1.05, 4.4],
  bus: [2.55, 4.4, 10.8],
  truck: [2.5, 3.2, 7.5],
  motorcycle: [0.8, 1.25, 2.1],
  bicycle: [0.6, 1.15, 1.8],
};

const FALLBACK_COLOR: Record<TrackedClass, string> = {
  person: "#c9a27e",
  car: "#6f93c9",
  bus: "#c8102e",
  truck: "#d9d9d9",
  motorcycle: "#3a3a42",
  bicycle: "#3a3a42",
};

const SNAP_RATE = 6;
const SPEED_RATE = 2;
/** Extra metres per second a pose may be corrected beyond its own speed, so a re-snap glides. */
const CORRECTION_MS = 8;
const YAW_RATE_VEHICLE = 6;
const YAW_RATE_PERSON = 6;
const RECKON_S = 25;
/** A reckoning vehicle this far from the origin has reached the map edge and may be retired even in view. */
const MAP_EDGE_M = WORLD_RADIUS_M - 20;
const FADE_IN_S = 0.6;
const FADE_OUT_S = 2.5;
const HANDOVER_M = 6;
const HANDOVER_COS = Math.cos(Math.PI / 4);
const DUP_CAR_M = 1.5;
const DUP_PERSON_M = 0.6;
const DUP_PROBATION_S = 1;
/** Minimum bumper-to-bumper gap, plus a share of the pair's combined length. */
const GAP_M = 2;
const GAP_PER_LENGTH = 0.04;
const PERSON_SEP_M = 0.7;
/** Largest single-frame push-back, kept under the 3 m teleport threshold. */
const MAX_PUSH_M = 2.5;
const MAX_PEOPLE = 400;
const PERSON_CELL = 2;
const PERSON_HASH = 4096;

export const enum State {
  Tracked = 0,
  Reckon = 1,
  Dying = 2,
}

export interface Entity {
  key: string;
  camId: string;
  cls: TrackedClass;
  kind: TrackedObject["kind"];
  model: Model;
  color: THREE.Color;
  skin: THREE.Color;
  seed: number;
  length: number;
  width: number;
  pts: TrackedObject["pts"];
  frame: CamFrame;
  clipStart: number;
  t1: number;
  state: State;
  reckonAt: number;
  fade: number;
  x: number;
  z: number;
  yaw: number;
  speed: number;
  /** Lane the vehicle last snapped to (edge*2 + rev) and its progress along the edge; -1 when off-network. */
  laneId: number;
  rev: boolean;
  s: number;
  /** Footway the person last snapped to and their progress / direction along it; -1 when off-network. */
  footway: number;
  fs: number;
  fdir: 1 | -1;
  /** Cleared when the entity dies; the array is compacted at the end of the step. */
  alive: boolean;
}

interface Pending {
  camId: string;
  clipStart: number;
  frame: CamFrame;
  tr: TrackedObject;
  cls: TrackedClass;
  t0: number;
  t1: number;
  probationUntil: number;
}

const _uv = new Float32Array(2);
const _xz = new Float32Array(2);
const _prev = new Float32Array(2);
const _p: XZ = [0, 0];
const _vs: VehicleSnap = { x: 0, z: 0, heading: 0, laneId: -1, s: 0, dist: 0 };
const _ps: PersonSnap = { x: 0, z: 0, footwayId: -1, s: 0, dist: 0 };
/** Observed pose scratch, filled by `observe`. */
const obs = { x: 0, z: 0, yaw: 0, speed: 0, moving: false };
const hashHead = new Int32Array(PERSON_HASH);
const hashNext = new Int32Array(MAX_PEOPLE);
const hashEnt = new Int32Array(MAX_PEOPLE);

function wrap(a: number) {
  while (a > Math.PI) a -= 2 * Math.PI;
  while (a < -Math.PI) a += 2 * Math.PI;
  return a;
}

function turnToward(a: number, b: number, k: number, maxStep: number) {
  let d = wrap(b - a) * k;
  if (d > maxStep) d = maxStep;
  else if (d < -maxStep) d = -maxStep;
  return a + d;
}

const cellKey = (x: number, z: number) =>
  ((Math.floor(x / PERSON_CELL) * 73856093) ^ (Math.floor(z / PERSON_CELL) * 19349663)) & (PERSON_HASH - 1);

/** Projects a track at clip time t into `obs` (ground point, heading, speed over the last 0.3 s). */
function observe(frame: CamFrame, pts: TrackedObject["pts"], t: number): boolean {
  if (!sampleInto(pts, t, _uv)) return false;
  projectInto(frame, _uv[0], _uv[1], _xz);
  obs.x = _xz[0];
  obs.z = _xz[1];
  obs.moving = false;
  obs.speed = 0;
  if (sampleInto(pts, t - 0.3, _uv)) {
    projectInto(frame, _uv[0], _uv[1], _prev);
    const dx = _xz[0] - _prev[0];
    const dz = _xz[1] - _prev[1];
    const d2 = dx * dx + dz * dz;
    if (d2 > 1e-4) {
      obs.yaw = Math.atan2(dx, dz);
      obs.moving = true;
      obs.speed = Math.sqrt(d2) / 0.3;
    }
  }
  return true;
}

/** YOLO's point is the bottom of the box: push a vehicle's centre away from the camera by half its footprint. */
function pushToCentre(e: Entity, frame: CamFrame, yaw: number) {
  const rx = obs.x - frame.x;
  const rz = obs.z - frame.z;
  const rl = Math.hypot(rx, rz) || 1;
  const along = Math.abs((rx / rl) * Math.sin(yaw) + (rz / rl) * Math.cos(yaw));
  const push = 0.5 * (along * e.length + Math.sqrt(1 - Math.min(1, along * along)) * e.width);
  obs.x += (rx / rl) * push;
  obs.z += (rz / rl) * push;
}

/**
 * Moves a lane vehicle `d` metres on (negative = back the way it came), through junctions along the
 * straightest edge. At a dead end it stops there (forward) or stays put (back); false only when it is off the network.
 */
function advanceLane(net: RoadNetwork, e: Entity, d: number): boolean {
  let edge = net.edges[e.laneId >> 1];
  e.s += e.rev ? -d : d;
  for (let guard = 0; guard < 4 && (e.s < 0 || e.s > edge.length); guard++) {
    const atTo = e.s > edge.length;
    const over = atTo ? e.s - edge.length : -e.s;
    const node = net.nodes[atTo ? edge.to : edge.from];
    // Leaving the node onto a new lane when moving with the lane, arriving from one when backing up.
    const downstream = atTo !== e.rev;
    let next = edge;
    let nextRev = false;
    let best = -0.3;
    for (let i = 0; i < node.edges.length; i++) {
      const cand = net.edges[node.edges[i]];
      if (cand.id === edge.id) continue;
      const candRev = downstream ? cand.to === node.id : cand.from === node.id;
      if (candRev && cand.oneway) continue;
      const eps = Math.min(0.5, cand.length * 0.4);
      const h = net.sample(cand.id, downstream === candRev ? cand.length - eps : eps, 0, _p);
      const score = Math.cos((candRev ? h + Math.PI : h) - e.yaw);
      if (score > best) {
        best = score;
        next = cand;
        nextRev = candRev;
      }
    }
    if (next === edge) {
      // Dead end: a vehicle driving on stops short of it; one backing up stays put.
      e.s = downstream ? (e.rev ? e.length / 2 : edge.length - e.length / 2) : e.rev ? edge.length - e.length / 2 : e.length / 2;
      if (downstream) e.speed = 0;
      break;
    }
    edge = next;
    e.rev = nextRev;
    e.laneId = edge.id * 2 + (nextRev ? 1 : 0);
    e.s = downstream === nextRev ? edge.length - over : over;
  }
  placeOnLane(net, e);
  return true;
}

/** Writes the lane-line pose at the vehicle's (lane, s) into `obs`: always on the asphalt, heading along the road. */
function placeOnLane(net: RoadNetwork, e: Entity) {
  const edge = net.edges[e.laneId >> 1];
  e.s = Math.min(edge.length, Math.max(0, e.s));
  const lat = laneLateral(edge.width);
  const h = net.sample(edge.id, e.s, e.rev ? -lat : lat, _p);
  obs.x = _p[0];
  obs.z = _p[1];
  obs.yaw = e.rev ? h + Math.PI : h;
}

/** Moves a person `d` metres along their footway, continuing onto the straightest touching footway. */
function advanceFootway(net: RoadNetwork, e: Entity, d: number): boolean {
  let f = net.footways[e.footway];
  e.fs += e.fdir * d;
  for (let guard = 0; guard < 4 && (e.fs < 0 || e.fs > f.length); guard++) {
    const over = e.fdir > 0 ? e.fs - f.length : -e.fs;
    const end = e.fdir > 0 ? f.pts[f.pts.length - 1] : f.pts[0];
    let next = f;
    let nextDir: 1 | -1 = 1;
    let best = -0.3;
    for (let i = 0; i < f.neighbours.length; i++) {
      const g = net.footways[f.neighbours[i]];
      if (g.id === f.id || g.length < 1e-3) continue;
      const a = g.pts[0];
      const b = g.pts[g.pts.length - 1];
      const atStart = Math.hypot(a[0] - end[0], a[1] - end[1]) < 1.5;
      const atEnd = Math.hypot(b[0] - end[0], b[1] - end[1]) < 1.5;
      if (!atStart && !atEnd) continue;
      const dir: 1 | -1 = atStart ? 1 : -1;
      const h = net.sampleFootway(g.id, dir > 0 ? Math.min(0.5, g.length) : Math.max(0, g.length - 0.5), _p);
      const score = Math.cos((dir > 0 ? h : h + Math.PI) - e.yaw);
      if (score > best) {
        best = score;
        next = g;
        nextDir = dir;
      }
    }
    if (next === f) return false;
    f = next;
    e.fdir = nextDir;
    e.footway = f.id;
    e.fs = nextDir > 0 ? over : f.length - over;
  }
  const h = net.sampleFootway(f.id, e.fs, _p);
  obs.x = _p[0];
  obs.z = _p[1];
  obs.yaw = e.fdir > 0 ? h : h + Math.PI;
  return true;
}

export class MotionSim {
  readonly ents: Entity[] = [];
  /** Keys that left the drawn set legitimately in the latest `step`: retired out of sight, or renamed by a handover. For the audit. */
  readonly retired: string[] = [];
  private readonly pending: Pending[] = [];
  private readonly seenClips = new Map<string, number>();
  private readonly people: Entity[] = [];

  /** Registers clips that have not been played yet; call when the store's clips change. */
  ingest(cams: AroundCam[], clips: Record<string, CamClipTracks>, now: number) {
    for (const cam of cams) {
      const clip = clips[cam.id];
      if (!clip || clip.duration <= 0 || this.seenClips.get(cam.id) === clip.at) continue;
      this.seenClips.set(cam.id, clip.at);
      const frame = camFrame(cam);
      for (const tr of clip.tracks) {
        if (tr.pts.length < 2) continue;
        this.pending.push({
          camId: cam.id,
          clipStart: now,
          frame,
          tr,
          cls: tr.cls ?? (tr.kind === "person" ? "person" : "car"),
          t0: tr.pts[0][0],
          t1: tr.pts[tr.pts.length - 1][0],
          probationUntil: 0,
        });
      }
    }
  }

  /** Re-projects bound tracks after a camera pose edit. */
  reframe(cams: AroundCam[]) {
    for (const cam of cams) {
      const frame = camFrame(cam);
      for (const e of this.ents) if (e.camId === cam.id) e.frame = frame;
      for (const p of this.pending) if (p.camId === cam.id) p.frame = frame;
    }
  }

  /** `keep` says whether a reckoning vehicle must stay (in view, or near the camera that tracked it); without it none retire. */
  step(now: number, dt: number, snap: SnapIndex | null, keep: ((e: Entity) => boolean) | null = null) {
    this.retired.length = 0;
    this.activate(now, snap);
    const lerpK = 1 - Math.exp(-SNAP_RATE * dt);
    const speedK = 1 - Math.exp(-SPEED_RATE * dt);
    const net = snap?.net ?? null;
    for (let i = 0; i < this.ents.length; i++) {
      const e = this.ents[i];
      const isPerson = e.kind === "person";
      let hasObs = false;
      if (e.state === State.Tracked) {
        const t = now - e.clipStart;
        if (t > e.t1 || !observe(e.frame, e.pts, t)) {
          e.state = State.Reckon;
          e.reckonAt = now;
        } else {
          hasObs = true;
          if (!obs.moving) obs.yaw = e.yaw;
          e.speed += (obs.speed - e.speed) * speedK;
          if (isPerson) {
            if (snap && snapPerson(snap, obs.x, obs.z, _ps)) {
              obs.x = _ps.x;
              obs.z = _ps.z;
              e.footway = _ps.footwayId;
              e.fs = _ps.s;
              const h = net!.sampleFootway(_ps.footwayId, _ps.s, _p);
              e.fdir = Math.cos(h - obs.yaw) >= 0 ? 1 : -1;
            } else e.footway = -1;
          } else {
            pushToCentre(e, e.frame, obs.yaw);
            if (snap && snapVehicle(snap, obs.x, obs.z, obs.moving ? obs.yaw : NaN, _vs, VEHICLE_REACH_M, e.laneId, e.cls === "bus")) {
              // Progress along the lane eases toward the track's; the pose is always the lane line's.
              const reach = (CORRECTION_MS + e.speed) * dt;
              e.s = _vs.laneId === e.laneId ? e.s + Math.max(-reach, Math.min(reach, (_vs.s - e.s) * lerpK)) : _vs.s;
              e.laneId = _vs.laneId;
              e.rev = (_vs.laneId & 1) === 1;
              placeOnLane(net!, e);
            } else e.laneId = -1;
          }
        }
      }
      if (e.state === State.Reckon) {
        if (isPerson && now - e.reckonAt > RECKON_S) e.state = State.Dying;
        else {
          const d = e.speed * dt;
          let onNet = false;
          if (net && isPerson && e.footway >= 0) onNet = advanceFootway(net, e, d);
          else if (net && !isPerson && e.laneId >= 0) onNet = advanceLane(net, e, d);
          if (!onNet) {
            if (isPerson) e.footway = -1;
            else e.laneId = -1;
            obs.x = e.x + Math.sin(e.yaw) * d;
            obs.z = e.z + Math.cos(e.yaw) * d;
            obs.yaw = e.yaw;
            // Off the network there is nothing to follow: a figure coasts briefly, then goes.
            if (isPerson && now - e.reckonAt > RECKON_S * 0.3) e.state = State.Dying;
          }
          const atEdge = obs.x * obs.x + obs.z * obs.z > (isPerson ? WORLD_RADIUS_M * WORLD_RADIUS_M : MAP_EDGE_M * MAP_EDGE_M);
          if (isPerson && atEdge) e.state = State.Dying;
          // A vehicle only leaves once it is out of sight (or at the map edge): never a pop in view.
          else if (!isPerson && (atEdge || (keep && !keep(e)))) {
            e.alive = false;
            this.retired.push(e.key);
            continue;
          }
          hasObs = true;
        }
      }
      if (hasObs && !isPerson && e.laneId >= 0) {
        e.x = obs.x;
        e.z = obs.z;
        e.yaw = turnToward(e.yaw, obs.yaw, 1, YAW_RATE_VEHICLE * dt);
      } else if (hasObs) {
        const maxStep = (CORRECTION_MS + e.speed) * dt;
        let dx = (obs.x - e.x) * lerpK;
        let dz = (obs.z - e.z) * lerpK;
        const dl = Math.hypot(dx, dz);
        if (dl > maxStep) {
          dx *= maxStep / dl;
          dz *= maxStep / dl;
        }
        e.x += dx;
        e.z += dz;
        e.yaw = turnToward(e.yaw, obs.yaw, lerpK, (isPerson ? YAW_RATE_PERSON : YAW_RATE_VEHICLE) * dt);
      }
      if (e.state === State.Dying) {
        e.fade -= dt / FADE_OUT_S;
        if (e.fade <= 0) e.alive = false;
      } else if (e.fade < 1) e.fade = Math.min(1, e.fade + dt / FADE_IN_S);
    }
    this.separateVehicles(snap);
    this.separatePeople();
    let w = 0;
    for (let i = 0; i < this.ents.length; i++) if (this.ents[i].alive) this.ents[w++] = this.ents[i];
    this.ents.length = w;
  }

  private activate(now: number, snap: SnapIndex | null) {
    for (let i = this.pending.length - 1; i >= 0; i--) {
      const p = this.pending[i];
      const t = now - p.clipStart;
      if (t > p.t1) {
        this.pending.splice(i, 1);
        continue;
      }
      if (t < p.t0 || !observe(p.frame, p.tr.pts, t)) continue;
      const isPerson = p.tr.kind === "person";
      const dims = DIMS[p.cls as keyof typeof DIMS] ?? DIMS.car;
      if (!isPerson) {
        pushToCentre({ length: dims[2], width: dims[0] } as Entity, p.frame, obs.moving ? obs.yaw : 0);
        if (snap && snapVehicle(snap, obs.x, obs.z, obs.moving ? obs.yaw : NaN, _vs, VEHICLE_REACH_M, -1, p.cls === "bus")) {
          obs.x = _vs.x;
          obs.z = _vs.z;
          obs.yaw = _vs.heading;
        }
      } else if (snap && snapPerson(snap, obs.x, obs.z, _ps)) {
        obs.x = _ps.x;
        obs.z = _ps.z;
      }
      const taken = this.handover(p, isPerson, now);
      if (taken) {
        motionStats.handovers++;
        this.pending.splice(i, 1);
        continue;
      }
      if (this.nearDuplicate(p, isPerson) || (!isPerson && this.crowded(p, dims, snap))) {
        if (!p.probationUntil) p.probationUntil = now + DUP_PROBATION_S;
        else if (now >= p.probationUntil) {
          motionStats.duplicatesDropped++;
          this.pending.splice(i, 1);
        }
        continue;
      }
      p.probationUntil = 0;
      this.spawn(p, isPerson, dims, now);
      this.pending.splice(i, 1);
    }
  }

  /** A reckoned entity of the same class within HANDOVER_M (and roughly the same heading) continues as this track. */
  private handover(p: Pending, isPerson: boolean, now: number): boolean {
    let best: Entity | null = null;
    let bestD = HANDOVER_M * HANDOVER_M;
    for (const e of this.ents) {
      if (e.state === State.Tracked || e.camId !== p.camId || e.cls !== p.cls) continue;
      const d2 = (e.x - obs.x) ** 2 + (e.z - obs.z) ** 2;
      if (d2 >= bestD) continue;
      if (!isPerson && obs.moving && Math.cos(obs.yaw - e.yaw) < HANDOVER_COS) continue;
      best = e;
      bestD = d2;
    }
    if (!best) return false;
    best.pts = p.tr.pts;
    best.frame = p.frame;
    best.clipStart = p.clipStart;
    best.t1 = p.t1;
    best.state = State.Tracked;
    best.reckonAt = now;
    this.retired.push(best.key);
    best.key = `${p.camId}:${p.clipStart}:${p.tr.id}`;
    best.color.set(p.tr.color ?? FALLBACK_COLOR[p.cls]);
    return true;
  }

  /** True while a vehicle spawned here would sit inside another's bumper gap, so its spawn waits for the spot to clear. */
  private crowded(p: Pending, dims: [number, number, number], snap: SnapIndex | null): boolean {
    const yaw = obs.moving ? obs.yaw : Math.atan2(obs.x - p.frame.x, obs.z - p.frame.z);
    const atJunction = snap ? junctionLookup(snap)(obs.x, obs.z) : false;
    for (const e of this.ents) {
      if (e.kind === "person" || e.state === State.Dying) continue;
      if (bodiesClash(obs.x, obs.z, yaw, dims[2], dims[0], e.x, e.z, e.yaw, e.length, e.width, atJunction)) return true;
    }
    return false;
  }

  private nearDuplicate(p: Pending, isPerson: boolean): boolean {
    const lim = isPerson ? DUP_PERSON_M : DUP_CAR_M;
    for (const e of this.ents) {
      if (e.state !== State.Tracked || e.camId !== p.camId || e.cls !== p.cls) continue;
      if ((e.x - obs.x) ** 2 + (e.z - obs.z) ** 2 < lim * lim) return true;
    }
    return false;
  }

  private spawn(p: Pending, isPerson: boolean, dims: [number, number, number], now: number) {
    const e: Entity = {
      key: `${p.camId}:${p.clipStart}:${p.tr.id}`,
      camId: p.camId,
      cls: p.cls,
      kind: p.tr.kind,
      model: MODEL_OF[p.cls],
      color: new THREE.Color(p.tr.color ?? FALLBACK_COLOR[p.cls]),
      skin: SKIN[p.tr.id % SKIN.length],
      seed: (p.tr.id * 0.618) % 1,
      length: isPerson ? 0.5 : dims[2],
      width: isPerson ? 0.5 : dims[0],
      pts: p.tr.pts,
      frame: p.frame,
      clipStart: p.clipStart,
      t1: p.t1,
      state: State.Tracked,
      reckonAt: now,
      fade: 0,
      x: obs.x,
      z: obs.z,
      yaw: obs.moving ? obs.yaw : isPerson ? 0 : Math.atan2(obs.x - p.frame.x, obs.z - p.frame.z),
      speed: obs.speed,
      laneId: -1,
      rev: false,
      s: 0,
      footway: -1,
      fs: 0,
      fdir: 1,
      alive: true,
    };
    this.ents.push(e);
    motionStats.spawned++;
  }

  /** Vehicles never sit inside each other's bumper gap: whichever is behind (or, at a junction, later in the list) is held back. */
  private separateVehicles(snap: SnapIndex | null) {
    const ents = this.ents;
    const junction = snap ? junctionLookup(snap) : null;
    for (let i = 0; i < ents.length; i++) {
      const a = ents[i];
      if (a.kind === "person" || a.state === State.Dying) continue;
      for (let j = i + 1; j < ents.length; j++) {
        const b = ents[j];
        if (b.kind === "person" || b.state === State.Dying) continue;
        const atJunction = junction ? junction(a.x, a.z) || junction(b.x, b.z) : false;
        if (!bodiesClash(a.x, a.z, a.yaw, a.length, a.width, b.x, b.z, b.yaw, b.length, b.width, atJunction)) continue;
        const along = (b.x - a.x) * Math.sin(a.yaw) + (b.z - a.z) * Math.cos(a.yaw);
        const sameWay = Math.cos(a.yaw - b.yaw) > 0;
        const follower = !sameWay ? b : along >= 0 ? a : b;
        const leader = follower === a ? b : a;
        const gap = GAP_M + GAP_PER_LENGTH * (a.length + b.length);
        const need = (a.length + b.length) / 2 + gap;
        const back = Math.min(Math.max(need - Math.hypot(b.x - a.x, b.z - a.z), 0.1), MAX_PUSH_M);
        if (follower.laneId >= 0 && snap) {
          // Held back along its own lane, so it stays on the asphalt and the hold survives the next frame.
          advanceLane(snap.net, follower, -back);
          follower.x = obs.x;
          follower.z = obs.z;
        } else {
          follower.x -= Math.sin(follower.yaw) * back;
          follower.z -= Math.cos(follower.yaw) * back;
        }
        if (follower.state === State.Reckon && follower.speed > leader.speed) follower.speed = leader.speed;
      }
    }
  }

  /** Spatial hash over people; pairs closer than PERSON_SEP_M step apart along their own walking direction. */
  private separatePeople() {
    const people = this.people;
    people.length = 0;
    for (const e of this.ents) if (e.kind === "person" && e.state !== State.Dying && people.length < MAX_PEOPLE) people.push(e);
    hashHead.fill(-1);
    for (let i = 0; i < people.length; i++) {
      const k = cellKey(people[i].x, people[i].z);
      hashNext[i] = hashHead[k];
      hashHead[k] = i;
      hashEnt[i] = i;
    }
    for (let i = 0; i < people.length; i++) {
      const a = people[i];
      const cx = Math.floor(a.x / PERSON_CELL);
      const cz = Math.floor(a.z / PERSON_CELL);
      for (let ox = -1; ox <= 1; ox++) {
        for (let oz = -1; oz <= 1; oz++) {
          const k = (((cx + ox) * 73856093) ^ ((cz + oz) * 19349663)) & (PERSON_HASH - 1);
          for (let j = hashHead[k]; j !== -1; j = hashNext[j]) {
            if (j <= i) continue;
            const b = people[j];
            const dx = b.x - a.x;
            const dz = b.z - a.z;
            const d2 = dx * dx + dz * dz;
            if (d2 >= PERSON_SEP_M * PERSON_SEP_M) continue;
            const d = Math.sqrt(d2);
            const push = (PERSON_SEP_M - d) * 0.5;
            const ax = Math.sin(a.yaw);
            const az = Math.cos(a.yaw);
            const bx = Math.sin(b.yaw);
            const bz = Math.cos(b.yaw);
            const sa = dx * ax + dz * az >= 0 ? -1 : 1;
            const sb = dx * bx + dz * bz >= 0 ? 1 : -1;
            a.x += ax * push * sa;
            a.z += az * push * sa;
            b.x += bx * push * sb;
            b.z += bz * push * sb;
          }
        }
      }
    }
  }
}
