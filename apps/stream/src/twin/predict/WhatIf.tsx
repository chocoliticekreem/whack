"use client";

import { areaName, useOrigin } from "../origin";
import { KIND_COLOR } from "./Ghost";
import { removeIntervention, runForecast, updateIntervention, usePredict } from "./store";
import type { Intervention, InterventionKind, PredictRun } from "./types";
import { Button, Chip, Delta, card, label } from "./ui";

const KINDS: { kind: InterventionKind; name: string }[] = [
  { kind: "closure", name: "Close street" },
  { kind: "incident", name: "Incident" },
  { kind: "event", name: "Event crowd" },
  { kind: "weather", name: "Heavy rain" },
];
const KIND_NAME = Object.fromEntries(KINDS.map((k) => [k.kind, k.name])) as Record<InterventionKind, string>;
const KIND_VERB: Record<InterventionKind, string> = { closure: "closed", incident: "blocked traffic", event: "drew a crowd", weather: "hit" };

/** "72 m W / 40 m N" from twin metres (x east, z south). */
export function placeLabel(where: { x: number; z: number }): string {
  const ew = `${Math.abs(where.x)} m ${where.x < 0 ? "W" : "E"}`;
  const ns = `${Math.abs(where.z)} m ${where.z < 0 ? "N" : "S"}`;
  return `${ew} / ${ns}`;
}

function describe(iv: Intervention & { replanned?: number }): string {
  const what = iv.note ? `${KIND_NAME[iv.kind].toLowerCase()} "${iv.note}"` : KIND_NAME[iv.kind].toLowerCase();
  return `${what} ${KIND_VERB[iv.kind]} ${placeLabel(iv.where)} at +${iv.atMin} min`;
}

function countReroutes(run: PredictRun): number {
  let n = 0;
  for (const step of run.timeline) {
    for (const e of step.events) if (/re-?rout|replan|divert|shelter|abandon/i.test(JSON.stringify(e))) n++;
  }
  return n;
}

export function Diff({ baseline, whatIf }: { baseline: PredictRun; whatIf: PredictRun }) {
  const last = whatIf.timeline[whatIf.timeline.length - 1];
  const base = baseline.timeline[baseline.timeline.length - 1];
  const basePeople = new Map(base.zones.map((z) => [z.id, z.people]));
  const rows = last.zones
    .map((z) => ({ id: z.id, name: z.name, delta: z.people - (basePeople.get(z.id) ?? 0), people: z.people }))
    .filter((r) => Math.abs(r.delta) >= 1)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, 6);
  const reroutes = countReroutes(whatIf);
  const user = whatIf.interventions.filter((iv) => iv.source !== "observed" && typeof iv.where === "object" && "x" in iv.where);
  const why = user.length ? `because ${user.map((iv) => describe(iv as Intervention)).join("; ")}` : null;
  return (
    <div style={card}>
      <div style={{ fontWeight: 600 }}>What-if vs baseline at +{last.minute} min</div>
      {rows.length === 0 && <div style={label}>No zone moves by a person or more.</div>}
      {rows.map((r) => (
        <div key={r.id} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
          <span style={{ flex: 1 }}>{r.name}</span>
          <span style={label}>{Math.round(r.people)} people</span>
          <Delta value={r.delta} />
        </div>
      ))}
      <div style={{ fontSize: 12, color: "#c9c8d0" }}>
        {why ? `${why}, ` : ""}
        {reroutes > 0 ? `${reroutes} persona group${reroutes === 1 ? "" : "s"} rerouted` : "no group changed plan"}
      </div>
    </div>
  );
}

export function WhatIf() {
  const placing = usePredict((s) => s.placing);
  const interventions = usePredict((s) => s.interventions);
  const loading = usePredict((s) => s.loading);
  const baseline = usePredict((s) => s.baseline);
  const origin = useOrigin((s) => s.origin);
  return (
    <div style={card}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontWeight: 600, flex: 1 }}>What if…</span>
        <span style={label}>{placing ? "Click the map to place it" : "Pick a type, then click the map"}</span>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {KINDS.map((k) => (
          <Button key={k.kind} small active={placing === k.kind} onClick={() => usePredict.setState({ placing: placing === k.kind ? null : k.kind })}>
            {k.name}
          </Button>
        ))}
      </div>
      {interventions.map((iv, i) => (
        <div key={i} style={{ display: "grid", gap: 4, paddingTop: 6, borderTop: "1px solid var(--border)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Chip color={KIND_COLOR[iv.kind]}>{KIND_NAME[iv.kind]}</Chip>
            <span style={{ ...label, flex: 1 }}>{placeLabel(iv.where)}</span>
            <select
              value={iv.atMin}
              onChange={(e) => updateIntervention(i, { atMin: Number(e.target.value) })}
              style={{ background: "transparent", color: "var(--text)", border: "1px solid var(--border-strong)", borderRadius: 6, fontSize: 11, fontFamily: "inherit" }}
            >
              {[0, 5, 10, 15, 20].map((m) => (
                <option key={m} value={m} style={{ color: "#000" }}>
                  at +{m} min
                </option>
              ))}
            </select>
            <button type="button" onClick={() => removeIntervention(i)} aria-label="Remove" style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer", fontSize: 14 }}>
              ×
            </button>
          </div>
          <input
            value={iv.note}
            placeholder="Note, e.g. burst water main"
            onChange={(e) => updateIntervention(i, { note: e.target.value })}
            style={{ background: "rgba(255,255,255,0.05)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 8, padding: "5px 8px", fontSize: 12, fontFamily: "inherit" }}
          />
        </div>
      ))}
      {interventions.length > 0 && (
        <Button primary disabled={loading || !baseline} onClick={() => void runForecast("whatIf", origin, areaName(origin))}>
          Re-run with {interventions.length} intervention{interventions.length === 1 ? "" : "s"}
        </Button>
      )}
      {interventions.length > 0 && !baseline && <div style={label}>Run the baseline first so there is something to diff against.</div>}
    </div>
  );
}
