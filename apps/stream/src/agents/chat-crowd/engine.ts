import type { Archetype, ChatMessage, Persona, StreamContext } from "@/lib/types";
import { DISTANCES, DONATION_LINES, FIRST_MESSAGE_LINES, LEAD_INS, LINES, type LineKind } from "./lines";
import { PERSONAS, PERSONAS_BY_ARCHETYPE } from "./personas";
import { normalizePlace, type CrowdEvent, type SceneResponse } from "./scene";

export type Rng = () => number;

export const SURGE_MS = 45_000;

const MEMORY_TEXTS = 60;
const MEMORY_SPEAKERS = 8;
const PERSONA_COOLDOWN_MS = 25_000;
const MAX_MESSAGES_PER_SECOND = 2.5;

const AMBIENT_CAST: [Archetype, number][] = [
  ["hype", 20],
  ["local", 18],
  ["questioner", 16],
  ["superfan", 14],
  ["hater", 12],
  ["opportunist", 8],
  ["mod", 3],
];

const SURGE_CAST: [Archetype, number][] = [
  ["opportunist", 3],
  ["local", 3],
  ["superfan", 1],
  ["questioner", 1],
  ["hater", 1],
  ["hype", 1],
  ["mod", 0.5],
];

const CAST: Record<CrowdEvent, [Archetype, number][]> = {
  ambient: AMBIENT_CAST,
  location_leaked: [
    ["opportunist", 3],
    ["local", 3],
    ["superfan", 1],
    ["hater", 1],
    ["questioner", 1],
    ["mod", 1],
    ["hype", 1],
  ],
  mirror: [
    ["questioner", 3],
    ["hype", 1],
    ["local", 1],
    ["hater", 1],
    ["superfan", 1],
    ["opportunist", 1],
    ["mod", 1],
  ],
  streamer_message: [
    ["hype", 2],
    ["superfan", 2],
    ["hater", 1],
    ["questioner", 1],
    ["local", 1],
    ["opportunist", 1],
  ],
};

export const EVENT_LINES: Record<CrowdEvent, LineKind> = {
  ambient: "ambient",
  streamer_message: "streamer",
  location_leaked: "leaked",
  mirror: "mirror",
};

const LEAD_IN_ARCHETYPES: Archetype[] = ["hype", "opportunist", "hater", "local"];

/** What chat said recently, so the local crowd neither repeats a line nor lets one username talk twice in a row. */
export interface CrowdMemory {
  texts: string[];
  speakers: string[];
  spokeAt: Map<string, number>;
  uses: Map<string, number>;
  bursting: boolean;
  phaseEnd: number;
  /** Ambient chatter stays low until this time so an event's reactions get the floor. */
  quietUntil: number;
}

export function createMemory(): CrowdMemory {
  return {
    texts: [],
    speakers: [],
    spokeAt: new Map(),
    uses: new Map(),
    bursting: false,
    phaseEnd: 0,
    quietUntil: 0,
  };
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim() || text;
}

function overlap(a: string, b: string): number {
  const wa = new Set(a.split(" "));
  const wb = new Set(b.split(" "));
  let shared = 0;
  wa.forEach((w) => wb.has(w) && shared++);
  return shared / Math.max(wa.size, wb.size);
}

export function isFresh(memory: CrowdMemory, text: string): boolean {
  const key = normalize(text);
  const words = key.split(" ").length;
  return !memory.texts.some((seen) => seen === key || (words >= 4 && overlap(seen, key) >= 0.8));
}

export function remember(memory: CrowdMemory, username: string, text: string, now: number) {
  memory.texts.push(normalize(text));
  if (memory.texts.length > MEMORY_TEXTS) memory.texts.shift();
  memory.speakers.push(username);
  if (memory.speakers.length > MEMORY_SPEAKERS) memory.speakers.shift();
  memory.spokeAt.set(username, now);
}

function pick<T>(items: T[], rng: Rng): T {
  return items[Math.floor(rng() * items.length)];
}

function pickWeighted<T>(items: [T, number][], rng: Rng): T {
  const total = items.reduce((s, [, w]) => s + w, 0);
  let roll = rng() * total;
  for (const [item, w] of items) {
    roll -= w;
    if (roll < 0) return item;
  }
  return items[0][0];
}

function pickPersona(
  archetype: Archetype | undefined,
  avoid: ReadonlySet<string>,
  rng: Rng,
  spokeAt?: Map<string, number>,
  now = 0,
): Persona {
  const pool = archetype ? PERSONAS_BY_ARCHETYPE[archetype] : PERSONAS;
  const rested = pool.filter(
    (p) => !avoid.has(p.username) && now - (spokeAt?.get(p.username) ?? -Infinity) > PERSONA_COOLDOWN_MS,
  );
  if (rested.length > 0) return pick(rested, rng);
  const free = pool.filter((p) => !avoid.has(p.username));
  return pick(free.length > 0 ? free : pool, rng);
}

