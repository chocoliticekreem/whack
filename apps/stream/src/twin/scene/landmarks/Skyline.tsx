"use client";

import { useMemo } from "react";
import * as THREE from "three";
import { bearingToYaw, LANDMARKS, landmarkInRange, landmarkPos, type Landmark } from "../../landmarks";
import { Kit, KitMeshes, scaleUV, winBoxGeometry, winCylinder } from "./kit";

function shard(kit: Kit) {
  kit.add("glass", scaleUV(new THREE.ConeGeometry(30, 282, 4, 1, true), 6.5, 4.9), 0, 141, 0, Math.PI / 4);
  const splinters: [number, number, number, number, number][] = [
    [0, 0, 6.5, 44, 292],
    [4.5, 2, 4.2, 30, 288],
    [-4, -3, 4, 34, 285],
    [1, -5, 3.2, 26, 282],
  ];
  for (const [x, z, r, h, y] of splinters) {
    kit.add("glass", scaleUV(new THREE.ConeGeometry(r, h, 4, 1, true), 1, 1), x, y + h / 2 - 6, z, Math.PI / 4);
  }
  kit.add("beacon", new THREE.SphereGeometry(1.1, 8, 6), 0, 318, 0);
}

function gherkin(kit: Kit) {
  const pts: THREE.Vector2[] = [];
  for (let i = 0; i <= 28; i++) {
    const t = i / 28;
    const r = t < 0.4 ? 28 * (0.62 + 0.38 * Math.sin((t / 0.4) * (Math.PI / 2))) : 28 * Math.pow(Math.cos(((t - 0.4) / 0.6) * (Math.PI / 2)), 0.75);
    pts.push(new THREE.Vector2(Math.max(r, 0.05), t * 180));
  }
  kit.add("lattice", new THREE.LatheGeometry(pts, 28));
  kit.cyl("stone", 29, 29, 1.2, 28, 0, 0, 0);
  kit.add("lamp", new THREE.SphereGeometry(1.4, 8, 6), 0, 181, 0);
}

function flared(kit: Kit, w: number, h: number, d: number, topW: number, topD: number, x: number, z: number) {
  const g = winBoxGeometry((w + topW) / 2, h, (d + topD) / 2, 2);
  const p = g.attributes.position as THREE.BufferAttribute;
  for (let i = 0; i < p.count; i++) {
    const top = p.getY(i) > 0;
    p.setX(i, (p.getX(i) / ((w + topW) / 2)) * (top ? topW : w));
    p.setZ(i, (p.getZ(i) / ((d + topD) / 2)) * (top ? topD : d));
  }
  kit.add("glass", g, x, h / 2, z);
}

function walkieTalkie(kit: Kit) {
  flared(kit, 40, 160, 48, 58, 62, 0, 0);
  kit.box("stone", 58.6, 1.4, 62.6, 0, 160, 0);
  kit.box("lamp", 58.8, 0.3, 62.8, 0, 161.4, 0);
  kit.winBox("glass", 44, 10, 46, 0, 161.7, 0, 0, 3);
}

function cheesegrater(kit: Kit) {
  const s = new THREE.Shape();
  s.moveTo(0, 0);
  s.lineTo(42, 0);
  s.lineTo(8, 225);
  s.lineTo(0, 225);
  s.closePath();
  const g = new THREE.ExtrudeGeometry(s, { depth: 45, bevelEnabled: false });
  g.translate(-21, 0, -22.5);
  kit.add("glassW", g);
  kit.winBox("glass", 12, 225, 18, -27, 0, 0, 0, 4);
  kit.add("beacon", new THREE.SphereGeometry(1, 8, 6), -18, 227, 0);
}

function tower42(kit: Kit) {
  const stages: [number, number, number][] = [
    [25, 0, 100],
    [20, 100, 52],
    [13, 152, 32],
  ];
  for (const [r, y, h] of stages) {
    kit.add("glass", winCylinder(r, r, h, 8), 0, y + h / 2, 0, Math.PI / 8);
    kit.add("stone", new THREE.CylinderGeometry(r + 0.6, r + 0.6, 1.4, 8), 0, y + h + 0.7, 0, Math.PI / 8);
  }
  kit.cyl("stone", 3, 5, 12, 8, 0, 185, 0);
  kit.add("beacon", new THREE.SphereGeometry(0.9, 8, 6), 0, 199, 0);
}

function cityHall(kit: Kit) {
  kit.add("glass", scaleUV(new THREE.SphereGeometry(1, 28, 16), 9, 2), 0, 22, 0, 0, 0.32, 0, [27, 22, 29]);
  kit.cyl("stone", 24, 24, 3, 20, 2, 0, 0);
}

