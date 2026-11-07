"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { bearingToYaw, LANDMARKS, landmarkPos } from "../../landmarks";
import { river } from "../../live/river";
import { Kit, KitMeshes } from "./kit";

/** Bridge-local frame: x across the deck, z along it (towers at +-TOWER_Z), y up, water at y = 0. */
const TOWER_Z = 41.5;
const DECK_Y = 9.2;
const SPAN_END = 123;
const ROAD_END = 163;
const CHAIN_X = 10.5;
const LEAF_LEN = TOWER_Z - 7;
const MAX_LIFT = (70 * Math.PI) / 180;
/** Height exaggeration so the hero reads from the Aerial camera. */
const SCALE: [number, number, number] = [1.1, 1.22, 1.1];

function slits(kit: Kit, z: number, face: number, rows: number[], xs: number[]) {
  for (const y of rows) {
    for (const x of xs) {
      kit.box("lamp", 1.1, 4.2, 0.3, x, y, z + face * 7.1);
      kit.add("lamp", new THREE.ConeGeometry(0.8, 1.4, 3), x, y + 4.9, z + face * 7.1, 0, 0, 0, [1, 1, 0.3]);
    }
  }
}

function tower(kit: Kit, z: number, seed: number) {
  kit.box("stone", 40, 14.4, 23, 0, -5.5, z);
  for (const sx of [-1, 1]) {
    kit.winBox("stoneLit", 5, 24, 14, sx * 8, DECK_Y - 0.4, z, 0, seed + sx);
    kit.box("stone", 5.8, 1, 14.8, sx * 8, DECK_Y + 4, z);
    kit.box("stone", 0.9, 24, 14.8, sx * 10.9, DECK_Y - 0.4, z);
  }
  kit.winBox("stoneLit", 21, 24.5, 14, 0, DECK_Y + 12, z, 0, seed);
  kit.box("stone", 22, 1, 15, 0, DECK_Y + 11.5, z);
  for (const y of [DECK_Y + 22, DECK_Y + 30]) kit.box("stone", 22.4, 0.7, 14.6, 0, y, z);
  kit.box("stone", 24, 1.5, 17, 0, 45.5, z);
  kit.winBox("stoneLit", 19, 8, 12, 0, 47, z, 0, seed + 3);
  kit.box("stone", 22, 1.2, 15, 0, 55, z);
  kit.add("roof", new THREE.ConeGeometry(15.5, 15, 4, 1), 0, 63.7, z, Math.PI / 4);
  kit.add("roof", new THREE.ConeGeometry(4.5, 8, 4, 1), 0, 71.5, z, Math.PI / 4);
  kit.add("gold", new THREE.SphereGeometry(0.7, 6, 4), 0, 76, z);
  for (const face of [-1, 1]) {
    slits(kit, z, face, [DECK_Y + 14, DECK_Y + 24], [-7, -3.5, 0, 3.5, 7]);
    slits(kit, z, face, [47.5], [-5, 0, 5]);
    kit.add("roof", new THREE.ConeGeometry(2.4, 5, 4, 1), 0, 57.5, z + face * 5.6, Math.PI / 4);
    kit.box("roof", 3.4, 3, 2.6, 0, 55.2, z + face * 5.6);
  }
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const cx = sx * 10.2;
      const cz = z + sz * 6.4;
      kit.box("stone", 4.8, 48, 4.8, cx, DECK_Y - 1, cz);
      kit.box("stone", 5.8, 1.1, 5.8, cx, 41, cz);
      kit.box("stone", 5.8, 1.1, 5.8, cx, 52, cz);
      kit.add("roof", new THREE.ConeGeometry(4, 12, 4, 1), cx, 62, cz, Math.PI / 4);
      kit.add("gold", new THREE.SphereGeometry(0.6, 6, 4), cx, 68.4, cz);
    }
  }
  kit.add("beacon", new THREE.SphereGeometry(1, 8, 6), 0, 77.5, z);
}

function chain(kit: Kit, side: number, end: number, lift: number) {
  const dir = Math.sign(end);
  const z0 = dir * (TOWER_Z + 8.2);
  const yTop = 43 - lift;
  const yEnd = 25.5 - lift;
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 14; i++) {
    const t = i / 14;
    pts.push(new THREE.Vector3(side * CHAIN_X, yEnd + (yTop - yEnd) * Math.pow(1 - t, 1.75), z0 + (end - z0) * t));
  }
  kit.add("chain", new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 42, 0.75, 6));
  if (lift !== 0) return;
  const hangers = 14;
  for (let i = 1; i < hangers; i++) {
    const t = i / hangers;
    const y = yEnd + (yTop - yEnd) * Math.pow(1 - t, 1.75);
    kit.box("chain", 0.35, y - DECK_Y, 0.35, side * CHAIN_X, DECK_Y, z0 + (end - z0) * t);
  }
}

