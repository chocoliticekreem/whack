import type { WorldRow } from "./types";

function Sparkline({ values, dead }: { values: number[]; dead: boolean }) {
  const w = 96;
  const h = 24;
  if (values.length < 2) return <svg width={w} height={h} aria-hidden />;
  const max = Math.max(...values, 1e-9);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / max) * (h - 2) - 1}`).join(" ");
  const rising = values[values.length - 1] >= values[values.length - 2];
  return (
    <svg width={w} height={h} aria-hidden>
      <polyline fill="none" strokeWidth={1.5} points={pts} stroke={dead ? "var(--dim)" : rising ? "var(--ok)" : "var(--ember)"} />
    </svg>
  );
}

function change(history: number[]): string {
  if (history.length < 2 || history[history.length - 2] === 0) return "";
  const pct = (history[history.length - 1] / history[history.length - 2] - 1) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`;
}

export function Board({ rows, selected, onSelect }: { rows: WorldRow[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-2">World</th>
            <th className="px-3 py-2 text-right">Price</th>
            <th className="px-3 py-2">History</th>
            <th className="px-3 py-2">Parent</th>
            <th className="px-3 py-2">Assumptions / why it died</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const dead = r.status === "delisted";
            return (
              <tr
                key={r.id}
                onClick={() => onSelect(r.id)}
                className={`cursor-pointer border-b border-line/60 ${dead ? "opacity-40" : ""} ${selected === r.id ? "bg-surface-3" : "hover:bg-surface-2"}`}
              >
                <td className={`px-3 py-1.5 font-semibold ${dead ? "line-through" : ""}`}>{r.id}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">
                  {dead ? "delisted" : `${(r.price * 100).toFixed(1)}%`}
                  {!dead && <span className="ml-2 text-xs text-muted">{change(r.history)}</span>}
                </td>
                <td className="px-3 py-1.5">
                  <Sparkline values={r.history} dead={dead} />
                </td>
                <td className="px-3 py-1.5 text-muted">{r.parent ?? "—"}</td>
                <td className="px-3 py-1.5 text-xs text-muted">
                  {dead ? <span className="text-ember">{r.delistReason}</span> : r.labels.slice(0, 4).join(" · ")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
