"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useDemoSettings } from "@/platform/demo";
import { toLocal } from "../config";
import { layoutOf, ROAD_Y } from "../network/layout";
import { useNetwork } from "../network/useNetwork";
import { ATMOS } from "../scene/palette";
import { perf } from "../scene/perf";
import { getLastField, useCoveredFrames, useFieldStore } from "../sim/useField";
import { syncClock } from "../compare/store";
import { useScenario } from "./scenario";
import { useTwinCounts } from "./sim/counts";
import { coveredBy, snapToField } from "./sim/field";
import { writeYawMatrix } from "./sim/matrix";
import { beam, cabBody, carBody, lampMaterial, lamps, paintMaterial, vanBody } from "./models/vehicles";
import { ROLE_AMBULANCE, ROLE_NONE, ROLE_POLICE, ROLE_QUARRY, TrafficSim } from "./sim/traffic";
import { useLive } from "./store";

/** Instance capacity per kind; how many are actually out comes from the field. */
const CAPACITY = [260, 44, 56];
/** Share of the estimated fleet kept on the road per performance tier. */
const TIER_SCALE = { high: 1, medium: 0.75, low: 0.5 };
/** Edges whose midpoint is this close to a TfL disruption line take its speed factor. */
const DISRUPTION_M = 20;
const WHITE_C = new THREE.Color(1, 1, 1);
const BLUE = new THREE.Color(0.2, 0.4, 3);
const POLICE_PAINT = new THREE.Color("#f2f4f6");
const AMBULANCE_PAINT = new THREE.Color("#f5e31a");
const QUARRY_PAINT = new THREE.Color("#8a1c1c");
const SEED = 4717;
const GROUND_Y = ROAD_Y;
/** Small pool kept on the road in responders-only mode: enough cars and vans to cast a chase or an ambulance. */
const RESPONDER_POOL = 12;

const PAINTS = [
  "#0d0e12",
  "#1b1f27",
  "#26303f",
  "#3a1418",
  "#d9dce0",
  "#8a9099",
  "#16322a",
  "#1d2c52",
  "#6b7078",
  "#c4c8cc",
  "#7a1c1c",
].map((c) => new THREE.Color(c));
const VAN_PAINTS = ["#d9dce0", "#c4c8cc", "#1d2c52", "#8a9099", "#e8e2d0"].map((c) => new THREE.Color(c));
const CAB_PAINTS = ["#0b0b0e", "#111216", "#15161b"].map((c) => new THREE.Color(c));

const PALETTES = [PAINTS, VAN_PAINTS, CAB_PAINTS];

/** Ambient paint per vehicle, with responders in their livery; lamps go back to white when a role ends. */
function paint(sim: TrafficSim, bodies: THREE.InstancedMesh[], lamp: THREE.InstancedMesh[]) {
  let i = 0;
  for (const v of sim.vehicles) {
    const set = PALETTES[v.kind];
    const c =
      v.role === ROLE_POLICE
        ? POLICE_PAINT
        : v.role === ROLE_AMBULANCE
          ? AMBULANCE_PAINT
          : v.role === ROLE_QUARRY
            ? QUARRY_PAINT
            : set[(i * 7 + (i >> 2)) % set.length];
    bodies[v.kind].setColorAt(v.slot, c);
    if (v.role === ROLE_NONE) lamp[v.kind].setColorAt(v.slot, WHITE_C);
    i++;
  }
  for (const m of [...bodies, ...lamp]) if (m.instanceColor) m.instanceColor.needsUpdate = true;
}

interface StreamerSeen {
  lat: number;
  lon: number;
}

const viewProj = new THREE.Matrix4();
const viewFrustum = new THREE.Frustum();
const viewPlanes = new Float32Array(24);

/** The camera's frustum as six (nx, ny, nz, constant) planes, the form `TrafficSim.update` takes. */
function frustumPlanes(camera: THREE.Camera): Float32Array {
  camera.updateMatrixWorld();
  viewProj.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  viewFrustum.setFromProjectionMatrix(viewProj);
  for (let i = 0; i < 6; i++) {
    const p = viewFrustum.planes[i];
    viewPlanes[i * 4] = p.normal.x;
    viewPlanes[i * 4 + 1] = p.normal.y;
    viewPlanes[i * 4 + 2] = p.normal.z;
    viewPlanes[i * 4 + 3] = p.constant;
  }
  return viewPlanes;
}

