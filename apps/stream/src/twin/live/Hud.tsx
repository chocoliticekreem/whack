"use client";

import { useEffect, useState } from "react";
import { useDemoSettings } from "@/platform/demo";
import { useCompare } from "../compare/store";
import { useFieldStore } from "../sim/useField";
import { distanceM } from "@/platform/around/geo";
import { insideGeometryBounds, toLocal } from "../config";
import { areaName, useOrigin } from "../origin";
import { useScenario } from "./scenario";
import { useLive, useLiveFeeds, useNow } from "./store";

const DUE_SOON_S = 300;
const MOBILE_QUERY = "(max-width: 640px)";

function useMobile() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const sync = () => setMobile(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return mobile;
}

function StreamerStatus() {
  const streamer = useLive((s) => s.streamer);
  const source = useLive((s) => s.streamerSource);
  const accuracy = useLive((s) => s.streamerAccuracyM);
  const ts = useLive((s) => s.streamerTs);
  const speed = useLive((s) => s.streamerSpeed);
  const now = useNow();
  const origin = useOrigin((s) => s.origin);
  const pending = useOrigin((s) => s.pending);
  const away = distanceM(origin.lat, origin.lon, streamer.lat, streamer.lon);
  const outside = !insideGeometryBounds(...toLocal(streamer.lat, streamer.lon));
  const age = ts ? `${Math.max(0, Math.round((now - ts) / 1000))} s ago` : "";
  const label =
    source === "phone"
      ? `Phone GPS · ±${accuracy ?? "?"} m · ${age}`
      : source === "gps"
        ? `This device GPS · ±${accuracy ?? "?"} m · ${age}`
        : "Demo position";
  const pace = speed !== null && speed > 0.3 ? ` · ${(speed * 3.6).toFixed(1)} km/h` : "";
  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)", fontSize: 11 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <Swatch color="var(--ember)" glow />
        <span style={{ color: "var(--muted)", flex: 1 }}>Streamer</span>
        <span style={{ fontWeight: 600 }}>
          {label}
          {pace}
        </span>
      </div>
      {pending ? (
        <div style={{ marginTop: 6, color: "var(--muted)", fontWeight: 600 }}>Re-centring on {areaName(pending)}…</div>
      ) : (
        outside && (
          <div style={{ marginTop: 6, color: "var(--warn)", fontWeight: 600 }}>
            Outside the mirrored area ({(away / 1000).toFixed(1)} km from {areaName(origin)})
          </div>
        )
      )}
    </div>
  );
}

function CrowdChip({ crowd }: { crowd: number }) {
  return (
    <span
      style={{
        padding: "1px 7px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        color: "var(--warn)",
        border: "1px solid color-mix(in srgb, var(--warn) 45%, transparent)",
        background: "color-mix(in srgb, var(--warn) 12%, transparent)",
      }}
    >
      ×{crowd} crowd
    </span>
  );
}

function Swatch({ color, glow }: { color: string; glow?: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 9,
        height: 9,
        borderRadius: 999,
        background: color,
        boxShadow: glow ? `0 0 8px ${color}` : "none",
        flex: "none",
      }}
    />
  );
}

