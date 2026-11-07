"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Database, Film } from "lucide-react";
import type { AdviceLevel, ArchiveMoment, ArchiveSearch, VerdictsResponse } from "@/platform/agent/types";

const LEVEL_TEXT: Record<AdviceLevel, string> = { calm: "text-ok", busy: "text-warn", danger: "text-live" };
const EXAMPLES = ["pedestrians crossing", "crowd gathering at a corner", "someone filming the camera holder"];

const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

function Sparkline({ points }: { points: { t: number; people: number }[] }) {
  const peak = Math.max(1, ...points.map((p) => p.people));
  return (
    <span className="flex h-6 items-end gap-px" aria-label={`people per second, peak ${peak}`}>
      {points.map((p) => (
        <span
          key={p.t}
          className="w-1 rounded-sm bg-ember/70"
          style={{ height: `${Math.max(8, (p.people / peak) * 100)}%` }}
          title={`${p.t}s: ${p.people} people`}
        />
      ))}
    </span>
  );
}

function Moment({ m }: { m: ArchiveMoment }) {
  const [playing, setPlaying] = useState(false);
  const people = m.counts.person ?? 0;
  const timeline = m.tools?.people_timeline ?? [];
  const context = m.tools?.context ?? [];
  return (
    <li className="rounded-lg border border-line bg-surface-2 px-3 py-2">
      <p className="flex flex-wrap items-baseline gap-x-2 text-[12px] text-text">
        <span className="font-semibold">{m.camera || m.video}</span>
        <span className="text-dim">
          {m.start_s}–{m.end_s} s · match {Math.round(m.similarity * 100)}% · {people} people
          {m.tools?.people_peak !== undefined ? ` · peak ${m.tools.people_peak} in frame` : ""}
        </span>
      </p>
      <p className="mt-1 text-[12px] leading-5 text-muted">{m.description}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[11px] text-dim">
        {timeline.length > 0 && <Sparkline points={timeline} />}
        {context.length > 0 && (
          <span>
            Parent video:{" "}
            {context.map((c) => (
              <span key={c.segment} className={c.current ? "font-semibold text-text" : ""}>
                {c.people}
                {c.segment < context.length ? " · " : ""}
              </span>
            ))}{" "}
            people per segment
          </span>
        )}
        {m.clip_url && !playing && (
          <button type="button" onClick={() => setPlaying(true)} className="flex items-center gap-1 text-text hover:underline">
            <Film className="size-3" aria-hidden /> Play clip
          </button>
        )}
      </div>
      {playing && (
        <video
          className="mt-2 w-full rounded-md border border-line bg-black"
          controls
          autoPlay
          playsInline
          src={`/api/agent/clip?source=${encodeURIComponent(m.source)}`}
        />
      )}
    </li>
  );
}

function useVerdicts(refreshKey: number) {
  const [data, setData] = useState<VerdictsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    fetch("/api/agent/verdicts?limit=6", { signal: ctrl.signal })
      .then(async (r) => {
        const body = (await r.json()) as VerdictsResponse | { error: string };
        if (!r.ok || "error" in body) throw new Error("error" in body ? body.error : `HTTP ${r.status}`);
        setData(body);
        setError(null);
      })
      .catch((e: Error) => {
        if (!ctrl.signal.aborted) setError(e.message);
      });
    return () => ctrl.abort();
  }, [refreshKey]);
  return { data, error };
}

/** "Ask the archive": a free-text VSS search with Cosmos text, YOLO counts and playable clips, plus the agent's VastDB memory. */
export function ArchivePanel() {
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<ArchiveSearch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const verdicts = useVerdicts(refreshKey);

  const search = useCallback(async (query: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/agent/search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query, top_k: 4 }),
      });
      const body = (await res.json()) as ArchiveSearch | { error: string };
      if (!res.ok || "error" in body) throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      setResult(body);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  function submit(e: FormEvent) {
    e.preventDefault();
    const q = draft.trim();
    if (q) void search(q);
  }

  useEffect(() => {
    const id = setInterval(() => setRefreshKey((k) => k + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  const byLevel = verdicts.data?.last_hour_by_level;

  return (
    <section>
      <p className="mb-2 text-[12px] text-dim">
        {loading ? <span className="animate-pulse text-muted">Searching VSS…</span> : result ? `${result.latency_ms} ms` : "VSS search over the city cameras"}
      </p>
      <div>
        <form onSubmit={submit} className="flex items-center gap-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. is anyone following me?"
            className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong bg-surface-2 px-3 text-[13px] text-text placeholder:text-dim focus:border-text/40 focus:outline-none"
          />
          <button
            type="submit"
            disabled={loading || !draft.trim()}
            className="h-9 shrink-0 rounded-lg bg-text px-3 text-[13px] font-semibold text-bg disabled:opacity-40"
          >
            Search
          </button>
        </form>
        {!result && (
          <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-dim">
            Try:
            {EXAMPLES.map((q) => (
              <button key={q} type="button" onClick={() => void search(q)} className="text-muted hover:text-text hover:underline">
                {q}
              </button>
            ))}
          </p>
        )}
        {error && <p className="mt-2 text-[12px] text-warn">{error}</p>}
        {result && (
          <ul className="mt-3 flex flex-col gap-2">
            {result.moments.length === 0 && <li className="text-[12px] text-dim">No moments matched “{result.query}”.</li>}
            {result.moments.map((m) => (
              <Moment key={`${m.source}-${m.start_s}`} m={m} />
            ))}
          </ul>
        )}

        <div className="mt-4 border-t border-line pt-3">
          <p className="flex flex-wrap items-center gap-x-2 text-[12px] text-text">
            <Database className="size-3.5 text-ember" aria-hidden />
            <span className="font-semibold">Recent verdicts</span>
            <span className="text-dim">VastDB · {verdicts.data ? verdicts.data.table : "…"}</span>
            {byLevel && (
              <span className="ml-auto text-dim">
                last hour: <span className={LEVEL_TEXT.calm}>{byLevel.calm} calm</span> ·{" "}
                <span className={LEVEL_TEXT.busy}>{byLevel.busy} busy</span> ·{" "}
                <span className={LEVEL_TEXT.danger}>{byLevel.danger} danger</span>
              </span>
            )}
          </p>
          {verdicts.error && <p className="mt-1 text-[12px] text-warn">{verdicts.error}</p>}
          {verdicts.data && verdicts.data.verdicts.length === 0 && (
            <p className="mt-1 text-[12px] text-dim">No verdicts stored yet. The first assessment writes one.</p>
          )}
          <ul className="mt-1.5 flex flex-col gap-1 text-[12px]">
            {verdicts.data?.verdicts.map((v) => (
              <li key={`${v.ts}-${v.question}`} className="flex gap-2 leading-5">
                <span className="shrink-0 text-dim">{time(v.ts)}</span>
                <span className={`shrink-0 font-semibold uppercase ${LEVEL_TEXT[v.level] ?? "text-muted"}`}>{v.level}</span>
                <span className="min-w-0 truncate text-muted" title={v.advice}>
                  {v.location_name ? `${v.location_name} · ` : ""}
                  {v.advice}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
