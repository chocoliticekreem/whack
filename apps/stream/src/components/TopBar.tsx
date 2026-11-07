"use client";

import { Bell, Search } from "lucide-react";

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
        <span className="hidden items-center gap-1.5 border-l border-line pl-3 font-mono text-[10px] tracking-normal text-dim lg:flex">
          London
          <span className="size-0.5 rounded-full bg-dim" />
          IRL
        </span>
      </div>

      <div className="mx-auto hidden w-full max-w-sm items-center gap-2 rounded-full bg-bg px-3.5 py-2 shadow-[inset_0_2px_4px_rgb(0_0_0/0.5),0_0_0_1px_rgb(255_255_255/0.07)] transition-shadow focus-within:shadow-[inset_0_2px_4px_rgb(0_0_0/0.5),0_0_0_2px_rgb(255_141_16/0.6)] md:flex">
        <Search className="size-3.5 text-dim" aria-hidden />
        <input
          type="search"
          placeholder="Search streams, places, people"
          className="w-full bg-transparent text-[13px] text-text placeholder:text-dim focus:outline-none"
        />
        <kbd className="rounded border border-line px-1 font-mono text-[10px] text-dim">/</kbd>
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
