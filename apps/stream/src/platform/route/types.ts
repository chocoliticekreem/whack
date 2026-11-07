export type RouteMode = "calm" | "fast";

export interface RoutePath {
  mode: RouteMode;
  /** [lat, lon] pairs along the route. */
  polyline: [number, number][];
  /** Local twin metres, same order as `polyline`. */
  xz: [number, number][];
  lengthM: number;
  etaMin: number;
  /** Expected number of people passed along the route (Σ density per 100 m × length / 100). */
  exposure: number;
  /** Peak pedestrian density met on the way, per 100 m. */
  peakPeds: number;
}

export interface RouteResponse {
  from: { lat: number; lon: number };
  to: { name: string; lat: number; lon: number };
  fastest: RoutePath;
  calmest: RoutePath;
  /** Names of the busiest places the calmest route steers around. */
  avoids: string[];
  /** Extra walking time for the calmer route, minutes (negative never happens; 0 when identical). */
  extraMin: number;
  /** Crowd exposure saved by the calmer route, 0..1 of the fastest route's exposure. */
  lessCrowd: number;
  at: number;
}

export interface RouteDestination {
  name: string;
  lat: number;
  lon: number;
}

/** Walkable places around Tower Hill the streamer can pick as a destination. */
export const DESTINATIONS: RouteDestination[] = [
  { name: "Tower Hill station", lat: 51.5098, lon: -0.0766 },
  { name: "Tower Bridge", lat: 51.5055, lon: -0.0754 },
  { name: "St Katharine Docks", lat: 51.5075, lon: -0.0716 },
  { name: "Tower Gateway", lat: 51.5106, lon: -0.0743 },
  { name: "Fenchurch Street", lat: 51.5113, lon: -0.0774 },
  { name: "Aldgate", lat: 51.5143, lon: -0.0757 },
  { name: "Monument", lat: 51.5107, lon: -0.0859 },
  { name: "Tower of London", lat: 51.5081, lon: -0.0759 },
];
