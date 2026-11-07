import { create } from "zustand";
import type { AroundDisruption } from "@/platform/around/types";
import { toLocal } from "../../config";
import type { RoadNetwork, XZ } from "../../network/types";
import { Chase, Driver, PHASE_HOLD, RoadGraph, type Helicopter } from "./chase";
import { mulberry32, wrapAngle } from "./rng";

export interface EmergencyVehicle {
  x: number;
  z: number;
  /** Travel yaw, atan2(dx, dz): 0 = +z (south), π/2 = +x (east). */
  heading: number;
  speed: number;
}

const SIREN_POOL: EmergencyVehicle[] = Array.from({ length: 24 }, () => ({ x: 0, z: 0, heading: 0, speed: 0 }));

/**
 * Plain mutable state, rewritten every frame by the London director (never reallocated, so read it
 * inside useFrame with zero garbage). `vehicles` lists every siren-on vehicle that is moving or
 * parking right now: the police chase (fleeing car + pursuers) and ambulance / fire / police
 * call-outs. Ambient traffic can read it to pull over: for each car, if any vehicle is within ~30 m
 * and `speed > 5`, steer to the kerb and brake. `active` is true while `vehicles` is non-empty.
 */
export const emergency: { active: boolean; vehicles: EmergencyVehicle[] } = { active: false, vehicles: [] };

/**
 * Time of day, rush-hour level and rain as the director sees them. Written every frame; the Traffic
 * agent can read `rush` (0..1) to thicken ambient traffic at 08-09 and 17-19.
 */
export const londonState = { hour: 12, rush: 0, rain: 0 };

export interface ChaseAlert {
  id: number;
  title: string;
  detail: string;
}

/** The HUD subscribes to this: `alert` is set when a chase starts and cleared a few seconds after it ends. */
export const useChaseAlert = create<{ alert: ChaseAlert | null }>(() => ({ alert: null }));

let forceChase = false;

/** Starts a police chase on the next frame (demo button / hotkey). */
export function requestChase() {
  forceChase = true;
}

export const LIFE_KINDS = [
  "police",
  "ambulance",
  "roadworks",
  "cabs",
  "vans",
  "couriers",
  "cyclists",
  "scooters",
  "sweeper",
  "birds",
  "tourists",
  "performers",
  "pubs",
] as const;
export type LifeKind = (typeof LIFE_KINDS)[number];

/** `?life=police,cabs` enables only those, `?life=off` none; no param enables everything. */
export function parseLife(search: string): Set<LifeKind> {
  const raw = new URLSearchParams(search).get("life");
  if (raw === null) return new Set(LIFE_KINDS);
  const want = raw.split(",").map((s) => s.trim().toLowerCase());
  return new Set(LIFE_KINDS.filter((k) => want.includes(k)));
}

const fmt = new Intl.DateTimeFormat("en-GB", { hour: "numeric", minute: "numeric", hour12: false, timeZone: "Europe/London" });

/** Fractional London hour (0-24) from the real clock. */
export function londonHour(now = new Date()): number {
  let h = 0;
  let m = 0;
  for (const p of fmt.formatToParts(now)) {
    if (p.type === "hour") h = Number(p.value) % 24;
    else if (p.type === "minute") m = Number(p.value);
  }
  return h + m / 60;
}

export const K = {
  POLICE: 0,
  QUARRY: 1,
  AMBULANCE: 2,
  FIRE: 3,
  CAB: 4,
  VAN: 5,
  MOPED: 6,
  BIKE: 7,
  COURIER: 8,
  SCOOTER: 9,
  SWEEPER: 10,
  EXCAVATOR: 11,
  HUMAN: 12,
  UMBRELLA: 13,
  BIRD: 14,
  CONE: 15,
  BARRIER: 16,
} as const;
export const KIND_COUNT = 17;
/** Instance caps: vehicles total 101 (<= 150), humans + birds 300. */
export const CAP = [12, 2, 6, 4, 18, 12, 8, 12, 10, 10, 1, 6, 200, 40, 100, 120, 40];

export class Actor {
  live = false;
  x = 0;
  y = 0;
  z = 0;
  yaw = 0;
  scale = 1;
  /** Linear body tint (multiplies the vertex colours) and glow / skin colour. */
  tr = 1;
  tg = 1;
  tb = 1;
  gr = 0;
  gg = 0;
  gb = 0;
  siren = false;
  phase = 0;
  speed = 0;
  /** Wing flap amount for birds. */
  flap = 0;
  /** 0 = standing still, otherwise a walking / fidgeting bob amount for people. */
  walk = 0;
  hands = false;
}

