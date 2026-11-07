import { mulberry32, wrapAngle } from "../sim/rng";
import { LANES_M, river, sampleRiver, type RiverSample } from "../river";
import { waveHeight, waveSlope } from "../../scene/Water";
import { SPECS, type BoatKind, type BoatSpec } from "./models";

const STOP_M = 60;
const PAST_M = 80;
const OPEN_LEAD_S = 60;
const OPEN_S = 25;
const CLOSE_S = 25;
/** After this long fully open, boats still short of the stop line must wait for the next lift. */
const HOLD_S = 45;
const GATE_OPEN = 0.88;
/** A tall boat this far past the stop line is committed to the span and is never stopped. */
const COMMITTED_M = 8;
const ENTRY_M = 30;
const WATER_Y = 0.14;

/** The swell from scene/Water is small (about +-0.2 m), so hulls answer it with extra gain to read from the aerial camera. */
const HEAVE_GAIN = 1.2;
const TILT_GAIN = 0.5;
/** Bow lift per m/s of speed, radians. */
const BOW_LIFT = 0.0035;

const POPULATION: [BoatKind, number][] = [
  ["clipper", 4],
  ["rib", 2],
  ["sleek", 2],
  ["cruise", 3],
  ["tug", 2],
];
/** Lighters towed behind each tug, in tug order. */
const TOWS = [2, 1];

export interface Boat {
  kind: BoatKind;
  spec: BoatSpec;
  /** Instance index within its kind's InstancedMesh. */
  slot: number;
  /** Distance along the centreline from the west end. */
  s: number;
  dir: 1 | -1;
  laneIdx: 0 | 1 | 2;
  /** Signed lane offset: positive = right of west -> east (south side), so eastbound boats are positive. */
  lane: number;
  speed: number;
  vmax: number;
  half: number;
  /** Extent behind the midship point including towed barges. */
  tail: number;
  /** Towed boats follow `leader` at a fixed distance. */
  leader: Boat | null;
  gone: boolean;
  respawn: number;
  posed: boolean;
  turn: number;
  yaw: number;
  x: number;
  y: number;
  z: number;
  pitch: number;
  roll: number;
  /** 0..1 scale: grows in at the river ends and when respawning. */
  fade: number;
}

export interface Fleet {
  boats: Boat[];
  time: number;
  /** Clock the water shader runs on (`uTime`), so hulls and surface stay in phase. */
  seaTime: number;
  rng: () => number;
  forceLift: boolean;
  lift: { cooldown: number; target: Boat | null; progress: number; held: number };
}

const P0: RiverSample = { x: 0, z: 0, tx: 0, tz: 0, hw: 0 };
const SLOPE = { x: 0, z: 0 };
const P1: RiverSample = { x: 0, z: 0, tx: 0, tz: 0, hw: 0 };

function makeBoat(f: Fleet, kind: BoatKind, slot: number, dir: 1 | -1): Boat {
  const spec = SPECS[kind];
  const vmax = spec.speed[0] + f.rng() * (spec.speed[1] - spec.speed[0]);
  return {
    kind,
    spec,
    slot,
    s: 0,
    dir,
    laneIdx: spec.lane,
    lane: dir * LANES_M[spec.lane],
    speed: vmax,
    vmax,
    half: spec.length / 2,
    tail: spec.length / 2,
    leader: null,
    gone: false,
    respawn: 0,
    posed: false,
    turn: 0,
    yaw: 0,
    x: 0,
    y: WATER_Y,
    z: 0,
    pitch: 0,
    roll: 0,
    fade: 1,
  };
}

