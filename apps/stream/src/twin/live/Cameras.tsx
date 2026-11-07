"use client";

import { Billboard } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { Label } from "./Label";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { AroundCam } from "@/platform/around/types";
import { camFrame, usePoseVersion, viewConePoints } from "./project";
import { useCompare } from "../compare/store";
import { useLive, useNow } from "./store";

const POLE_H = 7;
const SCREEN_W = 12;
const SCREEN_H = 9;
const FRAME_REFRESH_MS = 15_000;
const CROSSFADE_S = 1.2;

type ScreenMode = "loading" | "video" | "frame";

/**
 * Live clip as a VideoTexture when TfL's S3 allows CORS; otherwise the same-origin annotated frame.
 * The video reloads when the YOLO reading's `at` changes (TfL published a new clip); the previous
 * texture is kept so the screen can crossfade instead of hard-cutting.
 */
function useScreenTexture(cam: AroundCam, clipAt: number) {
  const [texture, setTexture] = useState<THREE.Texture | null>(null);
  const [previous, setPrevious] = useState<THREE.Texture | null>(null);
  const [mode, setMode] = useState<ScreenMode>("loading");

  useEffect(() => {
    let disposed = false;
    let frameTimer: ReturnType<typeof setInterval> | null = null;
    const loader = new THREE.TextureLoader();

    const swap = (t: THREE.Texture) => {
      if (disposed) {
        t.dispose();
        return;
      }
      t.colorSpace = THREE.SRGBColorSpace;
      setTexture((old) => {
        setPrevious(old);
        return t;
      });
    };

    const loadFrame = () => {
      loader.load(`/api/around/frame?id=${encodeURIComponent(cam.id)}&t=${Date.now()}`, swap, undefined, () => {});
    };

    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.preload = "auto";
    const onError = () => {
      if (disposed) return;
      setMode("frame");
      useLive.setState((s) => (s.screenMode === "video" ? {} : { screenMode: "frame" }));
      loadFrame();
      frameTimer = setInterval(loadFrame, FRAME_REFRESH_MS);
    };
    const onReady = () => {
      if (disposed) return;
      const t = new THREE.VideoTexture(video);
      t.minFilter = THREE.LinearFilter;
      t.generateMipmaps = false;
      swap(t);
      setMode("video");
      useLive.setState({ screenMode: "video" });
      void video.play().catch(() => {});
    };
    video.addEventListener("error", onError);
    video.addEventListener("loadeddata", onReady);
    video.src = clipAt ? `${cam.videoUrl}${cam.videoUrl.includes("?") ? "&" : "?"}v=${clipAt}` : cam.videoUrl;

    return () => {
      disposed = true;
      video.removeEventListener("error", onError);
      video.removeEventListener("loadeddata", onReady);
      video.pause();
      video.removeAttribute("src");
      video.load();
      if (frameTimer) clearInterval(frameTimer);
    };
  }, [cam.id, cam.videoUrl, clipAt]);

  useEffect(() => () => previous?.dispose(), [previous]);

  return { texture, previous, mode, clearPrevious: () => setPrevious(null) };
}

/** The new clip fades in over the old one; the old texture is released once it is fully covered. */
function Screen({ texture, previous, clearPrevious, onClick }: { texture: THREE.Texture | null; previous: THREE.Texture | null; clearPrevious: () => void; onClick: (e: { stopPropagation(): void }) => void }) {
  const front = useRef<THREE.MeshBasicMaterial>(null);
  const fadeFrom = useRef(-1);
  useEffect(() => {
    fadeFrom.current = previous ? performance.now() / 1000 : -1;
  }, [texture, previous]);
  useFrame(() => {
    const m = front.current;
    if (!m) return;
    if (fadeFrom.current < 0) {
      m.opacity = 1;
      return;
    }
    const k = Math.min(1, (performance.now() / 1000 - fadeFrom.current) / CROSSFADE_S);
    m.opacity = k;
    if (k >= 1) {
      fadeFrom.current = -1;
      clearPrevious();
    }
  });
  return (
    <>
      {previous && (
        <mesh position={[0, 0, -0.02]}>
          <planeGeometry args={[SCREEN_W, SCREEN_H]} />
          <meshBasicMaterial map={previous} toneMapped={false} />
        </mesh>
      )}
      <mesh onClick={onClick}>
        <planeGeometry args={[SCREEN_W, SCREEN_H]} />
        {texture ? (
          <meshBasicMaterial key="live" ref={front} map={texture} toneMapped={false} transparent />
        ) : (
          <meshBasicMaterial key="blank" color="#131318" />
        )}
      </mesh>
    </>
  );
}

function clipAge(now: number, at: number) {
  const s = Math.max(0, Math.round((now - at) / 1000));
  return s < 90 ? `${s} s ago` : `${Math.round(s / 60)} min ago`;
}