function lin(hex: number): [number, number, number] {
  return [((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255].map((c) => Math.pow(c, 2.2)) as [number, number, number];
}

function tint(a: Actor, hex: number) {
  [a.tr, a.tg, a.tb] = lin(hex);
}

function glow(a: Actor, hex: number) {
  [a.gr, a.gg, a.gb] = lin(hex);
}

const SKIN = [0xf1c8a5, 0xd9a07a, 0xb97b55, 0x8a5a3c, 0x5e3b27, 0xe8b894];
const JACKETS = [0x2b3a55, 0x6b2a2a, 0x2d4a3a, 0x8a8a92, 0x1d1d22, 0xb08a3a, 0x4a3a66, 0x3a6a8a, 0xa0a0a8];
const HIVIS = 0xd8ff1f;
const POLICE_NAVY = 0x14203c;
const PARAMEDIC = 0x1f9e4a;
const TAU = Math.PI * 2;

/** One-line schedule from the London clock. */
export interface Schedule {
  rush: number;
  cabs: number;
  vans: number;
  couriers: number;
  cyclists: number;
  scooters: number;
  sweeper: number;
  tourists: boolean;
  performers: boolean;
  pigeons: boolean;
  kebab: boolean;
  pub: number;
  disperse: number;
}

function tri(h: number, a: number, b: number, c: number) {
  return h <= a || h >= c ? 0 : h < b ? (h - a) / (b - a) : (c - h) / (c - b);
}

function clamp01(x: number) {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

export function schedule(hour: number, rain: number, out: Schedule): Schedule {
  const rush = Math.max(tri(hour, 7.2, 8.6, 10), tri(hour, 16.3, 18, 19.7));
  const day = clamp01(Math.min((hour - 6) / 2, (22 - hour) / 2));
  const lunch = tri(hour, 11, 13, 15);
  const dinner = tri(hour, 17.5, 20, 22.8);
  const dry = 1 - rain * 0.6;
  const late = hour >= 21.5 ? hour : hour + 24;
  out.rush = rush;
  out.cabs = 0.45 + 0.55 * day + 0.25 * rush;
  out.vans = hour >= 5 && hour < 18 ? 0.55 + 0.45 * rush : 0.15;
  out.couriers = 0.25 + 0.75 * Math.max(lunch, dinner);
  out.cyclists = (0.12 + 0.6 * day + 0.5 * rush) * dry;
  out.scooters = (hour >= 9 && hour < 23 ? 0.35 + 0.65 * day : 0.1) * dry * dry;
  out.sweeper = hour >= 4 && hour < 10 ? 1 : hour >= 23 || hour < 1 ? 1 : 0;
  out.tourists = hour >= 9.5 && hour < 18;
  out.performers = hour >= 11 && hour < 22;
  out.pigeons = hour >= 6 && hour < 20.5;
  out.kebab = hour >= 19 || hour < 4;
  out.pub = late >= 21.5 && late < 24.7 ? 1 : 0;
  out.disperse = clamp01((late - 23) / 0.6);
  return out;
}

interface Fleet {
  d: Driver;
  a: Actor;
  group: LifeKind;
  rank: number;
  base: number;
  lat0: number;
  bike: boolean;
}

interface FleetSpec {
  group: LifeKind;
  kind: number;
  n: number;
  v: [number, number];
  lat: number;
  corner: number;
  tints: number[];
  glow?: number[];
}

const FLEET: FleetSpec[] = [
  { group: "cabs", kind: K.CAB, n: 14, v: [8.5, 11.5], lat: 1.6, corner: 4.8, tints: [0x0b0b0e, 0x101116, 0x0d0f18] },
  { group: "vans", kind: K.VAN, n: 10, v: [7.5, 10.5], lat: 1.7, corner: 4.2, tints: [0xdcdfe3, 0xc4c8cc, 0x1d2c52, 0xe8e2d0, 0x7a1c1c, 0xf2f2f2] },
  { group: "couriers", kind: K.MOPED, n: 6, v: [9, 12], lat: 2.5, corner: 5.5, tints: [0x16161a, 0x2a2a30, 0xb8b8c0], glow: [0x00ccbc, 0xff8000, 0x06c167] },
  { group: "couriers", kind: K.COURIER, n: 8, v: [4.8, 6.6], lat: 3.2, corner: 3.4, tints: [0x16161a, 0x20242a], glow: [0x00ccbc, 0xff8000, 0x06c167] },
  { group: "cyclists", kind: K.BIKE, n: 12, v: [4.2, 6], lat: 3.3, corner: 3.2, tints: [0xe2001a, 0xe2001a, 0xc0001a, 0xd6dce0] },
  { group: "scooters", kind: K.SCOOTER, n: 10, v: [4, 5.6], lat: 3.5, corner: 3.2, tints: [0x9bff1a, 0xb6ff3a] },
  { group: "sweeper", kind: K.SWEEPER, n: 1, v: [2.6, 3.2], lat: 3.7, corner: 2.4, tints: [0x1f6b43] },
];

const CALLOUT_CAP = 6;
const PAVEMENT_OFF = 1.5;

interface Callout {
  d: Driver;
  a: Actor;
  /** 0 driving with siren, 1 easing into its park spot, 2 parked. */
  mode: number;
  holdLeft: number;
  /** Scene responders park for good; plain call-outs vanish after a moment. */
  scene: boolean;
  px: number;
  pz: number;
  pyaw: number;
  /** Seconds until a scene responder sets off. */
  wait: number;
  fromNode: number;
  toNode: number;
}

class Bird {
  state = 0;
  t = 0;
  vx = 0;
  vz = 0;
  ang = 0;
  rad = 20;
  step = 0;
  tx = 0;
  tz = 0;
  constructor(
    readonly a: Actor,
    readonly hx: number,
    readonly hz: number,
    readonly raven: boolean,
    readonly orbit: boolean,
  ) {}
}

class Person {
  d: Driver | null = null;
  hx = 0;
  hz = 0;
  dx = 0;
  dz = 0;
  constructor(readonly a: Actor) {}
}

interface Glow {
  x: number;
  y: number;
  z: number;
  size: number;
  r: number;
  g: number;
  b: number;
  kind: "kebab" | "pub" | "all";
  on: boolean;
}

export interface DirectorContext {
  hour: number;
  rain: number;
  /** Camera x/z: chases tend to start near the view. */
  focus: XZ;
}

const TOWER: XZ = [26, 190];
const TOWER_BRIDGE: XZ = [83, 479];
const WORLD_M = 620;

/**
 * The London life director: a pure, seeded sim that owns every emergency and everyday-life actor
 * (police chases, call-outs, disruption scenes, cabs, couriers, birds, tour groups, pub crowds…) and
 * exposes them as pooled `Actor`s per kind for the renderer to turn into instances.
 */
export class LondonDirector {
  readonly graph: RoadGraph;
  readonly pool: Actor[][];
  readonly glows: Glow[] = [];
  readonly sched: Schedule = {
    rush: 0, cabs: 1, vans: 1, couriers: 1, cyclists: 1, scooters: 1, sweeper: 0,
    tourists: true, performers: true, pigeons: true, kebab: false, pub: 0, disperse: 0,
  };
  chase: Chase | null = null;
  private clock = 0;
  private readonly rng: () => number;
  private readonly on: Set<LifeKind>;
  private nextChaseIn: number;
  private nextCalloutIn = 14;
  private alertClearAt = 0;
  private alertId = 0;
  private chaseActors: Actor[] = [];
  private chaseOfficers: Actor[] = [];
  private readonly fleet: Fleet[] = [];
  private readonly callouts: Callout[] = [];
  private sceneActors: Actor[] = [];
  private sceneKey = "";
  private readonly birds: Bird[] = [];
  private tour: { people: Person[]; routes: number[][]; leg: number; hold: number; moving: boolean } | null = null;
  private performers: Person[] = [];
  private pubGroups: Person[][] = [];
  private lane = { lane: 0, s: 0, dist: 0 };
  private readonly reserve: number[] = CAP.map(() => 0);
  private phaseCtr = 0.3;
  private umbrellaSlots: Actor[] = [];
  private out: XZ = [0, 0];

  constructor(
    net: RoadNetwork,
    seed: number,
    enabled: Set<LifeKind>,
  ) {
    this.graph = new RoadGraph(net);
    this.on = enabled;
    this.rng = mulberry32(seed);
    this.nextChaseIn = 14 + this.rng() * 8;
    this.pool = CAP.map((n) => Array.from({ length: n }, () => new Actor()));
    if (this.graph.empty) return;
    this.buildFleet();
    this.buildBirds();
    this.buildCrowds();
    for (let i = 0; i < CALLOUT_CAP; i++) this.callouts.push(this.makeCallout());
  }

  heli(): Helicopter | null {
    return this.chase && this.chase.alive ? this.chase.heli : null;
  }

  private alloc(kind: number): Actor | null {
    const list = this.pool[kind];
    for (let i = this.reserve[kind]; i < list.length; i++) {
      if (!list[i].live) {
        const a = list[i];
        a.live = true;
        a.phase = this.nextPhase();
        a.y = 0;
        a.yaw = 0;
        a.scale = 1;
        a.siren = false;
        a.flap = 0;
        a.walk = 0;
        a.hands = false;
        a.speed = 0;
        a.tr = a.tg = a.tb = 1;
        a.gr = a.gg = a.gb = 0;
        return a;
      }
    }
    return null;
  }

  private nextPhase() {
    this.phaseCtr = (this.phaseCtr + 0.618034) % 1;
    return this.phaseCtr;
  }

  /** Permanently reserves the next actor of `kind` for a long-lived sim entity (never handed out by alloc). */
  private take(kind: number): Actor {
    const a = this.pool[kind][this.reserve[kind]++];
    a.phase = this.nextPhase();
    return a;
  }

  private human(hex: number, x: number, z: number, yaw: number): Actor | null {
    const a = this.alloc(K.HUMAN);
    if (!a) return null;
    tint(a, hex);
    glow(a, SKIN[Math.floor(this.rng() * SKIN.length)]);
    a.scale = 0.92 + this.rng() * 0.16;
    a.x = x;
    a.z = z;
    a.yaw = yaw;
    return a;
  }

  private pick<T>(list: T[]): T {
    return list[Math.floor(this.rng() * list.length)];
  }

  private randomLane(): number {
    const g = this.graph;
    return g.mainLanes[Math.floor(this.rng() * g.mainLanes.length)];
  }

  /** A pavement spot near (cx, cz): x, z and the road's yaw. */
  private spotNear(cx: number, cz: number, radius: number, out: [number, number, number]): boolean {
    const g = this.graph;
    for (let tries = 0; tries < 40; tries++) {
      const L = this.randomLane();
      if (g.len[L] < 25 || Math.hypot(g.midX[L] - cx, g.midZ[L] - cz) > radius) continue;
      const s = g.len[L] * (0.25 + this.rng() * 0.5);
      const yaw = g.pose(L, s, g.width[L] / 2 + PAVEMENT_OFF + 1.4, this.out);
      out[0] = this.out[0];
      out[1] = this.out[1];
      out[2] = yaw;
      return true;
    }
    return false;
  }

  private buildFleet() {
    const g = this.graph;
    for (const spec of FLEET) {
      for (let i = 0; i < spec.n; i++) {
        const a = this.take(spec.kind);
        tint(a, this.pick(spec.tints));
        if (spec.glow) glow(a, this.pick(spec.glow));
        if (spec.kind === K.CAB) glow(a, 0xffc233);
        const d = new Driver();
        const vMax = spec.v[0] + this.rng() * (spec.v[1] - spec.v[0]);
        d.vMax = vMax;
        d.accel = 2.4;
        d.brake = 5.5;
        d.cornerV = spec.corner;
        d.latMag = spec.lat;
        d.smooth = 11;
        d.avoidDead = true;
        g.place(d, this.randomLane(), this.rng() * 30, this.rng);
        const bike = spec.group !== "cabs" && spec.group !== "vans" && !(spec.kind === K.MOPED);
        this.fleet.push({ d, a, group: spec.group, rank: i, base: spec.n, lat0: spec.lat, bike });
      }
    }
  }

  private buildBirds() {
    const spot: [number, number, number] = [0, 0, 0];
    for (let f = 0; f < 9; f++) {
      if (f < 7 ? !this.spotNear(0, 120, 330, spot) : !this.spotNear(TOWER[0], TOWER[1], 140, spot)) continue;
      for (let i = 0; i < 6; i++) this.addBird(spot[0] + (this.rng() - 0.5) * 6, spot[1] + (this.rng() - 0.5) * 6, false, false);
    }
    for (let i = 0; i < 6; i++) {
      const hop = i < 4;
      this.addBird(TOWER[0] + (this.rng() - 0.5) * 24, TOWER[1] + (this.rng() - 0.5) * 24, true, !hop);
    }
  }

  private addBird(x: number, z: number, raven: boolean, orbit: boolean) {
    if (this.reserve[K.BIRD] >= CAP[K.BIRD]) return;
    const a = this.take(K.BIRD);
    if (raven) tint(a, 0x08080b);
    else tint(a, this.pick([0x8c8f99, 0x74777f, 0xa4a6ad, 0x686b74]));
    a.scale = raven ? 1.9 : 1;
    const b = new Bird(a, x, z, raven, orbit);
    b.ang = this.rng() * TAU;
    b.rad = 16 + this.rng() * 18;
    b.t = this.rng() * 3;
    if (orbit) b.state = 3;
    this.birds.push(b);
  }

  private buildCrowds() {
    const g = this.graph;
    const rng = this.rng;
    const spot: [number, number, number] = [0, 0, 0];

    const a = g.nearestNode(0, 0);
    const b = g.nearestNode(TOWER_BRIDGE[0], TOWER_BRIDGE[1]);
    const full = a >= 0 && b >= 0 ? g.path(a, b) : null;
    let there: number[] | null = null;
    let back: number[] | null = null;
    if (full) {
      let walked = 0;
      let k = full.length;
      while (k > 0 && walked < 300) walked += g.len[full[--k]];
      there = full.slice(k);
      back = there.map((l) => l ^ 1).reverse();
    }
    if (there && back && there.length > 1) {
      const people: Person[] = [];
      for (let i = 0; i < 12; i++) {
        const p = new Person(this.take(K.HUMAN));
        tint(p.a, i === 0 ? 0xc0392b : JACKETS[i % JACKETS.length]);
        glow(p.a, SKIN[i % SKIN.length]);
        const d = new Driver();
        d.vMax = 1.25 + rng() * 0.25;
        d.accel = 1.2;
        d.brake = 3;
        d.cornerV = 1.2;
        d.smooth = 10;
        d.latMag = 5;
        p.d = d;
        p.dx = (rng() - 0.5) * 2;
        people.push(p);
      }
      this.tour = { people, routes: [there, back], leg: 0, hold: 0, moving: true };
      this.startTour(0);
    }

    if (this.spotNear(0, 60, 200, spot)) {
      const cx = spot[0];
      const cz = spot[1];
      for (let i = 0; i < 15; i++) {
        const p = new Person(this.take(K.HUMAN));
        const ang = (i / 14) * TAU;
        const r = i === 14 ? 0 : 3.1 + rng() * 0.5;
        p.hx = cx + Math.cos(ang) * r;
        p.hz = cz + Math.sin(ang) * r;
        p.a.x = p.hx;
        p.a.z = p.hz;
        p.a.yaw = i === 14 ? 0 : Math.atan2(cx - p.hx, cz - p.hz);
        tint(p.a, i === 14 ? 0xff2d9a : this.pick(JACKETS));
        glow(p.a, this.pick(SKIN));
        this.performers.push(p);
      }
      this.glows.push({ x: cx, y: 2.6, z: cz, size: 9, r: 1, g: 0.6, b: 0.9, kind: "all", on: true });
    }

    for (let grp = 0; grp < 3; grp++) {
      if (!this.spotNear(0, 80, 260, spot)) continue;
      const crowd: Person[] = [];
      for (let i = 0; i < 8; i++) {
        const p = new Person(this.take(K.HUMAN));
        const r = 0.8 + rng() * 2.4;
        const ang = rng() * TAU;
        p.hx = spot[0] + Math.cos(ang) * r;
        p.hz = spot[1] + Math.sin(ang) * r;
        p.dx = Math.cos(rng() * TAU);
        p.dz = Math.sin(rng() * TAU);
        tint(p.a, this.pick(JACKETS));
        glow(p.a, this.pick(SKIN));
        p.a.yaw = rng() * TAU;
        crowd.push(p);
      }
      this.pubGroups.push(crowd);
      this.glows.push({ x: spot[0], y: 3.4, z: spot[1], size: 12, r: 1, g: 0.62, b: 0.24, kind: "pub", on: false });
    }
    for (let k = 0; k < 2; k++) {
      if (this.spotNear(0, 40, 240, spot)) {
        this.glows.push({ x: spot[0], y: 2.8, z: spot[1], size: 10, r: 1, g: 0.36, b: 0.12, kind: "kebab", on: false });
      }
    }
  }

  private startTour(leg: number) {
    const t = this.tour;
    if (!t) return;
    const g = this.graph;
    const route = t.routes[leg];
    t.leg = leg;
    t.moving = true;
    t.people.forEach((p, i) => {
      const d = p.d;
      if (!d) return;
      d.route = route.slice(1);
      d.delay = i * 0.9;
      d.latMag = g.width[route[0]] / 2 + 1.4 + p.dx;
      g.place(d, route[0], 0.5, this.rng);
      d.x = d.z = 0;
      d.fresh = true;
      g.settle(d, 0.016);
    });
  }

  private pushSiren(x: number, z: number, heading: number, speed: number) {
    const list = emergency.vehicles;
    if (list.length >= SIREN_POOL.length) return;
    const o = SIREN_POOL[list.length];
    o.x = x;
    o.z = z;
    o.heading = heading;
    o.speed = speed;
    list.push(o);
  }

  update(dtRaw: number, ctx: DirectorContext) {
    const dt = Math.min(dtRaw, 0.05);
    this.clock += dt;
    emergency.vehicles.length = 0;
    schedule(ctx.hour, ctx.rain, this.sched);
    londonState.hour = ctx.hour;
    londonState.rush = this.sched.rush;
    londonState.rain = ctx.rain;
    if (this.graph.empty) {
      emergency.active = false;
      return;
    }
    this.updateChase(dt, ctx);
    this.updateCallouts(dt);
    this.updateFleet(dt, ctx);
    this.updateBirds(dt);
    this.updateCrowds(dt, ctx);
    emergency.active = emergency.vehicles.length > 0;
    if (this.alertClearAt && this.clock > this.alertClearAt) {
      this.alertClearAt = 0;
      useChaseAlert.setState({ alert: null });
    }
  }

  private startChase(ctx: DirectorContext) {
    const rng = this.rng;
    const cops = 2 + Math.floor(rng() * 3);
    const focusX = Math.max(-420, Math.min(420, ctx.focus[0]));
    const focusZ = Math.max(-420, Math.min(420, ctx.focus[1]));
    const chase = new Chase(this.graph, rng, [focusX, focusZ], cops, rng() < 0.65);
    if (!chase.alive) return;
    this.chase = chase;
    const q = this.alloc(K.QUARRY);
    if (q) {
      tint(q, this.pick([0x0a0a0c, 0x111214, 0x0e1016]));
      this.chaseActors.push(q);
    }
    for (let i = 0; i < chase.cops.length; i++) {
      const c = this.alloc(K.POLICE);
      if (!c) break;
      c.siren = true;
      this.chaseActors.push(c);
    }
    this.chaseOfficers = [];
    this.alertId++;
    useChaseAlert.setState({ alert: { id: this.alertId, title: "POLICE PURSUIT", detail: "Tower Hill" } });
    this.alertClearAt = 0;
  }

  private updateChase(dt: number, ctx: DirectorContext) {
    const enabled = this.on.has("police");
    if (!this.chase) {
      this.nextChaseIn -= dt;
      if (enabled && (forceChase || this.nextChaseIn <= 0)) {
        forceChase = false;
        this.startChase(ctx);
        this.nextChaseIn = 40 + this.rng() * 50;
      }
      return;
    }
    const chase = this.chase;
    const prev = chase.phase;
    chase.update(dt);
    if (prev !== chase.phase && chase.phase === PHASE_HOLD) {
      useChaseAlert.setState({ alert: { id: this.alertId, title: "SUSPECT DETAINED", detail: "Tower Hill" } });
      this.alertClearAt = this.clock + 9;
    }
    const q = chase.quarry;
    const acts = this.chaseActors;
    const fade = chase.fade;
    const qa = acts[0];
    if (qa) {
      qa.x = q.x;
      qa.z = q.z;
      qa.yaw = q.yaw + q.slip;
      qa.speed = q.v;
      qa.scale = fade;
      qa.live = fade > 0.001;
    }
    if (q.v > 1) this.pushSiren(q.x, q.z, q.yaw, q.v);
    for (let i = 0; i < chase.cops.length; i++) {
      const c = chase.cops[i];
      const a = acts[i + 1];
      if (!a) continue;
      a.x = c.x;
      a.z = c.z;
      a.yaw = c.yaw;
      a.speed = c.v;
      a.scale = fade;
      a.live = fade > 0.001;
      if (c.v > 1) this.pushSiren(c.x, c.z, c.yaw, c.v);
    }
    for (let i = 0; i < chase.officers.length; i++) {
      const o = chase.officers[i];
      if (o.live && !this.chaseOfficers[i]) {
        const a = this.human(o.suspect ? 0x3a3f4a : i % 2 ? HIVIS : POLICE_NAVY, o.x, o.z, o.yaw);
        if (a) this.chaseOfficers[i] = a;
      }
      const a = this.chaseOfficers[i];
      if (!a) continue;
      a.live = o.live && fade > 0.001;
      a.x = o.x;
      a.z = o.z;
      a.yaw = o.yaw;
      a.walk = o.moving ? 1 : 0;
      a.hands = o.suspect && !o.moving;
      a.scale = (o.suspect ? 1 : 1.02) * Math.max(0.01, fade);
    }
    if (!chase.alive) {
      for (const a of this.chaseActors) a.live = false;
      for (const a of this.chaseOfficers) if (a) a.live = false;
      this.chaseActors = [];
      this.chaseOfficers = [];
      this.chase = null;
      if (!this.alertClearAt) this.alertClearAt = this.clock + 2;
    }
  }

  private makeCallout(): Callout {
    const d = new Driver();
    d.vMax = 15 + this.rng() * 4;
    d.accel = 4;
    d.brake = 9;
    d.cornerV = 7;
    d.latMag = 0;
    d.smooth = 9;
    return { d, a: new Actor(), mode: 3, holdLeft: 0, scene: false, px: 0, pz: 0, pyaw: 0, wait: 0, fromNode: -1, toNode: -1 };
  }

  private dispatch(kind: number, fromNode: number, toNode: number, c: Callout, scene: boolean, park?: [number, number, number]) {
    const g = this.graph;
    const route = g.path(fromNode, toNode);
    if (!route || route.length < 2) return false;
    const a = this.alloc(kind);
    if (!a) return false;
    a.siren = true;
    tint(a, 0xffffff);
    c.a = a;
    const d = c.d;
    d.route = route.slice(1);
    d.follow = null;
    g.place(d, route[0], 0.5, this.rng);
    d.fresh = true;
    g.settle(d, 0.016);
    c.mode = 0;
    c.scene = scene;
    c.holdLeft = 2 + this.rng() * 2;
    if (park) {
      c.px = park[0];
      c.pz = park[1];
      c.pyaw = park[2];
    }
    return true;
  }

  private updateCallouts(dt: number) {
    if (!this.on.has("ambulance")) return;
    const g = this.graph;
    this.nextCalloutIn -= dt;
    if (this.nextCalloutIn <= 0) {
      this.nextCalloutIn = 22 + this.rng() * 35;
      const free = this.callouts.find((c) => c.mode === 3);
      if (free) {
        const r = this.rng();
        const kind = r < 0.55 ? K.AMBULANCE : r < 0.8 ? K.POLICE : K.FIRE;
        for (let tries = 0; tries < 6; tries++) {
          const a = g.from[this.randomLane()];
          const b = g.from[this.randomLane()];
          if (Math.hypot(g.nodeX(a) - g.nodeX(b), g.nodeZ(a) - g.nodeZ(b)) < 350) continue;
          if (this.dispatch(kind, a, b, free, false)) break;
        }
      }
    }
    for (const c of this.callouts) {
      if (c.mode === 3 || c.mode === 4) continue;
      const a = c.a;
      const d = c.d;
      if (c.mode === 0) {
        if (c.wait > 0) {
          c.wait -= dt;
          a.live = false;
          continue;
        }
        a.live = true;
        g.step(d, dt, d.vMax * (g.major[d.lane] ? 1 : 0.75), this.rng);
        a.x = d.x;
        a.z = d.z;
        a.yaw = d.yaw;
        a.speed = d.v;
        this.pushSiren(d.x, d.z, d.yaw, d.v);
        if (d.arrived) c.mode = c.scene ? 1 : 2;
      } else if (c.mode === 1) {
        const dx = c.px - a.x;
        const dz = c.pz - a.z;
        const dist = Math.hypot(dx, dz);
        const sp = Math.min(9, dist * 1.2 + 0.4);
        const step = Math.min(dist, sp * dt);
        if (dist > 0.1) {
          a.x += (dx / dist) * step;
          a.z += (dz / dist) * step;
        }
        const heading = dist > 2 ? Math.atan2(dx, dz) : c.pyaw;
        a.yaw += wrapAngle(heading - a.yaw) * Math.min(1, dt * 3);
        a.speed = sp;
        this.pushSiren(a.x, a.z, a.yaw, a.speed);
        if (dist < 0.2) {
          c.mode = 4;
          a.speed = 0;
        }
      } else if (c.mode === 2) {
        c.holdLeft -= dt;
        a.speed = 0;
        if (c.holdLeft <= 0) {
          a.live = false;
          c.mode = 3;
        }
      }
    }
  }

  private updateFleet(dt: number, ctx: DirectorContext) {
    const g = this.graph;
    const s = this.sched;
    const rush = s.rush;
    const slow = 1 - 0.35 * rush - 0.1 * ctx.rain;
    const evs = emergency.vehicles;
    for (let i = 0; i < this.fleet.length; i++) {
      const f = this.fleet[i];
      const share = this.on.has(f.group) ? (s[f.group as keyof Schedule] as number) : 0;
      const want = f.group === "sweeper" ? (share > 0 ? 1 : 0) : Math.ceil(f.base * Math.min(1, share));
      if (f.rank >= want) {
        f.a.live = false;
        continue;
      }
      const d = f.d;
      const a = f.a;
      if (!a.live) {
        a.live = true;
        g.place(d, this.randomLane(), this.rng() * g.len[d.lane], this.rng);
      }
      d.latMag = f.bike ? Math.min(f.lat0, g.width[d.lane] / 2 - 0.9) : Math.min(2, Math.max(1.1, g.width[d.lane] * 0.25));
      let target = d.vMax * (g.major[d.lane] ? 1 : 0.72) * slow;
      let gap = 40;
      let lead = 0;
      for (let j = 0; j < this.fleet.length; j++) {
        if (j === i) continue;
        const o = this.fleet[j];
        if (!o.a.live || o.d.lane !== d.lane || o.d.s <= d.s) continue;
        if (Math.abs(o.d.latMag - d.latMag) > 1.3) continue;
        const gp = o.d.s - d.s - 4.5;
        if (gp < gap) {
          gap = gp;
          lead = o.d.v;
        }
      }
      if (gap < 40) target = Math.min(target, lead + Math.max(0, gap - 3) * 0.8);
      for (let k = 0; k < evs.length; k++) {
        const e = evs[k];
        if (e.speed > 5 && (e.x - d.x) ** 2 + (e.z - d.z) ** 2 < 900) {
          target = Math.min(target, 1.5);
          break;
        }
      }
      g.step(d, dt, target, this.rng);
      a.x = d.x;
      a.z = d.z;
      a.yaw = d.yaw;
      a.speed = d.v;
    }
  }

  private updateBirds(dt: number) {
    const s = this.sched;
    const evs = emergency.vehicles;
    const clock = this.clock;
    for (const b of this.birds) {
      const a = b.a;
      a.live = this.on.has("birds") && (b.raven || s.pigeons);
      if (!a.live) continue;
      if (b.state === 0) {
        a.flap = 0;
        b.t -= dt;
        if (b.t <= 0) {
          b.t = 1 + this.rng() * 2.5;
          b.tx = b.hx + (this.rng() - 0.5) * 3.5;
          b.tz = b.hz + (this.rng() - 0.5) * 3.5;
          b.step = this.rng() < 0.4 ? 0 : 0.7;
        }
        const dx = b.tx - a.x;
        const dz = b.tz - a.z;
        const d = Math.hypot(dx, dz);
        if (d > 0.1 && b.step > 0) {
          a.x += (dx / d) * b.step * dt;
          a.z += (dz / d) * b.step * dt;
          a.yaw += wrapAngle(Math.atan2(dx, dz) - a.yaw) * Math.min(1, dt * 6);
        }
        a.y = 0.1 + (b.raven ? Math.abs(Math.sin(clock * 5 + a.phase * 9)) * 0.08 * (b.step > 0 ? 1 : 0) : 0);
        for (let k = 0; k < evs.length; k++) {
          const e = evs[k];
          const dx2 = a.x - e.x;
          const dz2 = a.z - e.z;
          if (e.speed > 5 && dx2 * dx2 + dz2 * dz2 < 484) {
            const l = Math.hypot(dx2, dz2) || 1;
            b.state = 1;
            b.t = 5 + this.rng() * 5;
            b.vx = (dx2 / l) * (5 + this.rng() * 3) + (this.rng() - 0.5) * 3;
            b.vz = (dz2 / l) * (5 + this.rng() * 3) + (this.rng() - 0.5) * 3;
            break;
          }
        }
      } else if (b.state === 1) {
        b.t -= dt;
        const turn = 0.9 * dt * (a.phase > 0.5 ? 1 : -1);
        const c = Math.cos(turn);
        const sn = Math.sin(turn);
        const vx = b.vx * c - b.vz * sn;
        b.vz = b.vx * sn + b.vz * c;
        b.vx = vx;
        a.x += b.vx * dt;
        a.z += b.vz * dt;
        a.y += (8 + a.phase * 6 - a.y) * Math.min(1, dt * 1.5);
        a.yaw = Math.atan2(b.vx, b.vz);
        a.flap = Math.sin(clock * 24 + a.phase * 20);
        if (b.t <= 0) b.state = 2;
      } else if (b.state === 2) {
        const dx = b.hx - a.x;
        const dz = b.hz - a.z;
        const d = Math.hypot(dx, dz);
        const sp = Math.min(7, d * 0.6 + 1.5);
        a.x += (dx / (d || 1)) * sp * dt;
        a.z += (dz / (d || 1)) * sp * dt;
        a.yaw = Math.atan2(dx, dz);
        a.y = Math.max(0.1, a.y - (2.2 + a.y * 0.25) * dt);
        a.flap = Math.sin(clock * 24 + a.phase * 20);
        if (d < 1.2 && a.y < 0.3) {
          b.state = 0;
          b.t = 1;
          a.flap = 0;
        }
      } else {
        b.ang += (0.28 + a.phase * 0.12) * dt;
        const nx = b.hx + Math.cos(b.ang) * b.rad;
        const nz = b.hz + Math.sin(b.ang) * b.rad;
        a.yaw = Math.atan2(nx - a.x, nz - a.z);
        a.x = nx;
        a.z = nz;
        a.y = 13 + Math.sin(b.ang * 2 + a.phase * 6) * 3;
        a.flap = Math.sin(clock * 9 + a.phase * 20) * 0.6;
      }
    }
  }

  private updateCrowds(dt: number, ctx: DirectorContext) {
    const g = this.graph;
    const s = this.sched;
    const clock = this.clock;
    const tour = this.tour;
    if (tour) {
      const show = this.on.has("tourists") && s.tourists;
      let arrived = true;
      tour.people.forEach((p) => {
        const a = p.a;
        const d = p.d;
        a.live = show;
        if (!show || !d) return;
        d.latMag = g.width[d.lane] / 2 + PAVEMENT_OFF + p.dx * 0.6;
        if (tour.moving) g.step(d, dt, d.vMax, this.rng);
        a.x = d.x;
        a.z = d.z;
        a.yaw = d.yaw;
        a.walk = d.v > 0.2 ? 1 : 0;
        if (!d.arrived) arrived = false;
      });
      if (show && tour.moving && arrived) {
        tour.moving = false;
        tour.hold = 25 + this.rng() * 15;
      } else if (show && !tour.moving) {
        tour.hold -= dt;
        for (const p of tour.people) {
          p.a.walk = 0.35;
        }
        if (tour.hold <= 0) this.startTour(tour.leg === 0 ? 1 : 0);
      }
      this.syncUmbrellas(tour.people, ctx.rain, show);
    }

    const showPerf = this.on.has("performers") && s.performers;
    for (let i = 0; i < this.performers.length; i++) {
      const p = this.performers[i];
      const a = p.a;
      a.live = showPerf;
      if (!showPerf) continue;
      const centre = i === this.performers.length - 1;
      a.x = p.hx;
      a.z = p.hz;
      a.walk = centre ? 1 : 0.3;
      a.hands = centre ? Math.sin(clock * 3) > 0 : Math.sin(clock * 6 + i) > 0.7;
    }

    const showPub = this.on.has("pubs") && s.pub > 0;
    const k = s.disperse;
    for (let gi = 0; gi < this.pubGroups.length; gi++) {
      for (const p of this.pubGroups[gi]) {
        const a = p.a;
        a.live = showPub && k < 1;
        if (!a.live) continue;
        const run = k * (22 + gi * 9);
        a.x = p.hx + p.dx * run;
        a.z = p.hz + p.dz * run;
        a.walk = k > 0.02 ? 1 : 0.25;
        a.yaw += wrapAngle(Math.atan2(p.dx, p.dz) - a.yaw) * (k > 0.02 ? Math.min(1, dt * 4) : 0);
      }
    }
    for (const gl of this.glows) {
      gl.on =
        gl.kind === "all" ? this.on.has("performers") && s.performers
          : gl.kind === "kebab" ? this.on.has("pubs") && s.kebab
          : this.on.has("pubs") && s.pub > 0;
    }
  }

  private syncUmbrellas(people: Person[], rain: number, show: boolean) {
    for (let i = 0; i < people.length; i++) {
      const p = people[i];
      let u = this.umbrellaSlots[i];
      const wants = show && (i === 0 || (rain > 0.25 && i % 3 !== 1));
      if (wants && !u) {
        const n = this.alloc(K.UMBRELLA);
        if (n) {
          tint(n, i === 0 ? 0xe0342b : this.pick([0x1d1d22, 0x2b3a55, 0x6b2a2a, 0xe0342b]));
          this.umbrellaSlots[i] = u = n;
        }
      }
      if (!u) continue;
      u.live = wants;
      u.x = p.a.x;
      u.z = p.a.z;
      u.yaw = p.a.yaw;
    }
  }

  /** Rebuilds the disruption scenes (cones, barriers, parked emergency vehicles) from the live TfL list. */
  setDisruptions(items: AroundDisruption[]) {
    if (this.graph.empty) return;
    const key = items.map((d) => d.id).join("|");
    if (key === this.sceneKey) return;
    this.sceneKey = key;
    for (const a of this.sceneActors) a.live = false;
    this.sceneActors = [];
    for (const c of this.callouts) {
      if (c.scene) {
        c.a.live = false;
        c.mode = 3;
        c.scene = false;
      }
    }
    const list = [...items].sort((p, q) => p.distanceM - q.distanceM).slice(0, 6);
    const g = this.graph;
    list.forEach((it, idx) => {
      if (it.line.length < 1) return;
      const mid = it.line[Math.floor(it.line.length / 2)];
      const [x, z] = toLocal(mid[1], mid[0]);
      if (Math.hypot(x, z) > WORLD_M) return;
      if (!g.nearestLane(x, z, this.lane) || this.lane.dist > 40) return;
      const yaw = g.pose(this.lane.lane, this.lane.s, 0, this.out);
      const px = this.out[0];
      const pz = this.out[1];
      const text = `${it.category} ${it.comments} ${it.severity}`;
      const incident = /collision|accident|incident|breakdown|casualt|fire|hazard|emergency|flood/i.test(text);
      const fire = /fire|smoke/i.test(text) || (incident && idx % 3 === 0);
      const event = !incident && /event|demonstrat|parade|march|film|ceremony/i.test(text);
      if (incident && this.on.has("ambulance")) this.placeIncident(px, pz, yaw, fire);
      else if (event && this.on.has("police")) this.placeEvent(px, pz, yaw);
      else if (this.on.has("roadworks")) this.placeWorks(px, pz, yaw);
      if (incident && this.on.has("ambulance")) this.dispatchToScene(px, pz, yaw, fire);
    });
  }

  private at(px: number, pz: number, yaw: number, f: number, l: number, out: XZ) {
    out[0] = px + Math.sin(yaw) * f + Math.cos(yaw) * l;
    out[1] = pz + Math.cos(yaw) * f - Math.sin(yaw) * l;
  }

  private stat(kind: number, px: number, pz: number, yaw: number, f: number, l: number, turn = 0): Actor | null {
    const a = this.alloc(kind);
    if (!a) return null;
    this.at(px, pz, yaw, f, l, this.out);
    a.x = this.out[0];
    a.z = this.out[1];
    a.yaw = yaw + turn;
    this.sceneActors.push(a);
    return a;
  }

  private cone(px: number, pz: number, yaw: number, f: number, l: number) {
    const a = this.stat(K.CONE, px, pz, yaw, f, l, this.rng() * TAU);
    if (a) tint(a, 0xffffff);
  }

  private taper(px: number, pz: number, yaw: number, from: number, n: number, side: number) {
    for (let i = 0; i < n; i++) this.cone(px, pz, yaw, from + i * 2.2, side * 0.5 * (n - i));
  }

  private barrier(px: number, pz: number, yaw: number, f: number, l: number, turn: number) {
    const a = this.stat(K.BARRIER, px, pz, yaw, f, l, turn);
    if (a) tint(a, 0xffffff);
  }

  private standing(px: number, pz: number, yaw: number, f: number, l: number, hex: number) {
    const a = this.human(hex, 0, 0, yaw + this.rng() * 6);
    if (!a) return;
    this.at(px, pz, yaw, f, l, this.out);
    a.x = this.out[0];
    a.z = this.out[1];
    a.walk = 0.2;
    this.sceneActors.push(a);
  }

  private placeWorks(px: number, pz: number, yaw: number) {
    this.taper(px, pz, yaw, -20, 6, 1);
    for (let i = 0; i < 6; i++) this.cone(px, pz, yaw, -4 + i * 2.6, 2.6);
    this.barrier(px, pz, yaw, -3, 0.9, Math.PI / 2);
    this.barrier(px, pz, yaw, 11, 0.9, Math.PI / 2);
    this.barrier(px, pz, yaw, 4, 2.2, 0);
    const ex = this.stat(K.EXCAVATOR, px, pz, yaw, 3.5, 0.2, 0.4);
    if (ex) tint(ex, 0xffffff);
    for (let i = 0; i < 3; i++) this.standing(px, pz, yaw, -1 + i * 2.2, -1.6 + (i % 2) * 0.8, HIVIS);
  }

  private placeIncident(px: number, pz: number, yaw: number, fire: boolean) {
    this.taper(px, pz, yaw, -26, 7, 1);
    const pol = this.stat(K.POLICE, px, pz, yaw, -9, 1.2, 0.3);
    if (pol) pol.siren = true;
    const amb = this.stat(K.AMBULANCE, px, pz, yaw, 1.5, -1.4, 0);
    if (amb) amb.siren = true;
    if (fire) {
      const fe = this.stat(K.FIRE, px, pz, yaw, 13, 1.6, -0.1);
      if (fe) fe.siren = true;
      this.barrier(px, pz, yaw, 21, 0, Math.PI / 2);
    }
    this.standing(px, pz, yaw, -6, -0.6, POLICE_NAVY);
    this.standing(px, pz, yaw, -7.4, 2.4, HIVIS);
    this.standing(px, pz, yaw, 4.2, -2.6, PARAMEDIC);
    this.standing(px, pz, yaw, 0, -3.6, PARAMEDIC);
    this.barrier(px, pz, yaw, -12, 0, Math.PI / 2);
  }

  private placeEvent(px: number, pz: number, yaw: number) {
    for (let i = 0; i < 4; i++) this.barrier(px, pz, yaw, -8 + i * 2.2, 3.4, 0.2);
    const pol = this.stat(K.POLICE, px, pz, yaw, -14, 1.6, 0.2);
    if (pol) pol.siren = true;
    this.standing(px, pz, yaw, -4, 0, POLICE_NAVY);
    this.standing(px, pz, yaw, -3, 2, HIVIS);
  }

  private dispatchToScene(px: number, pz: number, yaw: number, fire: boolean) {
    const free = this.callouts.find((c) => c.mode === 3);
    if (!free) return;
    const g = this.graph;
    const target = g.nearestNode(px, pz);
    for (let tries = 0; tries < 8; tries++) {
      const from = g.from[this.randomLane()];
      if (Math.hypot(g.nodeX(from) - px, g.nodeZ(from) - pz) < 220) continue;
      this.at(px, pz, yaw, fire ? -18 : 7, 2.6, this.out);
      const park: [number, number, number] = [this.out[0], this.out[1], yaw + 0.2];
      if (this.dispatch(fire ? K.FIRE : K.AMBULANCE, from, target, free, true, park)) {
        free.wait = 4 + this.rng() * 6;
        free.a.live = false;
        return;
      }
    }
  }
}
