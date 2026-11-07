"use client";

import { Chip, Confidence, Delta, card, label } from "./ui";
import type { PredictRun } from "./types";

const ARCHETYPE_LABEL: Record<string, string> = {
  commuter: "Commuters",
  tourist: "Tourists",
  office_lunch: "Office lunch",
  resident: "Residents",
  delivery_rider: "Delivery riders",
  cyclist: "Cyclists",
  school_group: "School group",
  transit_arrival: "Bus arrivals",
  event_goer: "Event-goers",
};

export function ModeChip({ run }: { run: PredictRun }) {
  return run.report.mode === "llm" ? (
    <Chip color="var(--ok)">LLM · Nemotron via W&B</Chip>
  ) : (
    <Chip color="var(--warn)">Rules</Chip>
  );
}

export function Report({ run }: { run: PredictRun }) {
  const weave = run.trace ? `https://wandb.ai/${run.trace.weave_project}/weave` : null;
  return (
    <div style={card}>
      <div style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.4 }}>{run.report.headline}</div>
      <div style={{ display: "grid", gap: 8, marginTop: 2 }}>
        {run.report.items.map((it) => (
          <div key={it.zoneId} style={{ display: "grid", gap: 3, paddingTop: 6, borderTop: "1px solid var(--border)" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <span style={{ fontWeight: 600, flex: 1 }}>{it.zone}</span>
              <span style={label}>+{Math.round(it.atMin)} min</span>
              <Delta value={it.delta} />
            </div>
            <div style={label}>
              {Math.round(it.now)} → {Math.round(it.predicted)} people · {it.direction}
              {it.intervention ? " · what-if" : ""}
            </div>
            {it.reasons.length > 0 && <div style={{ fontSize: 12, color: "#c9c8d0" }}>{it.reasons.join("; ")}</div>}
            <Confidence value={it.confidence} />
          </div>
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
      <details style={{ fontSize: 12 }}>
        <summary style={{ cursor: "pointer", color: "var(--muted)" }}>{run.personas.length} persona groups</summary>
        <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
          {run.personas.map((p) => (
            <div key={p.id} style={{ display: "grid", gap: 1 }}>
              <div>
                <span style={{ fontWeight: 600 }}>{ARCHETYPE_LABEL[p.archetype] ?? p.archetype}</span>
                <span style={label}>
                  {" "}
                  · {p.size} people · {p.zoneName} → {p.destination}
                </span>
              </div>
              <div style={{ color: "#a9a8b2", fontSize: 11 }}>{p.rationale}</div>
            </div>
          ))}
        </div>
      </details>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <ModeChip run={run} />
        {weave && (
          <a href={weave} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: "var(--muted)", textDecoration: "underline" }}>
            Weave trace
          </a>
        )}
        <span style={{ ...label, marginLeft: "auto" }}>
          run {run.runId} · observed {run.seed.observedSegments} of {run.seed.segmentCount} segments, inferred the rest
        </span>
      </div>
    </div>
  );
}
