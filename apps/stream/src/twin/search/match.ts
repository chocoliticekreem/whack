import type { AroundCam, CamClipTracks, TrackedClass, TrackedObject } from "@/platform/around/types";

/**
 * Search across the live JamCams by what YOLO is tracking in each one right now: class, colour and how
 * many. The question is turned into a CamQuery by Nemotron (app/api/twin/search) or, if that is slow or
 * down, by `keywordQuery`; matching runs here, in the browser, over the clips the twin already holds.
 */

export const CLASSES: TrackedClass[] = ["person", "car", "bus", "truck", "motorcycle", "bicycle"];
export const COLORS = ["red", "orange", "yellow", "green", "blue", "white", "grey", "black"] as const;
export type ColorName = (typeof COLORS)[number];

export interface CamQuery {
  /** Empty = any class. */
  classes: TrackedClass[];
  /** Empty = any colour. */
  colors: ColorName[];
  /** A camera matches when it holds at least this many matching tracks. */
  minCount: number;
  /** Short human phrase for the result list, e.g. "red buses". */
  label: string;
}

export interface CamMatch {
  cam: AroundCam;
  count: number;
}

const WORDS: [RegExp, TrackedClass][] = [
  [/\b(people|person|pedestrians?|crowds?|walkers?|someone|man|woman|men|women|kids?)\b/, "person"],
  [/\b(cars?|taxis?|cabs?|suvs?)\b/, "car"],
  [/\b(bus|buses|coach(es)?|double.?deckers?)\b/, "bus"],
  [/\b(trucks?|lorr(y|ies)|vans?|hgvs?)\b/, "truck"],
  [/\b(motorbikes?|motorcycles?|scooters?|mopeds?)\b/, "motorcycle"],
  [/\b(bikes?|bicycles?|cyclists?)\b/, "bicycle"],
];

const COLOR_WORDS: [RegExp, ColorName][] = [
  [/\bred\b/, "red"],
  [/\borange\b/, "orange"],
  [/\byellow\b/, "yellow"],
  [/\bgreen\b/, "green"],
  [/\b(blue|navy)\b/, "blue"],
  [/\bwhite\b/, "white"],
  [/\b(gr[ae]y|silver)\b/, "grey"],
  [/\b(black|dark)\b/, "black"],
];

/** Offline parser: class and colour words, "crowd" for five or more people, "busy"/"traffic" for ten vehicles. */
export function keywordQuery(text: string): CamQuery {
  const q = text.toLowerCase();
  const classes = [...new Set(WORDS.filter(([re]) => re.test(q)).map(([, c]) => c))];
  const colors = [...new Set(COLOR_WORDS.filter(([re]) => re.test(q)).map(([, c]) => c))];
  const number = q.match(/\b(\d{1,3})\b/);
  let minCount = number ? Math.max(1, Number(number[1])) : 1;
  if (!number && /\bcrowd|busy|packed|lots of|many\b/.test(q)) minCount = classes.includes("person") || !classes.length ? 5 : 8;
  if (!number && /\b(traffic|jam|queue)\b/.test(q)) {
    minCount = 10;
    if (!classes.length) classes.push("car", "bus", "truck");
  }
  if (/\bcrowd/.test(q) && !classes.length) classes.push("person");
  return { classes, colors, minCount, label: text.trim().slice(0, 60) };
}

/**
 * Names the median colour YOLO sampled off a track. JamCam footage is dim and blue-cast, so anything
 * barely saturated counts as white, grey or black by brightness alone.
 */
export function colorName(hex: string | undefined): ColorName | null {
  if (!hex || !/^#[0-9a-f]{6}$/i.test(hex)) return null;
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const sat = max ? (max - min) / max : 0;
  if (sat < 0.26) return max >= 0.5 ? "white" : max >= 0.28 ? "grey" : "black";
  if (max < 0.22) return "black";
  let hue = 0;
  if (max === r) hue = ((g - b) / (max - min)) * 60;
  else if (max === g) hue = (2 + (b - r) / (max - min)) * 60;
  else hue = (4 + (r - g) / (max - min)) * 60;
  if (hue < 0) hue += 360;
  if (hue < 18 || hue >= 300) return "red";
  if (hue < 45) return "orange";
  if (hue < 70) return "yellow";
  if (hue < 165) return "green";
  return "blue";
}

function classOf(t: TrackedObject): TrackedClass {
  return t.cls ?? (t.kind === "person" ? "person" : "car");
}

export function matches(query: CamQuery, t: TrackedObject) {
  if (query.classes.length && !query.classes.includes(classOf(t))) return false;
  if (!query.colors.length) return true;
  const c = colorName(t.color);
  return c !== null && query.colors.includes(c);
}

/** Cameras holding at least `minCount` matching tracks in their latest clip, most matches first. */
export function searchCams(query: CamQuery, cams: AroundCam[], clips: Record<string, CamClipTracks>): CamMatch[] {
  const out: CamMatch[] = [];
  for (const cam of cams) {
    const clip = clips[cam.id];
    if (!clip) continue;
    let count = 0;
    for (const t of clip.tracks) if (matches(query, t)) count++;
    if (count >= query.minCount) out.push({ cam, count });
  }
  return out.sort((a, b) => b.count - a.count);
}

/** Keeps only fields a CamQuery can hold; anything the model invented is dropped. */
export function cleanQuery(raw: Partial<Record<keyof CamQuery, unknown>>, fallback: CamQuery): CamQuery {
  const classes = Array.isArray(raw.classes) ? raw.classes.filter((c): c is TrackedClass => CLASSES.includes(c as TrackedClass)) : fallback.classes;
  const colors = Array.isArray(raw.colors) ? raw.colors.filter((c): c is ColorName => COLORS.includes(c as ColorName)) : fallback.colors;
  const n = Number(raw.minCount);
  return {
    classes,
    colors,
    minCount: Number.isFinite(n) && n >= 1 ? Math.min(200, Math.round(n)) : fallback.minCount,
    label: typeof raw.label === "string" && raw.label.trim() ? raw.label.trim().slice(0, 60) : fallback.label,
  };
}
