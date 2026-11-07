"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { LANDMARKS, landmarkPos } from "../../landmarks";
import { Kit, KitMeshes } from "./kit";

/** 3+ km out the fog would erase these, so they use the fog-free "haze" and "far" materials. */

function elizabethTower(kit: Kit) {
  kit.winBox("hazeLit", 13, 52, 13, 0, 0, 0, 0, 1);
  kit.box("haze", 15, 2, 15, 0, 52, 0);
  kit.winBox("hazeLit", 15, 15, 15, 0, 54, 0, 0, 2);
  for (const [dx, dz, ry] of [[0, 7.7, 0], [0, -7.7, 0], [7.7, 0, Math.PI / 2], [-7.7, 0, Math.PI / 2]] as const) {
    kit.add("farWarm", new THREE.CircleGeometry(4.2, 16), dx, 61.5, dz, ry);
    kit.add("farWarm", new THREE.CircleGeometry(4.2, 16), dx, 61.5, dz, ry + Math.PI);
  }
  kit.box("haze", 16, 2, 16, 0, 69, 0);
  kit.box("haze", 12, 9, 12, 0, 71, 0);
  kit.box("farWarm", 8, 3, 12.4, 0, 73, 0);
  kit.add("haze", new THREE.ConeGeometry(10, 16, 4, 1), 0, 88, 0, Math.PI / 4);
  kit.add("haze", new THREE.ConeGeometry(1.2, 10, 4, 1), 0, 101, 0, Math.PI / 4);
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) kit.cyl("haze", 0.2, 1.1, 8, 4, sx * 6.4, 80, sz * 6.4);
  }
}

function victoriaTower(kit: Kit, z: number) {
  kit.winBox("hazeLit", 24, 70, 24, 0, 0, z, 0, 6);
  kit.box("haze", 27, 2, 27, 0, 70, z);
  kit.winBox("hazeLit", 25, 20, 25, 0, 72, z, 0, 7);
  kit.box("haze", 28, 3, 28, 0, 92, z);
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) kit.add("haze", new THREE.ConeGeometry(2.8, 10, 4, 1), sx * 12, 100, z + sz * 12, Math.PI / 4);
  }
}

function palace(kit: Kit) {
  const len = 292;
  kit.winBox("hazeLit", 30, 17, len, -6, 0, len / 2 + 6, 0, 3);
  kit.box("haze", 32, 1.4, len + 2, -6, 17, len / 2 + 6);
  kit.add("haze", new THREE.BoxGeometry(18, 7, len - 20), -6, 22, len / 2 + 6);
  const cone = new THREE.ConeGeometry(1.7, 8, 4, 1);
  for (let z = 14; z < len; z += 11) {
    for (const sx of [-1, 1]) kit.add("haze", cone.clone(), sx * 15.5 - 6, 21.5, z, Math.PI / 4);
  }
  cone.dispose();
  elizabethTower(kit);
  victoriaTower(kit, len + 14);
}

function buildPalace(): Kit {
  const kit = new Kit();
  palace(kit);
  return kit;
}

function buildEyeStatic(): Kit {
  const kit = new Kit();
  for (const sx of [-1, 1]) {
    kit.add("haze", new THREE.BoxGeometry(2.4, 84, 2.4), 9, 36, sx * 14, 0, -sx * 0.32);
  }
  kit.box("haze", 30, 3, 36, 0, 0, 0);
  kit.add("farGlow", new THREE.CylinderGeometry(3, 3, 6, 12), 4, 70, 0, 0, 0, Math.PI / 2);
  return kit;
}

function buildEyeWheel(): Kit {
  const kit = new Kit();
  const R = 67.5;
  kit.add("farGlow", new THREE.TorusGeometry(R, 1.1, 5, 72), 0, 0, 0);
  kit.add("farGlow", new THREE.TorusGeometry(R - 3, 0.5, 4, 72), 0, 0, 0);
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * Math.PI * 2;
    kit.add("farGlow", new THREE.BoxGeometry(0.25, R - 1, 0.25), (Math.sin(a) * R) / 2, (Math.cos(a) * R) / 2, 0, 0, 0, -a);
  }
  const capsule = new THREE.SphereGeometry(2.1, 8, 6);
  for (let i = 0; i < 32; i++) {
    const a = (i / 32) * Math.PI * 2;
    kit.add("farWarm", capsule.clone(), Math.sin(a) * (R + 2.5), Math.cos(a) * (R + 2.5), 0, 0, 0, 0, [1, 1, 1.4]);
  }
  capsule.dispose();
  return kit;
}

export function Westminster() {
  const palaceKit = useMemo(() => buildPalace(), []);
  const [tx, tz] = landmarkPos(LANDMARKS.elizabethTower);
  const yaw = Math.PI - (LANDMARKS.elizabethTower.headingDeg * Math.PI) / 180;
  return (
    <group position={[tx, 0, tz]} rotation={[0, yaw, 0]}>
      <KitMeshes kit={palaceKit} />
    </group>
  );
}

export function LondonEye() {
  const staticKit = useMemo(() => buildEyeStatic(), []);
  const wheelKit = useMemo(() => buildEyeWheel(), []);
  const wheel = useRef<THREE.Group>(null);
  useFrame((_, dt) => {
    if (wheel.current) wheel.current.rotation.z -= dt * 0.035;
  });
  const [x, z] = landmarkPos(LANDMARKS.londonEye);
  const yaw = Math.PI - (LANDMARKS.londonEye.headingDeg * Math.PI) / 180;
  return (
    <group position={[x, 0, z]} rotation={[0, yaw, 0]}>
      <KitMeshes kit={staticKit} />
      <group position={[0, 70, 0]} rotation={[0, Math.PI / 2, 0]}>
        <group ref={wheel}>
          <KitMeshes kit={wheelKit} />
        </group>
      </group>
    </group>
  );
}
