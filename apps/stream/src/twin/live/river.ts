import { toLocal, type Ring } from "../config";

export type LiftPhase = "closed" | "opening" | "open" | "closing";

/**
 * Plain mutable state of the Tower Bridge lift. The boat scheduler writes it every frame; the bascule
 * animation and any HUD only read it (never zustand: it changes every frame).
 */
export const river = {
  /** Eased 0 (deck down) .. 1 (bascules fully raised). */
  bridgeOpen: 0,
  /** Centreline x / z where the river passes under Tower Bridge, metres. */
  bridgeX: 0,
  bridgeZ: 0,
  /** Distance along the river centreline (west -> east) at the bridge, metres. */
  bridgeS: 0,
  liftPhase: "closed" as LiftPhase,
  /** While closed: seconds until the next lift starts opening. 0 in every other phase. */
  liftEta: 0,
  /** Centreline length in metres (0 until the geometry is loaded). */
  length: 0,
};

export interface RiverPath {
  length: number;
  step: number;
  count: number;
  xs: Float32Array;
  zs: Float32Array;
  /** Unit tangent of the west -> east direction. */
  tx: Float32Array;
  tz: Float32Array;
  /** Distance from the centreline to the nearest bank. */
  hw: Float32Array;
}

export interface RiverSample {
  x: number;
  z: number;
  /** Unit tangent, west -> east. Reverse it for westbound traffic. */
  tx: number;
  tz: number;
  hw: number;
}

const STEP_M = 4;
/** Tower Bridge landmark (landmarks.ts): the channel between its towers is centred here, not on the medial line. */
const BRIDGE_LAT = 51.50552;
const BRIDGE_LON = -0.07537;
/** Beyond this |x| the water polygon is cut by the tile box, not by a bank. */
const CLIP_X = 730;
const MIN_REACH_M = 150;
/** Lane offsets used by traffic (inner, middle, outer), metres from the centreline. */
export const LANES_M = [16, 34, 52];
/** Where those lanes squeeze to under the bridge, so even the widest yacht clears the towers (central span is about +-33 m). */
const BRIDGE_LANES_M = [5.5, 12, 20];
const BRIDGE_FUNNEL_FROM_M = 60;
const BRIDGE_FUNNEL_LEN_M = 220;

let active: RiverPath | null = null;
/** Lateral shift (right of west -> east) that centres traffic on the bridge's central span. */
let bridgeBias = 0;

function ringArea(ring: Ring) {
  let a = 0;
  for (let i = 0; i < ring.length; i++) {
    const [ax, az] = ring[i];
    const [bx, bz] = ring[(i + 1) % ring.length];
    a += ax * bz - bx * az;
  }
  return Math.abs(a) / 2;
}

/** In-water intervals (as offsets along n) of the line o + u*n, sorted, as [u0, u1, u0, u1...]. */
function sectionIntervals(ring: Ring, ox: number, oz: number, nx: number, nz: number, out: number[]) {
  out.length = 0;
  const us: number[] = [];
  for (let i = 0; i < ring.length; i++) {
    const [ax, az] = ring[i];
    const [bx, bz] = ring[(i + 1) % ring.length];
    const da = (ax - ox) * -nz + (az - oz) * nx;
    const db = (bx - ox) * -nz + (bz - oz) * nx;
    if (da > 0 === db > 0) continue;
    const k = da / (da - db);
    us.push((ax + (bx - ax) * k - ox) * nx + (az + (bz - az) * k - oz) * nz);
  }
  us.sort((a, b) => a - b);
  for (let i = 0; i + 1 < us.length; i += 2) out.push(us[i], us[i + 1]);
}

function chaikin(pts: [number, number][], rounds: number) {
  let cur = pts;
  for (let r = 0; r < rounds; r++) {
    const next: [number, number][] = [cur[0]];
    for (let i = 0; i + 1 < cur.length; i++) {
      const [ax, az] = cur[i];
      const [bx, bz] = cur[i + 1];
      next.push([ax * 0.75 + bx * 0.25, az * 0.75 + bz * 0.25], [ax * 0.25 + bx * 0.75, az * 0.25 + bz * 0.75]);
    }
    next.push(cur[cur.length - 1]);
    cur = next;
  }
  return cur;
}

function relax(pts: [number, number][], passes: number) {
  let cur = pts;
  for (let r = 0; r < passes; r++) {
    cur = cur.map((p, i) => {
      if (i === 0 || i === cur.length - 1) return p;
      return [cur[i - 1][0] * 0.25 + p[0] * 0.5 + cur[i + 1][0] * 0.25, cur[i - 1][1] * 0.25 + p[1] * 0.5 + cur[i + 1][1] * 0.25];
    });
  }
  return cur;
}

