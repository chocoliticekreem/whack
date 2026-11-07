"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { toLocal } from "../config";
import { useCameraStore } from "../scene/cameraStore";
import { useLive } from "./store";

const COLUMN_H = 60;

export function Streamer() {
  const streamer = useLive((s) => s.streamer);
  const heading = useLive((s) => s.streamerHeading);
  const firstPerson = useCameraStore((s) => s.preset === "streamer" && !s.cctv);
  const [x, z] = useMemo(() => toLocal(streamer.lat, streamer.lon), [streamer]);
  const ring = useRef<THREE.Mesh>(null);
  const column = useRef<THREE.Mesh>(null);
  const light = useRef<THREE.PointLight>(null);

  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const pulse = 0.5 + 0.5 * Math.sin(t * 2.2);
    if (ring.current) {
      const s = 1 + ((t * 0.6) % 1) * 2.2;
      ring.current.scale.set(s, s, s);
      (ring.current.material as THREE.MeshBasicMaterial).opacity = 0.7 * (1 - ((t * 0.6) % 1));
    }
    if (column.current) {
      (column.current.material as THREE.MeshBasicMaterial).opacity = 0.18 + pulse * 0.14;
    }
    if (light.current) light.current.intensity = 180 + pulse * 140;
  });

  return (
    <group position={[x, 0, z]} visible={!firstPerson}>
      <mesh ref={column} position={[0, COLUMN_H / 2, 0]}>
        <cylinderGeometry args={[1.4, 2.2, COLUMN_H, 16, 1, true]} />
        <meshBasicMaterial
          color="#ff4d2e"
          transparent
          opacity={0.25}
          depthWrite={false}
          side={THREE.DoubleSide}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <mesh position={[0, 1.2, 0]}>
        <sphereGeometry args={[1, 16, 12]} />
        <meshStandardMaterial color="#ff4d2e" emissive="#ff4d2e" emissiveIntensity={3} />
      </mesh>
      <mesh ref={ring} position={[0, 0.1, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[3.2, 4, 48]} />
        <meshBasicMaterial color="#ff4d2e" transparent opacity={0.6} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      <mesh position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[4.6, 5, 48]} />
        <meshBasicMaterial color="#ff4d2e" transparent opacity={0.35} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      {heading !== null && (
        // GPS heading: 0 = north (-z), clockwise. Rotating about y by -heading points the cone that way.
        <group rotation={[0, (-heading * Math.PI) / 180, 0]}>
          <mesh position={[0, 0.3, -4.5]} rotation={[-Math.PI / 2, 0, 0]}>
            <coneGeometry args={[1.6, 4, 3]} />
            <meshBasicMaterial color="#ffb37a" transparent opacity={0.85} depthWrite={false} />
          </mesh>
        </group>
      )}
      <pointLight ref={light} position={[0, 6, 0]} color="#ff6a3d" intensity={200} distance={70} decay={2} />
    </group>
  );
}
