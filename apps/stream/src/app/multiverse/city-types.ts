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

/** A market's simulated futures as paths of the number it settles on (services/city/fan.py). */
export interface Futures {
  n: number;
  alive: number;
  ruledOut: number;
  /** Alive futures landing on each option; sums to `alive`. */
  byOption: Record<string, number>;
  /** One path per future, [ts, value] points from open to settle. */
  paths: [number, number][][];
  /** 1 when that path's future is still alive. */
  alivePaths: number[];
  /** Real readings since open. */
  real: [number, number][];
  /** The over/under line or the bracket edges. */
  lines: number[];
  label: string;
  unit: string;
  about: string;
  realised?: { value: number | null; closest: number | null };
}

export type MarketStatus = "open" | "closed" | "halted" | "settled" | "void";

export interface Market {
  id: string;
  /** The recurring question this market belongs to, e.g. "city_pulse". */
  series?: string;
  kind: "versus" | "yesno" | "overunder";
  question: string;
  hotspots: string[];
  closesAt: number;
  settlesAt: number;
  /** The simulated futures behind the price; missing in demo data. */
  futures?: Futures;
  options: MarketOption[];
  aiBets: AiBet[];
  /** Feed, place and settle time, e.g. "TfL Crowding API · Oxford Circus · settles 17:50:00 Europe/London". */
  source?: string;
  /** Full one-line settlement rule. */
  rule?: string;
  status?: MarketStatus;
  /** Sent while halted: "stale feed", "jump: …" or "suspended: …; reopens HH:MM:SS". */
  haltReason?: string;
  /** 1–2 clues from services/city/signals.py; null or missing renders nothing. */
  signals?: Signal[] | null;
  integrity?: Integrity;
}

export type UsualTrend = "up" | "down" | "flat";

/** Live busyness ÷ the typical value for this weekday and 15-min band; raw fractions (1 = usual). */
export interface HubVsUsual {
  id: string;
  name: string;
  live: number;
  typical: number;
  vsUsual: number;
  typicalNext15: { at: number; typical: number }[];
  usualTrend: UsualTrend;
  furthestBelow: boolean;
  leader: boolean;
}

export interface HubsVsUsual {
  day: string;
  asOf: number;
  laggard: string;
  leader: string;
  hubs: HubVsUsual[];
}

export interface LineDisruption {
  id: string;
  name: string;
  sev: number;
  desc: string;
  reason: string;
  sinceMin: number | null;
}

export interface LineBoard {
  good: number;
  of: number;
  sinceLastChangeMin: number | null;
  disrupted: LineDisruption[];
}

export type Signal =
  | { id: "vs_usual"; text: string; data: HubsVsUsual }
  | {
      id: "usual_curve";
      text: string;
      data: { hub: string; name: string; vsUsual: number; typicalNow: number; typicalIn15: number; usualTrend: UsualTrend };
    }
  | {
      id: "fixture";
      text: string;
      data: { home: string; away: string; score: string; status: string; hub: string; minutesSinceEgress: number };
    }
  | {
      id: "pulse";
      text: string;
      data: { pulse: number; usual: number | null; typicalIn15: number | null; usualTrend: UsualTrend; change5: number | null };
    }
  | { id: "vix_driver"; text: string; data: { vix: number; driver: string; driverDelta: number; change5: number | null } }
  | { id: "stacks"; text: string; data: { total: number; stacks: Record<string, number>; avg30: number; change10: number | null } }
  | { id: "lines"; text: string; data: LineBoard };

/** GET /signals: the top strip. */
export interface CitySignals {
  now: number;
  hubs: (HubsVsUsual & { text: string }) | null;
  lines: (LineBoard & { text: string }) | null;
  football: { home: string; away: string; score: string; status: string; hub: string | null; text: string } | null;
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
  /** Wallet (round 5): the ledger's reply. `balance` is the player's cash after the stake moved to escrow. */
  code?: "insufficient_funds" | "confirm_required" | "loss_limit" | "on_break" | "price_too_short" | "house_limit";
  /** Sent with confirm_required: a 60 s server token for exactly this stake, sent back as `confirm_token`. */
  confirmToken?: string;
  betId?: string;
  payout?: number;
  balance?: number;
  escrow?: number;
  replayed?: boolean;
  /** Bet delay: a bet is accepted as pending and polled with GET /bet until it fills or is cancelled. */
  status?: "pending" | "filled" | "cancelled";
  fillsAt?: number;
  /** Why a pending bet was cancelled, e.g. "market paused: Central now Severe Delays". */
  reason?: string;
}

/** Courtsiding defences for one market (services/city/integrity.py). */
export interface Integrity {
  delayS: number;
  suspendsOn: string[];
  coolOffS: number;
  capPct: number;
}

export interface LeaderRow {
  name: string;
  points: number;
  kind: "human" | "ai";
}
