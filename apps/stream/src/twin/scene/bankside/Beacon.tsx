"use client";

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Label } from "../../live/Label";
import { ATMOS } from "../palette";

const BEAM_H = 120;
const BEAM_R = 3;

const BEAM_VERT = /* glsl */ `
uniform float uH;
varying float vY;
varying vec3 vN;
varying vec3 vV;
void main() {
  vY = position.y / uH + 0.5;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vN = normalMatrix * normal;
  vV = -mv.xyz;
  gl_Position = projectionMatrix * mv;
}`;

const BEAM_FRAG = /* glsl */ `
uniform vec3 uColor;
uniform float uAlpha;
uniform float uTime;
varying float vY;
varying vec3 vN;
varying vec3 vV;
void main() {
  float rim = pow(abs(dot(normalize(vN), normalize(vV))), 1.4);
  float fade = pow(1.0 - vY, 1.6) * smoothstep(0.0, 0.02, vY);
  float pulse = 0.88 + 0.12 * sin(uTime * 1.6 + vY * 9.0);
  gl_FragColor = vec4(uColor * 1.8, rim * fade * pulse * uAlpha);
}`;

const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const _p = new THREE.Vector3();
const _n = new THREE.Vector3();
/** Left screen margin that keeps clamped labels clear of the HUD panel. */
const LEFT_PANEL_PX = 360;
const LABEL_W = 270;
const LABEL_H = 62;
/** Final screen position (px from centre, y up) of each site label this frame. */
const placed: ({ x: number; y: number } | null)[] = [];

function distanceText(dx: number, dz: number) {
  const m = Math.hypot(dx, dz);
  const bearing = Math.atan2(dx, -dz);
  const dir = COMPASS[(Math.round(bearing / (Math.PI / 4)) + 8) % 8];
  return `${Math.round(m)} m ${dir}`;
}

/** A short additive light shaft marking the site. */
export function Beam({ x, z, color, height = BEAM_H }: { x: number; z: number; color: string; height?: number }) {
  const mesh = useRef<THREE.Mesh>(null);
  const { geometry, material } = useMemo(() => {
    const geometry = new THREE.CylinderGeometry(BEAM_R * 0.6, BEAM_R, height, 24, 1, true);
    const material = new THREE.ShaderMaterial({
      uniforms: {
        uH: { value: height },
        uColor: { value: new THREE.Color(color) },
        uAlpha: { value: 0.6 },
        uTime: { value: 0 },
      },
      vertexShader: BEAM_VERT,
      fragmentShader: BEAM_FRAG,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
      toneMapped: false,
    });
    return { geometry, material };
  }, [color, height]);

  useFrame(({ clock }) => {
    const live = mesh.current?.material as THREE.ShaderMaterial | undefined;
    if (!live) return;
    live.uniforms.uTime.value = clock.elapsedTime;
    live.uniforms.uAlpha.value = 0.3 + 0.5 * ATMOS.windowGlow;
  });

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  return <mesh ref={mesh} geometry={geometry} material={material} position={[x, height / 2, z]} frustumCulled={false} />;
}

/** A pulsing flat ring on the ground that marks the venue from a distance; one transparent mesh, no lights. */
export function GroundRing({ x, z, color, radius }: { x: number; z: number; color: string; radius: number }) {
  const mesh = useRef<THREE.Mesh>(null);
  const { geometry, material } = useMemo(
    () => ({
      geometry: new THREE.RingGeometry(radius * 0.9, radius, 64).rotateX(-Math.PI / 2),
      material: new THREE.MeshBasicMaterial({
        color: new THREE.Color(color).multiplyScalar(1.6),
        transparent: true,
        opacity: 0.7,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        toneMapped: false,
        polygonOffset: true,
        polygonOffsetFactor: -4,
        polygonOffsetUnits: -4,
      }),
    }),
    [color, radius],
  );

  useFrame(({ clock }) => {
    const m = mesh.current;
    if (!m) return;
    const t = clock.elapsedTime;
    m.scale.setScalar(1 + 0.04 * Math.sin(t * 1.6));
    (m.material as THREE.MeshBasicMaterial).opacity = 0.45 + 0.3 * Math.sin(t * 1.6);
  });

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  return <mesh ref={mesh} geometry={geometry} material={material} position={[x, 0.6, z]} renderOrder={5} />;
}

