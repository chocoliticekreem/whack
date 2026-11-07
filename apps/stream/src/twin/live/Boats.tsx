"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { ATMOS } from "../scene/palette";
import { useGeometry } from "../scene/useGeometry";
import { createFleet, stepFleet, type Fleet } from "./boats/fleet";
import { buildBoat, type BoatKind } from "./boats/models";
import { deriveRiver, river, setRiver } from "./river";

interface KindMeshes {
  hull: THREE.InstancedMesh;
}

interface Rig {
  group: THREE.Group;
  kinds: Map<BoatKind, KindMeshes>;
  hulls: THREE.InstancedMesh[];
  wake: THREE.InstancedMesh;
  materials: { hull: THREE.MeshStandardMaterial; glass: THREE.MeshBasicMaterial; lamps: THREE.MeshBasicMaterial; strobe: THREE.MeshBasicMaterial; wake: THREE.MeshBasicMaterial };
}

const WAKE_Y = 0.2;
const WAKE_FULL_SPEED = 6;
const WAKE_SCALE = 0.4;

/** V-shaped foam wake behind a stern at the origin, trailing toward -z: two arms plus a faint filled wedge and a centre trail. */
function wakeGeometry() {
  const pos: number[] = [];
  const col: number[] = [];
  const vert = (x: number, z: number, b: number) => {
    pos.push(x, 0, z);
    col.push(b * 0.82, b * 0.93, b);
  };
  const quad = (a: [number, number, number], b: [number, number, number], c: [number, number, number], d: [number, number, number]) => {
    for (const v of [a, b, c, a, c, d]) vert(v[0], v[1], v[2]);
  };
  const seg = 8;
  for (let i = 0; i < seg; i++) {
    const z0 = -i / seg;
    const z1 = -(i + 1) / seg;
    const b0 = 0.95 * Math.pow(1 - i / seg, 1.6);
    const b1 = 0.95 * Math.pow(1 - (i + 1) / seg, 1.6);
    for (const s of [-1, 1]) {
      const x0 = s * (0.04 - 0.36 * z0);
      const x1 = s * (0.04 - 0.36 * z1);
      const w0 = 0.02 - 0.03 * z0;
      const w1 = 0.02 - 0.03 * z1;
      quad([x0 - w0, z0, b0], [x0 + w0, z0, b0], [x1 + w1, z1, b1], [x1 - w1, z1, b1]);
    }
    const f0 = 0.16 * (1 - i / seg);
    const f1 = 0.16 * (1 - (i + 1) / seg);
    const e0 = 0.04 - 0.34 * z0;
    const e1 = 0.04 - 0.34 * z1;
    quad([-e0, z0, f0], [e0, z0, f0], [e1, z1, f1], [-e1, z1, f1]);
    const c0 = 0.55 * Math.pow(1 - i / seg, 1.3);
    const c1 = 0.55 * Math.pow(1 - (i + 1) / seg, 1.3);
    quad([-0.025, z0, c0], [0.025, z0, c0], [0.025, z1, c1], [-0.025, z1, c1]);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  return g;
}

function buildRig(fleet: Fleet): Rig {
  const materials = {
    hull: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.15, side: THREE.DoubleSide }),
    glass: new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false }),
    lamps: new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false }),
    strobe: new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false }),
    wake: new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
      polygonOffset: true,
      polygonOffsetFactor: -2,
    }),
  };
  const counts = new Map<BoatKind, number>();
  for (const b of fleet.boats) counts.set(b.kind, Math.max(counts.get(b.kind) ?? 0, b.slot + 1));

  const group = new THREE.Group();
  const kinds = new Map<BoatKind, KindMeshes>();
  counts.forEach((count, kind) => {
    const geo = buildBoat(kind);
    const hull = new THREE.InstancedMesh(geo.hull, materials.hull, count);
    hull.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    hull.frustumCulled = false;
    group.add(hull);
    const attach = (geometry: THREE.BufferGeometry, material: THREE.Material) => {
      const mesh = new THREE.InstancedMesh(geometry, material, count);
      mesh.instanceMatrix = hull.instanceMatrix;
      mesh.frustumCulled = false;
      group.add(mesh);
      return mesh;
    };
    attach(geo.glass, materials.glass);
    attach(geo.lamps, materials.lamps);
    if (geo.strobe) attach(geo.strobe, materials.strobe);
    kinds.set(kind, { hull });
  });

  const wake = new THREE.InstancedMesh(wakeGeometry(), materials.wake, fleet.boats.length);
  wake.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  wake.frustumCulled = false;
  wake.renderOrder = 2;
  group.add(wake);
  return { group, kinds, hulls: Array.from(kinds.values(), (k) => k.hull), wake, materials };
}

