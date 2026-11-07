"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  isLive,
  LIVE_SETTINGS,
  setDemoSettings,
  useDemoSettings,
  type DemoSettings,
  type TimeOfDay,
  type WeatherKind,
} from "@/platform/demo";
import { triggerScenario, useScenario } from "@/twin/live/scenario";
import { setDemoStreamer } from "@/twin/live/store";
import { PLACES, useOrigin } from "@/twin/origin";

const WEATHER: { value: WeatherKind | null; label: string }[] = [
  { value: null, label: "Live" },
  { value: "clear", label: "Stop scenario" },
  { value: "cloudy", label: "Cloudy" },
  { value: "rain", label: "Rain" },
  { value: "fog", label: "Fog" },
  { value: "storm", label: "Storm" },
];

const TIME: { value: TimeOfDay | null; label: string }[] = [
  { value: null, label: "Live" },
  { value: "day", label: "Day" },
  { value: "dusk", label: "Dusk" },
  { value: "night", label: "Night" },
];

const CROWD: DemoSettings["crowd"][] = [1, 3, 10];

/** First override the Mode switch jumps to, so flipping it visibly changes the twin. */
const DEMO_DEFAULT: DemoSettings = { weather: "rain", time: "night", crowd: 1, estimate: false, boats: false };

const LAYERS: { key: "estimate" | "boats"; label: string }[] = [
  { key: "estimate", label: "Estimated crowd (inferred)" },
  { key: "boats", label: "Illustrative boats" },
];

function Segment<T>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium text-muted">{label}</span>
      <div className="flex flex-wrap gap-1">
        {options.map((o) => {
          const active = o.value === value;
          return (
            <button
              key={String(o.value)}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(o.value)}
              className={`rounded-md px-2.5 py-1 text-[12px] font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-text ${
                active ? "bg-text text-bg" : "bg-surface-3 text-muted hover:text-text"
              }`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Floating dev overrides for demos. Shared by the stream page and the twin; writes go through
 * setDemoSettings so every open tab follows. Hidden inside the stream page's twin iframe, where the
 * host page already shows one.
 */
export function DevPanel() {
  const settings = useDemoSettings();
  const [open, setOpen] = useState(false);
  const [embedded] = useState(() => typeof window !== "undefined" && window.self !== window.top);
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  const live = isLive(settings);
  const scenario = useScenario((s) => s.scenario);
  const origin = useOrigin((s) => s.origin);
  const pending = useOrigin((s) => s.pending);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onClick);
    };
  }, [open]);

  if (embedded) return null;

  const update = (patch: Partial<DemoSettings>) => setDemoSettings({ ...settings, ...patch });

  return (
    <div
      ref={root}
      className="pointer-events-auto fixed bottom-16 right-3 z-40 flex flex-col items-end gap-2 md:bottom-4 md:right-4"
      style={{ fontFamily: "var(--font-figtree), system-ui, sans-serif" }}
    >
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label="Demo overrides"
          className="flex w-[272px] flex-col gap-3 rounded-xl border border-line-strong bg-surface/95 p-3.5 text-text shadow-[0_16px_48px_rgba(0,0,0,0.6)] backdrop-blur"
        >
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-semibold">Demo overrides</span>
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                live ? "bg-ok/15 text-ok" : "bg-warn/15 text-warn"
              }`}
            >
              {live ? "Live" : "Override"}
            </span>
          </div>
          <Segment
            label="Mode"
            options={[
              { value: true, label: "Live" },
              { value: false, label: "Override" },
            ]}
            value={live}
            onChange={(v) => setDemoSettings(v ? LIVE_SETTINGS : DEMO_DEFAULT)}
          />
          <Segment label="Weather" options={WEATHER} value={settings.weather} onChange={(weather) => update({ weather })} />
          <Segment label="Time" options={TIME} value={settings.time} onChange={(time) => update({ time })} />
          <Segment
            label="Crowd"
            options={CROWD.map((c) => ({ value: c, label: `×${c}` }))}
            value={settings.crowd}
            onChange={(crowd) => update({ crowd })}
          />
          <div role="group" aria-label="Layers (demo only)" className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-muted">Layers (demo only)</span>
            <div className="flex flex-wrap gap-1">
              {LAYERS.map((o) => {
                const active = settings[o.key];
                return (
                  <button
                    key={o.key}
                    type="button"
                    aria-pressed={active}
                    onClick={() => update({ [o.key]: !active })}
                    className={`rounded-md px-2.5 py-1 text-[12px] font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-text ${
                      active ? "bg-text text-bg" : "bg-surface-3 text-muted hover:text-text"
                    }`}
                  >
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div role="group" aria-label="Scenario (demo only)" className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-muted">Scenario (demo only)</span>
            <div className="flex flex-wrap gap-1">
              {(
                [
                  { value: "chase", label: "Police chase" },
                  { value: "ambulance", label: "Ambulance call-out" },
                  { value: "clear", label: "Stop scenario" },
                ] as const
              ).map((o) => {
                const active = o.value === "clear" ? scenario === null : scenario?.source === "demo" && scenario.kind === o.value;
                return (
                  <button
                    key={o.value}
                    type="button"
                    aria-pressed={active}
                    onClick={() => triggerScenario(o.value)}
                    className={`rounded-md px-2.5 py-1 text-[12px] font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-text ${
                      active ? "bg-text text-bg" : "bg-surface-3 text-muted hover:text-text"
                    }`}
                  >
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div role="group" aria-label="Jump to" className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-muted">Jump to {pending ? "· loading…" : ""}</span>
            <div className="flex flex-wrap gap-1">
              {PLACES.map((p) => {
                const active = Math.abs(p.lat - origin.lat) < 0.003 && Math.abs(p.lon - origin.lon) < 0.004;
                return (
                  <button
                    key={p.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setDemoStreamer(p.lat, p.lon)}
                    className={`rounded-md px-2.5 py-1 text-[12px] font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-text ${
                      active ? "bg-text text-bg" : "bg-surface-3 text-muted hover:text-text"
                    }`}
                  >
                    {p.name}
                  </button>
                );
              })}
            </div>
          </div>
          <button
            type="button"
            disabled={live}
            onClick={() => setDemoSettings(LIVE_SETTINGS)}
            className="rounded-lg border border-line-strong px-3 py-1.5 text-[12px] font-semibold text-text transition-colors hover:border-text/40 disabled:cursor-default disabled:opacity-40"
          >
            Back to live
          </button>
        </div>
      )}
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
        className={`flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12px] font-semibold backdrop-blur transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-text ${
          live
            ? "border-line-strong bg-surface/90 text-muted hover:text-text"
            : "border-warn/50 bg-surface/90 text-warn"
        }`}
      >
        <span className={`size-1.5 rounded-full ${live ? "bg-ok" : "bg-warn"}`} />
        Dev
      </button>
    </div>
  );
}
