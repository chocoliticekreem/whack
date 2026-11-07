"use client";

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useId, useRef, type ReactNode } from "react";
import * as THREE from "three";
import { create } from "zustand";

/**
 * DOM labels for things in the scene. <Label> (inside the Canvas) registers an anchor object and
 * content; <LabelHost> (in the DOM overlay) renders them; <LabelLayer> projects them every frame.
 * Replaces drei's <Html>, whose per-label React roots get torn down in React 19 StrictMode and vanish.
 */
interface LabelEntry {
  anchor: THREE.Object3D;
  node: ReactNode;
  priority: number;
}

/** Cameras keep the default; the streamer sits above them, buses, stations and landmarks below. */
const DEFAULT_PRIORITY = 80;
/** Overlay panels labels must not slide under (the HUD and the Predict panel). */
const BLOCKER_SELECTOR = "[data-twin-block], .predict-panel";

const useLabels = create<{ labels: Map<string, LabelEntry> }>(() => ({ labels: new Map() }));
const elements = new Map<string, HTMLDivElement>();
const _v = new THREE.Vector3();

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const intersects = (a: Rect, b: Rect) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

export function Label({
  position,
  priority = DEFAULT_PRIORITY,
  children,
}: {
  position: [number, number, number];
  priority?: number;
  children: ReactNode;
}) {
  const id = useId();
  const anchor = useRef<THREE.Group>(null);
  useEffect(() => {
    if (!anchor.current) return;
    const labels = new Map(useLabels.getState().labels);
    labels.set(id, { anchor: anchor.current, node: children, priority });
    useLabels.setState({ labels });
  }, [id, children, priority]);
  useEffect(
    () => () => {
      const labels = new Map(useLabels.getState().labels);
      labels.delete(id);
      elements.delete(id);
      useLabels.setState({ labels });
    },
    [id],
  );
  return <group ref={anchor} position={position} />;
}

let host: HTMLDivElement | null = null;

function blockerRects(): Rect[] {
  if (!host) return [];
  const origin = host.getBoundingClientRect();
  const rects: Rect[] = [];
  for (const el of document.querySelectorAll<HTMLElement>(BLOCKER_SELECTOR)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    rects.push({ x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height });
  }
  return rects;
}

/** Mount once inside the Canvas. */
export function LabelLayer() {
  const { camera, size } = useThree();
  useFrame(() => {
    const placed = blockerRects();
    const candidates: { el: HTMLDivElement; rect: Rect; priority: number; px: number; py: number }[] = [];
    for (const [id, entry] of useLabels.getState().labels) {
      const el = elements.get(id);
      if (!el) continue;
      entry.anchor.getWorldPosition(_v).project(camera);
      if (_v.z > 1 || !entry.anchor.visible) {
        el.style.display = "none";
        continue;
      }
      const px = (_v.x * 0.5 + 0.5) * size.width;
      const py = (-_v.y * 0.5 + 0.5) * size.height;
      // Why: a hidden label measures 0×0; show it before reading its box so the first frame is right.
      el.style.display = "block";
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      candidates.push({ el, rect: { x: px - w / 2, y: py - h, w, h }, priority: entry.priority, px, py });
    }
    // Why: the camera moves every frame; a deterministic order keeps labels from flickering between winners.
    candidates.sort((a, b) => b.priority - a.priority || a.rect.y - b.rect.y || a.rect.x - b.rect.x);
    for (const c of candidates) {
      if (placed.some((r) => intersects(r, c.rect))) {
        c.el.style.display = "none";
        continue;
      }
      placed.push(c.rect);
      c.el.style.transform = `translate(-50%, -100%) translate(${c.px.toFixed(1)}px, ${c.py.toFixed(1)}px)`;
    }
  });
  return null;
}

/** Mount once in the DOM overlay, positioned over the canvas. */
export function LabelHost() {
  const labels = useLabels((s) => s.labels);
  return (
    <div
      ref={(el) => {
        host = el;
      }}
      style={{ position: "absolute", inset: 0, overflow: "hidden", pointerEvents: "none", zIndex: 20 }}
    >
      {Array.from(labels, ([id, entry]) => (
        <div
          key={id}
          ref={(el) => {
            if (el) elements.set(id, el);
            else elements.delete(id);
          }}
          style={{ position: "absolute", left: 0, top: 0, display: "none", willChange: "transform" }}
        >
          {entry.node}
        </div>
      ))}
    </div>
  );
}
