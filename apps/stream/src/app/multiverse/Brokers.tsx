import type { BrokerRow } from "./types";

const GLYPHS: Record<string, string> = {
  resident: "⌂",
  contra: "⇅",
  shop: "◧",
  event: "✦",
  weather: "☂",
  commuter: "⇢",
  tourist: "✈",
  bus: "◫",
  rider: "➤",
  lunch: "◔",
};
const FALLBACK = ["◆", "▲", "●", "■", "★", "◐", "⬢", "◈"];
export const HIGH_CONVICTION = 0.75;

export function brokerGlyph(b: Pick<BrokerRow, "id" | "archetype" | "glyph">): string {
  if (b.glyph) return b.glyph;
  const id = b.id.toLowerCase();
  const key = Object.keys(GLYPHS).find((k) => id.includes(k)) ?? Object.keys(GLYPHS).find((k) => b.archetype?.includes(k));
  if (key) return GLYPHS[key];
  const hash = [...b.id].reduce((h, c) => h + c.charCodeAt(0), 0);
  return FALLBACK[hash % FALLBACK.length];
}

export function shortName(id: string): string {
  return id.replace(/^B-/, "").slice(0, 8);
}

function Pnl({ values }: { values: number[] }) {
  return (
    <span className="flex h-6 flex-col justify-center" aria-hidden>
      <span className="flex h-3 items-end gap-0.5">
        {values.slice(-10).map((v, i) => (
          <span key={i} className={v >= 0 ? "mv-pnl-up" : ""} style={{ width: 6, height: v >= 0 ? Math.max(2, Math.min(12, v * 6)) : 0 }} />
        ))}
      </span>
      <span className="flex h-3 items-start gap-0.5">
        {values.slice(-10).map((v, i) => (
          <span key={i} className={v < 0 ? "mv-pnl-down" : ""} style={{ width: 6, height: v < 0 ? Math.max(2, Math.min(12, -v * 6)) : 0 }} />
        ))}
      </span>
    </span>
  );
}

export function Brokers({ rows }: { rows: BrokerRow[] }) {
  const lead = rows.reduce<BrokerRow | null>((best, b) => (!best || b.trackRecord > best.trackRecord ? b : best), null);
  return (
    <div className="mv-panel" data-accent="violet">
      <div className="mv-head">
        <span className="mv-label">Brokers</span>
        <span className="mv-label">{rows.length}</span>
      </div>
      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        {rows.map((b) => {
          const leading = b.id === lead?.id;
          const tip = [b.persona, b.rationale && `“${b.rationale}”`].filter(Boolean).join(" — ");
          return (
            <div key={b.id} className="mv-tip flex items-center gap-2 py-1.5" data-tip={tip}>
              <span className={`w-5 text-center text-base ${leading ? "c-amber" : (b.conviction ?? 0) >= HIGH_CONVICTION ? "c-violet" : "c-cyan"}`}>
                {brokerGlyph(b)}
              </span>
              <span
                className={`h-1.5 w-1.5 ${b.ideaFrom === "haiku" ? "mv-source-ai" : "mv-source-rules"}`}
                aria-hidden
              />
              <span className={`w-20 truncate ${leading ? "c-amber" : ""}`}>{shortName(b.id)}</span>
              <span className={`mv-num w-12 text-right ${b.trackRecord >= 0 ? "c-lime" : "c-magenta"}`}>
                {b.trackRecord >= 0 ? "+" : ""}
                {b.trackRecord.toFixed(2)}
              </span>
              <Pnl values={b.pnl} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
