import type { ChatMessage, StreamLocation } from "@/lib/types";

/** Mirrors WhatIfScenarioId in apps/web/src/world/streamerContract.ts (the city owns the scenario list). */
/** "unknown" = no fresh camera counts and nothing else busy: not enough data to call the street calm. */
export type SurroundingsLevel = "calm" | "busy" | "danger" | "unknown";

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
  | { type: "surroundings.update"; summary: string; level: SurroundingsLevel }
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

/**
 * Events that describe current state rather than a moment. The latest of each is kept so a tab or
 * component that joins late (the /twin iframe mounting, a panel opening) starts from it instead of
 * waiting for the next change, and a repeat of the same state is not re-broadcast.
 */
const STICKY = new Set<BusEvent["type"]>(["stream.location", "stream.viewers", "surroundings.update", "route.suggested"]);

interface Latest {
  event: BusEvent;
  at: number;
  /** Published by this tab: only these are replayed to other tabs, so a relayed copy is never echoed back stale. */
  own: boolean;
  text: string;
}

/** Control messages share the channel with events; `__bus` keeps them apart. */
type Control = { __bus: "hello" } | { __bus: "replay"; at: number; event: BusEvent };

let channel: BroadcastChannel | null = null;
const local = new Set<(e: BusEvent) => void>();
const latest = new Map<BusEvent["type"], Latest>();

const isControl = (d: unknown): d is Control => typeof d === "object" && d !== null && "__bus" in d;

function remember(event: BusEvent, at: number, own: boolean): boolean {
  if (!STICKY.has(event.type)) return true;
  const prev = latest.get(event.type);
  if (prev && prev.at > at) return false;
  latest.set(event.type, { event, at, own, text: JSON.stringify(event) });
  return true;
}

function deliver(event: BusEvent) {
  local.forEach((fn) => fn(event));
}

function onChannel(m: MessageEvent<BusEvent | Control>) {
  const d = m.data;
  if (!isControl(d)) {
    remember(d, Date.now(), false);
    return;
  }
  if (d.__bus === "hello") {
    for (const l of latest.values()) {
      if (l.own) channel?.postMessage({ __bus: "replay", at: l.at, event: l.event } satisfies Control);
    }
    return;
  }
  if (remember(d.event, d.at, false)) deliver(d.event);
}

function getChannel() {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") return null;
  if (!channel) {
    channel = new BroadcastChannel(CHANNEL);
    channel.addEventListener("message", onChannel);
    // Ask the other tabs for their current state once, as this tab joins.
    channel.postMessage({ __bus: "hello" } satisfies Control);
  }
  return channel;
}

export function publish(event: BusEvent) {
  if (STICKY.has(event.type)) {
    const text = JSON.stringify(event);
    const prev = latest.get(event.type);
    if (prev?.own && prev.text === text) return;
  }
  remember(event, Date.now(), true);
  getChannel()?.postMessage(event);
  deliver(event);
}

/** Subscribers get every event from now on, plus the latest known state (location, surroundings…) right away. */
export function subscribe(fn: (e: BusEvent) => void) {
  const ch = getChannel();
  const onMessage = (m: MessageEvent<BusEvent | Control>) => {
    if (!isControl(m.data)) fn(m.data);
  };
  ch?.addEventListener("message", onMessage);
  local.add(fn);
  const snapshot = [...latest.values()].map((l) => l.event);
  queueMicrotask(() => {
    if (local.has(fn)) snapshot.forEach(fn);
  });
  return () => {
    ch?.removeEventListener("message", onMessage);
    local.delete(fn);
  };
}
