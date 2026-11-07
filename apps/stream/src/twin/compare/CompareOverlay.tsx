"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { isVirtualCam, liveOffsetS } from "@/lib/vcams";
import type { AroundCam } from "@/platform/around/types";
import { calibrationFor, camFrame, usePoseVersion } from "../live/project";
import { LiveClock } from "@/components/LiveClock";
import { AlignBar } from "./AlignBar";
import { useAutofit } from "./autofit";
import { useLive } from "../live/store";
import { syncClock, useCompare } from "./store";

const FRAME_REFRESH_MS = 15_000;

const panel: CSSProperties = {
  position: "absolute",
  left: 0,
  top: 0,
  width: "50%",
  height: "100%",
  background: "var(--surface)",
  borderRight: "1px solid var(--border-strong)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  overflow: "hidden",
};

const caption: CSSProperties = {
  position: "absolute",
  bottom: 20,
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 12px",
  borderRadius: 999,
  background: "rgba(12,12,16,0.78)",
  border: "1px solid var(--border-strong)",
  backdropFilter: "blur(10px)",
  fontSize: 13,
  fontWeight: 600,
  whiteSpace: "nowrap",
};

function Dot({ color }: { color: string }) {
  return <span style={{ width: 8, height: 8, borderRadius: 999, background: color, boxShadow: `0 0 8px ${color}` }} />;
}

/** Size of a frame of the given aspect fitted inside a box (object-fit: contain), as CSS percentages. */
function containBox(boxW: number, boxH: number, aspect: number) {
  if (boxW <= 0 || boxH <= 0) return { width: "100%", height: "100%" };
  return boxW / boxH > aspect
    ? { width: `${((boxH * aspect) / boxW) * 100}%`, height: "100%" }
    : { width: "100%", height: `${(boxW / aspect / boxH) * 100}%` };
}

export function CompareOverlay() {
  const camId = useCompare((s) => s.camId);
  const cam = useLive((s) => s.cams.find((c) => c.id === camId));
  if (!cam) return null;
  return <ComparePanel key={cam.id} cam={cam} />;
}

