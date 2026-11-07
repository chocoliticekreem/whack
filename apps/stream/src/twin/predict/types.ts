export type InterventionKind = "closure" | "incident" | "event" | "weather";

export interface Intervention {
  kind: InterventionKind;
  where: { x: number; z: number };
  note: string;
  atMin: number;
}

export interface Persona {
  id: string;
  archetype: string;
  size: number;
  zone?: string;
  zoneName: string;
  origin?: { x: number; z: number };
  destination: string;
  departMin?: number;
  rationale: string;
}

export interface TimelineZone {
  id: string;
  name: string;
  people: number;
  vehicles: number;
  deltaPeople?: number;
}

export interface TimelineStep {
  t: number;
  minute: number;
  zones: TimelineZone[];
  segments: { id: number; peds: number; vehicles: number }[];
  walking: number;
  events: unknown[];
}

export interface ReportItem {
  zone: string;
  zoneId: string;
  atMin: number;
  now: number;
  predicted: number;
  delta: number;
  direction: "busier" | "quieter";
  reasons: string[];
  inputs: string[];
  confidence: number;
  intervention: boolean;
  text?: string;
}

export interface PredictRun {
  runId: string;
  at: number;
  horizonMin: number;
  seed: { segmentCount: number; observedSegments: number; zoneCount: number; area?: string; origin?: { lat: number; lon: number } };
  personas: Persona[];
  timeline: TimelineStep[];
  report: { headline: string; items: ReportItem[]; assumptions: string[]; mode: "llm" | "rules" };
  interventions: (Intervention & { source?: string; affectedSegments?: number[] })[];
  camSegments?: { camId: string; segmentId: number; zoneId: string | null; seedPeds: number }[];
  /** Zones named in the report: the ghost layer draws only these. */
  keyZoneSegments?: Record<string, { name: string; x: number; z: number; segmentIds: number[] }>;
  trace?: { weave_project: string; model: string };
}

export interface ScoreResult {
  runId: string;
  compared: number;
  error?: string;
  forecastMinute?: number;
  comparedAt?: number;
  cams?: { camId: string; zoneId: string | null; predictedPeople: number; nowPeople: number; actualPeople: number; actualAt: number }[];
  zones?: {
    zoneId: string;
    predicted: number;
    now: number;
    actual: number;
    errorPct: number | null;
    predictedDirection: "busier" | "quieter" | "flat";
    actualDirection: "busier" | "quieter" | "flat";
    verdict: "right" | "wrong";
    verdictText: string;
  }[];
  summary?: { zonesRight: number; zonesWrong: number; meanAbsError: number | null };
}
