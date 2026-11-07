import type { ChatMessage, StreamLocation } from "@/lib/types";

/** Mirrors WhatIfScenarioId in apps/web/src/world/streamerContract.ts (the city owns the scenario list). */
export type WhatIfScenarioId =
  | "leak_location_50"
  | "crosswalk_surge_50"
  | "sniper_ambush"
  | "junction_accident"
  | "flash_mob_100";

/**
 * Cross-tab event bus. The stream page, the 3D city (Arvin) and any sim/agent tab
 * open on the same origin share one channel, so they integrate without a backend.
 */
export type BusEvent =
  | { type: "stream.location"; location: StreamLocation }
  | { type: "stream.viewers"; viewers: number }
  | { type: "chat.message"; message: ChatMessage }
  | { type: "streamer:location_leaked"; location: StreamLocation; viewers: number; at: number }
  | {
      type: "reality:fork";
      scenario: WhatIfScenarioId;
      label: string;
      location: StreamLocation;
      viewers: number;
    }
  | { type: "surroundings.update"; summary: string; level: "calm" | "busy" | "danger" }
  | { type: "sim.trigger"; kind: "chase" | "ambulance" | "clear" }
  | {
      type: "route.suggested";
      to: string;
      /** Calmer route as local twin metres [x, z] and as [lat, lon]. */
      xz: [number, number][];
      polyline: [number, number][];
      lengthM: number;
      etaMin: number;
      extraMin: number;
      lessCrowd: number;
    };

const CHANNEL = "classroom-47";

let channel: BroadcastChannel | null = null;
const local = new Set<(e: BusEvent) => void>();

function getChannel() {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") return null;
  channel ??= new BroadcastChannel(CHANNEL);
  return channel;
}

export function publish(event: BusEvent) {
  getChannel()?.postMessage(event);
  local.forEach((fn) => fn(event));
}

export function subscribe(fn: (e: BusEvent) => void) {
  const ch = getChannel();
  const onMessage = (m: MessageEvent<BusEvent>) => fn(m.data);
  ch?.addEventListener("message", onMessage);
  local.add(fn);
  return () => {
    ch?.removeEventListener("message", onMessage);
    local.delete(fn);
  };
}