/**
 * Floating name pill above a site. Off screen (or behind the camera) it slides to the nearest screen edge
 * with an arrow, so the site can always be found; the readout is camera-relative distance and compass.
 */
export function SiteLabel({
  x,
  y,
  z,
  slot,
  name,
  color,
}: {
  x: number;
  y: number;
  z: number;
  /** Index among the site labels; later slots step aside when they would cover an earlier one. */
  slot: number;
  name: string;
  color: string;
}) {
  const { camera, size } = useThree();
  const follow = useRef<THREE.Group>(null);
  const readout = useRef<HTMLSpanElement>(null);
  const arrow = useRef<HTMLSpanElement>(null);
  const last = useRef("");
  const target = useMemo(() => new THREE.Vector3(x, y, z), [x, y, z]);

  const node = useMemo(
    () => (
      <div
        style={{
          fontFamily: "var(--font-figtree), system-ui, sans-serif",
          background: "rgba(12,12,16,0.82)",
          border: `1px solid ${color}`,
          boxShadow: `0 0 18px ${color}55`,
          borderRadius: 12,
          padding: "7px 12px",
          color: "#fff",
          whiteSpace: "nowrap",
          fontSize: 13,
          lineHeight: 1.3,
          backdropFilter: "blur(8px)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span ref={arrow} style={{ color, fontSize: 14, opacity: 0, display: "inline-block", width: 14, textAlign: "center" }}>
          ▲
        </span>
        <div>
          <div style={{ fontWeight: 650 }}>{name}</div>
          <div style={{ color, fontSize: 12, fontVariantNumeric: "tabular-nums" }}>
            <span ref={readout} />
          </div>
        </div>
      </div>
    ),
    [name, color],
  );

  useFrame(() => {
    const anchor = follow.current;
    if (!anchor) return;
    camera.updateMatrixWorld();
    _p.copy(target).project(camera);
    const behind = _p.z > 1;
    if (behind) {
      _p.x = -_p.x * 1000;
      _p.y = -_p.y * 1000;
    }
    const halfW = size.width / 2;
    const halfH = size.height / 2;
    const mx = Math.max(0.3, 1 - (_p.x < 0 ? LEFT_PANEL_PX : 140) / halfW);
    const top = Math.max(0.3, 1 - 80 / halfH);
    const bottom = Math.max(0.3, 1 - 100 / halfH);
    const k = Math.max(Math.abs(_p.x) / mx, _p.y > 0 ? _p.y / top : -_p.y / bottom);
    const clamped = behind || k > 1;
    const s = Math.max(k, 1e-6);
    const nx = clamped ? _p.x / s : _p.x;
    let ny = clamped ? _p.y / s : _p.y;
    let py = ny * halfH;
    for (let j = 0; j < slot; j++) {
      const o = placed[j];
      if (o && Math.abs(nx * halfW - o.x) < LABEL_W && Math.abs(py - o.y) < LABEL_H) {
        py = o.y + LABEL_H;
        if (py / halfH > top) py = o.y - LABEL_H;
      }
    }
    placed[slot] = { x: nx * halfW, y: py };
    const shifted = py !== ny * halfH;
    ny = py / halfH;
    if (clamped || shifted) {
      _n.set(nx, ny, 0.5).unproject(camera);
      anchor.position.copy(_n);
    } else {
      anchor.position.copy(target);
    }
    if (arrow.current) {
      arrow.current.style.opacity = clamped ? "1" : "0";
      arrow.current.style.transform = `rotate(${Math.atan2(_p.x * halfW, _p.y * halfH).toFixed(3)}rad)`;
    }
    const text = distanceText(target.x - camera.position.x, target.z - camera.position.z);
    if (readout.current && text !== last.current) {
      last.current = text;
      readout.current.textContent = text;
    }
  }, -1);

  useEffect(
    () => () => {
      placed[slot] = null;
    },
    [slot],
  );

  return (
    <group ref={follow}>
      <Label position={[0, 0, 0]} priority={20}>
        {node}
      </Label>
    </group>
  );
}
