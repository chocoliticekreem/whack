"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Compass, Route, Search, Sparkles } from "lucide-react";
import type { StreamLocation } from "@/lib/types";
import type { Assessment } from "@/platform/agent/types";
import { useAdvisor } from "@/platform/agent/useAdvisor";
import { ArchivePanel } from "@/components/stream/ArchivePanel";
import { Advisor, LEVEL } from "./Advisor";
import { AroundYou, useSurroundingsStatus } from "./AroundYou";
import { SaferRoute } from "./SaferRoute";

const STORAGE_KEY = "c47.copilot.open";
export const PANEL_W = 400;
export const RAIL_W = 48;

/** Panel open/closed, remembered per browser on desktop and toggled with `]`. Phones start with the sheet down. */
export function useCopilotOpen(desktop: boolean) {
  const [open, setOpen] = useState(() => {
    if (!desktop) return false;
    try {
      return localStorage.getItem(STORAGE_KEY) !== "0";
    } catch {
      return true;
    }
  });
  useEffect(() => {
    if (!desktop) return;
    try {
      localStorage.setItem(STORAGE_KEY, open ? "1" : "0");
    } catch {}
  }, [desktop, open]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key !== "]" || t?.tagName === "INPUT" || t?.tagName === "TEXTAREA" || t?.tagName === "SELECT") return;
      setOpen((o) => !o);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return [open, setOpen] as const;
}

function Section({
  icon: Icon,
  title,
  meta,
  defaultOpen = true,
  children,
}: {
  icon: typeof Sparkles;
  title: string;
  meta?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-b border-line">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex h-11 w-full items-center gap-2.5 px-4 text-left"
      >
        <Icon className="size-3.5 text-ember" aria-hidden />
        <span className="text-[13px] font-semibold text-text">{title}</span>
        <span className="ml-auto flex items-center gap-2 text-[11px] text-dim">
          {meta}
          <ChevronDown className={`size-3.5 text-dim transition-transform ${open ? "" : "-rotate-90"}`} />
        </span>
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </section>
  );
}

interface CopilotPanelProps {
  location: StreamLocation;
  live: boolean;
  desktop: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function Dot({ level }: { level: keyof typeof LEVEL | null }) {
  return <span className={`size-1.5 rounded-full ${level ? LEVEL[level].dot : "bg-dim"}`} aria-hidden />;
}

/** The right-hand copilot: advisor, surroundings, route and the archive, on every tab. */
export function CopilotPanel({ location, live, desktop, open, onOpenChange }: CopilotPanelProps) {
  const { assessment, error, loading, assess } = useAdvisor(location, live);
  const surroundings = useSurroundingsStatus();
  const level = assessment?.level ?? null;

  const body = (
    <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
      <Section icon={Sparkles} title="Advisor" meta={loading ? <span className="animate-pulse text-muted">Assessing…</span> : null}>
        <Advisor assessment={assessment} error={error} loading={loading} live={live} onAsk={(q) => void assess(q)} />
      </Section>
      <Section icon={Compass} title="Around you" meta={surroundings ? <Dot level={surroundings.level} /> : null}>
        <AroundYou location={location} live={live} />
      </Section>
      <Section icon={Route} title="Safer route" defaultOpen={level === "busy" || level === "danger"}>
        <SaferRoute location={location} level={level} />
      </Section>
      <Section icon={Search} title="Ask the archive" defaultOpen={false}>
        <ArchivePanel />
      </Section>
    </div>
  );

  if (desktop) {
    if (!open) {
      return (
        <aside
          className="glass absolute bottom-14 right-3 top-3 z-20 flex flex-col items-center gap-3 rounded-[24px] py-2"
          style={{ width: RAIL_W }}
          aria-label="Copilot, collapsed"
        >
          <button
            type="button"
            onClick={() => onOpenChange(true)}
            aria-label="Open copilot"
            title="Open copilot  ]"
            className="pill pill-sm glass !h-8 !w-8 !px-0"
          >
            <ChevronLeft className="size-4" />
          </button>
          <span className={`h-9 w-1 rounded-full ${level ? LEVEL[level].dot : "bg-line-strong"}`} aria-hidden />
          <span className="flex flex-col items-center gap-2.5" title="Advisor · Around you">
            <Dot level={level} />
            <Dot level={surroundings?.level ?? null} />
          </span>
          <span className="mt-auto font-mono text-[10px] text-dim" style={{ writingMode: "vertical-rl" }}>
            Copilot
          </span>
        </aside>
      );
    }
    return (
      <aside
        className="glass absolute bottom-14 right-3 top-3 z-20 flex flex-col overflow-hidden rounded-[28px]"
        style={{ width: PANEL_W }}
        aria-label="Copilot"
      >
        <PanelHeader level={level} assessment={assessment} onCollapse={() => onOpenChange(false)} />
        {body}
      </aside>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onOpenChange(true)}
        className="glass absolute bottom-3 left-16 right-3 z-20 flex h-14 items-center gap-3 rounded-full px-4 text-left"
      >
        <span className={`h-8 w-1 shrink-0 rounded-full ${level ? LEVEL[level].dot : "bg-line-strong"}`} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className={`block text-[12px] font-semibold ${level ? LEVEL[level].text : "text-muted"}`}>
            {level ? LEVEL[level].label : "Copilot"}
          </span>
          <span className="block truncate text-[13px] text-text/85">
            {assessment?.advice ?? (live ? "Reading the cameras around you…" : "Go live to start the advisor")}
          </span>
        </span>
        <ChevronDown className="size-4 rotate-180 text-muted" />
      </button>
    );
  }
  return (
    <div className="absolute inset-x-0 bottom-0 top-[12%] z-20 flex flex-col">
      <button type="button" aria-label="Collapse copilot" onClick={() => onOpenChange(false)} className="h-6 shrink-0" />
      <div className="glass animate-sheet-up relative flex min-h-0 flex-1 flex-col rounded-t-[28px]">
        <button
          type="button"
          aria-label="Collapse copilot"
          onClick={() => onOpenChange(false)}
          className="grid h-5 w-full shrink-0 place-items-center"
        >
          <span className="h-1 w-10 rounded-full bg-line-strong" aria-hidden />
        </button>
        <PanelHeader level={level} assessment={assessment} onCollapse={() => onOpenChange(false)} />
        {body}
      </div>
    </div>
  );
}

function PanelHeader({
  level,
  assessment,
  onCollapse,
}: {
  level: keyof typeof LEVEL | null;
  assessment: Assessment | null;
  onCollapse: () => void;
}) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-4">
      <span className={`size-2 rounded-full ${level ? `${LEVEL[level].dot} animate-live` : "bg-dim"}`} aria-hidden />
      <h2 className="text-[14px] font-extrabold tracking-[-0.01em] text-text">Copilot</h2>
      <span className="text-[11px] text-dim">{assessment ? `${assessment.liveCams} live cams` : "VAST agent"}</span>
      <button
        type="button"
        onClick={onCollapse}
        aria-label="Collapse copilot"
        title="Collapse  ]"
        className="pill pill-sm glass ml-auto !h-8 !w-8 !px-0"
      >
        <ChevronRight className="size-4" />
      </button>
    </header>
  );
}
