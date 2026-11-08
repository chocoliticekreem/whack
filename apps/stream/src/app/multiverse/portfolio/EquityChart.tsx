"use client";

import { useState } from "react";
import { signed, tone, when } from "./data";

const W = 1000;
const H = 220;
const PAD = { t: 12, b: 12 };

/** Cumulative P&L after each settled bet, from GET /history `series` ([settledAt, cumulative]). */
export function EquityChart({ series }: { series: [number, number][] }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts: [number, number][] = series.length ? [[series[0][0], 0], ...series] : [];
  if (pts.length < 2) return <div className="pf-empty pf-chart-empty">The curve starts with your first settled bet.</div>;
  const ys = pts.map((p) => p[1]);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (pts.length - 1)) * W;
  const y = (v: number) => PAD.t + (1 - (v - lo) / span) * (H - PAD.t - PAD.b);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const area = `${line} L${W},${y(0).toFixed(1)} L0,${y(0).toFixed(1)} Z`;
  const last = ys[ys.length - 1];
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left) / r.width) * (pts.length - 1));
    setHover(Math.max(1, Math.min(pts.length - 1, i)));
  };
  const pctY = (v: number) => `${(y(v) / H) * 100}%`;
  return (
    <figure className="pf-chart">
      <div className="pf-chart-plot">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          role="img"
          aria-label={`Cumulative P&L over ${pts.length - 1} settled bets, now ${signed(last)}`}
        >
          <path className="pf-area" data-tone={tone(last)} d={area} />
          <line className="pf-zero" x1={0} x2={W} y1={y(0)} y2={y(0)} vectorEffect="non-scaling-stroke" />
          <path className="pf-line" data-tone={tone(last)} d={line} vectorEffect="non-scaling-stroke" />
          {hover != null && <line className="pf-cross" x1={x(hover)} x2={x(hover)} y1={0} y2={H} vectorEffect="non-scaling-stroke" />}
        </svg>
        {hover != null && (
          <span className="pf-dot" data-tone={tone(last)} style={{ left: `${(x(hover) / W) * 100}%`, top: pctY(pts[hover][1]) }} />
        )}
        <span className="pf-ylab tl-data" style={{ top: pctY(hi) }}>
          {signed(hi)}
        </span>
        {lo < 0 && hi > 0 && (
          <span className="pf-ylab tl-data" style={{ top: pctY(0) }}>
            0
          </span>
        )}
        <span className="pf-ylab tl-data" style={{ top: pctY(lo) }}>
          {signed(lo)}
        </span>
        {hover != null && (
          <div className="pf-tip tl-data" style={{ left: `${Math.min(85, Math.max(15, (x(hover) / W) * 100))}%` }}>
            <b data-tone={tone(pts[hover][1])}>{signed(pts[hover][1])}</b>
            <span>
              after bet {hover} · {when(pts[hover][0])}
            </span>
          </div>
        )}
      </div>
      <figcaption className="pf-axis tl-data">
        <span>{when(pts[1][0])}</span>
        <span>
          {pts.length - 1} settled {pts.length - 1 === 1 ? "bet" : "bets"}
        </span>
        <span>{when(pts[pts.length - 1][0])}</span>
      </figcaption>
    </figure>
  );
}
