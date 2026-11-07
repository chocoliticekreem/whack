import type { Archetype } from "@/lib/types";

/** What made chat react. `ambient` is the steady background chatter. */
export type CrowdEvent = "ambient" | "streamer_message" | "location_leaked" | "mirror";

/** Wire format for POST /api/chat/scene. Shared by the route and the chat hooks. */
export interface SceneRequest {
  /** JPEG as a data URI or raw base64, ≤ 640 px wide. Omit when the canvas is tainted (cross-origin video). */
  frame?: string;
  /** Fallback when no frame could be captured client-side: the server grabs one from this clip. Without either, chat reacts from text alone. */
  videoUrl?: string;
  location: { name: string; area: string };
  viewers: number;
  event: CrowdEvent;
  /** The streamer has told chat where they are. Only then may lines talk about distances and travelling there. */
  leaked: boolean;
  streamerMessage?: string;
  surroundings?: { summary: string; level: "calm" | "busy" | "danger" };
  /** Last ~12 lines of chat, oldest first. */
  recent: { username: string; text: string }[];
  personas: { username: string; archetype: Archetype }[];
  /** How many lines to write; the server clamps it. */
  count?: number;
}

export interface SceneDescription {
  scene: string;
  notable: string[];
  mood: string;
  safety_flags: string[];
}

export interface SceneLine {
  username: string;
  text: string;
  locationSignal?: boolean;
}

export interface SceneResponse {
  /** Null when the request had no frame to look at. */
  scene: SceneDescription | null;
  lines: SceneLine[];
  backend: "wandb" | "mock";
  models: { vision: string | null; text: string };
  timings: { frameMs: number; visionMs: number; textMs: number };
}

export interface Place {
  /** Landmark or district, e.g. "Trafalgar Square". */
  name: string;
  /** Reads after a verb: "at Tower Hill" or "near Trafalgar Square". */
  at: string;
  /** Postcode district such as "EC3N", or "" when the location has none. */
  area: string;
}

const DISTANCE_LABEL = /^\s*(\d+)\s*m from\s+(.+)$/i;
const POSTCODE_DISTRICT = /^[A-Z]{1,2}\d[A-Z\d]?$/;
const FAR_M = 2500;

/**
 * Turn what the location picker holds into words chat can say. GPS fixes arrive as labels like
 * "1560 m from Trafalgar Square" with an area of "GPS ±12 m"; neither should reach a viewer.
 */
export function normalizePlace(name: string, area: string): Place {
  const trimmed = name.trim();
  const district = POSTCODE_DISTRICT.test(area.trim().toUpperCase()) ? area.trim().toUpperCase() : "";
  const away = DISTANCE_LABEL.exec(trimmed);
  if (!away) return { name: trimmed, at: `at ${trimmed}`, area: district };
  if (Number(away[1]) > FAR_M) return { name: "London", at: "in London", area: "" };
  const landmark = away[2].trim();
  return { name: landmark, at: `near ${landmark}`, area: district };
}
