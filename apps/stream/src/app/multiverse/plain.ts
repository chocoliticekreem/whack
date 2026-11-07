import { londonClock, type Settled } from "./city-store";
import type { Market } from "./city-types";

/** Plain words for each recurring question; the backend question, rule and line stay in the details sheet. */
interface Copy {
  title: (at: string, m: Market) => string;
  labels?: Record<string, string>;
  /** Why the winner won, in a few words, e.g. "TfL said 20.1". */
  why?: (value: number | null, m: Market, r: Settled) => string | null;
  /** What happened, as a sentence, e.g. "London got calmer (score 2.71, down from 2.87)". */
  story?: (value: number | null, line: number | null, r: Settled) => string | null;
}

const num = (v: number | null, digits: number) => (v == null ? null : v.toFixed(digits));

/** "3.68, up from 2.87": the settle value against the line the market opened at. */
function move(v: number | null, line: number | null, digits: number): string {
  if (v == null) return "";
  if (line == null) return num(v, digits) ?? "";
  const dir = v > line ? "up from" : v < line ? "down from" : "same as";
  return `${num(v, digits)}, ${dir} ${num(line, digits)}`;
}

function place(id: string | undefined): string {
  if (!id) return "you";
  return id
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

const COPY: Record<string, Copy> = {
  hub_surge: {
    title: (at, m) => `Which of these ${m.options.length} areas will see its crowd grow the most by ${at}?`,
    why: () => "its crowd grew the most",
  },
  city_pulse: {
    title: (at) => `Will London's Tube get busier or quieter by ${at}?`,
    labels: { over: "Busier", under: "Quieter" },
    why: (v) => (v == null ? null : `TfL said ${num(v, 1)}`),
    story: (v, l, r) => {
      const over = r.winner === "over";
      const vs = v == null || l == null ? "" : ` (busyness score ${num(v, 1)}, ${over ? "above" : "not above"} ${num(l, 1)})`;
      return `London's Tube got ${over ? "busier" : "quieter"}${vs}`;
    },
  },
  london_vix: {
    title: (at) => `Will London get more unpredictable or calmer by ${at}?`,
    labels: { over: "More unpredictable", under: "Calmer" },
    why: (v) => (v == null ? null : `the unpredictability score hit ${num(v, 2)}`),
    story: (v, l, r) => `London got ${r.winner === "over" ? "more unpredictable" : "calmer"} (score ${move(v, l, 2)})`,
  },
  heathrow_stack: {
    title: (at) => `More or fewer planes circling Heathrow at ${at}?`,
    labels: { over: "More planes", under: "Fewer planes" },
    why: (v) => (v == null ? null : `${Math.round(v)} planes were circling`),
    story: (v, l, r) => {
      const over = r.winner === "over";
      const vs = v == null || l == null ? "" : ` (${Math.round(v)}, ${over ? "more" : "no more"} than ${l})`;
      return `${over ? "More" : "Fewer"} planes circled Heathrow${vs}`;
    },
  },
  hub_hour: {
    title: (at) => `Which area has the busiest evening by ${at}?`,
    why: () => "it had the busiest evening",
  },
  clean_sheet: {
    title: (at) => `Will every Tube line keep running smoothly until ${at}?`,
    labels: { yes: "Yes, all smooth", no: "No, a line hits trouble" },
    why: (_v, _m, r) => {
      if (r.winner === "yes") return "every line ran smoothly";
      const line = r.reading.match(/^(.+?) broke the clean sheet/);
      return line ? `${line[1]} had problems` : "a line had problems";
    },
  },
};

const LOCAL: Copy = {
  title: (at, m) =>
    m.kind === "overunder"
      ? `Will it get busier or quieter near ${place(m.hotspots[0])} by ${at}?`
      : `What happens next near ${place(m.hotspots[0])} by ${at}?`,
  labels: { over: "Busier", under: "Quieter" },
};

export function seriesOf(m: Market): string {
  return m.series ?? m.id.replace(/-\d{4}$/, "");
}

function copyOf(m: Market): Copy | null {
  const s = seriesOf(m);
  return COPY[s] ?? (s.startsWith("local_") ? LOCAL : null);
}

export function plainTitle(m: Market): string {
  const c = copyOf(m);
  return c ? c.title(londonClock(m.settlesAt), m) : m.question;
}

export function plainLabel(m: Market, optionId: string): string {
  const fallback = m.options.find((o) => o.id === optionId)?.label ?? optionId;
  return copyOf(m)?.labels?.[optionId] ?? fallback;
}

/** "London got calmer (score 2.71, down from 2.87)", else "Quieter won: TfL said 20.1". */
export function plainResult(m: Market, r: Settled): string {
  if (r.winner == null) return "No result: every stake refunded";
  const fromReading = r.reading.match(/-?\d+(?:\.\d+)?/);
  const value = m.futures?.realised?.value ?? (fromReading ? Number(fromReading[0]) : null);
  const line = r.reading.match(/ vs (?:line )?(-?\d+(?:\.\d+)?)/);
  const c = copyOf(m);
  const story = c?.story?.(value, line ? Number(line[1]) : null, r);
  if (story) return story;
  const won = `${plainLabel(m, r.winner)} won`;
  const why = c?.why?.(value, m, r);
  return why ? `${won}: ${why}` : `${won}: ${r.reading.replace(/\s*→.*$/, "")}`;
}

/** The settle toast's line: what happened in London, then what it meant for you. */
export function plainSettle(m: Market, r: Settled, outcome: "won" | "lost" | "void", pts: string): string {
  if (outcome === "void") return `No data came in, so the question was cancelled. Your ${pts} points are back.`;
  return `${plainResult(m, r)}. You ${outcome} ${pts} points.`;
}

/** "95% as busy as a usual Saturday" with a day, "95% of usual" without. */
export function busyText(vsUsual: number, day?: string): string {
  const p = `${Math.round(vsUsual * 100)}%`;
  return day ? `${p} as busy as a usual ${day}` : `${p} of usual`;
}

const WATCHES: Record<string, string> = {
  tube: "How busy 20 Tube stations are vs a normal day",
  cams: "Street cameras: how unpredictable London is right now",
  lines: "Whether each Tube line is running normally",
  rain: "Whether it is raining in central London",
  bus: "Planes circling Heathrow, waiting to land",
  contrarian: "What everyone else bets, then bets the other way",
  sim: "A computer model of central London's streets",
};

/** What a computer trader watches, in plain words; the backend's precise feed name stays in its tooltip. */
export function plainWatches(id: string, fallback: string): string {
  return WATCHES[id] ?? fallback;
}

/** "2 wins, 4 losses". */
export function recordText(won: number, lost: number): string {
  return `${won} ${won === 1 ? "win" : "wins"}, ${lost} ${lost === 1 ? "loss" : "losses"}`;
}

const secOfDay = (hms: string) => {
  const [h, mi, s] = hms.split(":").map(Number);
  return h * 3600 + mi * 60 + (s || 0);
};

/** Seconds from now until a London "HH:MM:SS", or null once it has passed. */
function secondsUntil(hms: string, now: number): number | null {
  const d = (secOfDay(hms) - secOfDay(londonClock(now, true)) + 86400) % 86400;
  return d > 0 && d < 3600 ? d : null;
}

/** The trigger text from services/city/integrity.py, e.g. "Central now Severe Delays", in plain words. */
function pauseCause(what: string): string {
  const line = what.match(/^(.+?) now (.+)$/);
  if (line) return `the ${line[1].replace(/ line$/i, "")} line just changed to ${line[2].toLowerCase()}`;
  if (what.startsWith("London VIX")) return "London's unpredictability score just jumped";
  if (what.startsWith("City Pulse")) return "the Tube's busyness just jumped";
  if (what.startsWith("Heathrow holding")) return "the number of planes circling Heathrow just changed";
  const hub = what.match(/^(.+?) crowding ([+-])/);
  if (hub) return `${hub[1]} suddenly got ${hub[2] === "+" ? "busier" : "quieter"}`;
  return "the live data just changed sharply";
}

/** Why a paused market is paused, from the exchange's haltReason (services/city/exchange.py). */
export function pausedText(reason: string | undefined, now: number): string {
  if (!reason) return "Paused while new data comes in.";
  if (reason === "stale feed") return "Paused while new data comes in. The live feed is running late.";
  const jump = reason.match(/^jump: (.+?) [+−-]?\d/);
  if (jump) return `Paused for a moment: ${jump[1]} suddenly changed a lot. Betting reopens once it settles.`;
  const sus = reason.match(/^suspended: (.+?); reopens (\d\d:\d\d:\d\d)/);
  if (!sus) return "Paused while new data comes in.";
  const left = secondsUntil(sus[2], now);
  return `Paused for a moment: ${pauseCause(sus[1])}. ${left == null ? "Betting reopens any second." : `Betting reopens in ${left} s.`}`;
}
