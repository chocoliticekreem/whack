"use client";

import { useState, type FormEvent } from "react";
import { ChevronDown, Film, Send } from "lucide-react";
import type { AdviceEvidence, AdviceLevel, Assessment } from "@/platform/agent/types";

export const LEVEL: Record<AdviceLevel, { label: string; text: string; bg: string; dot: string; border: string }> = {
  calm: { label: "Calm", text: "text-ok", bg: "bg-ok/[0.09]", dot: "bg-ok", border: "border-ok/40" },
  busy: { label: "Busy", text: "text-warn", bg: "bg-warn/[0.09]", dot: "bg-warn", border: "border-warn/40" },
  danger: { label: "Danger", text: "text-live", bg: "bg-live/[0.09]", dot: "bg-live", border: "border-live/40" },
};

const SOURCE: Record<Assessment["source"], string> = {
  "cluster-agent": "VAST agent · Nemotron",
  "local-llm": "Local LLM · W&B",
};

const time = (at: number) => new Date(at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

function Evidence({ ev }: { ev: AdviceEvidence }) {
  const [playing, setPlaying] = useState(false);
  const playable = ev.video.startsWith("s3://");
  return (
    <li className="rounded-lg border border-line bg-surface-2 px-3 py-2">
      <p className="flex flex-wrap items-baseline gap-x-2 text-[12px] text-text">
        <span className="font-semibold">{ev.camera}</span>
        <span className="text-dim">
          {ev.start_s}–{ev.end_s} s · match {Math.round(ev.similarity * 100)}%
        </span>
      </p>
      <p className="mt-1 text-[12px] leading-5 text-muted">{ev.description}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px] text-dim">
        <span>
          YOLO:{" "}
          {Object.entries(ev.counts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 4)
            .map(([k, v]) => `${v} ${k}`)
            .join(" · ")}
        </span>
        {playable && !playing && (
          <button type="button" onClick={() => setPlaying(true)} className="flex items-center gap-1 text-text hover:underline">
            <Film className="size-3" aria-hidden /> Play
          </button>
        )}
      </div>
      {playing && (
        <video
          className="mt-2 w-full rounded-md border border-line bg-black"
          controls
          autoPlay
          playsInline
          src={`/api/agent/clip?source=${encodeURIComponent(ev.video)}`}
        />
      )}
    </li>
  );
}

interface AdvisorProps {
  assessment: Assessment | null;
  error: string | null;
  loading: boolean;
  live: boolean;
  onAsk: (question: string) => void;
}

/** The advisor's verdict: level band, one-line advice, reasons, evidence folded away, and a question box. */
export function Advisor({ assessment, error, loading, live, onAsk }: AdvisorProps) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);

  function ask(e: FormEvent) {
    e.preventDefault();
    const q = draft.trim();
    if (!q) return;
    onAsk(q);
    setDraft("");
  }

  const level = assessment ? LEVEL[assessment.level] : null;

  return (
    <div className="flex flex-col gap-3">
      {assessment && level ? (
        <div className={`rounded-lg border ${level.border} ${level.bg} px-4 py-3.5`}>
          <div className="flex items-baseline justify-between gap-3">
            <p className={`font-display text-[26px] font-semibold leading-none tracking-[-0.02em] ${level.text}`}>{level.label}</p>
            <p className="text-right text-[11px] leading-4 text-muted">
              {time(assessment.at)}
              <span className="block text-dim">{SOURCE[assessment.source]}</span>
            </p>
          </div>
          <p className="mt-3 text-[15px] font-medium leading-6 text-text">{assessment.advice}</p>
          {loading && <p className="mt-1.5 animate-pulse text-[11px] text-muted">Re-assessing…</p>}
        </div>
      ) : (
        <div className="rounded-lg border border-line bg-surface-2 px-4 py-3.5">
          <p className="text-[14px] leading-6 text-muted">
            {error ? (
              <>
                <span className="font-semibold text-text">Advisor offline.</span> {error}
              </>
            ) : live ? (
              <span className="animate-pulse">Reading the cameras around you…</span>
            ) : (
              "Go live and the advisor reads the cameras around you."
            )}
          </p>
        </div>
      )}

      {assessment && assessment.reasons.length > 0 && (
        <ul className="flex flex-col gap-1 text-[13px] leading-5 text-muted">
          {assessment.reasons.slice(0, 3).map((r) => (
            <li key={r} className="flex gap-2">
              <span className="mt-2 size-1 shrink-0 rounded-full bg-dim" aria-hidden />
              {r}
            </li>
          ))}
        </ul>
      )}

      {assessment && (
        <div>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex items-center gap-1.5 text-[12px] text-muted hover:text-text"
          >
            <ChevronDown className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
            Evidence · {assessment.evidence.length} clip{assessment.evidence.length === 1 ? "" : "s"} ·{" "}
            {assessment.liveCams} live cam{assessment.liveCams === 1 ? "" : "s"}
          </button>
          {open && (
            <ul className="mt-2 flex flex-col gap-2">
              {assessment.evidence.length === 0 && (
                <li className="text-[12px] text-dim">No VSS clips in this answer; it rests on live camera counts only.</li>
              )}
              {assessment.evidence.map((ev) => (
                <Evidence key={`${ev.video}-${ev.start_s}`} ev={ev} />
              ))}
            </ul>
          )}
        </div>
      )}
      {assessment && error && <p className="text-[12px] text-warn">Last refresh failed: {error}</p>}

      <form onSubmit={ask} className="flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask, e.g. is the bridge crowded right now?"
          className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface-2 px-3 text-[13px] text-text placeholder:text-dim focus:border-text/40 focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading || !draft.trim()}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-text text-bg disabled:opacity-40"
          aria-label="Ask"
        >
          <Send className="size-4" />
        </button>
      </form>
    </div>
  );
}
