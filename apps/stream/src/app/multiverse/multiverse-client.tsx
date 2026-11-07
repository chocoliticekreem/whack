"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Board } from "./Board";
import { CityMap } from "./CityMap";
import type { Geometry, MultiverseState, WorldRow } from "./types";

const SPEEDS = [1, 2, 4];
const LIVE_POLL_MS = 60_000;
const DELISTED_SHOWN = 15;
const KINDS = ["incident", "closure", "event", "weather"] as const;

async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/multiverse/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
  return data as T;
}

function WorldDetail({ world }: { world: WorldRow }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-3 text-sm">
      <div className="flex items-baseline justify-between">
        <span className="text-lg font-semibold">{world.id}</span>
        <span className="text-muted">
          gen {world.generation} · parent {world.parent ?? "—"} · sim {world.simMs} ms
        </span>
      </div>
      <div className="mt-2 text-xs uppercase tracking-wide text-muted">Assumptions</div>
      <ul className="mt-1 list-disc pl-5 text-muted">
        {world.labels.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
      <div className="mt-2 text-xs uppercase tracking-wide text-muted">Explanation trail</div>
      <ul className="mt-1 space-y-0.5 text-xs text-muted">
        {world.trail.length === 0 && <li>No observations scored yet.</li>}
        {world.trail.map((t) => (
          <li key={t.obs}>
            obs {t.obs} (+{t.minute} min): log-lik {t.ll.toFixed(2)}
            {t.worst && t.worst.cam !== "bus" && ` · worst cam ${t.worst.cam}: forecast ${t.worst.predicted.toFixed(0)}, saw ${t.worst.observed.toFixed(0)}`}
            {t.worst && t.worst.cam === "bus" && ` · bus delay assumed ${t.worst.predicted.toFixed(1)}, saw ${t.worst.observed.toFixed(1)}`}
          </li>
        ))}
        {world.delistReason && <li className="text-ember">Delisted: {world.delistReason}</li>}
      </ul>
      {world.events.length > 0 && (
        <>
          <div className="mt-2 text-xs uppercase tracking-wide text-muted">What happens in this world</div>
          <ul className="mt-1 space-y-0.5 text-xs text-muted">
            {world.events.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function MultiverseClient() {
  const [state, setState] = useState<MultiverseState | null>(null);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [n, setN] = useState(50);
  const [selected, setSelected] = useState<string | null>(null);
  const [eventZone, setEventZone] = useState("");
  const [eventKind, setEventKind] = useState<(typeof KINDS)[number]>("incident");
  const [eventAt, setEventAt] = useState(10);
  const [view, setView] = useState<"now" | "horizon">("horizon");
  const busyRef = useRef(false);

  const run = useCallback(async (fn: () => Promise<MultiverseState>) => {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    try {
      const next = await fn();
      setState(next);
      setError(null);
      return next;
    } catch (e) {
      setError((e as Error).message);
      setPlaying(false);
      return null;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, []);

  const spawn = useCallback(
    async (mode: "replay" | "live") => {
      setPlaying(false);
      const next = await run(() => api<MultiverseState>("spawn", { n, mode }));
      if (next) {
        setGeometry(await api<Geometry>("geometry"));
        setEventZone(next.zones[0]?.id ?? "");
        setSelected(null);
      }
    },
    [n, run],
  );

  const step = useCallback(async () => {
    const next = await run(() => api<MultiverseState>("observe", state?.mode === "live" ? {} : { next: true }));
    if (next?.done) setPlaying(false);
  }, [run, state?.mode]);

  useEffect(() => {
    api<MultiverseState>("state")
      .then(async (s) => {
        setState(s);
        setEventZone(s.zones[0]?.id ?? "");
        setGeometry(await api<Geometry>("geometry"));
      })
      .catch(() => spawn("replay"));
    // Load once; spawn is stable enough for the first mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!playing) return;
    const ms = state?.mode === "live" ? LIVE_POLL_MS : 2000 / speed;
    const id = window.setInterval(step, ms);
    return () => window.clearInterval(id);
  }, [playing, speed, step, state?.mode]);

  const placeEvent = () =>
    run(() => api<MultiverseState>("intervene", { kind: eventKind, zoneId: eventZone, atMin: eventAt }));

  const live = state?.board.filter((r) => r.status === "live") ?? [];
  const dead = state?.board.filter((r) => r.status === "delisted").slice(-DELISTED_SHOWN).reverse() ?? [];
  const selectedWorld = state?.board.find((r) => r.id === selected) ?? live[0];
  const last = state?.observations[state.observations.length - 1];
  const stats = view === "now" ? state?.consensusNow ?? [] : state?.consensusHorizon ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl space-y-4 px-4 py-5">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold">
              {state ? `${state.live} Londons` : "Spawning Londons"} that could exist in {state?.horizonMin ?? 30} minutes
            </h1>
            <p className="text-sm text-muted">
              Watch reality kill the ones that are wrong. {state?.area} · seed {state?.seedSource} · common random numbers
              (seed {state?.crnSeed})
            </p>
          </div>
          <div className="flex gap-4 text-right text-sm tabular-nums">
            <Stat label="now" value={`+${state?.minute ?? 0} min`} />
            <Stat label="live" value={state?.live ?? 0} />
            <Stat label="delisted" value={state?.delisted ?? 0} />
            <Stat label="effective worlds" value={state?.effectiveWorlds ?? 0} />
            <Stat label="mode" value={state?.mode ?? "—"} />
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface p-3 text-sm">
          <label className="flex items-center gap-1 text-muted">
            worlds
            <input type="number" min={2} max={200} value={n} onChange={(e) => setN(Number(e.target.value))}
              className="w-16 rounded bg-surface-2 px-2 py-1 text-text" />
          </label>
          <Btn onClick={() => spawn("replay")} disabled={busy}>Spawn (replay)</Btn>
          <Btn onClick={step} disabled={busy || !state}>Next observation</Btn>
          <Btn onClick={() => setPlaying((p) => !p)} disabled={!state}>{playing ? "Pause" : "Play"}</Btn>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="rounded bg-surface-2 px-2 py-1">
            {SPEEDS.map((s) => (
              <option key={s} value={s}>{s}x</option>
            ))}
          </select>
          <Btn onClick={() => spawn("live")} disabled={busy}>Go live</Btn>
          <span className="text-muted">
            {state?.mode === "replay" ? `frame ${state.replay.cursor}/${state.replay.frames}` : "polling cameras every 60 s"}
          </span>
          <span className="mx-2 h-5 w-px bg-line" />
          <select value={eventKind} onChange={(e) => setEventKind(e.target.value as (typeof KINDS)[number])} className="rounded bg-surface-2 px-2 py-1">
            {KINDS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          <select value={eventZone} onChange={(e) => setEventZone(e.target.value)} className="max-w-48 rounded bg-surface-2 px-2 py-1">
            {state?.zones.map((z) => (
              <option key={z.id} value={z.id}>{z.name}</option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-muted">
            at +
            <input type="number" min={0} max={state?.horizonMin ?? 30} value={eventAt} onChange={(e) => setEventAt(Number(e.target.value))}
              className="w-14 rounded bg-surface-2 px-2 py-1 text-text" />
            min
          </label>
          <Btn onClick={placeEvent} disabled={busy || !state || !eventZone} accent>Place event (fork the cloud)</Btn>
        </div>

        {error && <div className="rounded-xl border border-ember bg-ember-soft p-3 text-sm">{error}</div>}
        {last && (
          <div className="text-sm text-muted">
            Last observation #{last.index} at +{last.minute} min: {last.compared} cameras
            {last.busDelayMin != null && `, buses ${last.busDelayMin} min late`} → {last.delisted.length} delisted,{" "}
            {last.spawned.length} spawned.
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
          <div className="space-y-4">
            <div className="flex gap-2 text-sm">
              <Btn onClick={() => setView("now")} accent={view === "now"}>Consensus now</Btn>
              <Btn onClick={() => setView("horizon")} accent={view === "horizon"}>At +{state?.horizonMin ?? 30} min</Btn>
            </div>
            <CityMap geometry={geometry} stats={stats} label={view === "now" ? `Now (+${state?.minute ?? 0} min)` : `Forecast at +${state?.horizonMin ?? 30} min`} />
            <div className="rounded-xl border border-line bg-surface p-3 text-sm">
              <div className="mb-1 text-xs uppercase tracking-wide text-muted">Implied volatility by zone</div>
              {[...stats].sort((a, b) => b.iv - a.iv).map((s) => (
                <div key={s.zoneId} className="flex items-center gap-2 py-0.5">
                  <span className="w-56 truncate">{s.name}</span>
                  <span className="w-24 text-right tabular-nums text-muted">{s.mean}±{s.sd}</span>
                  <span className="h-2 rounded bg-candy" style={{ width: `${Math.min(100, s.iv * 100)}%`, maxWidth: 200 }} />
                  <span className="tabular-nums text-muted">{(s.iv * 100).toFixed(0)}%</span>
                </div>
              ))}
            </div>
            {selectedWorld && <WorldDetail world={selectedWorld} />}
          </div>
          <div className="space-y-2">
            <div className="text-xs uppercase tracking-wide text-muted">Exchange of Worlds · price = chance it is the London we are in</div>
            <Board rows={[...live, ...dead]} selected={selectedWorld?.id ?? null} onSelect={setSelected} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-lg font-semibold">{value}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

function Btn({ children, onClick, disabled, accent }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; accent?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-3 py-1.5 font-medium disabled:opacity-40 ${accent ? "bg-candy text-white" : "bg-surface-3 hover:bg-line-strong"}`}
    >
      {children}
    </button>
  );
}