export function Hud() {
  useLiveFeeds();
  const cams = useLive((s) => s.cams);
  const clips = useLive((s) => s.clips);
  const stops = useLive((s) => s.stops);
  const disruptions = useLive((s) => s.disruptions);
  const updatedAt = useLive((s) => s.updatedAt);
  const screenMode = useLive((s) => s.screenMode);
  const now = useNow();
  const comparing = useCompare((s) => s.camId !== null);
  const field = useFieldStore();
  const scenario = useScenario((s) => s.scenario);
  const settings = useDemoSettings();
  const origin = useOrigin((s) => s.origin);
  const mobile = useMobile();
  const [expanded, setExpanded] = useState(false);
  if (comparing) return null;

  let people = 0;
  let vehicles = 0;
  for (const cam of cams) {
    for (const t of clips[cam.id]?.tracks ?? []) {
      if (t.kind === "person") people++;
      else vehicles++;
    }
  }
  let busesDue = 0;
  for (const s of stops) {
    for (const a of s.arrivals) {
      const rem = a.seconds - (now - s.at) / 1000;
      if (rem > -20 && rem < DUE_SOON_S) busesDue++;
    }
  }
  const uniqueDisruptions = new Set(disruptions.map((d) => JSON.stringify(d.line))).size;
  const analysed = cams.filter((c) => clips[c.id]).length;

  const rows: { label: string; value: string; color: string }[] = [
    { label: "People (real, cameras)", value: String(people), color: "#ffb37a" },
    { label: "Vehicles (real, cameras)", value: String(vehicles), color: "#9fd3ff" },
    { label: "Cameras", value: cams.length ? `${analysed}/${cams.length} analysed` : "loading…", color: "var(--live)" },
    { label: "Buses (TfL), due < 5 min", value: String(busesDue), color: "#c8102e" },
    { label: "Disruptions (TfL)", value: String(uniqueDisruptions), color: "var(--warn)" },
  ];
  const estimate = field
    ? `${Math.round(field.people * field.crowd)} people · ${Math.round(field.vehicles * field.crowd)} vehicles`
    : "estimating…";
  const basis = field ? `${field.observed} observed segments, ${field.inferred} inferred` : "";
  const crowd = field?.crowd ?? settings.crowd;

  if (mobile && !expanded) {
    return (
      <button
        type="button"
        data-twin-block
        onClick={() => setExpanded(true)}
        aria-expanded={false}
        style={{
          position: "absolute",
          top: 76,
          left: 12,
          zIndex: 30,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 12px",
          borderRadius: 999,
          background: "rgba(12,12,16,0.72)",
          border: "1px solid var(--border)",
          backdropFilter: "blur(14px)",
          WebkitBackdropFilter: "blur(14px)",
          color: "var(--text)",
          fontFamily: "var(--font-figtree), system-ui, sans-serif",
          fontSize: 12,
          fontWeight: 600,
          cursor: "pointer",
          pointerEvents: "auto",
        }}
      >
        <Swatch color="var(--live)" glow />
        Live · {people} people · {vehicles} vehicles
        {crowd !== 1 && <CrowdChip crowd={crowd} />}
      </button>
    );
  }

  return (
    <div
      data-twin-block
      onClick={mobile ? () => setExpanded(false) : undefined}
      style={{
        position: "absolute",
        top: mobile ? 76 : 16,
        left: mobile ? 12 : 16,
        zIndex: 30,
        width: 236,
        maxHeight: mobile ? "50vh" : undefined,
        overflowY: mobile ? "auto" : undefined,
        padding: "12px 14px",
        borderRadius: 14,
        background: "rgba(12,12,16,0.72)",
        border: "1px solid var(--border)",
        backdropFilter: "blur(14px)",
        WebkitBackdropFilter: "blur(14px)",
        color: "var(--text)",
        fontFamily: "var(--font-figtree), system-ui, sans-serif",
        fontSize: 13,
        lineHeight: 1.35,
        pointerEvents: mobile ? "auto" : "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, fontSize: 14 }}>
        <Swatch color="var(--live)" glow />
        <span style={{ flex: 1 }}>Live · {areaName(origin)}</span>
        {crowd !== 1 && <CrowdChip crowd={crowd} />}
        {mobile && <span style={{ color: "var(--muted)", fontSize: 11, fontWeight: 500 }}>hide</span>}
      </div>
      <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 2 }}>
        {updatedAt ? `Updated ${new Date(updatedAt).toLocaleTimeString("en-GB")}` : "Connecting…"}
        {screenMode ? ` · screens: ${screenMode}` : ""}
      </div>
      <div style={{ display: "grid", gap: 5, marginTop: 10 }}>
        {rows.map((r) => (
          <div key={r.label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Swatch color={r.color} />
            <span style={{ color: "var(--muted)", flex: 1 }}>{r.label}</span>
            <span style={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{r.value}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)", fontSize: 12 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Swatch color="#e8d6c2" />
          <span style={{ color: "var(--muted)" }}>City estimate</span>
        </div>
        <div style={{ fontWeight: 600, fontVariantNumeric: "tabular-nums", paddingLeft: 17 }}>{estimate}</div>
        {basis && <div style={{ color: "var(--muted)", fontSize: 11, paddingLeft: 17 }}>{basis}</div>}
      </div>
      {scenario && (
        <div
          style={{
            marginTop: 10,
            paddingTop: 8,
            borderTop: "1px solid var(--border)",
            fontSize: 12,
            color: scenario.source === "demo" ? "var(--warn)" : "var(--text)",
          }}
        >
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Swatch color={scenario.source === "demo" ? "var(--warn)" : "#4f7cff"} glow />
            <span style={{ fontWeight: 600 }}>{scenario.source === "demo" ? "Demo scenario" : "Responding to real incident"}</span>
          </div>
          <div style={{ paddingLeft: 17 }}>{scenario.label}</div>
        </div>
      )}
      <StreamerStatus />
      <div
        style={{
          marginTop: 10,
          paddingTop: 8,
          borderTop: "1px solid var(--border)",
          color: "var(--muted)",
          fontSize: 11,
          display: "grid",
          gap: 3,
        }}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Swatch color="var(--ember)" glow /> Streamer beacon
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Swatch color="var(--ember)" /> Camera view cone · YOLO tracks move inside it
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Swatch color="var(--live)" /> Closed road · <Swatch color="var(--warn)" /> works
        </div>
        {settings.boats && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Swatch color="#9fd3ff" /> Boats · illustrative river traffic, not live data
          </div>
        )}
      </div>
    </div>
  );
}
