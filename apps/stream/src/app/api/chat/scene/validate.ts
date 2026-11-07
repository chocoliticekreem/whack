import type { SceneLine } from "@/agents/chat-crowd/scene";
import { repairJson, salvageObjects } from "./wandb";

export const MAX_LINE_CHARS = 120;

const PROFANITY: RegExp[] = [
  /n[i1!]+gg/i,
  /\bf[a@]gg?(?:[o0]t)?s?\b/i,
  /retard/i,
  /\btr[a@]nn(?:y|ie)/i,
  /\bch[i1]nks?\b/i,
  /\bp[a@]kis?\b/i,
  /\bsp[i1]cs?\b/i,
  /\bkys\b/i,
  /kill\s+(?:your|ur)self/i,
  /\bc[u*]nts?\b/i,
  /f[\W_]*u[\W_]*c[\W_]*k/i,
  /\bsh[i1]t/i,
  /\bbitch/i,
  /\bwank/i,
  /\bslut/i,
  /\bwhore/i,
  /\bdick/i,
  /\bpuss(?:y|ies)\b/i,
  /\bporn/i,
  /\bnazi/i,
  /\brape/i,
  /\bstab(?:bed|bing)?\b/i,
  /\b(?:shoot|bomb)(?:ing)?\b/i,
];

/** Streamers, celebrities and politicians the model must not put words in the mouth of. */
const REAL_PEOPLE =
  /\b(?:kai cenat|ishowspeed|i show speed|xqc|mrbeast|mr beast|valkyrae|emiru|cinna|airi sato|adin ross|pokimane|drake|taylor swift|kanye|trump|biden|starmer|sunak|farage|elon|musk|king charles|beckham|messi|ronaldo|harry kane|stormzy|skepta|central cee)\b/i;

const NOT_CHAT = /https?:|www\.|@\w|#\w|\b(?:as an ai|language model)\b/i;
/** Metres glued to "from" ("1560m from X") and km talk before the streamer has leaked. */
const METRES_FROM = /\d\s*m(?:etres?)?\s+(?:from|away)\b/i;
const KM_DISTANCE = /\d(?:\.\d)?\s*km\b/i;

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

function repeats(key: string, seen: string[]): boolean {
  const words = key.split(" ").length;
  return seen.some((s) => s === key || (words >= 4 && overlap(s, key) >= 0.8));
}

/** Why a line must not reach chat, or null when it is fine. */
export function rejection(text: string, leaked: boolean): string | null {
  if (PROFANITY.some((re) => re.test(text))) return "language";
  if (REAL_PEOPLE.test(text)) return "real person";
  if (NOT_CHAT.test(text)) return "not chat";
  if (METRES_FROM.test(text)) return "metres";
  if (!leaked && KM_DISTANCE.test(text)) return "distance before leak";
  return null;
}

export interface CleanOptions {
  allowed: Set<string>;
  recent: string[];
  leaked: boolean;
  max: number;
}

/**
 * Parse the model's reply and keep only lines that are usable: a known username (each at most once),
 * short, PG, not already said in recent chat or earlier in the batch. Tolerates fences, prose and a
 * mangled list; throws nothing, so an empty result means the caller should use local lines.
 */
export function cleanLines(raw: string, options: CleanOptions): SceneLine[] {
  const parsed = repairJson<{ lines?: unknown }>(raw);
  const items: unknown[] = Array.isArray(parsed?.lines) ? parsed.lines : salvageObjects(raw);
  const seen = options.recent.map(normalize);
  const used = new Set<string>();
  const lines: SceneLine[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null) continue;
    const { username, text, locationSignal } = item as Record<string, unknown>;
    if (typeof username !== "string" || typeof text !== "string") continue;
    if (!options.allowed.has(username) || used.has(username)) continue;
    const clean = text
      .replace(/^\s*@?(\w+)\s*:\s+/, (prefix, name: string) => (options.allowed.has(name) ? "" : prefix))
      .replace(/\s+/g, " ")
      .trim();
    if (!clean || clean.length > MAX_LINE_CHARS || rejection(clean, options.leaked)) continue;
    const key = normalize(clean);
    if (repeats(key, seen)) continue;
    used.add(username);
    seen.push(key);
    lines.push({ username, text: clean, locationSignal: locationSignal === true || undefined });
    if (lines.length >= options.max) break;
  }
  return lines;
}
