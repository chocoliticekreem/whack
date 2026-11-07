"use client";

import { Bell } from "lucide-react";

export function TopBar({ live }: { live: boolean }) {
  return (
    <header className="flex h-13 shrink-0 items-center gap-5 bg-surface/80 px-4 shadow-[0_1px_0_rgb(255_255_255/0.06)] backdrop-blur-md lg:px-5">
      <div className="flex items-center gap-3">
        <span className="relative grid size-8 place-items-center rounded-full bg-[image:var(--grad-candy)] font-mono text-[12px] font-extrabold leading-none text-white shadow-[inset_0_2px_3px_rgb(255_255_255/0.55),inset_0_-3px_5px_rgb(140_0_70/0.35),0_6px_14px_-4px_rgb(236_17_135/0.6)]">
          M
          <span className="absolute -right-0.5 -top-0.5 size-1.5 rounded-full bg-text" />
        </span>
        <span className="font-display text-[17px] font-semibold leading-none tracking-[-0.02em]">
          Mirror<span className="text-muted">verse</span>
        </span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <span
          className={`flex h-7 items-center gap-2 rounded-full px-3 text-[12px] font-extrabold tracking-normal ${
            live ? "live-badge !h-7 !text-[12px]" : "bg-white/[0.07] text-muted"
          }`}
        >
          <span className={`size-1.5 rounded-full ${live ? "animate-live bg-white" : "bg-dim"}`} />
          {live ? "On air" : "Standby"}
        </span>
        <button
          type="button"
          aria-label="Notifications"
          className="grid size-8 place-items-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-text"
        >
          <Bell className="size-4" />
        </button>
        <span className="grid size-8 place-items-center rounded-full bg-surface-3 text-[12px] font-extrabold ring-1 ring-white/10">
          SN
        </span>
      </div>
    </header>
  );
}
