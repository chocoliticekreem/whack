"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { Ring } from "../config";
import { mulberry32 } from "../live/sim/rng";
import { ATMOS } from "./palette";
import { useGeometry } from "./useGeometry";
import { ringToShape } from "./shapes";
import { grassTexture } from "./textures";

const MAX_TREES = 1500;
const TREE_SPACING_M = 5;
const M2_PER_TREE = 120;
const PARK_Y = 0.12;

const GRASS_DAY = new THREE.Color("#8fa879");
const GRASS_NIGHT = new THREE.Color("#80b4b0");
const GRASS_GLOW = new THREE.Color("#0c3a30");
const CANOPY_DAY = new THREE.Color("#ffffff");
const CANOPY_NIGHT = new THREE.Color("#6f9aa6");
const TRUNK_DAY = new THREE.Color("#6a4e38");
const TRUNK_NIGHT = new THREE.Color("#2e3646");

function ringArea(ring: Ring) {
  let a = 0;
  for (let i = 0; i < ring.length; i++) {
    const [x0, z0] = ring[i];
    const [x1, z1] = ring[(i + 1) % ring.length];
    a += x0 * z1 - x1 * z0;
  }
  return Math.abs(a) / 2;
}

function inRing(ring: Ring, x: number, z: number) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, zi] = ring[i];
    const [xj, zj] = ring[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

interface TreeSpec {
  x: number;
  z: number;
  trunk: number;
  rx: number;
  ry: number;
  yaw: number;
  color: THREE.Color;
}

/**
 * Three overlapping lobes merged into one cloud. Normals point out from the cloud's centre rather than from each lobe,
 * so it shades as one soft volume, and a vertex tint darkens the underside like cheap ambient occlusion.
 */
function canopyGeometry(): THREE.BufferGeometry {
  const lobes: [number, number, number, number, number][] = [
    [0, 0.05, 0, 0.82, 2],
    [0.42, -0.18, 0.2, 0.55, 1],
    [-0.36, -0.12, -0.28, 0.58, 1],
  ];
  const parts = lobes.map(([x, y, z, r, detail]) => {
    const g = new THREE.IcosahedronGeometry(r, detail);
    g.translate(x, y, z);
    return g;
  });
  const geo = mergeGeometries(parts);
  parts.forEach((g) => g.dispose());
  const pos = geo.attributes.position;
  const nrm = geo.attributes.normal;
  const col = new Float32Array(pos.count * 3);
  const n = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    n.set(pos.getX(i), pos.getY(i) + 0.25, pos.getZ(i)).normalize();
    nrm.setXYZ(i, n.x, n.y, n.z);
    const shade = 0.62 + 0.45 * THREE.MathUtils.smoothstep(pos.getY(i), -0.7, 0.75);
    col[i * 3] = col[i * 3 + 1] = col[i * 3 + 2] = shade;
  }
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  return geo;
}

/** Seeded dart throwing inside each park ring, one shared spacing grid so neighbouring parks don't overlap. */
function scatterTrees(parks: Ring[]): TreeSpec[] {
  const areas = parks.map(ringArea);
  const wanted = areas.map((a) => (a < 400 ? 0 : Math.floor(a / M2_PER_TREE)));
  const scale = Math.min(1, MAX_TREES / Math.max(1, wanted.reduce((s, n) => s + n, 0)));
  const taken = new Map<number, [number, number][]>();
  const cell = (v: number) => Math.floor(v / TREE_SPACING_M);
  const trees: TreeSpec[] = [];

  parks.forEach((ring, pi) => {
    const rnd = mulberry32(9001 + pi * 131);
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (const [x, z] of ring) {
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minZ = Math.min(minZ, z);
      maxZ = Math.max(maxZ, z);
    }
    const target = Math.floor(wanted[pi] * scale);
    let placed = 0;
    for (let attempt = 0; attempt < target * 14 && placed < target; attempt++) {
      const x = minX + rnd() * (maxX - minX);
      const z = minZ + rnd() * (maxZ - minZ);
      if (!inRing(ring, x, z)) continue;
      const cx = cell(x);
      const cz = cell(z);
      let blocked = false;
      for (let dx = -1; dx <= 1 && !blocked; dx++) {
        for (let dz = -1; dz <= 1 && !blocked; dz++) {
          const near = taken.get((cx + dx) * 100003 + (cz + dz));
          if (near) blocked = near.some(([nx, nz]) => (nx - x) ** 2 + (nz - z) ** 2 < TREE_SPACING_M ** 2);
        }
      }
      if (blocked) continue;
      const key = cx * 100003 + cz;
      const bucket = taken.get(key);
      if (bucket) bucket.push([x, z]);
      else taken.set(key, [[x, z]]);

      const conifer = rnd() < 0.18;
      const r = 2.2 + rnd() * 2.6;
      trees.push({
        x,
        z,
        trunk: 2.2 + rnd() * 1.8,
        rx: conifer ? r * 0.55 : r,
        ry: conifer ? r * 1.7 : r * (0.8 + rnd() * 0.4),
        yaw: rnd() * Math.PI * 2,
        // Muted park greens: olive to sage for broadleaf, a cooler blue-green for conifers.
        color: new THREE.Color().setHSL(
          conifer ? 0.36 + rnd() * 0.04 : 0.21 + rnd() * 0.08,
          conifer ? 0.18 + rnd() * 0.1 : 0.22 + rnd() * 0.14,
          conifer ? 0.2 + rnd() * 0.05 : 0.27 + rnd() * 0.09,
          THREE.SRGBColorSpace,
        ),
      });
      placed++;
    }
  });
  return trees;
}

