import type { Geometry, ZoneStat } from "./types";

const PAD = 40;

/** Local metres (+x east, +z south) drawn top-down; zone disc = consensus people, halo = spread across worlds. */
export function CityMap({ geometry, stats, label }: { geometry: Geometry | null; stats: ZoneStat[]; label: string }) {
  if (!geometry) return <div className="grid h-80 place-items-center rounded-xl border border-line text-muted">Loading streets…</div>;
  const xs = geometry.segments.flatMap((s) => s.line.map((p) => p[0])).concat(geometry.zones.map((z) => z.x));
  const zs = geometry.segments.flatMap((s) => s.line.map((p) => p[1])).concat(geometry.zones.map((z) => z.z));
  const minX = Math.min(...xs) - PAD;
  const minZ = Math.min(...zs) - PAD;
  const w = Math.max(...xs) + PAD - minX;
  const h = Math.max(...zs) + PAD - minZ;
  const byId = new Map(stats.map((s) => [s.zoneId, s]));
  const maxMean = Math.max(1, ...stats.map((s) => s.mean + s.sd));
  const r = (v: number) => 8 + 60 * Math.sqrt(Math.max(v, 0) / maxMean);
  return (
    <div className="rounded-xl border border-line bg-surface p-2">
      <div className="px-2 pb-1 text-xs uppercase tracking-wide text-muted">{label}</div>
      <svg viewBox={`${minX} ${minZ} ${w} ${h}`} className="h-auto w-full" role="img" aria-label="Zone uncertainty map">
        {geometry.segments.map((s) => (
          <polyline
            key={s.id}
            points={s.line.map((p) => `${p[0]},${p[1]}`).join(" ")}
            fill="none"
            stroke={s.kind === "major" ? "var(--border-strong)" : "var(--border)"}
            strokeWidth={s.kind === "major" ? 6 : 3}
          />
        ))}
        {geometry.zones.map((z) => {
          const s = byId.get(z.id);
          if (!s) return null;
          const hot = Math.min(1, s.iv);
          return (
            <g key={z.id}>
              <circle cx={z.x} cy={z.z} r={r(s.mean + s.sd)} fill={`rgb(236 17 135 / ${0.08 + 0.3 * hot})`} />
              <circle cx={z.x} cy={z.z} r={r(s.mean)} fill="rgb(255 141 16 / 0.55)" stroke="var(--candy-orange)" />
              <text x={z.x} y={z.z - r(s.mean + s.sd) - 6} textAnchor="middle" fontSize={20} fill="var(--text)">
                {z.name}
              </text>
              <text x={z.x} y={z.z + 7} textAnchor="middle" fontSize={18} fill="var(--bg)" fontWeight={700}>
                {Math.round(s.mean)}±{Math.round(s.sd)}
              </text>
            </g>
          );
        })}
        {geometry.cams.map((c) => (
          <rect key={c.id} x={c.x - 7} y={c.z - 7} width={14} height={14} fill="var(--ok)">
            <title>{c.name}</title>
          </rect>
        ))}
      </svg>
      <div className="px-2 pt-1 text-xs text-muted">
        Disc: weighted mean people across live worlds. Pink halo: the spread (implied volatility). Green: cameras.
      </div>
    </div>
  );
}
