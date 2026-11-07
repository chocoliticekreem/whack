"use client";

import { ModeChip } from "./Report";
import { startReplay } from "./replay";
import { shownRun, usePredict } from "./store";
import type { PredictRun, ReportItem } from "./types";
import { Button, Chip, Confidence, Delta, label } from "./ui";

const CSS = `
.predict-modal-back { position: absolute; inset: 0; z-index: 60; display: grid; place-items: center; padding: 16px;
  background: rgba(4,4,8,0.55); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); pointer-events: auto; }
.predict-modal { width: min(760px, 100%); max-height: calc(100% - 32px); overflow-y: auto; border-radius: 18px;
  background: rgba(14,14,20,0.94); border: 1px solid var(--border-strong); color: var(--text); padding: 22px 24px;
  font-family: var(--font-figtree), system-ui, sans-serif; font-size: 13px; line-height: 1.4; box-shadow: 0 30px 80px rgba(0,0,0,0.6);
  display: grid; gap: 14px; }
.predict-actions { position: sticky; bottom: -22px; margin: 0 -24px -22px; padding: 12px 24px 22px; background: rgba(14,14,20,0.96);
  border-top: 1px solid var(--border); display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.predict-modal h2 { margin: 0; font-size: 22px; line-height: 1.25; font-weight: 700; letter-spacing: -0.01em; }
.predict-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }
.predict-card { border-radius: 14px; padding: 12px 14px; border: 1px solid var(--border); background: rgba(255,255,255,0.04); display: grid; gap: 6px; }
.predict-card.hot { border-color: rgba(255,106,61,0.45); background: rgba(255,106,61,0.08); }
.predict-card.cold { border-color: rgba(79,159,224,0.45); background: rgba(79,159,224,0.08); }
.predict-card .big { font-size: 26px; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1; }
.predict-replay { font-size: 15px; padding: 11px 22px; border-radius: 999px; border: 0; cursor: pointer; font-family: inherit;
  font-weight: 700; color: #fff; background: var(--ember); box-shadow: 0 0 0 0 rgba(255,106,61,0.6); animation: predict-pulse 1.6s infinite; }
@keyframes predict-pulse { 0% { box-shadow: 0 0 0 0 rgba(255,106,61,0.55); } 70% { box-shadow: 0 0 0 14px rgba(255,106,61,0); } 100% { box-shadow: 0 0 0 0 rgba(255,106,61,0); } }
@media (max-width: 640px) { .predict-modal { padding: 16px; } .predict-actions { position: sticky; bottom: -22px; margin: 0 -24px -22px; padding: 12px 24px 22px; background: rgba(14,14,20,0.96);
  border-top: 1px solid var(--border); display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.predict-modal h2 { font-size: 18px; } }
`;

function keyItems(run: PredictRun): ReportItem[] {
  return run.report.items
    .slice()
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, 5)
    .sort((a, b) => a.atMin - b.atMin);
}

function Card({ it, at }: { it: ReportItem; at: number }) {
  const when = new Date(at + it.atMin * 60_000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const why = it.reasons.map((r) => r.replace(/_/g, " ")).join(" · ");
  return (
    <div className={`predict-card ${it.delta > 0 ? "hot" : "cold"}`}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontWeight: 700, flex: 1, fontSize: 14 }}>{it.zone}</span>
        {it.intervention && <Chip color="#ff2d55">what-if</Chip>}
      </div>
      <div className="big">
        <Delta value={it.delta} />
      </div>
      <div style={label}>
        {Math.round(it.now)} → {Math.round(it.predicted)} people at +{Math.round(it.atMin)} min ({when})
      </div>
      {why && <div style={{ fontSize: 12, color: "#d6d5dd" }}>{why}</div>}
      {it.inputs.length > 0 && <div style={{ ...label, fontSize: 10.5 }}>inputs: {it.inputs.slice(0, 2).join(" · ")}</div>}
      <Confidence value={it.confidence} />
    </div>
  );
}

