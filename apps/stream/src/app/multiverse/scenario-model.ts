/**
 * Scenario mode: the what-if contract (POST /api/sim/scenario), the adapter for the sim's dry-run
 * POST /scenario, and a stand-in model the proxy answers with when an older sim has no /scenario.
 */
import type { Hotspot, Market } from "./city-types";

export const HORIZONS = [15, 60] as const;
export type Horizon = (typeof HORIZONS)[number];
export type HKey = `+${Horizon}`;

export type Where = { lat: number; lon: number; radiusM: number } | { naptan: string };

/** Same shape as the sim's POST /intervene, minus mode and weight: a scenario never touches live worlds. */
export interface Intervention {
  kind: "closure" | "capacity" | "demand_surge";
  where: Where;
  /** Minutes from now. */
  start: number;
  end: number;
  magnitude: number;
  note?: string;
}

export interface ScenarioRequest {
  interventions: Intervention[];
  horizons: number[];
}

export type Pair = Record<HKey, number | null>;
const EMPTY: Pair = { "+15": null, "+60": null };

export interface StationResult {
  naptan: string;
  name: string;
  hub: string | null;
  lat: number;
  lon: number;
  before: Pair;
  after: Pair;
}

export interface HubResult {
  hub: string;
  name: string;
  before: Pair;
  after: Pair;
  /** People on the hub's streets; a street closure moves these, not station busyness. */
  peopleBefore: Pair;
  peopleAfter: Pair;
}

export interface ScenarioResponse {
  mock: boolean;
  asOf: number;
  unit: string;
  stations: StationResult[];
  hubs: HubResult[];
}

/* ---------- sim /state, the parts read here ---------- */

interface Band {
  mean: number | null;
  people?: { mean: number };
}
interface ForecastRow {
  naptan?: string;
  hub?: string | null;
  name: string;
  "+15"?: Band;
  "+60"?: Band;
}
export interface SimEvent {
  id: string;
  kind: string;
  reason?: string;
  planned?: boolean;
  start?: number;
  end?: number | null;
  inSim?: boolean;
}
export interface SimState {
  forecast?: { unit?: string; stations: ForecastRow[]; hubs: ForecastRow[] };
  zones?: { id: string; name: string; x: number; z: number; kind: string }[];
  graph?: { bbox?: [number, number, number, number] };
  events?: SimEvent[];
  seedAt?: number;
  minute?: number;
}

const M_PER_DEG = 111_320;

/** The sim's local frame is metres east (x) and south (z) of a seed origin that /state does not publish. */
interface Origin {
  lat: number;
  lon: number;
}

const kx = (o: Origin) => M_PER_DEG * Math.cos((o.lat * Math.PI) / 180);
const toLocal = (o: Origin, lat: number, lon: number) => ({ x: (lon - o.lon) * kx(o), z: -(lat - o.lat) * M_PER_DEG });
const toLatLon = (o: Origin, x: number, z: number) => ({ lat: o.lat - z / M_PER_DEG, lon: o.lon + x / kx(o) });

export const normName = (s: string) => s.toLowerCase().replace(/[^a-z]/g, "");

/** Recovers the origin from hubs whose lat/lon the city engine publishes and whose x/z the sim does. */
export function fitOrigin(state: SimState, hubs: Hotspot[]): Origin {
  const zones = state.zones ?? [];
  const fits: Origin[] = [];
  for (const h of hubs) {
    const z = zones.find((zz) => zz.kind === "station" && normName(zz.name) === normName(h.name));
    if (!z) continue;
    const lat = h.lat + z.z / M_PER_DEG;
    fits.push({ lat, lon: h.lon - z.x / (M_PER_DEG * Math.cos((lat * Math.PI) / 180)) });
  }
  if (fits.length) {
    return { lat: fits.reduce((s, f) => s + f.lat, 0) / fits.length, lon: fits.reduce((s, f) => s + f.lon, 0) / fits.length };
  }
  const b = state.graph?.bbox ?? [51.4995, -0.16, 51.5345, -0.084];
  return { lat: (b[0] + b[2]) / 2, lon: (b[1] + b[3]) / 2 };
}