function Camera({ cam }: { cam: AroundCam }) {
  const clip = useLive((s) => s.clips[cam.id]);
  const now = useNow();
  const { texture, previous, mode, clearPrevious } = useScreenTexture(cam, clip?.at ?? 0);
  const poseVersion = usePoseVersion((s) => s.v);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const frame = useMemo(() => camFrame(cam), [cam, poseVersion]);
  const comparing = useCompare((s) => s.camId === cam.id);
  const open = useCompare((s) => s.open);

  const cone = useMemo(() => {
    const pts = viewConePoints(frame);
    const g = new THREE.BufferGeometry();
    const n = pts.length - 1;
    const pos = new Float32Array(n * 3 * 3);
    for (let i = 1; i < n; i++) {
      const o = (i - 1) * 9;
      pos[o] = pts[0][0];
      pos[o + 1] = 0;
      pos[o + 2] = pts[0][1];
      pos[o + 3] = pts[i][0];
      pos[o + 4] = 0;
      pos[o + 5] = pts[i][1];
      pos[o + 6] = pts[i + 1][0];
      pos[o + 7] = 0;
      pos[o + 8] = pts[i + 1][1];
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return g;
  }, [frame]);
  useEffect(() => () => cone.dispose(), [cone]);

  const counts = useMemo(() => {
    let people = 0;
    let vehicles = 0;
    for (const t of clip?.tracks ?? []) {
      if (t.kind === "person") people++;
      else vehicles++;
    }
    return { people, vehicles };
  }, [clip]);

  return (
    <group visible={!comparing}>
      <mesh geometry={cone} position={[0, 0.08, 0]} renderOrder={1}>
        <meshBasicMaterial
          color="#ff4d2e"
          transparent
          opacity={0.14}
          depthWrite={false}
          side={THREE.DoubleSide}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <group position={[frame.x, 0, frame.z]}>
        <mesh position={[0, POLE_H / 2, 0]}>
          <cylinderGeometry args={[0.18, 0.22, POLE_H, 8]} />
          <meshStandardMaterial color="#2c2c36" roughness={0.6} metalness={0.5} />
        </mesh>
        <mesh position={[0, POLE_H, 0]}>
          <sphereGeometry args={[0.45, 12, 10]} />
          <meshStandardMaterial color="#ff2d55" emissive="#ff2d55" emissiveIntensity={2} />
        </mesh>
        <Billboard position={[0, POLE_H + SCREEN_H / 2 + 1.5, 0]}>
          <mesh position={[0, 0, -0.1]}>
            <planeGeometry args={[SCREEN_W + 0.6, SCREEN_H + 0.6]} />
            <meshBasicMaterial color="#0c0c10" />
          </mesh>
          <Screen texture={texture} previous={previous} clearPrevious={clearPrevious} onClick={(e) => (e.stopPropagation(), open(cam.id))} />
        </Billboard>
        <Label position={[0, POLE_H + SCREEN_H + 3.2, 0]}>
          <div
            role="button"
            onClick={() => open(cam.id)}
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              fontFamily: "var(--font-figtree), system-ui, sans-serif",
              background: "rgba(12,12,16,0.78)",
              border: `1px solid ${comparing ? "var(--ember)" : "var(--border-strong)"}`,
              borderRadius: 10,
              padding: "6px 10px",
              color: "var(--text)",
              whiteSpace: "nowrap",
              fontSize: 12,
              lineHeight: 1.3,
              backdropFilter: "blur(8px)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 600 }}>
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 999,
                  background: mode === "loading" ? "var(--muted)" : "var(--live)",
                  boxShadow: mode === "loading" ? "none" : "0 0 8px var(--live)",
                }}
              />
              {cam.name}
            </div>
            <div style={{ color: "var(--muted)", fontSize: 11 }}>
              {clip ? (
                <>
                  <span style={{ color: "#ffb37a" }}>{counts.people} people</span>
                  {" · "}
                  <span style={{ color: "#9fd3ff" }}>{counts.vehicles} vehicles</span>
                </>
              ) : (
                "analysing clip…"
              )}
              {mode === "frame" ? " · frame" : mode === "video" ? " · video" : ""}
            </div>
            {clip && now > 0 && (
              <div style={{ color: "var(--dim)", fontSize: 11 }}>Updated {clipAge(now, clip.at)} · TfL refreshes every few min</div>
            )}
            <div style={{ color: comparing ? "var(--ember)" : "var(--dim)", fontSize: 11, marginTop: 2 }}>
              {comparing ? "Comparing" : "Click to compare with real CCTV"}
            </div>
          </div>
        </Label>
      </group>
    </group>
  );
}

export function Cameras() {
  const cams = useLive((s) => s.cams);
  return (
    <group>
      {cams.map((cam) => (
        <Camera key={cam.id} cam={cam} />
      ))}
    </group>
  );
}
