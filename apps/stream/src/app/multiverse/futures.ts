import type { Assumptions, BrokerRow, MultiverseState, Observation, WorldRow } from "./types";

/** Plain names for the engine's data-source forecasters. No broker ids ever reach the screen. */
export const FORECASTER_NAME: Record<string, string> = {
  "B-AIR": "Flight-tracker AI",
  "B-RAIN": "Rain-radar AI",
  "B-GRID": "Power-grid AI",
  "B-NEWS": "News AI",
  "B-BIKES": "Bike-dock AI",
  "B-TUBE": "Tube-crowding AI",
  "B-WIKI": "Wikipedia AI",
  "B-BUS": "Bus-data AI",
  "B-CAMS": "CCTV AI",
  "B-CONTRA": "Contrarian AI",
};

export type IconName = "bus" | "users" | "rain" | "sun" | "cloud" | "home" | "walk" | "run";

export interface Ctx {
  /** Minutes since the current five-minute market opened. */
  sinceOpen: number;
  /** Clock time of the latest reading, ms. */
  now: number;
}

interface Bin {
  id: string;
  icon: IconName;
  title: (c: Ctx) => string;
  test: (a: Assumptions, c: Ctx) => boolean;
}

export interface Question {
  id: "bus" | "crowd" | "rain" | "move";
  knob: keyof Assumptions & string;
  ask: string;
  forecasters: string[];
  bins: Bin[];
}

const clock = (ms: number) =>
  new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" });

const rainIn = (a: Assumptions, c: Ctx) => (a.rainAtMin == null ? null : a.rainAtMin - c.sinceOpen);
const SOON_MIN = 2.5;

export const QUESTIONS: Question[] = [
  {
    id: "bus",
    knob: "busDelayMin",
    ask: "How late are the buses?",
    forecasters: ["B-BUS"],
    bins: [
      { id: "on-time", icon: "bus", title: () => "Buses run on time", test: (a) => a.busDelayMin < 2.5 },
      { id: "late", icon: "bus", title: () => "Buses run a few minutes late", test: (a) => a.busDelayMin >= 2.5 && a.busDelayMin < 5 },
      { id: "very-late", icon: "bus", title: () => "Buses run 5+ minutes late", test: (a) => a.busDelayMin >= 5 },
    ],
  },
  {
    id: "crowd",
    knob: "touristFactor",
    ask: "How crowded does Oxford Circus get?",
    forecasters: ["B-TUBE", "B-WIKI", "B-AIR"],
    bins: [
      { id: "normal", icon: "users", title: () => "A normal crowd", test: (a) => a.touristFactor < 1.2 },
      { id: "busy", icon: "users", title: () => "Busier than usual", test: (a) => a.touristFactor >= 1.2 && a.touristFactor < 1.5 },
      { id: "packed", icon: "users", title: () => "Packed with visitors", test: (a) => a.touristFactor >= 1.5 },
    ],
  },
  {
    id: "rain",
    knob: "rainAtMin",
    ask: "Does it rain?",
    forecasters: ["B-RAIN"],
    bins: [
      { id: "dry", icon: "sun", title: () => "It stays dry", test: (a, c) => rainIn(a, c) == null },
      {
        id: "soon",
        icon: "rain",
        title: (c) => `Rain hits by ${clock(c.now + SOON_MIN * 60_000)}`,
        test: (a, c) => {
          const r = rainIn(a, c);
          return r != null && r <= SOON_MIN;
        },
      },
      {
        id: "later",
        icon: "cloud",
        title: (c) => `Rain comes, but after ${clock(c.now + SOON_MIN * 60_000)}`,
        test: (a, c) => {
          const r = rainIn(a, c);
          return r != null && r > SOON_MIN;
        },
      },
    ],
  },
  {
    id: "move",
    knob: "movingShare",
    ask: "How many people are on the move?",
    forecasters: ["B-BIKES", "B-GRID"],
    bins: [
      { id: "still", icon: "home", title: () => "Most people stay put", test: (a) => a.movingShare < 0.45 },
      { id: "normal", icon: "walk", title: () => "A normal flow of people", test: (a) => a.movingShare >= 0.45 && a.movingShare < 0.7 },
      { id: "rush", icon: "run", title: () => "Everyone is on the move", test: (a) => a.movingShare >= 0.7 },
    ],
  },
];

export function ctxOf(s: MultiverseState): Ctx {
  const last = s.observations[s.observations.length - 1];
  const now = last?.ts ?? s.seedAt;
  return { sinceOpen: s.minute - (s.epochs ?? 0) * s.horizonMin, now };
}

const liveWorlds = (s: MultiverseState) => s.board.filter((w): w is WorldRow & { assumptions: Assumptions } => w.status === "live" && !!w.assumptions);

