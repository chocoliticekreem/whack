/** Streamer state relayed between devices via /api/live (the bus only reaches tabs in one browser). */
export interface LiveState {
  streamer: string;
  session: string;
  lat: number;
  lon: number;
  accuracyM: number;
  /** Compass heading in degrees (0 = north), null when the GPS has no fix on it. */
  heading: number | null;
  /** Metres per second, null when unknown. */
  speed: number | null;
  viewers: number;
  live: boolean;
  label: string;
  ts: number;
}

export const DEFAULT_SESSION = "default";
export const PUBLISH_MS = 2000;