/** Personas for one batch (local reaction or model call): distinct, weighted by event, not heard from lately. */
export function pickCast(
  event: CrowdEvent,
  count: number,
  avoid: ReadonlySet<string>,
  rng: Rng = Math.random,
  surging = false,
): Persona[] {
  const weights = event === "ambient" && surging ? SURGE_CAST : CAST[event];
  const taken = new Set(avoid);
  const cast: Persona[] = [];
  for (let i = 0; i < count; i++) {
    const persona = pickPersona(pickWeighted(weights, rng), taken, rng);
    if (taken.has(persona.username)) continue;
    taken.add(persona.username);
    cast.push(persona);
  }
  return cast;
}

function fill(template: string, values: Record<string, string | undefined>): string | null {
  let missing = false;
  const text = template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = values[key];
    if (value === undefined || value === "") missing = true;
    return value ?? "";
  });
  return missing ? null : text;
}

/** The part of the stream context a line template reads. */
export type LineContext = Pick<StreamContext, "streamer" | "locationRevealedAt"> & {
  location: { name: string; area: string };
};

function placeholders(ctx: LineContext, rng: Rng): Record<string, string | undefined> {
  const place = normalizePlace(ctx.location.name, ctx.location.area);
  const knowsPlace = place.name !== "London";
  return {
    loc: place.name,
    at: place.at,
    area: place.area,
    streamer: ctx.streamer,
    dist: ctx.locationRevealedAt != null && knowsPlace ? pick(DISTANCES, rng) : undefined,
  };
}

/** A fresh line for this archetype and moment, preferring templates used least; null when every candidate was said lately. */
export function compose(
  memory: CrowdMemory,
  ctx: LineContext,
  archetype: Archetype,
  kind: LineKind,
  rng: Rng,
): string | null {
  const values = placeholders(ctx, rng);
  const candidates = [...LINES[archetype][kind]]
    .map((template) => ({ template, order: (memory.uses.get(template) ?? 0) + rng() * 0.9 }))
    .sort((a, b) => a.order - b.order);
  for (const { template } of candidates) {
    let text = fill(template, values);
    if (!text) continue;
    if (LEAD_IN_ARCHETYPES.includes(archetype) && /^[a-z]/.test(text) && rng() < 0.2) text = pick(LEAD_INS, rng) + text;
    if (!isFresh(memory, text)) continue;
    memory.uses.set(template, (memory.uses.get(template) ?? 0) + 1);
    return text;
  }
  return null;
}

let seq = 0;

function makeMessage(
  persona: Persona,
  text: string,
  now: number,
  extra: Partial<ChatMessage> = {},
): ChatMessage {
  seq += 1;
  return {
    id: `m-${now}-${seq}`,
    personaId: persona.id,
    username: persona.username,
    color: persona.color,
    badges: persona.badges,
    text,
    timestamp: now,
    source: "local",
    ...extra,
  };
}

/** 0..1 — how far into the post-reveal surge window we are (1 = just revealed). */
export function surgeIntensity(ctx: StreamContext, now: number): number {
  if (ctx.locationRevealedAt == null) return 0;
  const elapsed = now - ctx.locationRevealedAt;
  if (elapsed < 0 || elapsed > SURGE_MS) return 0;
  return 1 - elapsed / SURGE_MS;
}

/**
 * Chat arrives in bursts with lulls between them, never above ~2 lines a second.
 * Busier after the streamer leaks their spot, scaled down when the model is writing most of the lines.
 */
export function messagesPerSecond(ctx: StreamContext, now: number, memory: CrowdMemory, scale = 1): number {
  const base = Math.min(1.4, 0.6 + ctx.viewers / 4000);
  const phase = memory.bursting ? 1.8 : 0.35;
  const quiet = now < memory.quietUntil ? 0.3 : 1;
  return Math.min(MAX_MESSAGES_PER_SECOND, base * phase * quiet * scale * (1 + 0.8 * surgeIntensity(ctx, now)));
}

