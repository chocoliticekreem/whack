"use client";

import { useMemo } from "react";
import * as THREE from "three";
import { toLocal } from "../config";
import { useLive } from "./store";

const STRIP_W = 1.4;
const STRIP_H = 0.9;
const GLOW_W = 7;

interface Segment {
  x: number;
  z: number;
  len: number;
  yaw: number;
  closed: boolean;
}

export function Disruptions() {
  const items = useLive((s) => s.disruptions);

  const segments = useMemo<Segment[]>(() => {
    const seen = new Set<string>();
    const out: Segment[] = [];
    for (const d of items) {
      if (d.line.length < 2) continue;
      const key = JSON.stringify(d.line);
      if (seen.has(key)) continue;
      seen.add(key);
      const closed = /closed/i.test(d.closure) || /closure/i.test(d.category);
      for (let i = 1; i < d.line.length; i++) {
        const [ax, az] = toLocal(d.line[i - 1][1], d.line[i - 1][0]);
        const [bx, bz] = toLocal(d.line[i][1], d.line[i][0]);
        const dx = bx - ax;
        const dz = bz - az;
        const len = Math.hypot(dx, dz);
        if (len < 0.5) continue;
        out.push({ x: (ax + bx) / 2, z: (az + bz) / 2, len, yaw: Math.atan2(dx, dz), closed });
      }
    }
    return out;
  }, [items]);

  return (
    <group>
      {segments.map((s, i) => {
        const color = s.closed ? "#ff2d55" : "#ffb020";
        return (
          <group key={i} position={[s.x, 0, s.z]} rotation={[0, s.yaw, 0]}>
            <mesh position={[0, STRIP_H / 2, 0]}>
              <boxGeometry args={[STRIP_W, STRIP_H, s.len]} />
              <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.6} roughness={0.5} />
            </mesh>
            <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]}>
              <planeGeometry args={[GLOW_W, s.len + GLOW_W * 0.6]} />
              <meshBasicMaterial
                color={color}
                transparent
                opacity={0.16}
                depthWrite={false}
                blending={THREE.AdditiveBlending}
              />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}