function step(
  sim: TrafficSim,
  meshes: { bodies: THREE.InstancedMesh[]; lamp: THREE.InstancedMesh[]; pool: THREE.InstancedMesh },
  lights: THREE.MeshBasicMaterial,
  pool: THREE.MeshBasicMaterial,
  seen: StreamerSeen,
  camera: THREE.Camera,
  dt: number,
  frame: number,
  respondersOnly: boolean,
) {
  const st = useLive.getState().streamer;
  if (st.lat !== seen.lat || st.lon !== seen.lon) {
    seen.lat = st.lat;
    seen.lon = st.lon;
    sim.setFocus(toLocal(st.lat, st.lon));
  }
  sim.update(dt, camera.position.x, camera.position.z, frustumPlanes(camera));
  if (respondersOnly) {
    const scenario = useScenario.getState().scenario;
    if (scenario && sim.active > 0 && !sim.vehicles.some((v) => v.role !== ROLE_NONE)) {
      sim.dispatch(scenario.kind);
      paint(sim, meshes.bodies, meshes.lamp);
    }
  }
  // The compare view shows only the vehicles its camera saw; ambient estimates would drive through them.
  const hideAmbient = respondersOnly || syncClock.camId !== null;
  const poolArr = meshes.pool.instanceMatrix.array;
  const vs = sim.vehicles;
  const strobe = Math.floor(performance.now() / 120) % 2 === 0;
  for (let i = 0; i < vs.length; i++) {
    const v = vs[i];
    // Every car keeps driving; only those in (or just outside) the view are drawn.
    const sc = v.parked || !v.seen || (hideAmbient && v.role === ROLE_NONE) ? 0 : 1;
    writeYawMatrix(meshes.bodies[v.kind].instanceMatrix.array, v.slot * 16, v.x, GROUND_Y, v.z, v.yaw, sc, sc);
    writeYawMatrix(poolArr, i * 16, v.x, GROUND_Y + 0.04, v.z, v.yaw, sc, sc);
    if (v.role === ROLE_POLICE || v.role === ROLE_AMBULANCE) {
      const lamp = meshes.lamp[v.kind];
      lamp.setColorAt(v.slot, strobe ? BLUE : WHITE_C);
      if (lamp.instanceColor) lamp.instanceColor.needsUpdate = true;
    }
  }
  for (const m of meshes.bodies) m.instanceMatrix.needsUpdate = true;
  meshes.pool.instanceMatrix.needsUpdate = true;
  const shown = respondersOnly ? 0 : sim.active;
  if (frame % 60 === 0) {
    if (useTwinCounts.getState().vehicles !== shown) useTwinCounts.setState({ vehicles: shown });
    // Console / Playwright: `__twin.traffic.audit()` counts overlapping bodies now; `.holds`, `.deadlocks`, `.overtakes`, `.yields` are running totals.
    const twin = (window as unknown as { __twin?: { traffic?: TrafficSim } }).__twin;
    if (twin) twin.traffic = sim;
  }
  const night = ATMOS.windowGlow;
  lights.color.setScalar(0.3 + 0.7 * night);
  pool.color.setScalar(night);
  meshes.pool.visible = night > 0.03;
}

/**
 * Arvin's field-driven ambient traffic (an estimate, not real vehicles). In `respondersOnly` mode the
 * ambient population is hidden and only the scenario's responder vehicles are drawn.
 */
