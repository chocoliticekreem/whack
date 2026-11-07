"use client";

import { useEffect, useState } from "react";
import { useCompare } from "../compare/store";
import { areaName, useOrigin } from "../origin";
import { useFieldStore } from "../sim/useField";
import { startReplay, useReplay } from "./replay";
import { ModeChip } from "./Report";
import { Score } from "./Score";
import { cancelForecast, clearRuns, FORECAST_TIMEOUT_MS, runForecast, setMinute, shownRun, usePredict } from "./store";
import { Button, Row, card, label } from "./ui";
import { Diff, WhatIf } from "./WhatIf";

const CSS = `
.predict-panel { position: absolute; top: 60px; right: 16px; width: 340px; max-height: calc(100% - 136px); z-index: 30;
  display: flex; flex-direction: column; border-radius: 14px; background: rgba(12,12,16,0.78); border: 1px solid var(--border);
  backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); color: var(--text);
  font-family: var(--font-figtree), system-ui, sans-serif; font-size: 13px; line-height: 1.35; pointer-events: auto; }
.predict-panel.closed { width: auto; max-height: none; }
.predict-body { overflow-y: auto; display: grid; gap: 8px; padding: 0 12px 12px; }
.predict-range { width: 100%; accent-color: var(--ember); margin: 0; }
.predict-spin { width: 12px; height: 12px; border-radius: 999px; border: 2px solid rgba(255,255,255,0.35); border-top-color: #fff;
  animation: predict-spin 0.8s linear infinite; display: inline-block; vertical-align: -2px; margin-right: 6px; }
@keyframes predict-spin { to { transform: rotate(360deg); } }
.predict-ready { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 700;
  color: var(--ok); border: 1px solid color-mix(in srgb, var(--ok) 45%, transparent); background: color-mix(in srgb, var(--ok) 12%, transparent); }
.predict-stages { display: grid; gap: 3px; font-size: 11px; color: var(--muted); }
.predict-stages .on { color: var(--text); font-weight: 600; }
.predict-stages .done { color: var(--ok); }
.predict-progress { height: 3px; border-radius: 2px; background: rgba(255,255,255,0.1); overflow: hidden; }
.predict-progress > span { display: block; height: 100%; background: var(--ember); transition: width 900ms linear; }
@media (max-width: 640px) { .predict-panel { top: auto; bottom: 112px; left: 12px; right: 12px; width: auto; max-height: 48vh; } }
`;

/** Rough wall-clock split of a run: the service does not stream progress, so stages are timed. */
const STAGES: { at: number; text: string }[] = [
  { at: 0, text: "Seeding from live field" },
  { at: 7, text: "Walking 80 persona groups" },
  { at: 40, text: "Writing report" },
];

function Progress({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setElapsed(Math.max(0, Math.floor((Date.now() - startedAt) / 1000))), 500);
    return () => clearInterval(t);
  }, [startedAt]);
  const current = STAGES.filter((st) => st.at <= elapsed).length - 1;
  return (
    <div style={card}>
      <Row>
        <span className="predict-spin" />
        <span style={{ fontWeight: 600 }}>Forecasting · {elapsed} s</span>
        <span style={{ marginLeft: "auto" }}>
          <Button small onClick={cancelForecast}>
            Cancel
          </Button>
        </span>
      </Row>
      <div className="predict-progress">
        <span style={{ width: `${Math.min(100, (elapsed / 60) * 100)}%` }} />
      </div>
      <div className="predict-stages">
        {STAGES.map((st, i) => (
          <span key={st.text} className={i < current ? "done" : i === current ? "on" : ""}>
            {i < current ? "✓" : i === current ? "→" : "·"} {st.text}
          </span>
        ))}
      </div>
      <span style={label}>Usually 20–60 s · gives up at {FORECAST_TIMEOUT_MS / 1000} s</span>
    </div>
  );
}

