"use client";

import { useState, type CSSProperties } from "react";
import { toggleGps, useLive } from "../live/store";
import { PRESETS, useCameraStore } from "../scene/cameraStore";
import { useCompare } from "./store";
import { calibrationFor, camHeadingDeg } from "../live/project";
import { CamSearch } from "../search/CamSearch";

const CSS = `
.twin-chip { padding: 6px 14px; }
@media (max-width: 640px) { .twin-chip { padding: 6px 10px; } }
`;

const pill: CSSProperties = {
  display: "flex",
  gap: 4,
  padding: 4,
  borderRadius: 999,
  border: "1px solid var(--border)",
  background: "rgba(0,0,0,0.5)",
  backdropFilter: "blur(12px)",
  pointerEvents: "auto",
};

function Chip({ active, onClick, children }: { active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="twin-chip"
      style={{
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 500,
        letterSpacing: "0.02em",
        border: "none",
        cursor: "pointer",
        fontFamily: "inherit",
        background: active ? "var(--text)" : "transparent",
        color: active ? "var(--bg)" : "rgba(255,255,255,0.7)",
        transition: "background 120ms, color 120ms",
      }}
    >
      {children}
    </button>
  );
}

/** Sits in the bottom row after the Aerial / Street / Skyline bar: streamer view, GPS, and the CCTV picker. */
export function ViewBar() {
  const preset = useCameraStore((s) => s.preset);
  const cctv = useCameraStore((s) => s.cctv);
  const cinematic = useCameraStore((s) => s.cinematic);
  const fly = useCameraStore((s) => s.fly);
  const gps = useLive((s) => s.gps);
  const cams = useLive((s) => s.cams);
  const camId = useCompare((s) => s.camId);
  const open = useCompare((s) => s.open);
  const [picking, setPicking] = useState(false);

  if (camId) return null;

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        alignItems: "flex-end",
        color: "var(--text)",
        fontFamily: "var(--font-figtree), system-ui, sans-serif",
      }}
    >
      <style>{CSS}</style>
      <div style={pill}>
        <Chip active={!cctv && !cinematic && preset === "streamer"} onClick={() => fly("streamer")}>
          {PRESETS.streamer.label}
        </Chip>
        <Chip active={gps === "on"} onClick={toggleGps}>
          {gps === "on" ? "GPS live" : gps === "error" ? "GPS unavailable" : "Use my GPS"}
        </Chip>
      </div>
      <div style={{ position: "relative" }}>
        <div style={pill}>
          <Chip active={picking} onClick={() => setPicking((p) => !p)}>
            Compare CCTV
          </Chip>
        </div>
        {picking && (
          <div
            style={{
              position: "absolute",
              bottom: "calc(100% + 8px)",
              left: 0,
              zIndex: 40,
              minWidth: 260,
              maxWidth: "calc(100vw - 24px)",
              padding: 6,
              borderRadius: 14,
              border: "1px solid var(--border)",
              background: "rgba(12,12,16,0.86)",
              backdropFilter: "blur(14px)",
              display: "grid",
              gap: 2,
              pointerEvents: "auto",
            }}
          >
            <div style={{ padding: "6px 10px 4px", fontSize: 11, color: "var(--muted)" }}>
              Real JamCam on the left, the twin from the same pose on the right
            </div>
            {cams.length === 0 && <div style={{ padding: "6px 10px", fontSize: 12, color: "var(--muted)" }}>Loading cameras…</div>}
            {cams.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => {
                  setPicking(false);
                  open(c.id);
                }}
                style={{
                  textAlign: "left",
                  padding: "7px 10px",
                  borderRadius: 9,
                  border: "none",
                  background: "transparent",
                  color: "var(--text)",
                  fontSize: 13,
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.08)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                {c.name}
                <span style={{ color: "var(--muted)", fontSize: 11 }}>
                  {" · "}
                  {calibrationFor(c.id)
                    ? `facing ${Math.round(camHeadingDeg(c))}°, calibrated`
                    : c.heading === null
                      ? "facing Tower Hill"
                      : `${c.heading}°`}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <CamSearch />
    </div>
  );
}
