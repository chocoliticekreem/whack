"use client";

import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { insideGeometryBounds, snapOrigin, toLocal, type LatLon, type TwinGeometry } from "@/twin/config";
import { Buildings } from "@/twin/scene/Buildings";
import { useCameraStore } from "@/twin/scene/cameraStore";
import { GeometryContext, loadGeometryFor } from "@/twin/scene/useGeometry";
import type { Hotspot } from "./city-types";

/** The twin's own OSM square (1.75 km) is centred between Oxford Circus and Piccadilly; farther hubs load as extra patches. */
export const CITY_ORIGIN = snapOrigin(51.5115, -0.1285);

const MAGENTA = new THREE.Color("#ff2d8a");
const WHITE = new THREE.Color("#ffffff");
const GOLD = new THREE.Color("#ffd23d");
const BEAM_H = 420;

export interface Pop {
  key: number;
  hotspot: string;
  text: string;
  good: boolean;
}

function spanOf(points: [number, number][]) {
  const xs = points.map((p) => p[0]);
  const zs = points.map((p) => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cz = (Math.min(...zs) + Math.max(...zs)) / 2;
  const spread = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs));
  return { cx, cz, spread };
}

/** Frames the given hubs; `shift` moves the aim point (fractions of the shot size) so the hubs clear the UI panels. */
export function flyToHotspots(hubs: Hotspot[], shot: { x: number; z: number; zoom: number; steep: boolean }) {
  if (!hubs.length) return;
  const { cx, cz, spread } = spanOf(hubs.map((h) => toLocal(h.lat, h.lon)));
  const d = Math.min(5600, Math.max(1700, spread * 0.85 + 900) * shot.zoom);
  const tx = cx + d * shot.x;
  const tz = cz + d * shot.z;
  const [up, back] = shot.steep ? [0.95, 0.32] : [0.75, 0.75];
  useCameraStore.getState().setCinematic({ pos: [tx + d * 0.05, d * up, tz + d * back], target: [tx, 0, tz] });
}

/** Loads OSM buildings around each far hub and shows each building once, in the patch nearest to it. */
const keyOf = (o: LatLon) => `${o.lat}_${o.lon}`;