function oneMessage(ctx: StreamContext, now: number, memory: CrowdMemory, rng: Rng): ChatMessage | null {
  const surge = surgeIntensity(ctx, now);
  const avoid = new Set(memory.speakers);

  if (surge > 0 && rng() < 0.2 + 0.5 * surge) {
    const persona = pickPersona(pickWeighted(SURGE_CAST, rng), avoid, rng, memory.spokeAt, now);
    const text = compose(memory, ctx, persona.archetype, "surge", rng);
    if (!text) return null;
    const signal = persona.archetype === "opportunist" || persona.archetype === "local";
    return makeMessage(persona, text, now, { locationSignal: signal || undefined });
  }

  const roll = rng();
  if (roll < 0.004) {
    const persona = pickPersona("superfan", avoid, rng, memory.spokeAt, now);
    const text = fill(pick(DONATION_LINES, rng), placeholders(ctx, rng));
    if (!text || !isFresh(memory, text)) return null;
    return makeMessage(persona, text, now, { highlight: "donation", amount: pick([2, 5, 5, 10, 20, 50], rng) });
  }
  if (roll < 0.012) {
    const persona = pickPersona("questioner", avoid, rng, memory.spokeAt, now);
    const text = fill(pick(FIRST_MESSAGE_LINES, rng), placeholders(ctx, rng));
    if (!text || !isFresh(memory, text)) return null;
    return makeMessage(persona, text, now, { highlight: "first" });
  }

  const persona = pickPersona(pickWeighted(AMBIENT_CAST, rng), avoid, rng, memory.spokeAt, now);
  const text = compose(memory, ctx, persona.archetype, "ambient", rng);
  return text ? makeMessage(persona, text, now) : null;
}

/** Messages to emit for one tick of `dtMs`: at most one, so the pace follows messagesPerSecond. */
export function nextMessages(
  ctx: StreamContext,
  now: number,
  memory: CrowdMemory,
  rng: Rng = Math.random,
  dtMs = 250,
  scale = 1,
): ChatMessage[] {
  if (!ctx.live) return [];
  if (now >= memory.phaseEnd) {
    memory.bursting = !memory.bursting;
    memory.phaseEnd = now + (memory.bursting ? 2000 + rng() * 2500 : 3500 + rng() * 3500);
  }
  if (rng() >= (messagesPerSecond(ctx, now, memory, scale) * dtMs) / 1000) return [];
  const message = oneMessage(ctx, now, memory, rng);
  return message ? [message] : [];
}

export function streamerMessage(text: string, ctx: StreamContext, now: number): ChatMessage {
  seq += 1;
  return {
    id: `s-${now}-${seq}`,
    personaId: null,
    username: ctx.streamer,
    color: "var(--ember)",
    badges: ["verified"],
    text,
    timestamp: now,
    fromStreamer: true,
  };
}

/** A line scheduled `delayMs` from now, from either the local crowd or the model. */
export interface PlannedLine {
  persona: Persona;
  text: string;
  locationSignal?: boolean;
  scene?: string;
  source: "wandb" | "local";
  delayMs: number;
}

/** Local reaction to an event: a short staggered burst in the voices that fit it. Sets memory.quietUntil. */
export function reactionPlan(
  event: CrowdEvent,
  ctx: StreamContext,
  memory: CrowdMemory,
  now: number,
  rng: Rng = Math.random,
  count = 6,
): PlannedLine[] {
  const cast = pickCast(event, count, new Set(memory.speakers), rng);
  const plan: PlannedLine[] = [];
  let delayMs = 400 + rng() * 400;
  for (const persona of cast) {
    const kind: LineKind =
      event === "streamer_message" && surgeIntensity(ctx, now) > 0 && rng() < 0.5 ? "surge" : EVENT_LINES[event];
    const text = compose(memory, ctx, persona.archetype, kind, rng);
    if (!text) continue;
    const signal = (kind === "leaked" || kind === "surge") && (persona.archetype === "opportunist" || persona.archetype === "local");
    plan.push({ persona, text, locationSignal: signal || undefined, source: "local", delayMs });
    delayMs += 400 + rng() * 600;
  }
  memory.quietUntil = Math.max(memory.quietUntil, now + delayMs + 2000);
  return plan;
}

/** Model lines mapped to personas, spread from `startMs` with a human-ish gap between them. */
export function linesFromResponse(
  res: SceneResponse,
  personas: Persona[],
  startMs: number,
  rng: Rng = Math.random,
): PlannedLine[] {
  const byName = new Map(personas.map((p) => [p.username, p]));
  let delayMs = startMs;
  const planned: PlannedLine[] = [];
  for (const line of res.lines) {
    const persona = byName.get(line.username);
    if (!persona) continue;
    planned.push({
      persona,
      text: line.text,
      locationSignal: line.locationSignal,
      scene: res.scene?.scene,
      source: "wandb",
      delayMs,
    });
    delayMs += 600 + rng() * 700;
  }
  return planned;
}

export function plannedMessage(line: PlannedLine, now: number): ChatMessage {
  return makeMessage(line.persona, line.text, now, {
    locationSignal: line.locationSignal || undefined,
    scene: line.scene,
    source: line.source,
  });
}