function approach(kit: Kit, dir: number) {
  const run = ROAD_END - SPAN_END;
  const slope = Math.atan2(DECK_Y - 1, run);
  const zc = dir * (SPAN_END + run / 2);
  kit.add("deck", new THREE.BoxGeometry(23, 2, run / Math.cos(slope)), 0, (DECK_Y + 1) / 2 - 1, zc, 0, dir * slope);
  for (const sx of [-1, 1]) {
    kit.add("steel", new THREE.BoxGeometry(0.8, 2.6, run / Math.cos(slope)), sx * 11.7, (DECK_Y + 1) / 2 - 0.5, zc, 0, dir * slope);
  }
}

function buildFixed(): Kit {
  const kit = new Kit();
  tower(kit, -TOWER_Z, 1);
  tower(kit, TOWER_Z, 5);

  for (const sx of [-1, 1]) {
    kit.box("glass", 3.8, 3.8, 70, sx * 7.6, 40.8, 0);
    kit.box("steel", 4.6, 0.7, 70, sx * 7.6, 44.6, 0);
    kit.box("steel", 4.6, 0.7, 70, sx * 7.6, 40.1, 0);
    for (let z = -33; z <= 33; z += 5.5) kit.box("steel", 0.45, 4.6, 0.45, sx * 7.6, 40.1, z);
  }
  kit.box("steel", 20, 1, 1.4, 0, 44.9, 0);
  kit.box("steel", 20, 1, 1.4, 0, 40.4, 0);

  const outer = SPAN_END - TOWER_Z - 7;
  for (const dir of [-1, 1]) {
    kit.box("deck", 23, 2.2, outer, 0, DECK_Y - 2.2, (dir * (SPAN_END + TOWER_Z + 7)) / 2);
    for (const sx of [-1, 1]) {
      kit.box("steel", 0.9, 2.8, outer, sx * 11.7, DECK_Y - 2.2, (dir * (SPAN_END + TOWER_Z + 7)) / 2);
      kit.box("stone", 6, 27, 7, sx * CHAIN_X, 0, dir * SPAN_END);
      kit.box("stone", 7, 1, 8, sx * CHAIN_X, 25.8, dir * SPAN_END);
      kit.add("roof", new THREE.ConeGeometry(4.6, 7, 4, 1), sx * CHAIN_X, 30.3, dir * SPAN_END, Math.PI / 4);
      kit.add("gold", new THREE.SphereGeometry(0.5, 6, 4), sx * CHAIN_X, 34.2, dir * SPAN_END);
      chain(kit, sx, dir * SPAN_END, 0);
      chain(kit, sx, dir * SPAN_END, 2.4);
    }
    approach(kit, dir);
  }

  const lampSphere = new THREE.SphereGeometry(0.6, 6, 4);
  for (let z = -ROAD_END + 6; z <= ROAD_END - 6; z += 9) {
    if (Math.abs(z) < TOWER_Z + 7) continue;
    const y = Math.abs(z) < SPAN_END ? DECK_Y : DECK_Y - (Math.abs(z) - SPAN_END) * ((DECK_Y - 1) / (ROAD_END - SPAN_END));
    for (const sx of [-1, 1]) {
      kit.box("steel", 0.2, 5.2, 0.2, sx * 12, y, z);
      kit.add("lamp", lampSphere.clone(), sx * 12, y + 5.4, z);
    }
  }
  lampSphere.dispose();
  return kit;
}

/** One bascule leaf in pivot space: it extends along +z from the tower's inner face. */
function buildLeaf(): Kit {
  const kit = new Kit();
  kit.box("deck", 22, 2.2, LEAF_LEN, 0, -2.2, LEAF_LEN / 2);
  for (const sx of [-1, 1]) {
    kit.box("steel", 0.9, 2.8, LEAF_LEN, sx * 11.2, -2.2, LEAF_LEN / 2);
    kit.box("chain", 0.4, 0.5, LEAF_LEN, sx * 11.2, 0.6, LEAF_LEN / 2);
    for (const z of [8, 22]) {
      kit.box("steel", 0.2, 5.2, 0.2, sx * 12, 0, z);
      kit.add("lamp", new THREE.SphereGeometry(0.6, 6, 4), sx * 12, 5.4, z);
    }
  }
  return kit;
}

export function TowerBridge() {
  const fixed = useMemo(() => buildFixed(), []);
  const leaf = useMemo(() => buildLeaf(), []);
  const north = useRef<THREE.Group>(null);
  const south = useRef<THREE.Group>(null);
  useFrame(() => {
    const angle = -Math.min(1, Math.max(0, river.bridgeOpen)) * MAX_LIFT;
    if (north.current) north.current.rotation.x = angle;
    if (south.current) south.current.rotation.x = angle;
  });
  const [x, z] = landmarkPos(LANDMARKS.towerBridge);
  return (
    <group position={[x, 0, z]} rotation={[0, bearingToYaw(LANDMARKS.towerBridge.headingDeg), 0]} scale={SCALE}>
      <KitMeshes kit={fixed} />
      <group position={[0, DECK_Y, -LEAF_LEN]}>
        <group ref={north}>
          <KitMeshes kit={leaf} />
        </group>
      </group>
      <group position={[0, DECK_Y, LEAF_LEN]} rotation={[0, Math.PI, 0]}>
        <group ref={south}>
          <KitMeshes kit={leaf} />
        </group>
      </group>
    </group>
  );
}
