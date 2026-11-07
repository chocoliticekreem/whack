import type { BrokerRow } from "./types";

const QUOTES = 3;

function Pnl({ values }: { values: number[] }) {
  return (
    <span className="flex items-end gap-px" aria-hidden>
      {values.slice(-8).map((v, i) => (
        <span
          key={i}
          className={v >= 0 ? "bg-ok" : "bg-ember"}
          style={{ width: 4, height: Math.max(2, Math.min(14, Math.abs(v) * 4)) }}
        />
      ))}
    </span>
  );
}

export function Brokers({ rows }: { rows: BrokerRow[] }) {
  return (
    <div className="rounded-xl border border-line bg-surface text-sm">
      <div className="px-3 pt-2 text-xs uppercase tracking-wide text-muted">
        Brokers · reality marks every persona to market
      </div>
      <table className="w-full text-left">
        <thead className="text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-1">Broker</th>
            <th className="px-3 py-1 text-right">Track</th>
            <th className="px-3 py-1 text-right">Weight</th>
            <th className="px-3 py-1 text-right">Worlds</th>
            <th className="px-3 py-1 text-right">Conv.</th>
            <th className="px-3 py-1">P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.id} className="border-b border-line/60">
              <td className="px-3 py-1">
                <span className="font-medium">{b.persona}</span>
                <span className="ml-1 text-xs text-muted">{b.source === "haiku" ? "Haiku" : "rules"}</span>
              </td>
              <td className={`px-3 py-1 text-right tabular-nums ${b.trackRecord >= 0 ? "text-ok" : "text-ember"}`}>
                {b.trackRecord >= 0 ? "+" : ""}
                {b.trackRecord.toFixed(2)}
              </td>
              <td className="px-3 py-1 text-right tabular-nums">{(b.weight * 100).toFixed(0)}%</td>
              <td className="px-3 py-1 text-right tabular-nums">{b.liveWorlds}</td>
              <td className="px-3 py-1 text-right tabular-nums text-muted">{b.conviction?.toFixed(2) ?? "—"}</td>
              <td className="px-3 py-1">
                <Pnl values={b.pnl} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="space-y-1 px-3 py-2 text-xs text-muted">
        {rows.slice(0, QUOTES).map((b) => (
          <li key={b.id}>
            <span className="text-text">&ldquo;{b.rationale}&rdquo;</span> — {b.persona}
          </li>
        ))}
      </ul>
    </div>
  );
}
