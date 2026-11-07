"use client";

import {
  createContext,
  useContext,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { EyeOff, Maximize, Minimize, Video } from "lucide-react";
import { useFullscreen } from "../useFullscreen";

const SIZES = [
  { label: "S", width: 220 },
  { label: "M", width: 340 },
  { label: "L", width: 520 },
] as const;

const GAP = 16;
const TOOLBAR_CLEARANCE = 64;
// Why: the global Dev chip is fixed to the bottom-right corner, so bottom-right items sit above it.
const DEV_CHIP_CLEARANCE = 52;
const ASPECT = 0.5625;
const MOVE = "350ms cubic-bezier(0.2, 0.8, 0.2, 1)";
// A press that travels less than this is a click (swap), not a drag.
const DRAG_THRESHOLD = 5;
const CORNER_KEY = "c47.pip.corner.";

type Corner = "top-left" | "top-right" | "bottom-left" | "bottom-right";
const CORNERS: Corner[] = ["top-left", "top-right", "bottom-left", "bottom-right"];

/**
 * Where the shell wants stage actions (full screen, extras) rendered. Without it, they float inside the stage.
 * Why: on desktop the page already has a toolbar row; a second one inside the stage crowded the PiP and the map.
 */
export const StageToolbarSlot = createContext<HTMLElement | null>(null);

interface StageState {
  swapped: boolean;
  fullscreen: boolean;
}

interface PipStageProps {
  /** The main surface (map, twin). Stays mounted and keeps its DOM position when swapped. */
  primary: ReactNode;
  /** The picture-in-picture surface (the stream). Nothing is shown while null. */
  secondary: ReactNode | null;
  primaryLabel: string;
  secondaryLabel: string;
  /** Starting corner; the viewer's last drop (kept per stage in localStorage) wins over it. */
  corner: Corner;
  /** Starting size of the PiP. */
  size?: "S" | "M" | "L";
  /** Keeps the bottom corners above controls the embedded page draws along its bottom edge. */
  bottomInset?: number;
  /** Rendered above the main surface; receives the swap state so it can hide while the main is shrunk. */
  overlay?: (state: StageState) => ReactNode;
  /** Extra buttons placed left of the fullscreen button. */
  toolbar?: ReactNode;
  /** Center keeps the buttons clear of a corner that the embedded page already uses. */
  toolbarAlign?: "right" | "center";
  className?: string;
}

function readCorner(key: string, fallback: Corner): Corner {
  try {
    const saved = localStorage.getItem(CORNER_KEY + key);
    return CORNERS.find((c) => c === saved) ?? fallback;
  } catch {
    return fallback;
  }
}

function saveCorner(key: string, corner: Corner) {
  try {
    localStorage.setItem(CORNER_KEY + key, corner);
  } catch {}
}

const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), Math.max(min, max));

interface Gesture {
  pointer: number;
  startX: number;
  startY: number;
  /** PiP origin and size, relative to the stage. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Allowed range for the PiP origin: inside the stage and inside the viewport. */
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  stageW: number;
  stageH: number;
  moved: boolean;
}

/**
 * Two surfaces share one stage. Swapping only changes their geometry, never their place in the tree,
 * so an iframe or WebGL map keeps running instead of reloading.
 */