function Scrubber() {
  const minute = usePredict((s) => s.minute);
  const playing = usePredict((s) => s.playing);
  const ghosts = usePredict((s) => s.ghosts);
  const whatIf = usePredict((s) => s.whatIf);
  const shown = usePredict((s) => s.shown);
  const run = usePredict(shownRun);
  if (!run) return null;
  const m = Math.round(minute);
  const step = run.timeline.find((s) => s.minute === m) ?? run.timeline[0];
  const at = new Date(run.at + m * 60_000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return (
    <div style={card}>
      <Row>
        <span style={{ fontWeight: 600, color: m > 0 ? "#ff6a3d" : "var(--text)" }}>{m === 0 ? "Now" : `Predicted +${m} min`}</span>
        <span style={label}>{at} · {step.walking} groups walking</span>
        <span style={{ marginLeft: "auto" }}>
          <Button small onClick={() => usePredict.setState({ playing: !playing, minute: !playing && minute >= 30 ? 0 : minute })}>
            {playing ? "Pause" : "Play"}
          </Button>
        </span>
      </Row>
      <input
        className="predict-range"
        type="range"
        min={0}
        max={30}
        step={1}
        value={m}
        aria-label="Forecast minute"
        onChange={(e) => {
          usePredict.setState({ playing: false });
          setMinute(Number(e.target.value));
        }}
      />
      <Row>
        <span style={label}>Key zones only: ember busier than now, blue quieter</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          {whatIf && (
            <>
              <Button small active={shown === "baseline"} onClick={() => usePredict.setState({ shown: "baseline" })}>
                Baseline
              </Button>
              <Button small active={shown === "whatIf"} onClick={() => usePredict.setState({ shown: "whatIf" })}>
                What-if
              </Button>
            </>
          )}
          <Button small active={ghosts} onClick={() => usePredict.setState({ ghosts: !ghosts })}>
            Ghosts
          </Button>
        </span>
      </Row>
    </div>
  );
}

export function PredictPanel() {
  const comparing = useCompare((s) => s.camId !== null);
  const replaying = useReplay((s) => s.active);
  const open = usePredict((s) => s.panelOpen);
  const loading = usePredict((s) => s.loading);
  const startedAt = usePredict((s) => s.startedAt);
  const error = usePredict((s) => s.error);
  const notice = usePredict((s) => s.notice);
  const baseline = usePredict((s) => s.baseline);
  const whatIf = usePredict((s) => s.whatIf);
  const run = usePredict(shownRun);
  const origin = useOrigin((s) => s.origin);
  const field = useFieldStore();
  const area = areaName(origin);
  useEffect(() => {
    // Why: a run's ghosts are in the frame it was seeded in; a new origin empties them.
    clearRuns();
  }, [origin.lat, origin.lon]);
  if (comparing || replaying) return null;

  return (
    <>
      <style>{CSS}</style>
      <div className={`predict-panel${open ? "" : " closed"}`}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px" }}>
          <span style={{ width: 9, height: 9, borderRadius: 999, background: "#ff6a3d", boxShadow: "0 0 8px #ff6a3d" }} />
          <span style={{ fontWeight: 600, fontSize: 14, flex: 1, whiteSpace: "nowrap" }}>Predict · {area} · 30 min</span>
          {run && <ModeChip run={run} />}
          <button
            type="button"
            onClick={() => usePredict.setState({ panelOpen: !open })}
            aria-label={open ? "Collapse" : "Expand"}
            style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer", fontSize: 14, fontFamily: "inherit" }}
          >
            {open ? "−" : "+"}
          </button>
        </div>
        {open && (
          <div className="predict-body">
            {!loading && (
              <Row>
                <Button primary onClick={() => void runForecast("baseline", origin, area)}>
                  {baseline ? "Re-run baseline" : "Predict next 30 min"}
                </Button>
                {run && (
                  <>
                    <span className="predict-ready">● Report ready</span>
                    <Button small onClick={() => usePredict.setState({ reportOpen: true })}>
                      Open report
                    </Button>
                    <Button small onClick={startReplay}>
                      ▶ Replay
                    </Button>
                  </>
                )}
              </Row>
            )}
            {startedAt !== null && <Progress key={startedAt} startedAt={startedAt} />}
            {error && <div style={{ ...card, color: "var(--live)", fontSize: 12 }}>{error}</div>}
            {notice && <div style={{ ...card, color: "var(--warn)", fontSize: 12 }}>{notice}</div>}
            {!run && !loading && !error && (
              <div style={label}>
                Seeds a parallel {area} from the live field, lets persona groups walk it forward and reports what changes.
                {field ? ` Observed ${field.observed} street segments from cameras, inferred the rest.` : ""}
              </div>
            )}
            <Scrubber />
            {baseline && whatIf && <Diff baseline={baseline} whatIf={whatIf} />}
            <WhatIf />
            <Score />
          </div>
        )}
      </div>
    </>
  );
}
