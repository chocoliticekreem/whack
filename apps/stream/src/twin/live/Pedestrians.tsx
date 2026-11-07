"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useDemoSettings } from "@/platform/demo";
import { useCompare } from "../compare/store";
import { useNetwork } from "../network/useNetwork";
import { ATMOS } from "../scene/palette";
import { getLastField, useCoveredFrames, useFieldStore } from "../sim/useField";
import { useTwinCounts } from "./sim/counts";
import { coveredBy, snapToField } from "./sim/field";
import { writeYawMatrix } from "./sim/matrix";
import { CLOTHES, personBody, personHead, personMaterial, SKIN } from "./models/people";
import { PedSim } from "./sim/pedestrians";

const CAPACITY = 1400;
const SEED = 9151;
const GLOW = { value: 0.4 };

function step(sim: PedSim, body: THREE.InstancedMesh, head: THREE.InstancedMesh, cam: THREE.Vector3, dt: number, frame: number) {
  sim.update(dt, cam.x, cam.z, frame);
  const arr = body.instanceMatrix.array;
  const peds = sim.peds;
  for (let i = 0; i < sim.active; i++) {
    const p = peds[i];
    if (p.dirty) writeYawMatrix(arr, i * 16, p.x, p.bob, p.z, p.yaw, p.scale, p.scale);
  }
  body.count = head.count = sim.active;
  body.instanceMatrix.needsUpdate = true;
  GLOW.value = 0.1 + 0.35 * ATMOS.windowGlow;
  if (frame % 60 === 0 && useTwinCounts.getState().people !== sim.active) useTwinCounts.setState({ people: sim.active });
}

export function Pedestrians() {
  const net = useNetwork();
  const sim = useMemo(() => new PedSim(net, CAPACITY, SEED), [net]);
  const frame = useRef(0);

  const fieldAt = useFieldStore((s) => s?.at ?? 0);
  const covered = useCoveredFrames((s) => s.frames);
  const crowd = useDemoSettings().crowd;
  useEffect(() => {
    const field = getLastField();
    if (!field) return;
    const snap = snapToField(sim.mids, field);
    const fws = net.footways;
    const weight = new Float32Array(fws.length);
    let target = 0;
    for (let i = 0; i < fws.length; i++) {
      const seg = snap[i];
      if (seg < 0 || coveredBy(covered, sim.mids[i * 2], sim.mids[i * 2 + 1])) continue;
      weight[i] = field.segments[seg].peds;
      target += (weight[i] * fws[i].length) / 100;
    }
    sim.setDensities(weight, target * crowd);
  }, [sim, net, fieldAt, covered, crowd]);

  const shared = useMemo(() => {
    const material = personMaterial(GLOW);
    return { material, body: personBody(), head: personHead() };
  }, []);

  const meshes = useMemo(() => {
    const n = Math.max(1, sim.peds.length);
    const body = new THREE.InstancedMesh(shared.body, shared.material, n);
    const head = new THREE.InstancedMesh(shared.head, shared.material, n);
    head.instanceMatrix = body.instanceMatrix;
    body.count = head.count = 0;
    sim.peds.forEach((p, i) => {
      body.setColorAt(i, CLOTHES[(i * 5 + (i >> 3)) % CLOTHES.length]);
      head.setColorAt(i, SKIN[(i * 3 + (i >> 4)) % SKIN.length]);
      writeYawMatrix(body.instanceMatrix.array, i * 16, p.x, p.bob, p.z, p.yaw, p.scale, p.scale);
    });
    body.frustumCulled = head.frustumCulled = false;
    return { body, head };
  }, [sim, shared]);

  useEffect(
    () => () => {
      meshes.body.dispose();
      meshes.head.dispose();
    },
    [meshes],
  );

  useEffect(
    () => () => {
      shared.material.dispose();
      shared.body.dispose();
      shared.head.dispose();
    },
    [shared],
  );

  useFrame(({ camera }, dt) => {
    step(sim, meshes.body, meshes.head, camera.position, dt, frame.current++);
  });

  // An estimate of the crowd, not people the camera saw: the compare view leaves it out.
  const comparing = useCompare((s) => s.camId !== null);

  return (
    <group visible={!comparing}>
      <primitive object={meshes.body} />
      <primitive object={meshes.head} />
    </group>
  );
}
