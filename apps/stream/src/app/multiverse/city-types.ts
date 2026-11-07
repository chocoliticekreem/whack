/** Shapes served by the city markets backend (services/city, proxied at /api/city/*). */

export interface Hotspot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  naptan?: string;
  cams?: string[];
}

export interface Trader {
  id: string;
  name: string;
  emoji: string;
  watches: string;
  style: string;
  record: { won: number; lost: number };
  points: number;
}

export interface MarketOption {
  id: string;
  label: string;
  prob: number;
  odds: number;
  hotspot?: string;
  /** Recent probabilities, oldest first; drawn as the sparkline. */
  history?: number[];
}

export interface AiBet {
  trader: string;
  option: string;
  why: string;
}

export type MarketStatus = "open" | "closed" | "halted" | "settled" | "void";

export interface Market {
  id: string;
  kind: "versus" | "yesno" | "overunder";
  question: string;
  hotspots: string[];
  closesAt: number;
  settlesAt: number;
  futures: number;
  options: MarketOption[];
  aiBets: AiBet[];
  /** Feed, place and settle time, e.g. "TfL Crowding API · Oxford Circus · settles 17:50:00 Europe/London". */
  source?: string;
  /** Full one-line settlement rule. */
  rule?: string;
  status?: MarketStatus;
}

export interface Feed {
  ageS: number | null;
  ok: boolean;
}

export interface MarketsResponse {
  asOf: number;
  live: boolean;
  markets: Market[];
  feeds?: Record<string, Feed>;
}

export type SettleResponse =
  | { settled: false; waitMs: number }
  | {
      settled: true;
      winner: string | null;
      reading: string;
      aiResults: { trader: string; won: boolean }[];
      futuresRuledOut?: number;
      void?: boolean;
    };

export interface BetResponse {
  ok: boolean;
  price?: number;
  shares?: number;
  odds?: number;
  options?: Pick<MarketOption, "id" | "prob" | "odds">[];
  error?: string;
}

export interface LeaderRow {
  name: string;
  points: number;
  kind: "human" | "ai";
}