export interface SimStation {
  naptan: string;
  name: string;
  hub: string | null;
  lat: number;
  lon: number;
  x: number;
  z: number;
}

/** Stations with coordinates: the sim's zones placed with the fitted origin, keyed by the forecast's NaPTANs. */
export function simStations(state: SimState, hubs: Hotspot[]): SimStation[] {
  const o = fitOrigin(state, hubs);
  const zones = state.zones ?? [];
  const out: SimStation[] = [];
  for (const f of state.forecast?.stations ?? []) {
    const z = zones.find((zz) => zz.kind === "station" && zz.name === f.name);
    if (!z || !f.naptan) continue;
    out.push({ naptan: f.naptan, name: f.name, hub: f.hub ?? null, ...toLatLon(o, z.x, z.z), x: z.x, z: z.z });
  }
  return out;
}

/* ---------- the stand-in model (only when the sim has no /scenario) ---------- */

const STREET_LOSS = 0.45;
const NEAR_M = 200;
const SURGE_DECAY_M = 400;
const SURGE_REACH_M = 1200;
const RECEIVE_M = 1600;
const KEPT = 0.8;
const TAIL_MIN = 10;

function activeAt(iv: Intervention, h: number): number {
  if (h < iv.start) return 0;
  if (h <= iv.end) return 1;
  return Math.exp(-(h - iv.end) / TAIL_MIN);
}

const round1 = (v: number) => Math.round(v * 10) / 10;

/**
 * Street closure: stations by the closed streets lose riders, who walk to the nearest unaffected stations.
 * Station closure or capacity cut: the station loses (1 - magnitude) of its riders to its two nearest
 * neighbours. Crowd surge: central.py's falloff, full inside the radius and exp(-(d - r) / 400 m) to 1.2 km.
 */
function applyOne(iv: Intervention, st: SimStation[], vals: number[], h: number, o: Origin): number[] {
  const a = activeAt(iv, h);
  if (a === 0) return vals;
  const out = [...vals];
  const atStation = "naptan" in iv.where;
  const centre = "naptan" in iv.where ? (st.find((s) => s.naptan === (iv.where as { naptan: string }).naptan) ?? null) : toLocal(o, iv.where.lat, iv.where.lon);
  if (!centre) return vals;
  const r = "radiusM" in iv.where ? iv.where.radiusM : 0;
  const dist = st.map((s) => Math.hypot(s.x - centre.x, s.z - centre.z));

  if (iv.kind === "demand_surge") {
    st.forEach((_, i) => {
      const d = dist[i];
      const w = d <= r ? 1 : d <= r + SURGE_REACH_M ? Math.exp(-(d - r) / SURGE_DECAY_M) : 0;
      out[i] = vals[i] * (1 + iv.magnitude * w * a);
    });
    return out;
  }

  const hit = st.map((_, i) => (atStation ? (dist[i] < 1 ? 1 : 0) : dist[i] <= r + NEAR_M ? 1 : Math.max(0, 1 - (dist[i] - r - NEAR_M) / 300)));
  const loss = atStation ? 1 - Math.min(1, iv.magnitude) : STREET_LOSS;
  let displaced = 0;
  st.forEach((_, i) => {
    const lost = vals[i] * loss * hit[i] * a;
    out[i] -= lost;
    displaced += lost;
  });
  const receivers = st
    .map((_, i) => i)
    .filter((i) => hit[i] < 0.2 && dist[i] <= RECEIVE_M + r)
    .sort((p, q) => dist[p] - dist[q]);
  const pool = atStation ? receivers.slice(0, 2) : receivers;
  const weights = pool.map((i, k) => (atStation ? [0.65, 0.35][k] : Math.exp(-dist[i] / 500)));
  const total = weights.reduce((s, w) => s + w, 0) || 1;
  pool.forEach((i, k) => {
    out[i] += (displaced * KEPT * weights[k]) / total;
  });
  return out;
}

