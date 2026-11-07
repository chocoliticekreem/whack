"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, BadgeCheck, ChevronDown, MapPinned, Sparkles } from "lucide-react";
import { LOCATIONS } from "@/lib/locations";
import type { StreamContext, StreamLocation } from "@/lib/types";

interface StreamerBarProps {
  ctx: StreamContext;
  /** Where the position comes from; the picker is a demo fallback unless it is "demo". */
  positionSource?: "gps" | "phone" | "demo";
  onLocationChange: (l: StreamLocation) => void;
  onReveal: () => void;
  onSimulate: () => void;
}

function useSince(at: number | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (at === null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [at]);
  if (at === null) return null;
  const s = Math.max(0, Math.floor((now - at) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function StreamerBar({ ctx, positionSource = "demo", onLocationChange, onReveal, onSimulate }: StreamerBarProps) {
  const since = useSince(ctx.locationRevealedAt);
  const revealed = ctx.locationRevealedAt !== null;
  const demo = positionSource === "demo";
  const pickerValue = demo ? ctx.location.id : "gps";
  const pickerLabel = demo ? ctx.location.name : positionSource === "gps" ? "Phone GPS" : "Following phone";

  return (
    <section className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className={`relative shrink-0 rounded-full p-[2px] ${ctx.live ? "bg-live" : "bg-line-strong"}`}>
          {/* eslint-disable-next-line @next/next/no-img-element -- remote SVG avatar, next/image adds nothing here */}
          <img
            src={`https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(ctx.streamer)}&backgroundColor=131318`}
            alt=""
            className="size-9 rounded-full bg-surface-2 ring-2 ring-bg"
          />
          {ctx.live && (
            <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 rounded-[3px] bg-live px-1 font-mono text-[8px] font-bold tracking-normal text-white ring-2 ring-bg">
              Live
            </span>
          )}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <h1 className="truncate font-display text-[15px] font-semibold leading-tight tracking-[-0.02em]">
              {ctx.streamer}
            </h1>
            <BadgeCheck className="size-4 shrink-0 text-ember" aria-label="Verified" />
            <span className="ml-1 hidden font-mono text-[10px] tracking-normal text-dim sm:inline">
              {ctx.live ? `${ctx.viewers.toLocaleString("en-GB")} watching` : "Offline"}
            </span>
          </div>
          <p className="truncate text-[13px] text-muted">{ctx.title}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="tile !gap-2 !rounded-full !px-4 h-10 cursor-pointer">
          <span className="font-mono text-[10px] tracking-normal text-dim">{demo ? "Demo location" : "At"}</span>
          <span className="relative">
            <span className="invisible block px-1 text-[13px] font-medium">{pickerLabel}</span>
            <select
              aria-label={demo ? "Demo location" : "Location"}
              value={pickerValue}
              onChange={(e) => {
                const loc = LOCATIONS.find((l) => l.id === e.target.value);
                if (loc) onLocationChange(loc);
              }}
              className="absolute inset-0 w-full cursor-pointer appearance-none bg-transparent px-1 text-[13px] font-medium text-text focus:outline-none"
            >
              {!demo && <option value="gps">{pickerLabel}</option>}
              {LOCATIONS.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </span>
          <ChevronDown className="pointer-events-none size-3.5 text-muted" aria-hidden />
        </label>

        <button
          type="button"
          onClick={onSimulate}
          className="pill glass"
        >
          <Sparkles className="size-3.5 text-ember" aria-hidden />
          Mirror
          <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
        </button>

        <button
          type="button"
          onClick={onReveal}
          disabled={revealed}
          className={`pill ${revealed ? "glass" : "pill-candy animate-wobble"}`}
        >
          <MapPinned className="size-3.5" aria-hidden />
          {revealed ? (
            <>
              Shared <span className="font-mono text-[11px]">{since}</span> ago
            </>
          ) : (
            "Tell chat my location"
          )}
        </button>
      </div>
    </section>
  );
}