const M = new THREE.Matrix4();
const POS = new THREE.Vector3();
const SCL = new THREE.Vector3();
const QUAT = new THREE.Quaternion();
const EULER = new THREE.Euler();
const UP = new THREE.Vector3(0, 1, 0);

/** Writes the sim state into the instance matrices and day/night material colours. */
function drawFleet(fleet: Fleet, rig: Rig) {
  const glow = ATMOS.windowGlow;
  const m = rig.materials;
  m.glass.color.setRGB(0.05 + glow * 1.85, 0.09 + glow * 1.4, 0.12 + glow * 0.73);
  m.lamps.color.setScalar(1.2 + glow * 2.2);
  m.strobe.color.setScalar(Math.floor(fleet.time * 5) % 2 ? 3.2 : 0.2);
  m.wake.color.setScalar(0.45 + 0.25 * glow);

  for (let i = 0; i < fleet.boats.length; i++) {
    const b = fleet.boats[i];
    QUAT.setFromEuler(EULER.set(b.pitch, b.yaw, b.roll, "YXZ"));
    POS.set(b.x, b.y, b.z);
    SCL.setScalar(Math.max(b.fade, 1e-4));
    M.compose(POS, QUAT, SCL);
    rig.kinds.get(b.kind)?.hull.setMatrixAt(b.slot, M);

    const length = b.spec.wake * WAKE_SCALE * b.fade * Math.min(1, Math.max(0, (b.speed - 0.4) / WAKE_FULL_SPEED));
    const sx = Math.sin(b.yaw);
    const sz = Math.cos(b.yaw);
    POS.set(b.x - sx * b.half * 0.9, WAKE_Y, b.z - sz * b.half * 0.9);
    QUAT.setFromAxisAngle(UP, b.yaw);
    SCL.set(length * 0.7, 1, length);
    M.compose(POS, QUAT, SCL);
    rig.wake.setMatrixAt(i, M);
  }
  for (let i = 0; i < rig.hulls.length; i++) rig.hulls[i].instanceMatrix.needsUpdate = true;
  rig.wake.instanceMatrix.needsUpdate = true;
}

export function Boats() {
  const { water } = useGeometry();
  const sim = useMemo(() => {
    setRiver(deriveRiver(water));
    if (!river.length) return null;
    const fleet = createFleet(new URLSearchParams(window.location.search).get("lift") === "1");
    return { fleet, rig: buildRig(fleet) };
  }, [water]);

  useEffect(() => {
    if (!sim) return;
    const { group, materials } = sim.rig;
    return () => {
      group.traverse((o) => {
        if (o instanceof THREE.InstancedMesh) {
          o.geometry.dispose();
          o.dispose();
        }
      });
      Object.values(materials).forEach((m) => m.dispose());
    };
  }, [sim]);

  useFrame(({ clock }, delta) => {
    if (!sim) return;
    const { fleet, rig } = sim;
    stepFleet(fleet, Math.min(delta, 0.1), clock.elapsedTime);
    drawFleet(fleet, rig);
  });

  if (!sim) return null;
  return <primitive object={sim.rig.group} />;
}