function WhatIfDiff({ baseline, whatIf }: { baseline: PredictRun; whatIf: PredictRun }) {
  const last = whatIf.timeline[whatIf.timeline.length - 1];
  const base = baseline.timeline[baseline.timeline.length - 1];
  const basePeople = new Map(base.zones.map((z) => [z.id, z.people]));
  const rows = last.zones
    .map((z) => ({ id: z.id, name: z.name, delta: z.people - (basePeople.get(z.id) ?? 0), people: z.people, was: basePeople.get(z.id) ?? 0 }))
    .filter((r) => Math.abs(r.delta) >= 1)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, 6);
  const ivs = whatIf.interventions
    .filter((iv) => !iv.source || iv.source === "user")
    .map((iv) => `${iv.kind}${iv.note ? ` (${iv.note})` : ""}`)
    .join(", ");
  return (
    <div style={{ borderRadius: 14, border: "1px solid rgba(255,45,85,0.45)", background: "rgba(255,45,85,0.07)", padding: "12px 14px", display: "grid", gap: 6 }}>
      <div style={{ fontWeight: 700, fontSize: 14 }}>What-if vs baseline at +{last.minute} min</div>
      <div style={label}>{ivs || "intervention"} · zones that move by a person or more</div>
      {rows.length === 0 && <div style={label}>No zone moves by a person or more.</div>}
      {rows.map((r) => (
        <div key={r.id} style={{ display: "flex", gap: 10, alignItems: "baseline", fontSize: 13 }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{r.name}</span>
          <span style={{ ...label, fontVariantNumeric: "tabular-nums" }}>
            {Math.round(r.was)} → {Math.round(r.people)}
          </span>
          <span style={{ fontSize: 16 }}>
            <Delta value={r.delta} />
          </span>
        </div>
      ))}
    </div>
  );
}

export function ReportModal() {
  const open = usePredict((s) => s.reportOpen);
  const run = usePredict(shownRun);
  const baseline = usePredict((s) => s.baseline);
  const shown = usePredict((s) => s.shown);
  if (!open || !run) return null;
  const items = keyItems(run);
  const weave = run.trace ? `https://wandb.ai/${run.trace.weave_project}/weave` : null;
  const close = () => usePredict.setState({ reportOpen: false });
  const isWhatIf = shown === "whatIf" && baseline && run !== baseline;
  return (
    <>
      <style>{CSS}</style>
      <div className="predict-modal-back" onClick={close}>
        <div className="predict-modal" role="dialog" aria-label="Forecast report" onClick={(e) => e.stopPropagation()}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ ...label, textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 700 }}>
              {isWhatIf ? "What-if forecast" : "What happens in the next 30 minutes"}
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
              <ModeChip run={run} />
              {weave && (
                <a href={weave} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: "var(--muted)", textDecoration: "underline" }}>
                  Weave trace
                </a>
              )}
            </span>
          </div>
          <h2>{run.report.headline}</h2>
          <div style={label}>
            {run.personas.length} persona groups · observed {run.seed.observedSegments} of {run.seed.segmentCount} segments, inferred the rest · run {run.runId}
          </div>
          {isWhatIf && <WhatIfDiff baseline={baseline} whatIf={run} />}
          <div className="predict-cards">
            {items.map((it) => (
              <Card key={it.zoneId} it={it} at={run.at} />
            ))}
          </div>
          <details style={{ fontSize: 12 }}>
            <summary style={{ cursor: "pointer", color: "var(--muted)" }}>{run.report.assumptions.length} assumptions</summary>
            <ul style={{ margin: "6px 0 0", paddingLeft: 16, color: "#c9c8d0", display: "grid", gap: 3 }}>
              {run.report.assumptions.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </details>
          <div className="predict-actions">
            <button type="button" className="predict-replay" onClick={startReplay}>
              ▶ Replay in 3D
            </button>
            <Button
              onClick={() => {
                usePredict.setState({ reportOpen: false, panelOpen: true, minute: items[0]?.atMin ?? 30, playing: false });
              }}
            >
              View on timeline
            </Button>
            <span style={{ marginLeft: "auto" }}>
              <Button onClick={close}>Close</Button>
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
