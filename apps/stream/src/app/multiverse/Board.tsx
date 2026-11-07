import type { BrokerRow, WorldRow } from "./types";
import { brokerGlyph, HIGH_CONVICTION } from "./Brokers";

function Sparkline({ values, dead }: { values: number[]; dead: boolean }) {
  const w = 120;
  const h = 22;
  if (values.length < 2) return <svg width="100%" height={h} aria-hidden />;
  const max = Math.max(...values, 1e-9);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / max) * (h - 2) - 1}`).join(" ");
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
      <polyline className="mv-spark" data-dead={dead} fill="none" strokeWidth={1.5} points={pts} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function delta(history: number[]): number | null {
  if (history.length < 2 || history[history.length - 2] === 0) return null;
  return (history[history.length - 1] / history[history.length - 2] - 1) * 100;
}

function Detail({ world }: { world: WorldRow }) {
  return (
    <div className="mv-detail">
      {world.labels.map((l) => (
        <span key={l} className="mv-chip">{l}</span>
      ))}
      {world.trail.map((t) => (
        <span key={t.obs} className="mv-chip mv-num">
          <span className="c-cyan">#{t.obs}</span> {t.ll.toFixed(1)}
          {t.worst && <span className="c-dim">{t.worst.predicted.toFixed(0)}→{t.worst.observed.toFixed(0)}</span>}
        </span>
      ))}
      {world.delistReason && <span className="mv-chip c-magenta">{world.delistReason}</span>}
    </div>
  );
}

export function Board({
  rows,
  brokers,
  selected,
  onSelect,
}: {
  rows: WorldRow[];
  brokers: BrokerRow[] | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const byBroker = new Map((brokers ?? []).map((b) => [b.id, b]));
  const top = rows.reduce<WorldRow | null>((best, r) => (r.status === "live" && (!best || r.price > best.price) ? r : best), null);
  return (
    <div className="mv-panel mv-grain">
      <div className="mv-head">
        <span className="mv-label">Worlds</span>
        <span className="mv-label c-cyan mv-num">{rows.filter((r) => r.status === "live").length}</span>
      </div>
      <div className="mv-scroll">
        {rows.map((r) => {
          const dead = r.status === "delisted";
          const broker = r.broker ? byBroker.get(r.broker) : undefined;
          const d = delta(r.history);
          const open = selected === r.id;
          return (
            <div key={r.id}>
              <div
                role="button"
                tabIndex={0}
                onClick={() => onSelect(open ? null : r.id)}
                onKeyDown={(e) => e.key === "Enter" && onSelect(open ? null : r.id)}
                className="mv-row mv-tip"
                data-dead={dead}
                data-top={r.id === top?.id}
                data-conv={!dead && (broker?.conviction ?? 0) >= HIGH_CONVICTION}
                data-open={open}
                data-tip={dead ? (r.delistReason ?? undefined) : undefined}
              >
                <span className={r.id === top?.id ? "c-amber" : broker ? "c-violet" : "c-cyan"}>
                  {dead ? "✕" : broker ? brokerGlyph(broker) : "●"}
                </span>
                <span className={r.id === top?.id ? "c-amber" : ""}>{r.id}</span>
                <Sparkline values={r.history} dead={dead} />
                <span
                  key={`${r.id}-${r.history.length}`}
                  className={`mv-num text-right ${!dead && d != null ? (d >= 0 ? "mv-flash-up" : "mv-flash-down") : ""}`}
                >
                  {dead ? "—" : `${(r.price * 100).toFixed(1)}%`}
                </span>
                <span className={`mv-num text-right text-xs ${d == null || dead ? "c-dim" : d >= 0 ? "c-lime" : "c-magenta"}`}>
                  {dead || d == null ? "" : `${d >= 0 ? "▲" : "▼"}${Math.abs(d).toFixed(0)}`}
                </span>
              </div>
              {open && <Detail world={r} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}
