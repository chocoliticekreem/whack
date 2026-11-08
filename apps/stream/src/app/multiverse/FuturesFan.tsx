"use client";

import { useId } from "react";
import type { Futures } from "./city-types";

const W = 380;
const H = 76;
const PAD_Y = 6;
/** Why: past this index paths are hidden at phone width (city.css), so the fan stays legible at card width. */
const PHONE_PATHS = 20;

/** Lines are exact (a City Pulse line can sit on a 0.25 step above 10, e.g. 23.75), so they keep 2 dp. */
export function fmtValue(v: number, unit: string, digits = Math.abs(v) < 10 ? 2 : 1): string {
  const n = String(Number(v.toFixed(digits)));
  return unit === "%" ? `${n}%` : unit === "pts" ? `${n} pts` : n;
}

/** The futures behind a price: every simulated path (alive ones in ink, ruled-out ones fading), the real readings
 * so far as a solid line, the over/under line or bracket edges dashed, and once settled the realised value with the
 * future that landed closest. */
export function FuturesFan({ f, settled, overUnder }: { f: Futures; settled: boolean; overUnder: boolean }) {
  const fade = useId();
  const all = f.paths.flat();
  const real = f.real;
  const realised = settled && f.realised?.value != null ? f.realised.value : null;
  const closest = settled ? (f.realised?.closest ?? null) : null;
  const xs = [...all, ...real].map((p) => p[0]);
  // Why: the futures start from the reading at open, which can be stamped a few minutes before they do; that
  // reading is drawn at the futures' start rather than leaving the fan squeezed to the right.
  const x0 = f.paths.length ? Math.min(...f.paths.map((p) => p[0]?.[0] ?? Infinity)) : Math.min(...xs);
  const x1 = Math.max(...xs);
  const ys = [
    ...f.paths.filter((_, i) => f.alivePaths[i]).flat().map((p) => p[1]),
    ...real.map((p) => p[1]),
    ...f.lines,
    ...(realised == null ? [] : [realised]),
  ];
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  if (hi - lo < 1e-9) {
    lo -= 1;
    hi += 1;
  }
  const span = hi - lo;
  lo -= span * 0.08;
  hi += span * 0.08;
  const xOf = (t: number) => (x1 > x0 ? (Math.max(0, t - x0) / (x1 - x0)) * W : 0);
  const yOf = (v: number) => H - PAD_Y - ((v - lo) / (hi - lo)) * (H - 2 * PAD_Y);
  const d = (pts: [number, number][]) => pts.map(([t, v], i) => `${i ? "L" : "M"}${xOf(t).toFixed(1)},${yOf(v).toFixed(1)}`).join("");
  const end = real[real.length - 1];
  const lineText = overUnder && f.lines.length === 1 ? ` · line ${fmtValue(f.lines[0], f.unit, 2)}` : "";
  const caption = settled
    ? `${f.ruledOut} of ${f.n} futures ruled out, here's what happened`
    : `${f.n} simulated futures · ${f.ruledOut} ruled out by live data`;

  return (
    <figure className="tl-fan" data-settled={settled || undefined}>
      <div className="tl-fan-plot">
        <span className="tl-fan-label">
          {f.label}
          {lineText}
          {realised != null && (
            <>
              {" · "}
              <span className="tl-fan-real">settled {fmtValue(realised, f.unit)}</span>
            </>
          )}
        </span>
        <svg viewBox={`0 0 ${W} ${H}`} aria-hidden>
          <defs>
            <linearGradient id={fade} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2={W} y2="0">
              <stop offset="0" className="tl-fan-fade-a" />
              <stop offset="1" className="tl-fan-fade-b" />
            </linearGradient>
          </defs>
          {f.lines.map((v) => (
            <line key={v} className="tl-fan-line" x1={0} x2={W} y1={yOf(v)} y2={yOf(v)} />
          ))}
          {f.paths.map((p, i) =>
            i === closest ? null : (
              <path
                key={i}
                d={d(p)}
                className={f.alivePaths[i] ? "tl-fan-alive" : "tl-fan-out"}
                stroke={f.alivePaths[i] ? undefined : `url(#${fade})`}
                data-extra={i >= PHONE_PATHS || undefined}
              />
            ),
          )}
          {closest != null && f.paths[closest] && <path className="tl-fan-closest" d={d(f.paths[closest])} />}
          {real.length > 1 && <path className="tl-fan-now" d={d(real)} />}
          {end && <circle className="tl-fan-dot" cx={xOf(end[0])} cy={yOf(end[1])} r={2.5} />}
          {realised != null && (
            <circle className="tl-fan-settle" cx={end ? xOf(end[0]) : W} cy={yOf(realised)} r={3.5} />
          )}
        </svg>
      </div>
      <figcaption className="tl-fan-cap">
        <span>{caption}</span>
        {f.about && <span className="tl-fan-about">{f.about}</span>}
      </figcaption>
    </figure>
  );
}