function FarPatches({ hotspots }: { hotspots: Hotspot[] }) {
  const originKeys = [
    ...new Set(
      hotspots
        .filter((h) => !insideGeometryBounds(...toLocal(h.lat, h.lon)))
        .map((h) => keyOf(snapOrigin(h.lat, h.lon))),
    ),
  ].join("|");
  const origins = useMemo(
    () =>
      originKeys
        ? originKeys.split("|").map((k) => {
            const [lat, lon] = k.split("_").map(Number);
            return { lat, lon };
          })
        : [],
    [originKeys],
  );
  const [loaded, setLoaded] = useState<{ origin: LatLon; geometry: TwinGeometry }[]>([]);

  useEffect(() => {
    let alive = true;
    for (const origin of origins) {
      loadGeometryFor(origin)
        .then((geometry) => alive && setLoaded((l) => [...l.filter((p) => keyOf(p.origin) !== keyOf(origin)), { origin, geometry }]))
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, [origins]);

  const patches = useMemo(() => {
    const offsets = origins.map((o) => toLocal(o.lat, o.lon));
    return loaded.flatMap(({ origin, geometry }) => {
      const i = origins.findIndex((o) => keyOf(o) === keyOf(origin));
      if (i < 0) return [];
      const [ox, oz] = offsets[i];
      const nearest = (x: number, z: number) => {
        let best = 0;
        let bestD = Infinity;
        offsets.forEach(([px, pz], j) => {
          const d = (x - px) ** 2 + (z - pz) ** 2;
          if (d < bestD) {
            bestD = d;
            best = j;
          }
        });
        return best;
      };
      const buildings = geometry.buildings.filter((b) => {
        const [bx, bz] = b.ring[0];
        const x = bx + ox;
        const z = bz + oz;
        return !insideGeometryBounds(x, z) && nearest(x, z) === i;
      });
      const g: TwinGeometry = { ...geometry, buildings, roads: [] };
      return [{ key: keyOf(origin), offset: [ox, oz] as const, geometry: g }];
    });
  }, [loaded, origins]);

  return (
    <>
      {patches.map((p) => (
        <group key={p.key} position={[p.offset[0], 0, p.offset[1]]}>
          <GeometryContext.Provider value={p.geometry}>
            <Buildings />
          </GeometryContext.Provider>
        </group>
      ))}
    </>
  );
}

/** The twin's fog is tuned for a 650 m street view; a city-wide shot needs it pushed back. Runs after Sky's frame hook. */
function WideFog() {
  useFrame(({ scene }) => {
    if (scene.fog instanceof THREE.Fog) {
      scene.fog.near = 4500;
      scene.fog.far = 12000;
    }
  });
  return null;
}

function Marker({
  hub,
  active,
  win,
  pop,
}: {
  hub: Hotspot;
  active: boolean;
  win: boolean;
  pop: Pop | null;
}) {
  const [x, z] = useMemo(() => toLocal(hub.lat, hub.lon), [hub.lat, hub.lon]);
  const beam = useRef<THREE.MeshBasicMaterial>(null);
  const ring = useRef<THREE.Mesh>(null);
  const ring2 = useRef<THREE.Mesh>(null);
  const color = win ? GOLD : active ? MAGENTA : WHITE;

  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const speed = win ? 1.6 : 0.6;
    [ring.current, ring2.current].forEach((m, i) => {
      if (!m) return;
      const p = (t * speed + i * 0.5) % 1;
      m.scale.setScalar(1 + p * (win ? 6 : active ? 3.5 : 2.5));
      (m.material as THREE.MeshBasicMaterial).opacity = (1 - p) * (active || win ? 0.9 : 0.55);
      (m.material as THREE.MeshBasicMaterial).color.copy(color);
    });
    if (beam.current) {
      beam.current.color.copy(color);
      beam.current.opacity = (win ? 0.85 : active ? 0.75 : 0.5) + Math.sin(t * 3) * 0.1;
    }
  });

  const width = win ? 30 : active ? 22 : 12;
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, BEAM_H / 2, 0]}>
        <cylinderGeometry args={[width * 0.6, width, BEAM_H, 16, 1, true]} />
        <meshBasicMaterial ref={beam} transparent depthWrite={false} side={THREE.DoubleSide} toneMapped={false} fog={false} />
      </mesh>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, 3, 0]}>
        <ringGeometry args={[30, 44, 64]} />
        <meshBasicMaterial transparent depthWrite={false} toneMapped={false} fog={false} />
      </mesh>
      <mesh ref={ring2} rotation={[-Math.PI / 2, 0, 0]} position={[0, 3, 0]}>
        <ringGeometry args={[30, 44, 64]} />
        <meshBasicMaterial transparent depthWrite={false} toneMapped={false} fog={false} />
      </mesh>
      <Html position={[0, BEAM_H + 20, 0]} center zIndexRange={[15, 0]}>
        <div className="tl-pin" data-active={active || undefined} data-win={win || undefined}>
          <span className="tl-pin-dot" />
          {hub.name}
        </div>
      </Html>
      {/* Why: always mounted; drei's Html unmounting mid-render makes React 19 log a root-unmount race. */}
      <Html position={[0, BEAM_H * 0.55, 0]} center zIndexRange={[16, 0]}>
        {pop && (
          <div key={pop.key} className="tl-pop3d" data-good={pop.good || undefined}>
            {pop.text}
          </div>
        )}
      </Html>
    </group>
  );
}

export function City3D({
  hotspots,
  active,
  winner,
  pop,
}: {
  hotspots: Hotspot[];
  active: string[];
  winner: string | null;
  pop: Pop | null;
}) {
  return (
    <group>
      <WideFog />
      <FarPatches hotspots={hotspots} />
      {hotspots.map((h) => (
        <Marker
          key={h.id}
          hub={h}
          active={active.includes(h.id)}
          win={winner === h.id}
          pop={pop?.hotspot === h.id ? pop : null}
        />
      ))}
    </group>
  );
}