export function mockScenario(req: ScenarioRequest, state: SimState, hubs: Hotspot[]): ScenarioResponse {
  const o = fitOrigin(state, hubs);
  const st = simStations(state, hubs);
  const rows = state.forecast?.stations ?? [];
  const meanOf = (naptan: string, k: HKey) => rows.find((r) => r.naptan === naptan)?.[k]?.mean ?? null;
  const stations: StationResult[] = st.map((s) => ({
    naptan: s.naptan,
    name: s.name,
    hub: s.hub,
    lat: s.lat,
    lon: s.lon,
    before: { "+15": meanOf(s.naptan, "+15"), "+60": meanOf(s.naptan, "+60") },
    after: { ...EMPTY },
  }));
  for (const h of HORIZONS) {
    const k: HKey = `+${h}`;
    let vals = stations.map((s) => s.before[k] ?? 0);
    for (const iv of req.interventions) vals = applyOne(iv, st, vals, h, o);
    stations.forEach((s, i) => {
      s.after[k] = s.before[k] == null ? null : round1(vals[i]);
    });
  }
  const hubsOut: HubResult[] = (state.forecast?.hubs ?? []).map((hb) => {
    const mine = stations.filter((s) => s.hub === hb.hub);
    const before = pairOf(hb);
    const after = { ...EMPTY };
    for (const h of HORIZONS) {
      const k: HKey = `+${h}`;
      const b = mine.reduce((s, m) => s + (m.before[k] ?? 0), 0);
      const a = mine.reduce((s, m) => s + (m.after[k] ?? 0), 0);
      after[k] = before[k] == null ? null : b ? round1(((before[k] as number) * a) / b) : before[k];
    }
    return { hub: hb.hub ?? hb.name, name: hb.name, before, after, peopleBefore: { ...EMPTY }, peopleAfter: { ...EMPTY } };
  });
  return { mock: true, asOf: asOf(state), unit: state.forecast?.unit ?? "% of usual", stations, hubs: hubsOut };
}

/* ---------- the sim's dry run ---------- */

/** The sim's POST /scenario reply: forecast blocks before and after, from copies of the live worlds. */
export interface SimScenario {
  before: { stations: ForecastRow[]; hubs: ForecastRow[] };
  after: { stations: ForecastRow[]; hubs: ForecastRow[] };
  ms?: number;
}

const asOf = (state: SimState) => (state.seedAt ?? Date.now()) + (state.minute ?? 0) * 60_000;
const pairOf = (row: ForecastRow | undefined): Pair => ({ "+15": row?.["+15"]?.mean ?? null, "+60": row?.["+60"]?.mean ?? null });
const peopleOf = (row: ForecastRow | undefined): Pair => ({ "+15": row?.["+15"]?.people?.mean ?? null, "+60": row?.["+60"]?.people?.mean ?? null });

/** Shapes the real reply like the stand-in's, placing stations with the same fitted origin. */
export function fromSim(real: SimScenario, state: SimState, hubs: Hotspot[]): ScenarioResponse {
  const geo = new Map(simStations(state, hubs).map((s) => [s.naptan, s]));
  const stations: StationResult[] = [];
  for (const b of real.before.stations) {
    const g = b.naptan ? geo.get(b.naptan) : undefined;
    if (!g || !b.naptan) continue;
    const a = real.after.stations.find((x) => x.naptan === b.naptan);
    stations.push({ naptan: b.naptan, name: b.name, hub: b.hub ?? null, lat: g.lat, lon: g.lon, before: pairOf(b), after: pairOf(a) });
  }
  const hubsOut: HubResult[] = real.before.hubs.map((b) => {
    const a = real.after.hubs.find((x) => x.hub === b.hub);
    return { hub: b.hub ?? b.name, name: b.name, before: pairOf(b), after: pairOf(a), peopleBefore: peopleOf(b), peopleAfter: peopleOf(a) };
  });
  return { mock: false, asOf: asOf(state), unit: state.forecast?.unit ?? "% of usual", stations, hubs: hubsOut };
}

