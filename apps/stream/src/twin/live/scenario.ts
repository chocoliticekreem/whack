"use client";

import { useEffect } from "react";
import { create } from "zustand";
import { subscribe } from "@/platform/bus";
import { useLive } from "./store";

export type ScenarioKind = "chase" | "ambulance";

export interface Scenario {
  kind: ScenarioKind;
  /** "demo" = fired from the DevPanel or the `sim.trigger` bus event; "tfl" = a real TfL incident nearby. */
  source: "demo" | "tfl";
  label: string;
}

export const useScenario = create<{ scenario: Scenario | null }>(() => ({ scenario: null }));

const INCIDENT_CATEGORY = /collision|accident|incident|emergency|hazard/i;
const INCIDENT_NOTES = /collision|accident|casualt|emergency services/i;

export function triggerScenario(kind: ScenarioKind | "clear") {
  if (kind === "clear") {
    useScenario.setState({ scenario: null });
    return;
  }
  const label = kind === "chase" ? "Police chase" : "Ambulance call-out";
  useScenario.setState({ scenario: { kind, source: "demo", label } });
}

/**
 * Live mode never invents incidents: the only non-demo scenario is an ambulance responding to a real
 * TfL disruption whose category or notes read like one. Demo triggers (panel or bus) take precedence.
 */
export function useScenarioFeeds() {
  const disruptions = useLive((s) => s.disruptions);
  useEffect(() => subscribe((e) => e.type === "sim.trigger" && triggerScenario(e.kind)), []);
  useEffect(() => {
    const current = useScenario.getState().scenario;
    if (current?.source === "demo") return;
    const hit = disruptions.find((d) => INCIDENT_CATEGORY.test(d.category) || INCIDENT_NOTES.test(d.comments));
    const next: Scenario | null = hit
      ? { kind: "ambulance", source: "tfl", label: `TfL ${hit.category.toLowerCase()} · ${hit.street}` }
      : null;
    if (next?.label !== current?.label) useScenario.setState({ scenario: next });
  }, [disruptions]);
}