/** Keyed by camera id so the video/fallback state resets when the camera changes. */
function ComparePanel({ cam }: { cam: AroundCam }) {
  const close = useCompare((s) => s.close);
  const clip = useLive((s) => s.clips[cam.id]);
  const [failed, setFailed] = useState(false);
  const [ended, setEnded] = useState(false);
  const [frameAt, setFrameAt] = useState(0);
  const [clipT, setClipT] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [overlay, setOverlay] = useState(false);
  const overlayVideo = useRef<HTMLVideoElement>(null);
  const fit = useAutofit((s) => s.byCam[cam.id]);
  usePoseVersion((s) => s.v); // re-render the caption while a pose is being aligned
  const frame = camFrame(cam);
  const cal = calibrationFor(cam.id);
  const virtual = isVirtualCam(cam.id);
  // Play the exact clip YOLO tracked (TfL overwrites its URL every few minutes); a new analysis swaps both together.
  // A virtual cam's video is the tracked footage itself, joined at the wall-clock "live" moment.
  const src = clip && !virtual ? `/api/around/clip?id=${encodeURIComponent(cam.id)}&at=${clip.at}` : cam.videoUrl;

  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setBox({ w: e.contentRect.width / 2, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = video.current;
    if (!el) return;
    syncClock.video = el;
    const tick = setInterval(() => {
      setClipT(el.currentTime);
      // Keep the overlay copy on the same frame as the real clip.
      const o = overlayVideo.current;
      if (o && Math.abs(o.currentTime - el.currentTime) > 0.08) o.currentTime = el.currentTime;
    }, 250);
    return () => {
      clearInterval(tick);
      if (syncClock.video === el) syncClock.video = null;
    };
    // `src` too: a new analysis remounts the <video>, and the twin must follow the new element's clock.
  }, [failed, src]);

  useEffect(() => {
    if (!failed) return;
    syncClock.video = null;
    const t = setInterval(() => setFrameAt(Date.now()), FRAME_REFRESH_MS);
    return () => clearInterval(t);
  }, [failed]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  let people = 0;
  let vehicles = 0;
  for (const t of clip?.tracks ?? []) {
    if (t.kind === "person") people++;
    else vehicles++;
  }

  return (
    <div
      ref={root}
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 25,
        color: "var(--text)",
        fontFamily: "var(--font-figtree), system-ui, sans-serif",
        pointerEvents: "none",
      }}
    >
      <div style={{ ...panel, pointerEvents: "auto" }}>
        {failed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            alt={`${cam.name} latest frame`}
            src={`/api/around/frame?id=${encodeURIComponent(cam.id)}&t=${frameAt}`}
            style={{ width: "100%", height: "100%", objectFit: "contain" }}
          />
        ) : (
          <video
            key={src}
            ref={video}
            src={src}
            autoPlay
            muted
            loop={virtual}
            playsInline
            onPlay={() => {
              syncClock.endedAt = null;
              setEnded(false);
            }}
            onEnded={() => {
              syncClock.endedAt = performance.now();
              setEnded(true);
            }}
            onLoadedMetadata={(e) => {
              if (virtual) e.currentTarget.currentTime = liveOffsetS(e.currentTarget.duration);
            }}
            onError={() => {
              setFailed(true);
              setFrameAt(Date.now());
            }}
            style={{ width: "100%", height: "100%", objectFit: "contain" }}
          />
        )}
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
          <div style={{ ...containBox(box.w, box.h, frame.aspect), position: "relative" }}>
            <LiveClock />
          </div>
        </div>
        <div style={{ ...caption, left: 72 }}>
          <Dot color="var(--live)" />
          Real CCTV
          <span style={{ color: "var(--muted)", fontWeight: 500 }}>
            · {cam.name}
            {failed
              ? " · latest frame (clip blocked)"
              : ended
                ? " · newest TfL clip played · the next one plays when TfL publishes it (every few min)"
                : ` · ${clipT.toFixed(1)} s${clip ? " · newest clip, as YOLO tracked it" : ""}`}
          </span>
        </div>
      </div>

      {/* The real frame's edges, drawn on the twin half: everything inside lines up with the left half. */}
      <div style={{ position: "absolute", left: "50%", top: 0, width: "50%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ ...containBox(box.w, box.h, frame.aspect), border: "1px dashed rgba(255,255,255,0.45)", boxSizing: "border-box", position: "relative" }}>
          {overlay && !failed && (
            <video
              key={src}
              ref={overlayVideo}
              src={src}
              autoPlay
              muted
              loop={virtual}
              playsInline
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "fill", opacity: 0.5, pointerEvents: "none" }}
            />
          )}
        </div>
      </div>

      <div style={{ position: "absolute", left: "50%", top: 70, width: "50%", display: "flex", justifyContent: "center", padding: "0 12px", boxSizing: "border-box" }}>
        <AlignBar key={fit ? "fit" : "model"} cam={cam} overlay={overlay} setOverlay={setOverlay} />
      </div>

      <div style={{ ...caption, left: "calc(50% + 20px)" }}>
        <Dot color="var(--ember)" />
        Simulated from real tracks
        <span style={{ color: "var(--muted)", fontWeight: 500 }}>
          · <span style={{ color: "#ffb37a" }}>{people} people</span>, <span style={{ color: "#9fd3ff" }}>{vehicles} vehicles</span>
          {clip ? ` · ${clip.duration.toFixed(1)} s clip` : " · analysing…"}
          {failed ? " · wall clock" : " · synced to the clip"}
          {fit && fit.afterM < fit.beforeM ? ` · auto-aligned to the roads (${fit.beforeM.toFixed(1)} → ${fit.afterM.toFixed(1)} m)` : ""}
        </span>
      </div>

      <div
        style={{
          position: "absolute",
          top: 20,
          left: "50%",
          transform: "translateX(-50%)",
          display: "flex",
          alignItems: "center",
          gap: 10,
          pointerEvents: "auto",
        }}
      >
        <div
          style={{
            padding: "8px 14px",
            borderRadius: 999,
            background: "rgba(12,12,16,0.78)",
            border: "1px solid var(--border-strong)",
            backdropFilter: "blur(10px)",
            fontSize: 13,
          }}
        >
          <span style={{ fontWeight: 600 }}>Compare CCTV</span>
          <span style={{ color: "var(--muted)" }}>
            {" · "}
            {cal
              ? `pose from ${cal.source?.startsWith("auto-fit") ? "Nemotron 3.5 Super VL, fitted to the real cars' lanes" : cal.source?.startsWith("aligned by hand") ? "Nemotron 3.5 Super VL + hand alignment" : "Nemotron 3.5 Super VL"}: facing ${Math.round(cal.headingDeg)}°, tilted ${cal.pitchDeg.toFixed(1)}°, ${cal.heightM.toFixed(1)} m up, ${Math.round(cal.hfovDeg)}° lens`
              : `uncalibrated: default ${Math.round((Math.atan(frame.tanH) * 360) / Math.PI)}° lens, ${frame.heightM} m up`}
          </span>
        </div>
        <button
          type="button"
          onClick={close}
          style={{
            padding: "8px 14px",
            borderRadius: 999,
            background: "var(--text)",
            color: "var(--bg)",
            border: "none",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          Exit compare
        </button>
      </div>
    </div>
  );
}