export function Parks() {
  const { parks } = useGeometry();

  const grass = useMemo(() => {
    const parts = parks.map((ring) => {
      const g = new THREE.ShapeGeometry(ringToShape(ring));
      g.rotateX(-Math.PI / 2);
      return g;
    });
    if (!parts.length) return null;
    const geometry = mergeGeometries(parts);
    parts.forEach((g) => g.dispose());
    const material = new THREE.MeshStandardMaterial({
      map: grassTexture({ tileM: 18 }),
      color: GRASS_DAY,
      roughness: 1,
      polygonOffset: true,
      polygonOffsetFactor: -2,
      polygonOffsetUnits: -2,
    });
    return { geometry, material };
  }, [parks]);

  const trees = useMemo(() => {
    const specs = scatterTrees(parks);
    if (!specs.length) return null;
    const canopyGeo = canopyGeometry();
    const trunkGeo = new THREE.CylinderGeometry(0.12, 0.24, 1, 6);
    trunkGeo.translate(0, 0.5, 0);
    const canopyMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95 });
    const trunkMat = new THREE.MeshStandardMaterial({ color: TRUNK_DAY, roughness: 1 });
    const canopy = new THREE.InstancedMesh(canopyGeo, canopyMat, specs.length);
    const trunk = new THREE.InstancedMesh(trunkGeo, trunkMat, specs.length);
    canopy.frustumCulled = false;
    trunk.frustumCulled = false;
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const pos = new THREE.Vector3();
    const scl = new THREE.Vector3();
    const up = new THREE.Vector3(0, 1, 0);
    specs.forEach((t, i) => {
      q.setFromAxisAngle(up, t.yaw);
      m.compose(pos.set(t.x, PARK_Y, t.z), q, scl.set(1, t.trunk + t.ry * 0.4, 1));
      trunk.setMatrixAt(i, m);
      m.compose(pos.set(t.x, PARK_Y + t.trunk + t.ry * 0.55, t.z), q, scl.set(t.rx, t.ry, t.rx));
      canopy.setMatrixAt(i, m);
      canopy.setColorAt(i, t.color);
    });
    return { canopy, trunk, canopyMat, trunkMat };
  }, [parks]);

  useFrame(() => {
    const day = 1 - ATMOS.windowGlow;
    if (grass) {
      grass.material.color.copy(GRASS_NIGHT).lerp(GRASS_DAY, day);
      grass.material.emissive.copy(GRASS_GLOW).multiplyScalar(0.55 * ATMOS.windowGlow);
    }
    if (trees) {
      trees.canopyMat.color.copy(CANOPY_NIGHT).lerp(CANOPY_DAY, day);
      trees.trunkMat.color.copy(TRUNK_NIGHT).lerp(TRUNK_DAY, day);
    }
  });

  useEffect(
    () => () => {
      grass?.geometry.dispose();
      grass?.material.dispose();
    },
    [grass],
  );

  useEffect(
    () => () => {
      if (!trees) return;
      trees.canopy.dispose();
      trees.trunk.dispose();
      trees.canopy.geometry.dispose();
      trees.trunk.geometry.dispose();
      trees.canopyMat.dispose();
      trees.trunkMat.dispose();
    },
    [trees],
  );

  return (
    <>
      {grass && <mesh geometry={grass.geometry} material={grass.material} position={[0, PARK_Y, 0]} />}
      {trees && (
        <>
          <primitive object={trees.trunk} />
          <primitive object={trees.canopy} />
        </>
      )}
    </>
  );
}
