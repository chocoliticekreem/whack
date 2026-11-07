"use client";

import { useMemo } from "react";
import * as THREE from "three";
import { TOWER_OF_LONDON } from "../../landmarks";
import type { Ring } from "../../config";
import { Kit, KitMeshes, winCylinder } from "./kit";

const OUTER = { h: 9.5, t: 4.2 };
const INNER = { h: 12.5, t: 3.2 };
const MERLON_STEP = 3.1;

/** Crenellated wall along a polyline: a solid band plus a merlon row on each face. */
function wall(kit: Kit, line: Ring, h: number, t: number) {
  for (let i = 0; i < line.length - 1; i++) {
    const [ax, az] = line[i];
    const [bx, bz] = line[i + 1];
    const len = Math.hypot(bx - ax, bz - az);
    const ry = -Math.atan2(bz - az, bx - ax);
    const ux = (bx - ax) / len;
    const uz = (bz - az) / len;
    kit.box("stone", len + t * 0.5, h, t, (ax + bx) / 2, 0, (az + bz) / 2, ry);
    for (const side of [-1, 1]) {
      const ox = -uz * side * (t / 2 - 0.4);
      const oz = ux * side * (t / 2 - 0.4);
      for (let d = 1.2; d < len - 0.8; d += MERLON_STEP) {
        kit.box("stone", 1.7, 1.4, 0.8, ax + ux * d + ox, h, az + uz * d + oz, ry);
      }
    }
  }
}

function roundTower(kit: Kit, x: number, z: number, r: number, h: number, cone: boolean) {
  kit.add("stoneLit", winCylinder(r + 0.5, r + 0.8, h, 12), x, h / 2, z);
  kit.add("stone", new THREE.CylinderGeometry(r + 1.1, r + 1.1, 1, 12), x, h + 0.5, z);
  if (cone) {
    kit.add("roof", new THREE.ConeGeometry(r + 1.5, r * 1.9, 12), x, h + 1 + r * 0.95, z);
    return;
  }
  const n = Math.max(6, Math.round((2 * Math.PI * r) / 3.2));
  for (let k = 0; k < n; k++) {
    const a = (k / n) * Math.PI * 2;
    kit.box("stone", 1.6, 1.4, 0.8, x + Math.cos(a) * (r + 0.7), h + 1, z + Math.sin(a) * (r + 0.7), -a - Math.PI / 2);
  }
}

function whiteTower(kit: Kit) {
  const { x, z, yaw, w, d, h } = TOWER_OF_LONDON.whiteTower;
  const ry = (-yaw * Math.PI) / 180;
  const c = Math.cos(ry);
  const s = Math.sin(ry);
  const at = (lx: number, lz: number): [number, number] => [x + lx * c + lz * s, z - lx * s + lz * c];
  kit.add("white", new THREE.BoxGeometry(w, h, d), x, h / 2, z, ry);
  for (const y of [9, 18]) {
    kit.add("stone", new THREE.BoxGeometry(w + 0.8, 0.8, d + 0.8), x, y, z, ry);
  }
  for (let ly = 6; ly < h - 4; ly += 9) {
    for (let lx = -w / 2 + 5; lx < w / 2 - 3; lx += 7) {
      for (const side of [-1, 1]) {
        const [px, pz] = at(lx, (side * (d + 0.4)) / 2);
        kit.add("lamp", new THREE.BoxGeometry(1.1, 2.6, 0.3), px, ly + 2, pz, ry);
      }
    }
  }
  const corners: [number, number, number, boolean][] = [
    [-1, -1, 4.2, false],
    [1, -1, 3.4, true],
    [-1, 1, 4.2, false],
    [1, 1, 4.2, false],
  ];
  for (const [sx, sz, rad, round] of corners) {
    const [px, pz] = at(sx * (w / 2 - 1.2), sz * (d / 2 - 1.2));
    const top = round ? h + 7 : h + 4;
    if (round) kit.add("white", new THREE.CylinderGeometry(rad, rad + 0.4, top, 12), px, top / 2, pz);
    else kit.add("white", new THREE.BoxGeometry(rad * 2, top, rad * 2), px, top / 2, pz, ry);
    kit.add("stone", new THREE.CylinderGeometry(rad + 0.6, rad + 0.6, 0.8, 12), px, top + 0.4, pz);
    kit.add("roof", new THREE.ConeGeometry(rad + 0.5, 4.2, 12), px, top + 2.9, pz);
    kit.add("gold", new THREE.SphereGeometry(0.5, 6, 4), px, top + 5.3, pz);
  }
  const n = 24;
  for (let k = 0; k < n; k++) {
    const t = (k + 0.5) / n;
    for (const [lx, lz, along] of [
      [-w / 2 + 0.5 + t * (w - 1), -d / 2 + 0.5, true],
      [-w / 2 + 0.5 + t * (w - 1), d / 2 - 0.5, true],
      [-w / 2 + 0.5, -d / 2 + 0.5 + t * (d - 1), false],
      [w / 2 - 0.5, -d / 2 + 0.5 + t * (d - 1), false],
    ] as [number, number, boolean][]) {
      if (k % 2) continue;
      const [px, pz] = at(lx, lz);
      kit.add("white", new THREE.BoxGeometry(along ? 1.6 : 0.8, 1.3, along ? 0.8 : 1.6), px, h + 0.65, pz, ry);
    }
  }
}

function buildTowerOfLondon(): Kit {
  const kit = new Kit();
  wall(kit, TOWER_OF_LONDON.outerWall as unknown as Ring, OUTER.h, OUTER.t);
  wall(kit, TOWER_OF_LONDON.innerWall as unknown as Ring, INNER.h, INNER.t);
  for (const t of TOWER_OF_LONDON.towers) roundTower(kit, t.x, t.z, t.r, t.h, t.cone);
  whiteTower(kit);
  return kit;
}

export function TowerOfLondon() {
  const kit = useMemo(() => buildTowerOfLondon(), []);
  return <KitMeshes kit={kit} />;
}