/* ---------- reading a result ---------- */

/** Relative change in %, e.g. 94.8 -> 111.9 is +18. */
export function rel(p: Pair | undefined, q: Pair | undefined, k: HKey): number | null {
  const b = p?.[k];
  const a = q?.[k];
  if (b == null || a == null || b <= 0) return null;
  return Math.round(((a - b) / b) * 100);
}

export const fmtRel = (v: number | null) => (v == null ? "–" : v > 0 ? `+${v}%` : v < 0 ? `−${Math.abs(v)}%` : "0%");

export interface Change {
  id: string;
  name: string;
  kind: "station" | "hub";
  /** busyness: % of usual at the station; people: people on the hub's streets. */
  basis: "busyness" | "people";
  d15: number | null;
  d60: number | null;
  lat?: number;
  lon?: number;
}

const size = (c: Change) => Math.max(Math.abs(c.d15 ?? 0), Math.abs(c.d60 ?? 0));

export function changes(res: ScenarioResponse, hubs: Hotspot[]): Change[] {
  const geo = new Map(hubs.map((h) => [h.id, h]));
  const st: Change[] = res.stations.map((s) => ({
    id: s.naptan,
    name: s.name,
    kind: "station",
    basis: "busyness",
    d15: rel(s.before, s.after, "+15"),
    d60: rel(s.before, s.after, "+60"),
    lat: s.lat,
    lon: s.lon,
  }));
  const hb: Change[] = res.hubs.map((h) => ({
    id: `hub:${h.hub}`,
    name: h.name,
    kind: "hub",
    basis: "people",
    d15: rel(h.peopleBefore, h.peopleAfter, "+15"),
    d60: rel(h.peopleBefore, h.peopleAfter, "+60"),
    lat: geo.get(h.hub)?.lat,
    lon: geo.get(h.hub)?.lon,
  }));
  return [...st, ...hb].filter((c) => size(c) >= 1).sort((p, q) => size(q) - size(p));
}

/**
 * One sentence: the action, the biggest rises, then the biggest fall, at the horizon that moved most.
 * Station busyness leads; when no station moves (a street closure), people on the hub streets do.
 */
export function summarise(action: string, list: Change[]): string {
  const st = list.filter((c) => c.basis === "busyness");
  const pool = st.length ? st : list.filter((c) => c.basis === "people");
  if (!pool.length) return `${action} barely moves any station or street crowd in the next hour.`;
  const peak = (k: "d15" | "d60") => Math.max(...pool.map((c) => Math.abs(c[k] ?? 0)));
  const k: "d15" | "d60" = peak("d15") >= peak("d60") ? "d15" : "d60";
  const when = k === "d15" ? "within 15 min" : "within the hour";
  const ups = pool.filter((c) => (c[k] ?? 0) > 0).sort((p, q) => (q[k] ?? 0) - (p[k] ?? 0)).slice(0, 2);
  const down = pool.filter((c) => (c[k] ?? 0) < 0).sort((p, q) => (p[k] ?? 0) - (q[k] ?? 0))[0];
  if (!st.length) {
    const parts = [...ups.map((c) => `${fmtRel(c[k])} people around ${c.name}`), ...(down ? [`${fmtRel(down[k])} around ${down.name}`] : [])];
    return `${action} moves the street crowd: ${parts.join(", ")} ${when}.`;
  }
  const upText = ups.map((c) => `${c.name} ${fmtRel(c[k])}`).join(" and ");
  if (!ups.length && down) return `${action} cuts ${down.name} ${fmtRel(down[k])} ${when}.`;
  return `${action} pushes ${upText} ${when}${down ? `; ${down.name} ${fmtRel(down[k])}` : ""}.`;
}