export function Traffic({ respondersOnly = false }: { respondersOnly?: boolean }) {
  const net = useNetwork();
  const sim = useMemo(() => {
    const { lat, lon } = useLive.getState().streamer;
    return new TrafficSim(net, CAPACITY, SEED, toLocal(lat, lon), layoutOf(net).hidden);
  }, [net]);
  const streamerSeen = useRef<StreamerSeen>({ lat: NaN, lon: NaN });
  const frame = useRef(0);

  const fieldAt = useFieldStore((s) => s?.at ?? 0);
  const covered = useCoveredFrames((s) => s.frames);
  const crowd = useDemoSettings().crowd;
  const disruptions = useLive((s) => s.disruptions);
  useEffect(() => {
    const field = getLastField();
    if (!field) return;
    const edges = net.edges;
    const snap = snapToField(sim.edgeMid, field);
    const weight = new Float32Array(edges.length);
    const speed = new Float32Array(edges.length).fill(1);
    const lines: { ax: number; az: number; bx: number; bz: number; factor: number }[] = [];
    for (const d of disruptions) {
      const factor = /closed/i.test(d.closure) || /closure/i.test(d.category) ? 0 : 0.5;
      for (let i = 1; i < d.line.length; i++) {
        const [ax, az] = toLocal(d.line[i - 1][1], d.line[i - 1][0]);
        const [bx, bz] = toLocal(d.line[i][1], d.line[i][0]);
        lines.push({ ax, az, bx, bz, factor });
      }
    }
    let target = 0;
    for (let i = 0; i < edges.length; i++) {
      const x = sim.edgeMid[i * 2];
      const z = sim.edgeMid[i * 2 + 1];
      for (const l of lines) {
        const dx = l.bx - l.ax;
        const dz = l.bz - l.az;
        const l2 = dx * dx + dz * dz;
        const t = l2 > 0 ? Math.max(0, Math.min(1, ((x - l.ax) * dx + (z - l.az) * dz) / l2)) : 0;
        if (Math.hypot(l.ax + dx * t - x, l.az + dz * t - z) < DISRUPTION_M) speed[i] = Math.min(speed[i], l.factor);
      }
      const seg = snap[i];
      if (seg < 0 || coveredBy(covered, x, z)) continue;
      weight[i] = field.segments[seg].vehicles;
      target += (weight[i] * edges[i].length) / 100;
    }
    sim.setField(weight, speed, respondersOnly ? RESPONDER_POOL : target * crowd * TIER_SCALE[perf.tier]);
  }, [sim, net, fieldAt, covered, crowd, disruptions, respondersOnly]);

  const shared = useMemo(() => {
    const paint = paintMaterial();
    const lights = lampMaterial();
    const pool = new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      toneMapped: false,
      side: THREE.DoubleSide,
      polygonOffset: true,
      polygonOffsetFactor: -2,
    });
    return {
      paint,
      lights,
      pool,
      bodies: [carBody(), vanBody(), cabBody()],
      lamps: [lamps(4.3, 1.8), lamps(5.3, 2), lamps(4.6, 1.78, 1.82)],
      beam: beam(),
    };
  }, []);

  const meshes = useMemo(() => {
    const per = [0, 0, 0];
    for (const v of sim.vehicles) per[v.kind]++;
    const bodies = shared.bodies.map((g, k) => new THREE.InstancedMesh(g, shared.paint, Math.max(1, per[k])));
    const lamp = shared.lamps.map((g, k) => {
      const m = new THREE.InstancedMesh(g, shared.lights, Math.max(1, per[k]));
      m.instanceMatrix = bodies[k].instanceMatrix;
      return m;
    });
    const pool = new THREE.InstancedMesh(shared.beam, shared.pool, Math.max(1, sim.vehicles.length));
    bodies.forEach((m, k) => (m.count = lamp[k].count = per[k]));
    pool.count = sim.vehicles.length;
    paint(sim, bodies, lamp);
    for (const m of [...bodies, ...lamp, pool]) m.frustumCulled = false;
    pool.renderOrder = 2;
    return { bodies, lamp, pool };
  }, [sim, shared]);

  useEffect(
    () => () => {
      for (const m of [...meshes.bodies, ...meshes.lamp, meshes.pool]) m.dispose();
    },
    [meshes],
  );

  useEffect(
    () => () => {
      shared.paint.dispose();
      shared.lights.dispose();
      shared.pool.dispose();
      shared.beam.dispose();
      [...shared.bodies, ...shared.lamps].forEach((g) => g.dispose());
    },
    [shared],
  );

  const scenario = useScenario((s) => s.scenario);
  useEffect(() => {
    if (scenario) sim.dispatch(scenario.kind);
    else sim.clearDispatch();
    paint(sim, meshes.bodies, meshes.lamp);
  }, [sim, meshes, scenario]);

  useFrame(({ camera }, dt) =>
    step(sim, meshes, shared.lights, shared.pool, streamerSeen.current, camera, dt, frame.current++, respondersOnly),
  );

  return (
    <group>
      {meshes.bodies.map((m, k) => (
        <primitive key={`b${k}`} object={m} />
      ))}
      {meshes.lamp.map((m, k) => (
        <primitive key={`l${k}`} object={m} />
      ))}
      <primitive object={meshes.pool} />
    </group>
  );
}