function resample(pts: [number, number][], step: number) {
  const out: [number, number][] = [pts[0]];
  let carry = 0;
  for (let i = 0; i + 1 < pts.length; i++) {
    const [ax, az] = pts[i];
    const [bx, bz] = pts[i + 1];
    const len = Math.hypot(bx - ax, bz - az);
    let d = step - carry;
    while (d <= len) {
      out.push([ax + ((bx - ax) * d) / len, az + ((bz - az) * d) / len]);
      d += step;
    }
    carry = len - (d - step);
  }
  return out;
}

function tangentAt(pts: [number, number][], i: number, reach: number): [number, number] {
  const a = pts[Math.max(0, i - reach)];
  const b = pts[Math.min(pts.length - 1, i + reach)];
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  return [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
}

function distToEdges(ring: Ring, px: number, pz: number) {
  let best = Infinity;
  for (let i = 0; i < ring.length; i++) {
    const [ax, az] = ring[i];
    const [bx, bz] = ring[(i + 1) % ring.length];
    if (Math.abs(ax) > CLIP_X && Math.abs(bx) > CLIP_X) continue;
    const vx = bx - ax;
    const vz = bz - az;
    const len2 = vx * vx + vz * vz;
    let t = len2 > 1e-9 ? ((px - ax) * vx + (pz - az) * vz) / len2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const d = Math.hypot(ax + vx * t - px, az + vz * t - pz);
    if (d < best) best = d;
  }
  return best;
}

/**
 * Derives the navigable centreline from the largest water polygon (the Thames): cross-sections across the
 * main flow axis give a rough medial axis, which is smoothed and then re-centred with sections that are
 * perpendicular to the curve itself. Pure: no module state is touched.
 */
export function deriveRiver(water: Ring[]): RiverPath | null {
  let ring: Ring | null = null;
  let bestArea = 0;
  for (const r of water) {
    const a = r.length >= 3 ? ringArea(r) : 0;
    if (a > bestArea) {
      bestArea = a;
      ring = r;
    }
  }
  if (!ring) return null;

  let mx = 0;
  let mz = 0;
  for (const [x, z] of ring) {
    mx += x;
    mz += z;
  }
  mx /= ring.length;
  mz /= ring.length;
  let sxx = 0;
  let szz = 0;
  let sxz = 0;
  for (const [x, z] of ring) {
    sxx += (x - mx) ** 2;
    szz += (z - mz) ** 2;
    sxz += (x - mx) * (z - mz);
  }
  const angle = 0.5 * Math.atan2(2 * sxz, sxx - szz);
  const dx = Math.cos(angle) * (Math.cos(angle) < 0 ? -1 : 1);
  const dz = Math.sin(angle) * (Math.cos(angle) < 0 ? -1 : 1);

  let tMin = Infinity;
  let tMax = -Infinity;
  for (const [x, z] of ring) {
    const t = (x - mx) * dx + (z - mz) * dz;
    tMin = Math.min(tMin, t);
    tMax = Math.max(tMax, t);
  }

  const iv: number[] = [];
  const rough: [number, number][] = [];
  for (let t = tMin; t <= tMax; t += 20) {
    const ox = mx + dx * t;
    const oz = mz + dz * t;
    sectionIntervals(ring, ox, oz, -dz, dx, iv);
    let best = -1;
    let bestLen = MIN_REACH_M;
    for (let k = 0; k < iv.length; k += 2) {
      if (iv[k + 1] - iv[k] > bestLen) {
        bestLen = iv[k + 1] - iv[k];
        best = k;
      }
    }
    if (best < 0) continue;
    const u = (iv[best] + iv[best + 1]) / 2;
    rough.push([ox - dz * u, oz + dx * u]);
  }
  if (rough.length < 4) return null;

  let line = resample(chaikin(rough, 2), 20);
  for (let pass = 0; pass < 2; pass++) {
    const moved = line.map(([x, z], i): [number, number] => {
      const [tx, tz] = tangentAt(line, i, 2);
      sectionIntervals(ring, x, z, -tz, tx, iv);
      let best = -1;
      let bestDist = Infinity;
      for (let k = 0; k < iv.length; k += 2) {
        const d = iv[k] <= 0 && iv[k + 1] >= 0 ? 0 : Math.min(Math.abs(iv[k]), Math.abs(iv[k + 1]));
        if (d < bestDist) {
          bestDist = d;
          best = k;
        }
      }
      if (best < 0 || iv[best + 1] - iv[best] < MIN_REACH_M) return [x, z];
      const u = (iv[best] + iv[best + 1]) / 2;
      return [x - tz * u, z + tx * u];
    });
    line = resample(chaikin(relax(moved, 6), 2), pass === 1 ? STEP_M : 20);
  }

  // Near the tile edge the polygon opens into a bay and the medial line hooks away from the flow: cut it there.
  const mid = line.length >> 1;
  let lo = 0;
  let hi = line.length - 1;
  for (let i = mid; i < line.length; i++) {
    const [tx, tz] = tangentAt(line, i, 3);
    if (tx * dx + tz * dz < 0.6) {
      hi = i - 12;
      break;
    }
  }
  for (let i = mid; i >= 0; i--) {
    const [tx, tz] = tangentAt(line, i, 3);
    if (tx * dx + tz * dz < 0.6) {
      lo = i + 12;
      break;
    }
  }
  line = line.slice(lo, hi + 1);

  const count = line.length;
  const path: RiverPath = {
    length: (count - 1) * STEP_M,
    step: STEP_M,
    count,
    xs: new Float32Array(count),
    zs: new Float32Array(count),
    tx: new Float32Array(count),
    tz: new Float32Array(count),
    hw: new Float32Array(count),
  };
  const rawHw = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const [tx, tz] = tangentAt(line, i, 3);
    path.xs[i] = line[i][0];
    path.zs[i] = line[i][1];
    path.tx[i] = tx;
    path.tz[i] = tz;
    rawHw[i] = distToEdges(ring, line[i][0], line[i][1]);
  }
  for (let i = 0; i < count; i++) {
    let sum = 0;
    let n = 0;
    for (let j = Math.max(0, i - 8); j <= Math.min(count - 1, i + 8); j++) {
      sum += rawHw[j];
      n++;
    }
    path.hw[i] = sum / n;
  }
  return path;
}