/** Share of the futures (by likelihood) that land in each answer. Sums to 1 over the bins. */
export function binMass(s: MultiverseState, q: Question): number[] {
  const c = ctxOf(s);
  const ws = liveWorlds(s);
  const total = ws.reduce((t, w) => t + w.price, 0) || 1;
  return q.bins.map((b) => ws.filter((w) => b.test(w.assumptions, c)).reduce((t, w) => t + w.price, 0) / total);
}

/** How close the futures are to a coin flip on this question: 1 = evenly split, 0 = settled. */
function openness(mass: number[]): number {
  const h = -mass.filter((m) => m > 0).reduce((t, m) => t + m * Math.log(m), 0);
  return h / Math.log(mass.length);
}

export function pickQuestion(s: MultiverseState, previous: string | null): Question {
  const ranked = QUESTIONS.filter((q) => q.id !== previous)
    .map((q) => ({ q, open: openness(binMass(s, q)) }))
    .sort((a, b) => b.open - a.open);
  return ranked[0].q;
}

export interface AiPick {
  name: string;
  bin: number;
}

/** The AI side: the best-trusted forecaster that reads this question's data source, else the futures' favourite. */
export function aiPick(s: MultiverseState, q: Question, mass: number[]): AiPick {
  const c = ctxOf(s);
  const candidates = (s.brokers ?? [])
    .filter((b): b is BrokerRow & { idea: Record<string, number | null> } => q.forecasters.includes(b.id) && !!b.idea)
    .sort((a, b) => b.weight - a.weight);
  for (const b of candidates) {
    const has = q.knob in b.idea;
    const v = b.idea[q.knob];
    if (!has || (v == null && q.knob !== "rainAtMin")) continue;
    // A rain idea is minutes from now; the bins read minutes since the market opened.
    const value = q.knob === "rainAtMin" && v != null ? v + c.sinceOpen : v;
    const probe = { movingShare: 0.6, touristFactor: 1.3, capacityScale: 1, busDelayMin: 3, rainAtMin: null, [q.knob]: value } as Assumptions;
    const bin = q.bins.findIndex((x) => x.test(probe, c));
    if (bin >= 0) return { name: FORECASTER_NAME[b.id] ?? "AI forecaster", bin };
  }
  return { name: "AI forecasters", bin: mass.indexOf(Math.max(...mass)) };
}

/** The answer reality gave: a direct reading when the question has one (bus delay), else the futures' new favourite. */
export function winningBin(next: MultiverseState, q: Question, after: number[]): number {
  const obs = next.observations[next.observations.length - 1];
  if (q.id === "bus" && obs?.busDelayMin != null) {
    const probe = { movingShare: 0.6, touristFactor: 1.3, capacityScale: 1, busDelayMin: obs.busDelayMin, rainAtMin: null } as Assumptions;
    const hit = q.bins.findIndex((b) => b.test(probe, ctxOf(next)));
    if (hit >= 0) return hit;
  }
  return after.indexOf(Math.max(...after));
}

export const pct =(m: number) => (m < 0.01 ? "under 1%" : `${Math.round(m * 100)}%`);

export function shortCam(name: string): string {
  const [first, second] = name.split(" / ");
  return second ? `${second} on ${first}` : name;
}

/** One plain sentence on what the newest reading saw. */
export function evidence(prev: MultiverseState, next: MultiverseState, q: Question): string {
  const obs: Observation | undefined = next.observations[next.observations.length - 1];
  if (!obs) return "";
  const ruled = obs.delisted.length;
  const tail = ruled ? ` ${ruled} futures that didn't fit were ruled out.` : " Every future still fits.";
  if (q.id === "bus" && obs.busDelayMin != null) {
    return `The live bus tracker says buses are ${obs.busDelayMin.toFixed(1)} min late.${tail}`;
  }
  const cam = surprise(prev, obs);
  const lead = cam
    ? `The ${shortCam(cam.name)} camera counted ${Math.round(cam.observed)} people (the futures guessed ${Math.round(cam.expected)}).`
    : `${obs.compared} street cameras reported in.`;
  return `${lead}${tail} The futures that still fit point to this answer.`;
}

export function surprise(prev: MultiverseState, obs: Observation) {
  let best: { camId: string; name: string; observed: number; expected: number; gap: number } | null = null;
  for (const c of prev.camsNow) {
    const observed = obs.readings?.[c.camId];
    if (observed == null) continue;
    const gap = Math.abs(observed - c.mean) / Math.max(c.sd, 1);
    if (!best || gap > best.gap) best = { camId: c.camId, name: c.name, observed, expected: c.mean, gap };
  }
  return best;
}

export function uncertaintyWord(s: MultiverseState): "calming" | "rising" | "steady" {
  const h = s.vix_history;
  if (h.length < 2) return "steady";
  const d = h[h.length - 1].vix - h[h.length - 2].vix;
  if (Math.abs(d) < 0.3) return "steady";
  return d < 0 ? "calming" : "rising";
}