/** Builds the fleet and spreads it along the river. Needs `setRiver` to have been called. */
export function createFleet(forceLift: boolean): Fleet {
  const f: Fleet = {
    boats: [],
    time: 0,
    seaTime: 0,
    rng: mulberry32(0xb0a7),
    forceLift,
    lift: { cooldown: 0, target: null, progress: 0, held: 0 },
  };
  f.lift.cooldown = forceLift ? 0 : 15 + f.rng() * 30;
  river.bridgeOpen = 0;
  river.liftPhase = "closed";
  river.liftEta = f.lift.cooldown;

  const slots = new Map<BoatKind, number>();
  const take = (kind: BoatKind) => {
    const slot = slots.get(kind) ?? 0;
    slots.set(kind, slot + 1);
    return slot;
  };
  let tugs = 0;
  for (const [kind, count] of POPULATION) {
    for (let i = 0; i < count; i++) {
      const dir: 1 | -1 = i % 2 === 0 ? 1 : -1;
      const boat = makeBoat(f, kind, take(kind), dir);
      f.boats.push(boat);
      if (kind !== "tug") continue;
      let leader = boat;
      for (let k = 0; k < TOWS[tugs]; k++) {
        const barge = makeBoat(f, "lighter", take("lighter"), dir);
        barge.leader = leader;
        barge.speed = barge.vmax = boat.vmax;
        boat.tail += 6 + barge.spec.length;
        f.boats.push(barge);
        leader = barge;
      }
      tugs++;
    }
  }

  const groups = new Map<number, Boat[]>();
  for (const b of f.boats) {
    if (b.leader) continue;
    const key = b.dir * 3 + b.laneIdx;
    const g = groups.get(key) ?? [];
    g.push(b);
    groups.set(key, g);
  }
  const L = river.length;
  groups.forEach((g) => {
    const slot = L / g.length;
    g.forEach((b, i) => {
      b.s = slot * (i + 0.15 + f.rng() * 0.7);
      if (b.spec.tall && Math.abs(b.s - river.bridgeS) < 220) {
        b.s = river.bridgeS + (b.s < river.bridgeS ? -1 : 1) * (220 + f.rng() * 40);
      }
      b.s = Math.min(Math.max(b.s, 40), L - 40);
    });
  });
  placeTowed(f);
  for (const b of f.boats) pose(f, b, 1 / 60);
  return f;
}

function placeTowed(f: Fleet) {
  for (const b of f.boats) {
    const lead = b.leader;
    if (!lead) continue;
    b.s = lead.s - lead.dir * (lead.half + 6 + b.half);
    b.gone = lead.gone;
    b.speed = lead.speed;
  }
}

function toBridge(b: Boat) {
  return (river.bridgeS - b.s) * b.dir;
}

/** Seconds until `b` reaches the stop line before the bridge. */
function etaOf(b: Boat) {
  const d = toBridge(b) - STOP_M - b.half;
  return d <= 0 ? 0 : d / Math.max(b.speed, b.vmax * 0.7);
}

function pickLiftTarget(f: Fleet) {
  let best: Boat | null = null;
  let bestD = Infinity;
  for (let i = 0; i < f.boats.length; i++) {
    const b = f.boats[i];
    if (!b.spec.tall || b.gone) continue;
    const d = toBridge(b);
    if (d > 0 && d < bestD) {
      bestD = d;
      best = b;
    }
  }
  if (best && f.forceLift && etaOf(best) > 70) {
    best.s = river.bridgeS - best.dir * (STOP_M + best.half + 70 * best.vmax * 0.8);
    best.speed = best.vmax * 0.8;
  }
  if (best) f.forceLift = false;
  return best;
}

function liftClear(f: Fleet) {
  for (let i = 0; i < f.boats.length; i++) {
    const b = f.boats[i];
    if (!b.spec.tall || b.gone) continue;
    const rel = -toBridge(b);
    const from = f.lift.held > HOLD_S ? -(STOP_M + b.half - COMMITTED_M) : -(STOP_M + b.half + 25);
    if (rel > from && rel < PAST_M + b.half) return false;
  }
  return true;
}

function stepLift(f: Fleet, dt: number) {
  const l = f.lift;
  river.liftEta = 0;
  if (river.liftPhase === "closed") {
    if (!l.target) {
      l.cooldown -= dt;
      river.liftEta = Math.max(0, l.cooldown);
      if (l.cooldown <= 0) l.target = pickLiftTarget(f);
    }
    if (l.target) {
      const eta = etaOf(l.target);
      river.liftEta = Math.max(0, eta - OPEN_LEAD_S);
      if (l.target.gone) l.target = null;
      else if (eta <= OPEN_LEAD_S) river.liftPhase = "opening";
    }
  } else if (river.liftPhase === "opening") {
    l.progress = Math.min(1, l.progress + dt / OPEN_S);
    if (l.progress >= 1) {
      river.liftPhase = "open";
      l.held = 0;
    }
  } else if (river.liftPhase === "open") {
    l.held += dt;
    if (liftClear(f)) river.liftPhase = "closing";
  } else {
    l.progress = Math.max(0, l.progress - dt / CLOSE_S);
    if (l.progress <= 0) {
      river.liftPhase = "closed";
      l.target = null;
      l.cooldown = 45 + f.rng() * 45;
    }
  }
  river.bridgeOpen = l.progress * l.progress * (3 - 2 * l.progress);
}