export function PipStage({
  primary,
  secondary,
  primaryLabel,
  secondaryLabel,
  corner: defaultCorner,
  size: defaultSize = "M",
  bottomInset = 0,
  overlay,
  toolbar,
  toolbarAlign = "right",
  className = "",
}: PipStageProps) {
  const [size, setSize] = useState(() => SIZES.findIndex((s) => s.label === defaultSize));
  const [swapped, setSwapped] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [corner, setCorner] = useState<Corner>(() => readCorner(primaryLabel, defaultCorner));
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const suppressClick = useRef(false);
  const stageRef = useRef<HTMLDivElement>(null);
  const { active: fullscreen, toggle: toggleFullscreen } = useFullscreen(stageRef);
  const slot = useContext(StageToolbarSlot);
  const toolbarInSlot = slot !== null && !fullscreen;

  const showPip = secondary !== null && !hidden;
  const width = `min(${SIZES[size].width}px, 46cqw)`;
  const height = `calc(${width} * ${ASPECT})`;
  const atTop = corner.startsWith("top");
  const atLeft = corner.endsWith("left");
  const topGap = toolbarInSlot ? GAP : TOOLBAR_CLEARANCE;
  const bottomGap = Math.max(bottomInset, atLeft ? GAP : DEV_CHIP_CLEARANCE);

  const pipGeometry: CSSProperties = drag
    ? { left: drag.x, top: drag.y, width, height }
    : {
        left: atLeft ? GAP : `calc(100% - ${GAP}px - ${width})`,
        top: atTop ? topGap : `calc(100% - ${bottomGap}px - ${height})`,
        width,
        height,
      };
  const fillGeometry: CSSProperties = { left: 0, top: 0, width: "100%", height: "100%" };

  function startDrag(e: ReactPointerEvent<HTMLButtonElement>) {
    suppressClick.current = false;
    const stage = stageRef.current;
    const pip = e.currentTarget.parentElement;
    if (e.button !== 0 || !stage || !pip) return;
    const s = stage.getBoundingClientRect();
    const p = pip.getBoundingClientRect();
    gesture.current = {
      pointer: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      x: p.left - s.left,
      y: p.top - s.top,
      w: p.width,
      h: p.height,
      minX: Math.max(GAP, GAP - s.left),
      maxX: Math.min(s.width, window.innerWidth - s.left) - p.width - GAP,
      minY: Math.max(GAP, GAP - s.top),
      maxY: Math.min(s.height, window.innerHeight - s.top) - p.height - GAP,
      stageW: s.width,
      stageH: s.height,
      moved: false,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function moveDrag(e: ReactPointerEvent<HTMLButtonElement>) {
    const g = gesture.current;
    if (!g || g.pointer !== e.pointerId) return;
    const dx = e.clientX - g.startX;
    const dy = e.clientY - g.startY;
    if (!g.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    g.moved = true;
    setDrag({ x: clamp(g.x + dx, g.minX, g.maxX), y: clamp(g.y + dy, g.minY, g.maxY) });
  }

  function endDrag(e: ReactPointerEvent<HTMLButtonElement>) {
    const g = gesture.current;
    if (!g || g.pointer !== e.pointerId) return;
    gesture.current = null;
    if (!g.moved) return;
    suppressClick.current = true;
    const x = clamp(g.x + e.clientX - g.startX, g.minX, g.maxX);
    const y = clamp(g.y + e.clientY - g.startY, g.minY, g.maxY);
    const next: Corner = `${y + g.h / 2 < g.stageH / 2 ? "top" : "bottom"}-${x + g.w / 2 < g.stageW / 2 ? "left" : "right"}`;
    setCorner(next);
    saveCorner(primaryLabel, next);
    setDrag(null);
  }

  function cancelDrag() {
    gesture.current = null;
    setDrag(null);
  }

  const layer = (key: string, isPip: boolean, children: ReactNode, label: string, canHide: boolean) => (
    <div
      key={key}
      className={`absolute overflow-hidden bg-black ${
        isPip
          ? "group z-20 rounded-[24px] shadow-[0_0_0_3px_rgb(255_255_255/0.16),0_20px_44px_-8px_rgb(0_0_0/0.75),0_0_34px_-6px_rgb(236_17_135/0.45)]"
          : "z-0 rounded-none"
      }`}
      style={{
        ...(isPip ? pipGeometry : fillGeometry),
        transition:
          isPip && drag
            ? `width ${MOVE}, height ${MOVE}`
            : `left ${MOVE}, top ${MOVE}, width ${MOVE}, height ${MOVE}, border-radius ${MOVE}`,
      }}
    >
      {children}
      {isPip && (
        <>
          <button
            type="button"
            aria-label={`Swap: show ${label} large. Drag to move.`}
            title="Click to swap · drag to move"
            onClick={() => {
              if (suppressClick.current) {
                suppressClick.current = false;
                return;
              }
              setSwapped((s) => !s);
            }}
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={endDrag}
            onPointerCancel={cancelDrag}
            className={`absolute inset-0 z-10 touch-none ${drag ? "cursor-grabbing" : "cursor-grab"}`}
          />
          <span
            aria-hidden
            className="pointer-events-none absolute left-1/2 top-1.5 z-20 h-1 w-10 -translate-x-1/2 rounded-full bg-white/70 shadow-[0_1px_4px_rgb(0_0_0/0.6)] transition-[width,background-color] duration-200 group-hover:w-14 group-hover:bg-white"
          />
          <div className="absolute right-2 top-3 z-20 flex items-center gap-1.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
            <button
              type="button"
              onClick={() => setSize((s) => (s + 1) % SIZES.length)}
              aria-label={`Resize picture-in-picture, now ${SIZES[size].label}`}
              className="pill pill-sm glass !h-7 !px-2.5"
            >
              {SIZES[size].label}
            </button>
            {canHide && (
              <button
                type="button"
                onClick={() => setHidden(true)}
                aria-label={`Hide ${secondaryLabel}`}
                className="pill pill-sm glass !h-7 !px-2.5"
              >
                <EyeOff className="size-3.5" aria-hidden />
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );

  const actions = (
    <>
      {toolbar}
      <button
        type="button"
        onClick={toggleFullscreen}
        aria-label={fullscreen ? "Exit full screen" : "Full screen"}
        className={toolbarInSlot ? "pill glass" : "pill pill-sm glass"}
      >
        {fullscreen ? <Minimize className="size-3.5" aria-hidden /> : <Maximize className="size-3.5" aria-hidden />}
        {fullscreen ? "Exit" : "Full screen"}
      </button>
    </>
  );

  return (
    <div
      ref={stageRef}
      className={`relative min-h-0 w-full overflow-hidden bg-black [container-type:inline-size] ${
        fullscreen ? "" : "rounded-[28px] shadow-[0_0_0_1px_rgb(255_255_255/0.08),0_30px_70px_-20px_rgb(0_0_0/0.8)]"
      } ${className}`}
    >
      {layer("primary", swapped && showPip, primary, primaryLabel, false)}
      {showPip && layer("secondary", !swapped, secondary, secondaryLabel, true)}

      <div className="pointer-events-none absolute inset-0 z-10">
        {overlay?.({ swapped: swapped && showPip, fullscreen: fullscreen })}
      </div>

      {toolbarInSlot ? (
        createPortal(actions, slot)
      ) : (
        <div className={`absolute top-3 z-30 flex items-center gap-2 ${toolbarAlign === "center" ? "left-1/2 -translate-x-1/2" : "right-3"}`}>
          {actions}
        </div>
      )}

      {secondary !== null && hidden && (
        <button
          type="button"
          onClick={() => setHidden(false)}
          className="pill pill-sm glass absolute z-20"
          style={{
            left: atLeft ? GAP : undefined,
            right: atLeft ? undefined : GAP,
            top: atTop ? topGap : undefined,
            bottom: atTop ? undefined : bottomGap,
          }}
        >
          <Video className="size-3.5" aria-hidden />
          Show {secondaryLabel}
        </button>
      )}
    </div>
  );
}
