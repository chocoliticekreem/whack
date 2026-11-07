"use client";

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

export const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Tweens a number to its new value over `ms`; jumps straight there with reduced motion. */
export function useCountUp(target: number, ms = 250, from = target): number {
  const [shown, setShown] = useState(from);
  const at = useRef(from);
  useEffect(() => {
    const start = at.current;
    if (start === target) return;
    const t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const k = reducedMotion() ? 1 : Math.min(1, (t - t0) / ms);
      const v = start + (target - start) * (1 - (1 - k) ** 3);
      at.current = v;
      setShown(v);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return shown;
}

/** Wraps a live number: it flashes green when the value rises and red when it falls. */
export function Tick({ value, className, children }: { value: number; className?: string; children: ReactNode }) {
  const [prev, setPrev] = useState(value);
  const [flash, setFlash] = useState<{ dir: "up" | "down"; n: number } | null>(null);
  if (value !== prev) {
    setPrev(value);
    setFlash({ dir: value > prev ? "up" : "down", n: (flash?.n ?? 0) + 1 });
  }
  return (
    <span key={flash?.n ?? 0} className={className} data-flash={flash?.dir}>
      {children}
    </span>
  );
}

export const signedPts = (n: number) => {
  const r = Math.round(n);
  return r > 0 ? `+${r.toLocaleString("en-GB")}` : r < 0 ? `−${(-r).toLocaleString("en-GB")}` : "±0";
};
export const toneOf = (n: number) => (Math.round(n) > 0 ? "up" : Math.round(n) < 0 ? "down" : undefined);

/** A tiny trend line; nothing until the value has actually moved. */
export function MiniSpark({ values, w = 56, h = 18, className = "ow-spark" }: { values: number[]; w?: number; h?: number; className?: string }) {
  const pts = values;
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  if (pts.length < 2 || lo === hi) return null;
  const span = hi - lo || 1;
  const d = pts
    .map((v, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * w).toFixed(1)},${(hi === lo ? h / 2 : 1.5 + (1 - (v - lo) / span) * (h - 3)).toFixed(1)}`)
    .join(" ");
  return (
    <svg className={className} data-tone={toneOf(pts[pts.length - 1] - pts[0])} viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden>
      <path d={d} />
    </svg>
  );
}

/** Closes a popover on Escape or a press outside `ref`. */
export function useDismiss(open: boolean, close: () => void, refs: RefObject<HTMLElement | null>[]) {
  const latest = useRef({ close, refs });
  useEffect(() => {
    latest.current = { close, refs };
  });
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (latest.current.refs.some((r) => r.current?.contains(t))) return;
      latest.current.close();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && latest.current.close();
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
}

const CONFETTI = ["#3df5ff", "#8b5cff", "#ff3da8", "#ffb547", "#b6ff5c"];

/** A small one-off burst of palette confetti from an element, about a second long; nothing with reduced motion. */
export function Confetti({ from }: { from: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = canvas.current;
    const src = document.querySelector(from)?.getBoundingClientRect();
    if (!c || !src || reducedMotion()) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = window.innerWidth * dpr;
    c.height = window.innerHeight * dpr;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    const ox = src.left + src.width / 2;
    const oy = src.bottom - 4;
    const bits = Array.from({ length: 34 }, (_, i) => {
      const a = Math.PI / 2 + (Math.random() - 0.5) * 2.2;
      const v = 3 + Math.random() * 4.5;
      return { x: ox, y: oy, vx: Math.cos(a) * v * (i % 2 ? 1 : -1), vy: Math.sin(a) * v - 1.5, r: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.4, w: 3 + Math.random() * 3, c: CONFETTI[i % CONFETTI.length] };
    });
    const t0 = performance.now();
    let raf = 0;
    const frame = (t: number) => {
      const k = (t - t0) / 1100;
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
      if (k >= 1) return;
      ctx.globalAlpha = k < 0.6 ? 1 : 1 - (k - 0.6) / 0.4;
      for (const b of bits) {
        b.x += b.vx;
        b.y += b.vy;
        b.vy += 0.22;
        b.vx *= 0.97;
        b.r += b.vr;
        ctx.save();
        ctx.translate(b.x, b.y);
        ctx.rotate(b.r);
        ctx.fillStyle = b.c;
        ctx.fillRect(-b.w / 2, -1.5, b.w, 3);
        ctx.restore();
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [from]);
  return <canvas ref={canvas} className="ow-confetti" aria-hidden />;
}
