"use client";

import { Bus, Camera, Layers, Pause, Play, Radio, RotateCcw, StepForward, Users, Zap } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Board } from "./Board";
import { Brokers } from "./Brokers";
import { CityMap } from "./CityMap";
import type { Geometry, MultiverseState, ZoneStat } from "./types";

const SPEEDS = [1, 2, 4];
const LIVE_POLL_MS = 60_000;
const DELISTED_SHOWN = 15;
const KINDS = ["incident", "closure", "event", "weather"] as const;
const SPIKE_PCT = 15;

type VixPoint = { t: number; vix: number; spike?: boolean };

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

// Fallback when the engine has no `vix` yet: mean-weighted coefficient of variation of the zone forecasts.
function deriveVix(stats: ZoneStat[]): number {
  const mean = stats.reduce((a, s) => a + s.mean, 0);
  if (mean <= 0) return 0;
  return (stats.reduce((a, s) => a + s.sd, 0) / mean) * 100;
}

function vixOf(s: MultiverseState): number {
  return s.vix ?? deriveVix(s.consensusHorizon);
}

function VixSpark({ points }: { points: VixPoint[] }) {
  const w = 160;
  const h = 36;
  if (points.length < 2) return <svg width={w} height={h} aria-hidden />;
  const vals = points.map((p) => p.vix);
  const max = Math.max(...vals);
  const min = Math.min(...vals);
  const span = Math.max(max - min, 1e-6);
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - ((v - min) / span) * (h - 4) - 2}`).join(" ");
  return (
    <svg width={w} height={h} aria-hidden>
      <polyline className="mv-spark-vix" fill="none" strokeWidth={2} points={pts} />
    </svg>
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
  const [withBrokers, setWithBrokers] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [forkOpen, setForkOpen] = useState(false);
  const [eventZone, setEventZone] = useState("");
  const [eventKind, setEventKind] = useState<(typeof KINDS)[number]>("incident");
  const [eventAt, setEventAt] = useState(3);
  const [view, setView] = useState<"now" | "horizon">("horizon");
  const [vixLocal, setVixLocal] = useState<VixPoint[]>([]);
  const busyRef = useRef(false);

  const accept = useCallback((next: MultiverseState, reset: boolean) => {
    setState(next);
    const point = { t: next.minute, vix: vixOf(next) };
    setVixLocal((h) => (reset ? [point] : [...h.filter((p) => p.t !== point.t), point]));
  }, []);

  const run = useCallback(
    async (fn: () => Promise<MultiverseState>, reset = false) => {
      if (busyRef.current) return null;
      busyRef.current = true;
      setBusy(true);
      try {
        const next = await fn();
        accept(next, reset);
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
    },
    [accept],
  );

  const spawn = useCallback(
    async (mode: "replay" | "live") => {
      setPlaying(false);
      const next = await run(() => api<MultiverseState>("spawn", { n, mode, brokers: withBrokers ? "on" : "off" }), true);
      if (next) {
        setGeometry(await api<Geometry>("geometry"));
        setEventZone(next.zones[0]?.id ?? "");
        setSelected(null);
      }
    },
    [n, run, withBrokers],
  );

  const step = useCallback(async () => {
    const next = await run(() => api<MultiverseState>("observe", state?.mode === "live" ? {} : { next: true }));
    if (next?.done) setPlaying(false);
  }, [run, state?.mode]);

  useEffect(() => {
    api<MultiverseState>("state")
      .then(async (s) => {
        accept(s, true);
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

  const horizon = state?.horizon_min ?? 5;
  const live = state?.board.filter((r) => r.status === "live") ?? [];
  const dead = state?.board.filter((r) => r.status === "delisted").slice(-DELISTED_SHOWN).reverse() ?? [];
  const last = state?.observations[state.observations.length - 1];
  const stats = view === "now" ? state?.consensusNow ?? [] : state?.consensusHorizon ?? [];
  const vixHistory = state?.vix_history?.length ? state.vix_history : vixLocal;
  const vix = state ? vixOf(state) : null;
  const prevVix = vixHistory.length > 1 ? vixHistory[vixHistory.length - 2].vix : null;
  const vixDelta = vix != null && prevVix ? ((vix - prevVix) / prevVix) * 100 : null;
  const spike = state?.vix_spike ?? (vixDelta != null && vixDelta >= SPIKE_PCT);
  const spread = [...stats].sort((a, b) => b.iv - a.iv);
  const maxIv = Math.max(1e-6, ...spread.map((s) => s.iv));

  return (
    <div className="mv-wrap">
      <header className="flex flex-wrap items-stretch justify-between gap-4">
        <div className="flex flex-col justify-between gap-4">
          <h1 className="mv-title">
            <span className="mv-grad-text">{state?.live ?? "··"}</span> Londons
            <span className="mv-chip ml-3 align-middle text-sm c-amber">{horizon} MIN</span>
          </h1>
          <div className="flex flex-wrap items-end gap-6">
            <Stat label="Live" value={state?.live ?? 0} tone="c-cyan" />
            <Stat label="Dead" value={state?.delisted ?? 0} tone="c-magenta" />
            <Stat label="ESS" value={state?.effectiveWorlds ?? 0} tone="c-violet" />
            <span className="mv-chip mv-num c-dim">+{state?.minute ?? 0}′</span>
            {state?.mode === "replay" ? (
              <span className="mv-chip mv-num">
                <span className="c-cyan">▮</span>
                {state.replay.cursor}/{state.replay.frames}
              </span>
            ) : (
              state && (
                <span className="mv-chip c-lime">
                  <Radio size={12} /> live
                </span>
              )
            )}
          </div>
        </div>
        <div
          className="mv-panel mv-grain mv-tip flex items-center gap-5 px-5"
          data-accent="magenta"
          data-tip={state?.vix_driver ?? undefined}
        >
          <div>
            <div className="mv-label">London VIX</div>
            <div className="mv-vix mv-num" data-spike={spike} key={vixHistory.length}>
              {vix == null ? "--" : vix.toFixed(1)}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1">
            <span className={`mv-num ${vixDelta == null ? "c-dim" : vixDelta >= 0 ? "c-magenta" : "c-lime"}`}>
              {vixDelta == null ? "·" : `${vixDelta >= 0 ? "▲" : "▼"}${Math.abs(vixDelta).toFixed(1)}%`}
            </span>
            <VixSpark points={vixHistory} />
          </div>
        </div>
      </header>

      <div className="mv-panel flex flex-wrap items-center gap-2">
        <Btn label="Play" onClick={() => setPlaying((p) => !p)} disabled={!state} on={playing} accent="lime">
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </Btn>
        <Btn label="Next observation" onClick={step} disabled={busy || !state}>
          <StepForward size={16} />
        </Btn>
        <div className="flex">
          {SPEEDS.map((s) => (
            <Btn key={s} label={`${s}x speed`} onClick={() => setSpeed(s)} on={speed === s}>
              {s}x
            </Btn>
          ))}
        </div>
        <span className="mx-1 h-6 w-px" aria-hidden />
        <label className="mv-chip h-[34px]" title="Worlds">
          <Layers size={14} className="c-cyan" />
          <input
            type="number"
            min={2}
            max={200}
            value={n}
            onChange={(e) => setN(Number(e.target.value))}
            className="mv-num w-12 bg-transparent outline-none"
            aria-label="Worlds"
          />
        </label>
        <Btn label="Brokers" onClick={() => setWithBrokers((b) => !b)} on={withBrokers} accent="violet">
          <Users size={16} />
        </Btn>
        <Btn label="Respawn replay" onClick={() => spawn("replay")} disabled={busy}>
          <RotateCcw size={16} />
        </Btn>
        <Btn label="Go live" onClick={() => spawn("live")} disabled={busy} accent="amber">
          <Radio size={16} />
        </Btn>
        <span className="flex-1" />
        <Btn label="Fork an event" onClick={() => setForkOpen((o) => !o)} on={forkOpen} accent="magenta">
          <Zap size={16} /> Fork
        </Btn>
        {forkOpen && (
          <div className="flex w-full flex-wrap items-center gap-2 pt-2">
            <select value={eventKind} onChange={(e) => setEventKind(e.target.value as (typeof KINDS)[number])} className="mv-field" aria-label="Event kind">
              {KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <select value={eventZone} onChange={(e) => setEventZone(e.target.value)} className="mv-field max-w-56" aria-label="Zone">
              {state?.zones.map((z) => (
                <option key={z.id} value={z.id}>{z.name}</option>
              ))}
            </select>
            <label className="mv-chip h-[34px]">
              +
              <input
                type="number"
                min={0}
                max={horizon}
                value={eventAt}
                onChange={(e) => setEventAt(Number(e.target.value))}
                className="mv-num w-10 bg-transparent outline-none"
                aria-label="Minutes from now"
              />
              ′
            </label>
            <Btn label="Place event" onClick={placeEvent} disabled={busy || !state || !eventZone} accent="magenta" on>
              <Zap size={16} />
            </Btn>
          </div>
        )}
      </div>

      {error && <div className="mv-error mv-tip" data-tip={error}>✕ engine</div>}

      {last && (
        <div className="flex flex-wrap gap-2 mv-num">
          <span className="mv-chip" title="Observation">
            <span className="c-cyan">#{last.index}</span>
          </span>
          <span className="mv-chip" title="Cameras compared">
            <Camera size={12} className="c-cyan" /> {last.compared}
          </span>
          {last.busDelayMin != null && (
            <span className="mv-chip" title="Bus delay, minutes">
              <Bus size={12} className="c-amber" /> {last.busDelayMin}′
            </span>
          )}
          <span className="mv-chip c-magenta" title="Delisted">✕ {last.delisted.length}</span>
          <span className="mv-chip c-lime" title="Spawned">+ {last.spawned.length}</span>
        </div>
      )}

      <div className="mv-grid">
        <div className="grid content-start gap-4">
          <div className="mv-panel mv-grain">
            <div className="mv-head">
              <div className="flex">
                <Btn label="Consensus now" onClick={() => setView("now")} on={view === "now"}>
                  Now
                </Btn>
                <Btn label={`Forecast at +${horizon} min`} onClick={() => setView("horizon")} on={view === "horizon"} accent="magenta">
                  +{horizon}′
                </Btn>
              </div>
              <span className="mv-label mv-num">{stats.length}</span>
            </div>
            <CityMap geometry={geometry} stats={stats} />
          </div>
          <div className="mv-panel" data-accent="magenta">
            <div className="mv-head">
              <span className="mv-label">Spread</span>
              <span className="mv-label mv-num c-magenta">{spread[0] ? `${(spread[0].iv * 100).toFixed(0)}%` : ""}</span>
            </div>
            <div className="flex h-16 items-end gap-1.5">
              {spread.map((s) => (
                <div
                  key={s.zoneId}
                  className="mv-tip flex-1"
                  style={{ height: "100%", display: "flex", alignItems: "flex-end" }}
                  data-tip={`${s.name} · ${Math.round(s.mean)}±${Math.round(s.sd)} · ${(s.iv * 100).toFixed(0)}%`}
                >
                  <div className="mv-bar w-full" style={{ height: `${Math.max(6, (s.iv / maxIv) * 100)}%` }} />
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="grid content-start gap-4">
          {state?.brokers && <Brokers rows={state.brokers} />}
          <Board rows={[...live, ...dead]} brokers={state?.brokers ?? null} selected={selected} onSelect={setSelected} />
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <div>
      <div className={`mv-stat mv-num ${tone}`}>{value}</div>
      <div className="mv-label">{label}</div>
    </div>
  );
}

function Btn({
  children,
  label,
  onClick,
  disabled,
  on,
  accent,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  on?: boolean;
  accent?: "magenta" | "violet" | "lime" | "amber";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="mv-btn"
      data-on={on ? "true" : "false"}
      data-accent={accent}
    >
      {children}
    </button>
  );
}
