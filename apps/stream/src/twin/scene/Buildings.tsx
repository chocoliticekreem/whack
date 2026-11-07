"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import type * as THREE from "three";
import { getNetwork } from "../network/useNetwork";
import { RoadIndex } from "./buildings/clearance";
import { makeFacadeMaterial, updateFacadeUniforms } from "./buildings/material";
import { buildBuildingChunks } from "./buildings/massing";
import { perf } from "./perf";
import { useGeometry } from "./useGeometry";

/** Camera distance (metres, to the chunk's bounding box) inside which each detail layer is drawn. */
const MID_RANGE_M = 450;
const FINE_RANGE_M = 220;
const TIER_RANGE: Record<typeof perf.tier, number> = { high: 1, medium: 0.7, low: 0.45 };
const REFRESH_FRAMES = 6;

interface DetailSlot {
  mid: THREE.Mesh | null;
  fine: THREE.Mesh | null;
}

function slotOf(slots: DetailSlot[], i: number): DetailSlot {
  return (slots[i] ??= { mid: null, fine: null });
}

/** Road clearance needs the shared network; if it is unavailable the buildings still render, just unclipped. */
function roadIndexFor(geometry: ReturnType<typeof useGeometry>): RoadIndex | null {
  try {
    return new RoadIndex(getNetwork(geometry));
  } catch {
    return null;
  }
}

export function Buildings() {
  const geometry = useGeometry();
  const chunks = useMemo(() => buildBuildingChunks(geometry.buildings, roadIndexFor(geometry)), [geometry]);
  const material = useMemo(() => makeFacadeMaterial(), []);
  const meshes = useRef<DetailSlot[]>([]);
  const frame = useRef(0);

  useFrame(({ clock, camera }) => {
    updateFacadeUniforms(clock.elapsedTime);
    if (frame.current++ % REFRESH_FRAMES !== 0) return;
    const range = TIER_RANGE[perf.tier];
    chunks.forEach((chunk, i) => {
      const box = chunk.core.boundingBox;
      const slot = meshes.current[i];
      if (!box || !slot) return;
      const d = box.distanceToPoint(camera.position);
      if (slot.mid) slot.mid.visible = d < MID_RANGE_M * range;
      if (slot.fine) slot.fine.visible = d < FINE_RANGE_M * range;
    });
  });

  useEffect(
    () => () => {
      chunks.forEach((chunk) => {
        chunk.core.dispose();
        chunk.mid?.dispose();
        chunk.fine?.dispose();
      });
      material.dispose();
    },
    [chunks, material],
  );

  return (
    <>
      {chunks.map((chunk, i) => (
        <group key={i}>
          <mesh geometry={chunk.core} material={material} />
          {chunk.mid && <mesh ref={(m) => void (slotOf(meshes.current, i).mid = m)} geometry={chunk.mid} material={material} />}
          {chunk.fine && <mesh ref={(m) => void (slotOf(meshes.current, i).fine = m)} geometry={chunk.fine} material={material} />}
        </group>
      ))}
    </>
  );
}
