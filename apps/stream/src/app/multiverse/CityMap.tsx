import type { Geometry, ZoneStat } from "./types";

const PAD = 40;

/** Local metres (+x east, +z south) drawn top-down; disc = consensus people, magenta halo = spread across worlds. */
export function CityMap({ geometry, stats }: { geometry: Geometry | null; stats: ZoneStat[] }) {
  if (!geometry) return <div className="grid h-80 place-items-center c-dim">···</div>;
  const xs = geometry.segments.flatMap((s) => s.line.map((p) => p[0])).concat(geometry.zones.map((z) => z.x));
  const zs = geometry.segments.flatMap((s) => s.line.map((p) => p[1])).concat(geometry.zones.map((z) => z.z));
  const minX = Math.min(...xs) - PAD;
  const minZ = Math.min(...zs) - PAD;
  const w = Math.max(...xs) + PAD - minX;
  const h = Math.max(...zs) + PAD - minZ;
  const byId = new Map(stats.map((s) => [s.zoneId, s]));
  const maxMean = Math.max(1, ...stats.map((s) => s.mean + s.sd));
  const r = (v: number) => 10 + 60 * Math.sqrt(Math.max(v, 0) / maxMean);
  return (
    <svg viewBox={`${minX} ${minZ} ${w} ${h}`} className="h-auto w-full" role="img" aria-label="Zone uncertainty map">
      {geometry.segments.map((s) => (
        <polyline
          key={s.id}
          className="mv-street"
          data-major={s.kind === "major"}
          points={s.line.map((p) => `${p[0]},${p[1]}`).join(" ")}
          fill="none"
          strokeWidth={s.kind === "major" ? 7 : 3}
        />
      ))}
      {geometry.cams.map((c) => (
        <rect key={c.id} className="mv-cam" x={c.x - 6} y={c.z - 6} width={12} height={12}>
          <title>{c.name}</title>
        </rect>
      ))}
      {geometry.zones.map((z) => {
        const s = byId.get(z.id);
        if (!s) return null;
        const hot = Math.min(1, s.iv * 4);
        return (
          <g key={z.id}>
            <title>{`${z.name} · ${Math.round(s.mean)}±${Math.round(s.sd)}`}</title>
            <circle className="mv-halo" cx={z.x} cy={z.z} r={r(s.mean + s.sd) + 6} opacity={0.12 + 0.4 * hot} />
            <circle className="mv-disc" cx={z.x} cy={z.z} r={r(s.mean)} strokeWidth={2} />
            {r(s.mean) > 22 && (
              <text className="mv-disc-label" x={z.x} y={z.z + 7} textAnchor="middle" fontSize={20}>
                {Math.round(s.mean)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