/* ---------- open markets ---------- */

export interface MarketMove {
  market: Market;
  horizon: Horizon;
  options: { id: string; label: string; from: number; to: number }[];
}

const SENS_PTS = 8;
const PEOPLE_WEIGHT = 0.5;
// Why: an emptied station would otherwise zero its hub's price; a rough mapping should not claim certainty.
const MAX_TILT = 1.5;

/**
 * Approximate: tilts a hub market's prices by each hub's forecast change, p' ∝ p · e^(Δ/8) with the exponent
 * capped at ±1.5, where Δ is the
 * change in points of usual busyness plus half the % change in people on its streets, at the horizon nearest
 * the market's settle time. Markets with no hub options are left out.
 */
export function marketMoves(markets: Market[], res: ScenarioResponse, now: number): MarketMove[] {
  const hubs = new Map(res.hubs.map((h) => [h.hub, h]));
  const out: MarketMove[] = [];
  for (const m of markets) {
    if (now >= m.closesAt || m.status === "settled" || m.status === "void") continue;
    if (!m.options.some((o) => o.hotspot && hubs.has(o.hotspot))) continue;
    const horizon: Horizon = m.settlesAt - now <= 30 * 60_000 ? 15 : 60;
    const k: HKey = `+${horizon}`;
    const tilted = m.options.map((o) => {
      const h = o.hotspot ? hubs.get(o.hotspot) : undefined;
      if (!h) return o.prob;
      const pts = h.before[k] != null && h.after[k] != null ? (h.after[k] as number) - (h.before[k] as number) : 0;
      const ppl = rel(h.peopleBefore, h.peopleAfter, k) ?? 0;
      return o.prob * Math.exp(Math.max(-MAX_TILT, Math.min(MAX_TILT, (pts + PEOPLE_WEIGHT * ppl) / SENS_PTS)));
    });
    const sum = tilted.reduce((s, v) => s + v, 0) || 1;
    out.push({ market: m, horizon, options: m.options.map((o, i) => ({ id: o.id, label: o.label, from: o.prob, to: tilted[i] / sum })) });
  }
  return out;
}

/* ---------- suggested what-ifs from the live event layer ---------- */

export interface Suggestion {
  id: string;
  label: string;
  action: string;
  /** Station what-ifs carry their interventions; street ones name the street for the map to place. */
  interventions: Intervention[];
  street?: string;
}

