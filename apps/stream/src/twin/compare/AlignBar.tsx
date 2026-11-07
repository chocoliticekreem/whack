"use client";

import { useEffect, useState, type CSSProperties } from "react";
import type { AroundCam } from "@/platform/around/types";
import { camFrame, currentPose, setPoseOverride, type CamCalibration } from "../live/project";
import { useCameraStore } from "../scene/cameraStore";

type Field = "headingDeg" | "pitchDeg" | "hfovDeg" | "heightM";

const STEP: Record<Field, number> = { headingDeg: 1, pitchDeg: 0.5, hfovDeg: 2, heightM: 0.5 };
const LABEL: Record<Field, string> = { headingDeg: "Heading", pitchDeg: "Tilt", hfovDeg: "Lens", heightM: "Height" };
const UNIT: Record<Field, string> = { headingDeg: "°", pitchDeg: "°", hfovDeg: "°", heightM: " m" };

/** Keys: ←/→ heading, ↑/↓ tilt, [ ] lens, - = height. Shift = 5× step. */
const KEYS: Record<string, [Field, number]> = {
  ArrowLeft: ["headingDeg", -1],
  ArrowRight: ["headingDeg", 1],
  ArrowUp: ["pitchDeg", -1],
  ArrowDown: ["pitchDeg", 1],
  "[": ["hfovDeg", -1],
  "]": ["hfovDeg", 1],
  "-": ["heightM", -1],
  "=": ["heightM", 1],
};

const bar: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "6px 8px",
  borderRadius: 999,
  background: "rgba(12,12,16,0.82)",
  border: "1px solid var(--border-strong)",
  backdropFilter: "blur(10px)",
  fontSize: 12,
  pointerEvents: "auto",
  flexWrap: "wrap",
  justifyContent: "center",
};

const btn: CSSProperties = {
  minWidth: 26,
  height: 26,
  borderRadius: 999,
  border: "1px solid var(--border-strong)",
  background: "transparent",
  color: "var(--text)",
  cursor: "pointer",
  fontFamily: "inherit",
  fontSize: 13,
};

function clampPose(p: CamCalibration): CamCalibration {
  return {
    ...p,
    headingDeg: ((p.headingDeg % 360) + 360) % 360,
    pitchDeg: Math.min(80, Math.max(1, p.pitchDeg)),
    hfovDeg: Math.min(110, Math.max(15, p.hfovDeg)),
    heightM: Math.min(40, Math.max(2, p.heightM)),
  };
}

/**
 * Hand alignment for the compare view. The models give the starting pose; nudging heading and tilt until the
 * twin's kerbs and buildings sit on the real ones finishes it. People and cars stay pixel-locked throughout,
 * because they are projected through the same pose.
 */
export function AlignBar({ cam, overlay, setOverlay }: { cam: AroundCam; overlay: boolean; setOverlay: (on: boolean) => void }) {
  const [pose, setPose] = useState<CamCalibration>(() => currentPose(cam));
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  const apply = (next: CamCalibration) => {
    const p = clampPose(next);
    setPose(p);
    setStatus("idle");
    setPoseOverride(cam.id, p);
    useCameraStore.getState().setCctv(camFrame(cam), true);
  };

  const nudge = (field: Field, dir: number, big = false) => {
    const current = currentPose(cam);
    apply({ ...current, [field]: current[field] + dir * STEP[field] * (big ? 5 : 1) });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "o" || e.key === "O") {
        setOverlay(!overlay);
        return;
      }
      const hit = KEYS[e.key];
      if (!hit) return;
      e.preventDefault();
      nudge(hit[0], hit[1], e.shiftKey);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const save = async () => {
    setStatus("saving");
    try {
      const res = await fetch("/api/twin/calibration", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ camId: cam.id, pose }),
      });
      setStatus(res.ok ? "saved" : "error");
    } catch {
      setStatus("error");
    }
  };

  return (
    <div style={bar}>
      <button type="button" style={{ ...btn, padding: "0 10px", background: overlay ? "var(--text)" : "transparent", color: overlay ? "var(--bg)" : "var(--text)" }} onClick={() => setOverlay(!overlay)} title="Show the real CCTV over the twin (O)">
        Overlay real
      </button>
      {(Object.keys(STEP) as Field[]).map((f) => (
        <span key={f} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <span style={{ color: "var(--muted)" }}>{LABEL[f]}</span>
          <button type="button" style={btn} onClick={(e) => nudge(f, -1, e.shiftKey)} aria-label={`${LABEL[f]} down`}>
            −
          </button>
          <span style={{ minWidth: 44, textAlign: "center", fontVariantNumeric: "tabular-nums" }}>
            {pose[f].toFixed(f === "headingDeg" || f === "hfovDeg" ? 0 : 1)}
            {UNIT[f]}
          </span>
          <button type="button" style={btn} onClick={(e) => nudge(f, 1, e.shiftKey)} aria-label={`${LABEL[f]} up`}>
            +
          </button>
        </span>
      ))}
      <button type="button" style={{ ...btn, padding: "0 12px", background: "var(--text)", color: "var(--bg)", fontWeight: 600 }} onClick={save} disabled={status === "saving"}>
        {status === "saving" ? "Saving…" : status === "saved" ? "Saved" : status === "error" ? "Save failed" : "Save pose"}
      </button>
      <span style={{ color: "var(--muted)", fontSize: 11 }}>←→ heading · ↑↓ tilt · [ ] lens · −/= height · Shift ×5 · O overlay</span>
    </div>
  );
}
