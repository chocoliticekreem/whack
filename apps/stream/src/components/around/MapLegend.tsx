"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown, Layers } from "lucide-react";

export type LayerKind = "cam" | "livecam" | "station" | "bus" | "closure" | "crime";

interface Row {
  kind: LayerKind | null;
  label: string;
  swatch: ReactNode;
}

const ROWS: Row[] = [
  { kind: null, label: "You", swatch: <span className="size-3 rounded-full bg-ember ring-2 ring-bg" /> },
  {
    kind: "cam",
    label: "JamCam · YOLO count",
    swatch: <span className="size-3.5 rounded-full border-2 border-line-strong bg-surface-2 shadow-[0_0_0_3px_rgba(255,176,32,0.3)]" />,
  },
  { kind: "livecam", label: "HD live cam", swatch: <span className="size-3.5 rounded-full border-2 border-[#b388ff] bg-[#2a1f45]" /> },
  {
    kind: "station",
    label: "Station · % of usual crowd",
    swatch: (
      <span className="grid size-3.5 place-items-center rounded-full border-2 border-ok/70 bg-bg text-ok">
        <span className="block h-[2px] w-2 rounded-sm bg-current" />
      </span>
    ),
  },
  { kind: "bus", label: "Bus stop · next bus", swatch: <span className="size-3 rounded-[3px] bg-[#e0352b]" /> },
  { kind: "closure", label: "Road works / closure", swatch: <span className="h-0 w-4 border-t-2 border-dashed border-warn" /> },
  { kind: "crime", label: "Crime, last month", swatch: <span className="size-3.5 rounded-full bg-[radial-gradient(circle,rgba(255,120,60,0.8),rgba(255,45,85,0))]" /> },
  {
    kind: null,
    label: "People · vehicles on cam",
    swatch: (
      <span className="flex items-center gap-0.5">
        <span className="size-2 rounded-full bg-[#ffd166]" />
        <span className="h-2.5 w-1.5 rounded-[2px] bg-[#5ec8ff]" />
      </span>
    ),
  },
  { kind: null, label: "Calmer route", swatch: <span className="h-[3px] w-4 rounded-full bg-ember" /> },
];

interface MapLegendProps {
  counts: Partial<Record<LayerKind, number>>;
  hidden: ReadonlySet<LayerKind>;
  onToggle: (kind: LayerKind) => void;
}

/** What each marker means, doubling as the layer filter. Collapsed to one pill so the map stays clear. */
export function MapLegend({ counts, hidden, onToggle }: MapLegendProps) {
  const [open, setOpen] = useState(false);
  return (
    <div className="glass pointer-events-auto w-fit overflow-hidden rounded-[20px]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex h-9 items-center gap-2 px-3 text-[12px] font-bold text-text"
      >
        <Layers className="size-3.5 text-muted" aria-hidden />
        Legend
        {hidden.size > 0 && <span className="text-white/45">· {hidden.size} hidden</span>}
        <ChevronDown className={`size-3.5 text-muted transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open && (
        <ul className="w-[232px] px-1.5 pb-1.5">
          {ROWS.map((row) => {
            const off = row.kind !== null && hidden.has(row.kind);
            const body = (
              <>
                <span className="grid w-5 shrink-0 place-items-center">{row.swatch}</span>
                <span className="min-w-0 flex-1 truncate text-left">{row.label}</span>
                {row.kind !== null && <span className="font-mono text-[11px] text-white/45">{counts[row.kind] ?? 0}</span>}
              </>
            );
            return (
              <li key={row.label}>
                {row.kind === null ? (
                  <span className="flex h-7 items-center gap-2 px-1.5 text-[12px] font-semibold text-white/70">{body}</span>
                ) : (
                  <button
                    type="button"
                    aria-pressed={!off}
                    onClick={() => onToggle(row.kind as LayerKind)}
                    className={`flex h-7 w-full items-center gap-2 rounded-xl px-1.5 text-[12px] font-semibold transition-opacity hover:bg-white/[0.06] ${
                      off ? "text-white/40 opacity-50" : "text-text"
                    }`}
                  >
                    {body}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
