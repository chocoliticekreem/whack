/**
 * Per-frame motion quality counters, read via `window.__motionStats`: single-frame jumps over
 * TELEPORT_M, vehicle pairs closer than VEHICLE_OVERLAP_M on the same lane and people closer than
 * PERSON_OVERLAP_M. Poses are recorded per entity key into fixed buffers; nothing allocates per frame.
 */

const TELEPORT_M = 3;
const VEHICLE_OVERLAP_M = 4;
const PERSON_OVERLAP_M = 0.5;
const CAP = 1024;

export const motionStats = {
  frames: 0,
  teleports: 0,
  vehicleOverlapPairs: 0,
  peopleOverlapPairs: 0,
  maxVehicleOverlap: 0,
  maxPeopleOverlap: 0,
  entities: 0,
  spawned: 0,
  handovers: 0,
  duplicatesDropped: 0,
  reset() {
    this.frames = this.teleports = this.vehicleOverlapPairs = this.peopleOverlapPairs = 0;
    this.maxVehicleOverlap = this.maxPeopleOverlap = 0;
    this.spawned = this.handovers = this.duplicatesDropped = 0;
  },
};

const keys: (string | null)[] = new Array(CAP).fill(null);
const px = new Float32Array(CAP);
const pz = new Float32Array(CAP);
const lane = new Int32Array(CAP);
const person = new Uint8Array(CAP);
const prev = new Map<string, number>();
let n = 0;

export function beginFrame() {
  n = 0;
}

export function recordPose(key: string, x: number, z: number, laneId: number, isPerson: boolean) {
  if (n >= CAP) return;
  const slot = prev.get(key);
  if (slot !== undefined && keys[slot] === key && Math.hypot(x - px[slot], z - pz[slot]) > TELEPORT_M) motionStats.teleports++;
  keys[n] = key;
  px[n] = x;
  pz[n] = z;
  lane[n] = laneId;
  person[n] = isPerson ? 1 : 0;
  n++;
}

export function endFrame() {
  let v = 0;
  let p = 0;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (person[i] !== person[j]) continue;
      const dx = px[i] - px[j];
      const dz = pz[i] - pz[j];
      const d2 = dx * dx + dz * dz;
      if (person[i]) {
        if (d2 < PERSON_OVERLAP_M * PERSON_OVERLAP_M) p++;
      } else if (lane[i] >= 0 && lane[i] === lane[j] && d2 < VEHICLE_OVERLAP_M * VEHICLE_OVERLAP_M) v++;
    }
  }
  motionStats.frames++;
  motionStats.vehicleOverlapPairs += v;
  motionStats.peopleOverlapPairs += p;
  if (v > motionStats.maxVehicleOverlap) motionStats.maxVehicleOverlap = v;
  if (p > motionStats.maxPeopleOverlap) motionStats.maxPeopleOverlap = p;
  motionStats.entities = n;
  for (let i = 0; i < n; i++) prev.set(keys[i] as string, i);
}

if (typeof window !== "undefined") (window as unknown as { __motionStats: typeof motionStats }).__motionStats = motionStats;