/** Installs a derived path as the one `sampleRiver` reads and locates Tower Bridge on it. */
export function setRiver(next: RiverPath | null) {
  active = next;
  if (!next) {
    river.length = 0;
    return;
  }
  const [bx, bz] = toLocal(BRIDGE_LAT, BRIDGE_LON);
  let nearest = 0;
  let bestD = Infinity;
  for (let i = 0; i < next.count; i++) {
    const d = (next.xs[i] - bx) ** 2 + (next.zs[i] - bz) ** 2;
    if (d < bestD) {
      bestD = d;
      nearest = i;
    }
  }
  river.length = next.length;
  river.bridgeS = nearest * next.step;
  river.bridgeX = next.xs[nearest];
  river.bridgeZ = next.zs[nearest];
  bridgeBias = (bx - next.xs[nearest]) * -next.tz[nearest] + (bz - next.zs[nearest]) * next.tx[nearest];
}

/**
 * Position on the river `s` metres from the west end, `lane` metres to the right of the west -> east
 * heading (negative = left, i.e. the starboard lane of westbound traffic). Lanes funnel into the central
 * span (centred on the bridge) near Tower Bridge and never reach the banks. Beyond either end the line continues straight.
 */
export function sampleRiver(s: number, lane: number, out: RiverSample) {
  const p = active;
  if (!p) return out;
  const f = Math.min(Math.max(s / p.step, 0), p.count - 1);
  const i = Math.min(Math.floor(f), p.count - 2);
  const k = f - i;
  let tx = p.tx[i] + (p.tx[i + 1] - p.tx[i]) * k;
  let tz = p.tz[i] + (p.tz[i + 1] - p.tz[i]) * k;
  const tl = Math.hypot(tx, tz) || 1;
  tx /= tl;
  tz /= tl;
  const overshoot = s < 0 ? s : s > p.length ? s - p.length : 0;
  const hw = p.hw[i] + (p.hw[i + 1] - p.hw[i]) * k;
  const squeeze = Math.max(0, Math.min(1, 1 - (Math.abs(s - river.bridgeS) - BRIDGE_FUNNEL_FROM_M) / BRIDGE_FUNNEL_LEN_M));
  const mag = Math.abs(lane);
  let narrowed = mag;
  for (let k = 0; k < LANES_M.length; k++) {
    const lo = k ? LANES_M[k - 1] : 0;
    if (mag <= LANES_M[k]) {
      const blo = k ? BRIDGE_LANES_M[k - 1] : 0;
      narrowed = blo + ((mag - lo) / (LANES_M[k] - lo)) * (BRIDGE_LANES_M[k] - blo);
      break;
    }
  }
  const shaped = lane < 0 ? -narrowed : narrowed;
  const lateral = Math.max(-hw * 0.72, Math.min(hw * 0.72, lane + (shaped - lane) * squeeze + bridgeBias * squeeze));
  out.x = p.xs[i] + (p.xs[i + 1] - p.xs[i]) * k + tx * overshoot - tz * lateral;
  out.z = p.zs[i] + (p.zs[i + 1] - p.zs[i]) * k + tz * overshoot + tx * lateral;
  out.tx = tx;
  out.tz = tz;
  out.hw = hw;
  return out;
}