const EXTEND_MIN = 90;
const LINE_RE = /^(.+?) (Severe Delays|Part Suspended|Suspended|Planned Closure|Part Closure|Minor Delays|Reduced Service)\b.*?\. ([^.:]+): \d+\/\d+ lines? affected, capacity x([\d.]+)/i;
const STREET_RE = /^(?:\[[A-Z0-9]+\] )?(.+?) \([^)]*\) closed \(/;
const SURGE_RE = /^([^|]+?) busyness [\d.]+ vs typical [\d.]+ .*?\(x([\d.]+)/;

/**
 * Up to three what-ifs built from whatever the event layer reports now, by general patterns: a line
 * disruption stays for another hour, a closed street stays closed, a station crowd keeps building. Events
 * whose station or street is outside the sim are dropped; nothing here names a place in advance.
 */
export function suggestions(state: SimState, stations: SimStation[]): Suggestion[] {
  const byName = new Map(stations.map((s) => [normName(s.name), s]));
  const find = (name: string) => byName.get(normName(name)) ?? stations.find((s) => normName(s.name).startsWith(normName(name)));
  const lines = new Map<string, Suggestion>();
  const streets: Suggestion[] = [];
  const surges: Suggestion[] = [];
  for (const e of state.events ?? []) {
    const reason = e.reason ?? "";
    if (e.kind === "capacity") {
      const m = LINE_RE.exec(reason);
      const st = m ? find(m[3]) : undefined;
      if (!m || !st) continue;
      const key = `${m[1]} ${m[2]}`.toLowerCase();
      const s = lines.get(key) ?? { id: `line:${key}`, label: `What if the ${m[1]} ${m[2].toLowerCase()} lasts another hour?`, action: `The ${m[1]} ${m[2].toLowerCase()} for another hour`, interventions: [] };
      if (s.interventions.length < 4 && !s.interventions.some((iv) => "naptan" in iv.where && iv.where.naptan === st.naptan)) {
        s.interventions.push({ kind: "capacity", where: { naptan: st.naptan }, start: 0, end: EXTEND_MIN, magnitude: Math.max(0, Math.min(1, Number(m[4]))) });
      }
      lines.set(key, s);
    } else if (e.kind === "closure" && e.inSim) {
      const m = STREET_RE.exec(reason);
      if (!m || streets.some((s) => s.street === m[1])) continue;
      streets.push({ id: `street:${m[1]}`, label: `What if ${m[1]} stays closed another hour?`, action: `${m[1]} closed another hour`, interventions: [], street: m[1] });
    } else if (e.kind === "demand_surge") {
      const m = SURGE_RE.exec(reason);
      const st = m ? find(m[1].trim()) : undefined;
      if (!m || !st || surges.some((s) => s.id === `surge:${st.naptan}`)) continue;
      const extra = Math.max(0.25, Math.min(1, Number(m[2]) - 1));
      surges.push({
        id: `surge:${st.naptan}`,
        label: `What if the crowd at ${st.name} keeps building?`,
        action: `The crowd at ${st.name} building`,
        interventions: [{ kind: "demand_surge", where: { lat: st.lat, lon: st.lon, radiusM: 150 }, start: 0, end: EXTEND_MIN, magnitude: extra }],
      });
    }
  }
  const out = [...lines.values()].slice(0, 1).concat(streets.slice(0, 1), surges.slice(0, 1), [...lines.values()].slice(1));
  return out.slice(0, 3);
}

/* ---------- request validation (the proxy) ---------- */

const KINDS = new Set(["closure", "capacity", "demand_surge"]);
const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function parseScenario(raw: unknown): ScenarioRequest | string {
  if (!raw || typeof raw !== "object") return "body must be a JSON object";
  const ivs = (raw as { interventions?: unknown }).interventions;
  if (!Array.isArray(ivs) || ivs.length < 1 || ivs.length > 6) return "interventions must hold 1 to 6 items";
  const clean: Intervention[] = [];
  for (const iv of ivs as Record<string, unknown>[]) {
    if (!iv || typeof iv !== "object" || !KINDS.has(String(iv.kind))) return "kind must be closure, capacity or demand_surge";
    const w = iv.where as Record<string, unknown> | undefined;
    let where: Where;
    if (w && typeof w.naptan === "string" && /^[0-9A-Z]{6,16}$/.test(w.naptan)) where = { naptan: w.naptan };
    else if (w && fin(w.lat) && fin(w.lon) && fin(w.radiusM) && w.radiusM > 0 && w.radiusM <= 1000) {
      where = { lat: w.lat, lon: w.lon, radiusM: w.radiusM };
    } else return "where must be {naptan} or {lat, lon, radiusM <= 1000}";
    if (!fin(iv.start) || !fin(iv.end) || iv.start < 0 || iv.end <= iv.start || iv.end > 240) return "start and end are minutes from now, 0 <= start < end <= 240";
    if (!fin(iv.magnitude) || iv.magnitude < 0 || iv.magnitude > 3) return "magnitude must be 0 to 3";
    clean.push({ kind: iv.kind as Intervention["kind"], where, start: iv.start, end: iv.end, magnitude: iv.magnitude, note: typeof iv.note === "string" ? iv.note.slice(0, 120) : undefined });
  }
  return { interventions: clean, horizons: [...HORIZONS] };
}
