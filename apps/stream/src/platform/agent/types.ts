export type AdviceLevel = "calm" | "busy" | "danger";
export type AdviceSource = "cluster-agent" | "local-llm";

export interface AssessRequest {
  question: string;
  location?: { name: string; lat: number; lon: number };
  yolo?: { people: number; vehicles: number; camera: string };
}

/** One VSS clip the CAMP agent grounded its answer on (Cosmos description + YOLO counts). */
export interface AdviceEvidence {
  video: string;
  camera: string;
  start_s: number;
  end_s: number;
  description: string;
  similarity: number;
  counts: Record<string, number>;
}

/** What the agent gathered with the VSS agent tools (`tools/detections`, `tools/segments`) for one moment. */
export interface MomentTools {
  people_timeline?: { t: number; people: number }[];
  people_peak?: number;
  detection_count?: number;
  context?: { segment: number; start_s: number; people: number; current: boolean }[];
}

export interface ArchiveMoment extends AdviceEvidence {
  source: string;
  location: string;
  /** Path on the agent (`/app/clip?source=`); the app proxies it at `/api/agent/clip`. */
  clip_url: string;
  tools?: MomentTools;
}

export interface ArchiveSearch {
  query: string;
  moments: ArchiveMoment[];
  latency_ms: number;
}

/** One row of the agent's VastDB memory table. */
export interface StoredVerdict {
  ts: string;
  question: string;
  location_name: string;
  level: AdviceLevel;
  advice: string;
  reasons: string[];
  evidence_ids: string[];
  model: string;
  latency_ms: number;
}

export interface VerdictsResponse {
  table: string;
  total: number;
  last_hour_by_level: Record<AdviceLevel, number>;
  verdicts: StoredVerdict[];
}

export interface Assessment {
  level: AdviceLevel;
  advice: string;
  reasons: string[];
  evidence: AdviceEvidence[];
  source: AdviceSource;
  /** Live TfL cameras whose YOLO counts fed the answer. */
  liveCams: number;
  trace?: { weave_project?: string };
  at: number;
}
