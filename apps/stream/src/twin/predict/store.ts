"use client";

import { create } from "zustand";
import type { LatLon } from "../config";
import type { Intervention, InterventionKind, PredictRun, ScoreResult } from "./types";

export const RESCORE_MS = 120_000;
export const FORECAST_TIMEOUT_MS = 90_000;

interface PredictState {
  baseline: PredictRun | null;
  whatIf: PredictRun | null;
  /** Which run the ghost layer and the report show. */
  shown: "baseline" | "whatIf";
  loading: boolean;
  /** Wall-clock start of the in-flight forecast, for the elapsed counter. */
  startedAt: number | null;
  abort: AbortController | null;
  error: string | null;
  /** Non-fatal message, e.g. a timeout that fell back to the last run. */
  notice: string | null;
  minute: number;
  playing: boolean;
  ghosts: boolean;
  interventions: Intervention[];
  placing: InterventionKind | null;
  score: ScoreResult | null;
  scoring: boolean;
  panelOpen: boolean;
  reportOpen: boolean;
}

export const usePredict = create<PredictState>(() => ({
  baseline: null,
  whatIf: null,
  shown: "baseline",
  loading: false,
  startedAt: null,
  abort: null,
  error: null,
  notice: null,
  minute: 0,
  playing: false,
  ghosts: true,
  interventions: [],
  placing: null,
  score: null,
  scoring: false,
  panelOpen: true,
  reportOpen: false,
}));

export function shownRun(s: PredictState): PredictRun | null {
  return s.shown === "whatIf" && s.whatIf ? s.whatIf : s.baseline;
}

async function postJson<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  const data = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok) throw new Error(data.error ?? `Predict failed (${res.status})`);
  return data;
}

export async function runForecast(kind: "baseline" | "whatIf", origin: LatLon, area: string) {
  const { interventions, abort: previous } = usePredict.getState();
  previous?.abort("replaced");
  const abort = new AbortController();
  const timer = window.setTimeout(() => abort.abort("timeout"), FORECAST_TIMEOUT_MS);
  usePredict.setState({ loading: true, startedAt: Date.now(), abort, error: null, notice: null, playing: false });
  try {
    const body = { horizonMin: 30, origin, area, ...(kind === "whatIf" ? { interventions } : {}) };
    const run = await postJson<PredictRun>("/api/predict", body, abort.signal);
    usePredict.setState(
      kind === "whatIf"
        ? { whatIf: run, shown: "whatIf", loading: false, startedAt: null, abort: null, minute: 30, reportOpen: true }
        : { baseline: run, whatIf: null, shown: "baseline", loading: false, startedAt: null, abort: null, score: null, minute: 0, reportOpen: true },
    );
  } catch (e) {
    if (usePredict.getState().abort !== abort) return;
    const hasRun = usePredict.getState().baseline !== null;
    const why = abort.signal.aborted ? String(abort.signal.reason) : "";
    const notice =
      why === "timeout"
        ? `Forecast timed out after ${FORECAST_TIMEOUT_MS / 1000} s${hasRun ? " — showing the last run" : ""}.`
        : why === "cancel"
          ? `Forecast cancelled${hasRun ? " — showing the last run" : ""}.`
          : null;
    usePredict.setState({ loading: false, startedAt: null, abort: null, notice, error: notice ? null : (e as Error).message });
  } finally {
    window.clearTimeout(timer);
  }
}

export function cancelForecast() {
  usePredict.getState().abort?.abort("cancel");
}

/** A run's ghosts are in the frame it was seeded in; a new origin empties them. */
export function clearRuns() {
  cancelForecast();
  usePredict.setState({
    baseline: null,
    whatIf: null,
    shown: "baseline",
    score: null,
    minute: 0,
    playing: false,
    interventions: [],
    placing: null,
    notice: null,
    error: null,
  });
}

export async function scoreLatest() {
  const run = usePredict.getState().baseline;
  if (!run) return;
  usePredict.setState({ scoring: true, error: null });
  try {
    const score = await postJson<ScoreResult>("/api/predict/score", { runId: run.runId });
    usePredict.setState({ score, scoring: false });
  } catch (e) {
    usePredict.setState({ scoring: false, error: (e as Error).message });
  }
}

export function setMinute(minute: number) {
  usePredict.setState({ minute: Math.max(0, Math.min(30, minute)) });
}

export function placeIntervention(x: number, z: number) {
  const { placing, interventions } = usePredict.getState();
  if (!placing) return;
  const iv: Intervention = { kind: placing, where: { x: Math.round(x), z: Math.round(z) }, note: "", atMin: 0 };
  usePredict.setState({ interventions: [...interventions, iv], placing: null });
}

export function updateIntervention(index: number, patch: Partial<Intervention>) {
  const list = usePredict.getState().interventions.slice();
  list[index] = { ...list[index], ...patch };
  usePredict.setState({ interventions: list });
}

export function removeIntervention(index: number) {
  usePredict.setState((s) => ({ interventions: s.interventions.filter((_, i) => i !== index) }));
}
