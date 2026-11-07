"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Ellipsis } from "lucide-react";

/** Secondary actions behind one "…" pill, so the toolbar keeps only what is used live. */
export function MoreMenu({ children }: { children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative z-40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="More"
        aria-haspopup="menu"
        aria-expanded={open}
        className="pill glass !w-10 !px-0"
      >
        <Ellipsis className="size-4" aria-hidden />
      </button>
      {open && (
        <div role="menu" className="glass absolute right-0 top-full mt-2 flex min-w-52 flex-col gap-1 rounded-[22px] p-1.5">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

export const MENU_ITEM =
  "flex h-10 items-center gap-2.5 rounded-full px-3.5 text-left text-[13px] font-extrabold tracking-[-0.01em] text-text transition-colors hover:bg-white/10";