function pose(f: Fleet, b: Boat, dt: number) {
  const L = river.length;
  const look = Math.max(4, b.half * 0.5);
  sampleRiver(b.s, b.lane, P0);
  sampleRiver(b.s + b.dir * look, b.lane, P1);
  const target = Math.atan2(P1.x - P0.x, P1.z - P0.z);
  if (!b.posed) {
    b.yaw = target;
    b.posed = true;
  } else {
    const dy = wrapAngle(target - b.yaw);
    b.turn += (dy / Math.max(dt, 1e-3) - b.turn) * Math.min(1, dt * 3);
    b.yaw += dy * Math.min(1, dt * 4);
  }
  const fx = Math.sin(b.yaw);
  const fz = Math.cos(b.yaw);
  waveSlope(P0.x, P0.z, f.seaTime, SLOPE);
  const slopeFwd = SLOPE.x * fx + SLOPE.z * fz;
  const slopePort = SLOPE.x * fz - SLOPE.z * fx;
  const lean = Math.max(-0.12, Math.min(0.12, b.turn * b.speed * 0.005 * b.spec.lean));
  b.x = P0.x;
  b.z = P0.z;
  b.y = WATER_Y + waveHeight(P0.x, P0.z, f.seaTime) * HEAVE_GAIN * b.spec.bob;
  b.roll = slopePort * TILT_GAIN * b.spec.bob - lean;
  b.pitch = -slopeFwd * TILT_GAIN * b.spec.bob - Math.min(b.speed, 12) * BOW_LIFT;
  const edge = Math.min(b.s, L - b.s);
  const k = Math.min(1, Math.max(0, (edge + 30) / 70));
  b.fade = b.gone ? 0 : k * k * (3 - 2 * k);
}

function entryClear(f: Fleet, b: Boat, spawnS: number) {
  for (let i = 0; i < f.boats.length; i++) {
    const o = f.boats[i];
    if (o === b || o.leader || o.gone || o.dir !== b.dir || o.laneIdx !== b.laneIdx) continue;
    if (Math.abs(o.s - spawnS) < 90 + o.tail + b.tail) return false;
  }
  return true;
}

function stepBoat(f: Fleet, b: Boat, dt: number) {
  const L = river.length;
  if (b.gone) {
    b.respawn -= dt;
    const spawnS = b.dir > 0 ? -ENTRY_M : L + ENTRY_M;
    if (b.respawn <= 0 && entryClear(f, b, spawnS)) {
      b.gone = false;
      b.s = spawnS;
      b.vmax = b.spec.speed[0] + f.rng() * (b.spec.speed[1] - b.spec.speed[0]);
      b.speed = b.vmax * 0.8;
    }
    return;
  }

  let vTarget = b.vmax;
  let gap = Infinity;
  let leadSpeed = 0;
  for (let i = 0; i < f.boats.length; i++) {
    const o = f.boats[i];
    if (o === b || o.leader || o.gone || o.dir !== b.dir || o.laneIdx !== b.laneIdx) continue;
    const ahead = (o.s - b.s) * b.dir;
    if (ahead <= 0) continue;
    const g = ahead - b.half - o.tail;
    if (g < gap) {
      gap = g;
      leadSpeed = o.speed;
    }
  }
  if (gap < 70) vTarget = Math.min(vTarget, Math.max(0, leadSpeed * Math.min(1, (gap - 6) / 25) + (gap - 6) * 0.1));

  const gateShut = river.bridgeOpen < GATE_OPEN || river.liftPhase === "closing" || (river.liftPhase === "open" && f.lift.held > HOLD_S);
  if (b.spec.tall && gateShut) {
    const d = toBridge(b) - STOP_M - b.half;
    if (toBridge(b) > 0 && d > -COMMITTED_M) vTarget = Math.min(vTarget, d > 0 ? Math.sqrt(2 * d) : 0);
  }

  const dv = vTarget - b.speed;
  b.speed += Math.max(-2 * dt, Math.min(0.8 * dt, dv));
  b.s += b.dir * b.speed * dt;

  if (b.dir > 0 ? b.s - b.tail > L + 40 : b.s + b.tail < -40) {
    b.gone = true;
    b.respawn = 2 + f.rng() * 16;
  }
}

/** Advances every vessel and the bridge-lift schedule by `dt` seconds of sim time. */
export function stepFleet(f: Fleet, dt: number, seaTime: number) {
  f.time += dt;
  f.seaTime = seaTime;
  stepLift(f, dt);
  for (let i = 0; i < f.boats.length; i++) {
    if (!f.boats[i].leader) stepBoat(f, f.boats[i], dt);
  }
  placeTowed(f);
  for (let i = 0; i < f.boats.length; i++) pose(f, f.boats[i], dt);
}