function stPauls(kit: Kit) {
  kit.box("white", 30, 26, 158, 0, 0, 0);
  kit.box("white", 76, 26, 30, 0, 0, 0);
  kit.box("stone", 31, 1.2, 159, 0, 26, 0);
  kit.box("stone", 77, 1.2, 31, 0, 26, 0);
  kit.cyl("white", 20, 20, 24, 24, 0, 26, 0);
  kit.cyl("stone", 21.5, 21.5, 2, 24, 0, 50, 0);
  kit.add("white", new THREE.SphereGeometry(20, 28, 12, 0, Math.PI * 2, 0, Math.PI / 2), 0, 52, 0, 0, 0, 0, [1, 1.35, 1]);
  kit.cyl("white", 4.6, 4.6, 16, 12, 0, 79, 0);
  kit.cyl("stone", 3.4, 5, 5, 12, 0, 95, 0);
  kit.add("gold", new THREE.SphereGeometry(2.1, 8, 6), 0, 102, 0);
  kit.box("gold", 0.7, 7, 0.7, 0, 103, 0);
  kit.box("gold", 3.6, 0.7, 0.7, 0, 107, 0);
  for (const sx of [-1, 1]) {
    kit.box("white", 12, 52, 12, sx * 11, 0, 76);
    kit.cyl("white", 3.4, 5, 8, 10, sx * 11, 52, 76);
    kit.add("gold", new THREE.SphereGeometry(0.9, 6, 4), sx * 11, 62, 76);
  }
  kit.box("white", 30, 12, 8, 0, 26, 74);
  for (let a = 0; a < 16; a++) {
    const t = (a / 16) * Math.PI * 2;
    kit.box("stone", 1.6, 24, 1.6, Math.cos(t) * 20.6, 26, Math.sin(t) * 20.6);
  }
}

function hmsBelfast(kit: Kit) {
  const hull = new THREE.Shape();
  hull.moveTo(-9.5, -64);
  hull.lineTo(9.5, -64);
  hull.lineTo(9.8, 30);
  hull.lineTo(8, 62);
  hull.lineTo(0, 93);
  hull.lineTo(-8, 62);
  hull.lineTo(-9.8, 30);
  hull.closePath();
  const g = new THREE.ExtrudeGeometry(hull, { depth: 13, bevelEnabled: false });
  g.rotateX(Math.PI / 2);
  kit.add("hull", g, 0, 9.5, 0);
  kit.box("hull", 9, 6, 70, 0, 9.5, -8);
  kit.box("hull", 8, 9, 14, 0, 15.5, 22);
  kit.box("hull", 6, 8, 8, 0, 24.5, 22);
  for (const [z, h] of [[-2, 17], [-30, 14]] as const) {
    kit.add("hull", new THREE.CylinderGeometry(2.4, 3, h, 10), 0, 15.5 + h / 2, z, 0, -0.1);
  }
  for (const z of [36, 8, -48, -62]) {
    kit.cyl("hull", 4.2, 4.6, 4, 10, 0, 9.5, z);
    for (const sx of [-1, 0, 1]) kit.box("hull", 0.5, 0.5, 7, sx * 1.1, 11.2, z + 5 * Math.sign(z + 10));
  }
  kit.cyl("hull", 0.5, 0.7, 34, 6, 0, 15.5, 22);
  kit.cyl("hull", 0.4, 0.6, 26, 6, 0, 15.5, -44);
  kit.add("lamp", new THREE.SphereGeometry(0.7, 6, 4), 0, 50, 22);
  for (let z = -60; z <= 80; z += 14) kit.add("lamp", new THREE.SphereGeometry(0.35, 5, 4), 8, 10.3, z);
}

const PIECES: { landmark: Landmark; build: (kit: Kit) => void }[] = [
  { landmark: LANDMARKS.shard, build: shard },
  { landmark: LANDMARKS.gherkin, build: gherkin },
  { landmark: LANDMARKS.walkieTalkie, build: walkieTalkie },
  { landmark: LANDMARKS.cheesegrater, build: cheesegrater },
  { landmark: LANDMARKS.tower42, build: tower42 },
  { landmark: LANDMARKS.cityHall, build: cityHall },
  { landmark: LANDMARKS.stPauls, build: stPauls },
  { landmark: LANDMARKS.hmsBelfast, build: hmsBelfast },
];

function Piece({ landmark, build }: { landmark: Landmark; build: (kit: Kit) => void }) {
  const kit = useMemo(() => {
    const k = new Kit();
    build(k);
    return k;
  }, [build]);
  const [x, z] = landmarkPos(landmark);
  return (
    <group position={[x, 0, z]} rotation={[0, bearingToYaw(landmark.headingDeg), 0]}>
      <KitMeshes kit={kit} />
    </group>
  );
}

export function Skyline() {
  return (
    <>
      {PIECES.filter((p) => landmarkInRange(p.landmark)).map((p) => (
        <Piece key={p.landmark.id} {...p} />
      ))}
    </>
  );
}
