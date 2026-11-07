export interface TrailStep {
  obs: number;
  minute: number;
  ll: number;
  worst: { cam: string; predicted: number; observed: number; ll: number } | null;
}

export interface WorldRow {
  id: string;
  parent: string | null;
  generation: number;
  status: "live" | "delisted";
  price: number;
  history: number[];
  labels: string[];
  delistReason: string | null;
  bornAtObs: number;
  simMs: number;
  trail: TrailStep[];
  events: string[];
  broker: string | null;
}

export interface BrokerRow {
  id: string;
  persona: string;
  archetype: string;
  trackRecord: number;
  weight: number;
  slots: number;
  liveWorlds: number;
  mass: number;
  conviction: number | null;
  rationale: string | null;
  source: "haiku" | "rules" | null;
  pnl: number[];
  cumulative: number;
}

export interface ZoneStat {
  zoneId: string;
  name: string;
  mean: number;
  sd: number;
  iv: number;
}

export interface Observation {
  index: number;
  ts: number;
  minute: number;
  compared: number;
  busDelayMin?: number | null;
  delisted: string[];
  spawned: string[];
}

export interface Zone {
  id: string;
  name: string;
  x: number;
  z: number;
  kind: string;
}

export interface MultiverseState {
  mode: "replay" | "live";
  replay: { frames: number; cursor: number; nextTs: number | null };
  area: string;
  seedAt: number;
  seedSource: string;
  horizonMin: number;
  n: number;
  crnSeed: number;
  minute: number;
  live: number;
  delisted: number;
  effectiveWorlds: number;
  spawnMs: number;
  observations: Observation[];
  board: WorldRow[];
  consensusNow: ZoneStat[];
  consensusHorizon: ZoneStat[];
  camsNow: { camId: string; name: string; mean: number; sd: number }[];
  zones: Zone[];
  brokers: BrokerRow[] | null;
  forked?: string[];
  observation?: Observation | null;
  done?: boolean;
}

export interface Geometry {
  segments: { id: string; kind: string; line: number[][] }[];
  zones: Zone[];
  cams: { id: string; name: string; x: number; z: number }[];
}
