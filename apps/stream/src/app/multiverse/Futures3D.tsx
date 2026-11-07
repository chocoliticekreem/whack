"use client";

import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { projection, toLocal } from "@/twin/config";
import type { Geometry, MultiverseState } from "./types";

/** The engine's local metres are centred on Oxford Circus itself (services/multiverse/fixture.py ORIGIN). */
export const OXFORD_CIRCUS = { lat: 51.5152, lon: -0.1419 };
const ENGINE = projection(OXFORD_CIRCUS);

const CALM = new THREE.Color("#3df5ff");
const UNSURE = new THREE.Color("#ff3da8");
const MAX_IV = 0.25;
const SHARDS = 10;
const SHATTER_S = 1.6;
const PULSE_S = 2.4;

function place(x: number, z: number): [number, number] {
  const ll = ENGINE.toLatLon(x, z);
  return toLocal(ll.lat, ll.lon);
}

export interface Reveal {
  key: number;
  camId: string | null;
  label: string | null;
  shattered: string[];
}

export function Futures3D({ state, cams, reveal }: { state: MultiverseState; cams: Geometry["cams"]; reveal: Reveal | null }) {
  const zones = useMemo(() => {
    const at = new Map(state.zones.map((z) => [z.id, place(z.x, z.z)]));
    return state.consensusHorizon
      .filter((s) => s.mean > 1 && at.has(s.zoneId))
      .map((s) => ({ ...s, pos: at.get(s.zoneId)! }));
  }, [state.zones, state.consensusHorizon]);
  const camPos = useMemo(() => cams.map((c) => ({ ...c, pos: place(c.x, c.z) })), [cams]);

  return (
    <group>
      {zones.map((z) => (
        <Column
          key={z.zoneId}
          pos={z.pos}
          mean={z.mean}
          iv={z.iv}
          shatterKey={reveal && reveal.shattered.includes(z.zoneId) ? reveal.key : 0}
          glowKey={reveal?.key ?? 0}
        />
      ))}
      {camPos.map((c) => (
        <CamMarker key={c.id} pos={c.pos} pulseKey={reveal?.camId === c.id ? reveal.key : 0} label={reveal?.camId === c.id ? reveal.label : null} />
      ))}
    </group>
  );
}

function Column({ pos, mean, iv, shatterKey, glowKey }: { pos: [number, number]; mean: number; iv: number; shatterKey: number; glowKey: number }) {
  const height = 20 + mean * 0.7;
  const radius = 14 + Math.sqrt(mean) * 2.2;
  const color = useMemo(() => CALM.clone().lerp(UNSURE, Math.min(1, iv / MAX_IV)), [iv]);
  const body = useRef<THREE.MeshBasicMaterial>(null);
  const ring = useRef<THREE.MeshBasicMaterial>(null);
  const shards = useRef<THREE.Group>(null);
  const shatterAt = useRef(-1);
  const glowAt = useRef(-1);
  const shown = useRef(height);
  const group = useRef<THREE.Group>(null);
  const dirs = useMemo(
    () => Array.from({ length: SHARDS }, (_, i) => {
      const a = (i / SHARDS) * Math.PI * 2;
      return new THREE.Vector3(Math.cos(a), 0.6 + (i % 3) * 0.35, Math.sin(a));
    }),
    [],
  );

  useEffect(() => {
    if (shatterKey) shatterAt.current = performance.now();
  }, [shatterKey]);
  useEffect(() => {
    if (glowKey) glowAt.current = performance.now();
  }, [glowKey]);

  useFrame((_, dt) => {
    shown.current += (height - shown.current) * Math.min(1, dt * 2.5);
    if (group.current) group.current.scale.y = shown.current / height;
    const now = performance.now();
    const glow = glowAt.current > 0 ? Math.max(0, 1 - (now - glowAt.current) / 1500) : 0;
    const t = shatterAt.current > 0 ? (now - shatterAt.current) / 1000 / SHATTER_S : 2;
    const breaking = t < 1;
    if (body.current) body.current.opacity = breaking ? 0.1 + 0.4 * t : 0.5 + glow * 0.35;
    if (ring.current) ring.current.opacity = 0.75 + glow * 0.25;
    if (shards.current) {
      shards.current.visible = breaking;
      shards.current.children.forEach((m, i) => {
        const d = dirs[i];
        const r = radius + t * 70;
        m.position.set(d.x * r, height * 0.5 + d.y * t * 60 - t * t * 40, d.z * r);
        m.rotation.set(t * 6 + i, t * 4, 0);
        ((m as THREE.Mesh).material as THREE.MeshBasicMaterial).opacity = 1 - t;
      });
    }
  });

  return (
    <group position={[pos[0], 0, pos[1]]}>
      <group ref={group}>
        <mesh position={[0, height / 2, 0]}>
          <cylinderGeometry args={[radius * 0.8, radius, height, 40, 1, true]} />
          <meshBasicMaterial ref={body} color={color} transparent opacity={0.32} depthWrite={false} side={THREE.DoubleSide} toneMapped={false} />
        </mesh>
      </group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 1.5, 0]}>
        <ringGeometry args={[radius, radius + 4, 64]} />
        <meshBasicMaterial ref={ring} color={color} transparent opacity={0.8} depthWrite={false} toneMapped={false} />
      </mesh>
      <group ref={shards} visible={false}>
        {dirs.map((_, i) => (
          <mesh key={i}>
            <boxGeometry args={[13, 13, 2]} />
            <meshBasicMaterial color={UNSURE} transparent opacity={1} depthWrite={false} toneMapped={false} />
          </mesh>
        ))}
      </group>
    </group>
  );
}

function CamMarker({ pos, pulseKey, label }: { pos: [number, number]; pulseKey: number; label: string | null }) {
  const wave = useRef<THREE.Mesh>(null);
  const at = useRef(-1);
  useEffect(() => {
    if (pulseKey) at.current = performance.now();
  }, [pulseKey]);
  useFrame(() => {
    const m = wave.current;
    if (!m) return;
    const t = at.current > 0 ? ((performance.now() - at.current) / 1000 / PULSE_S) % 1 : 1;
    const live = at.current > 0 && performance.now() - at.current < PULSE_S * 3000;
    m.visible = live;
    m.scale.setScalar(1 + t * 9);
    (m.material as THREE.MeshBasicMaterial).opacity = 1 - t;
  });
  return (
    <group position={[pos[0], 0, pos[1]]}>
      <mesh position={[0, 14, 0]}>
        <sphereGeometry args={[4, 16, 12]} />
        <meshBasicMaterial color="#ffffff" toneMapped={false} />
      </mesh>
      <mesh position={[0, 7, 0]}>
        <cylinderGeometry args={[0.8, 0.8, 14, 6]} />
        <meshBasicMaterial color="#ffffff" transparent opacity={0.6} />
      </mesh>
      <mesh ref={wave} rotation={[-Math.PI / 2, 0, 0]} position={[0, 2, 0]} visible={false}>
        <ringGeometry args={[5, 7, 48]} />
        <meshBasicMaterial color="#ffffff" transparent opacity={1} depthWrite={false} toneMapped={false} />
      </mesh>
      {label && (
        <Html position={[0, 30, 0]} center zIndexRange={[20, 0]}>
          <div className="mvg-tag">{label}</div>
        </Html>
      )}
    </group>
  );
}
