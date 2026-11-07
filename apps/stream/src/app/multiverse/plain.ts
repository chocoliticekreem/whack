import { londonClock, type Settled } from "./city-store";
import type { Market } from "./city-types";

/** Plain words for each recurring question; the backend question, rule and line stay in the details sheet. */
interface Copy {
  title: (at: string, m: Market) => string;
  labels?: Record<string, string>;
  /** Why the winner won, in a few words, e.g. "TfL said 20.1". */
  why?: (value: number | null, m: Market, r: Settled) => string | null;
}

const num = (v: number | null, digits: number) => (v == null ? null : v.toFixed(digits));

function place(id: string | undefined): string {
  if (!id) return "you";
  return id
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

const COPY: Record<string, Copy> = {
  hub_surge: {
    title: (at, m) => `Which of these ${m.options.length} areas gets busier fastest by ${at}?`,
    why: () => "it got busier fastest",
  },
  city_pulse: {
    title: (at) => `Will London's Tube get busier or quieter by ${at}?`,
    labels: { over: "Busier", under: "Quieter" },
    why: (v) => (v == null ? null : `TfL said ${num(v, 1)}`),
  },
  london_vix: {
    title: (at) => `Will London get more unpredictable or calmer by ${at}?`,
    labels: { over: "More unpredictable", under: "Calmer" },
    why: (v) => (v == null ? null : `the unpredictability score hit ${num(v, 2)}`),
  },
  heathrow_stack: {
    title: (at) => `More or fewer planes circling Heathrow at ${at}?`,
    labels: { over: "More planes", under: "Fewer planes" },
    why: (v) => (v == null ? null : `${Math.round(v)} planes were circling`),
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

/** "Quieter won: TfL said 20.1". */
export function plainResult(m: Market, r: Settled): string {
  if (r.winner == null) return "No result: every stake refunded";
  const won = `${plainLabel(m, r.winner)} won`;
  const fromReading = r.reading.match(/-?\d+(?:\.\d+)?/);
  const value = m.futures?.realised?.value ?? (fromReading ? Number(fromReading[0]) : null);
  const why = copyOf(m)?.why?.(value, m, r);
  return why ? `${won}: ${why}` : `${won}: ${r.reading.replace(/\s*→.*$/, "")}`;
}
