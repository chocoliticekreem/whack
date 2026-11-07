"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { TrackedClass, TrackedObject } from "@/platform/around/types";
import { ATMOS } from "../scene/palette";
import { BUS_L, getBusKit } from "./bus/busKit";
import { personBody, personHead, personMaterial, SKIN } from "./models/people";
import { bikeBody, CAR_LENGTH, carBody, lampMaterial, lamps, paintMaterial, VAN_LENGTH, vanBody } from "./models/vehicles";
import { camFrame, currentPose, projectInto, sampleInto, setPoseOverride, usePoseVersion, type CamFrame } from "./project";
import { fitPose, useAutofit } from "../compare/autofit";
import { syncClock, useCompare } from "../compare/store";
import { buildingIndexOf, inBuilding, type BuildingIndex } from "../network/buildings";
import { PAVEMENT_Y, ROAD_Y } from "../network/layout";
import { useCameraStore } from "../scene/cameraStore";
import { useGeometryLoader } from "../scene/useGeometry";
import { asphaltMargin, SNAP_STATS, useSnapIndex, type SnapIndex } from "../network/snap";
import { DIMS, MODEL_OF, MotionSim, State, type Entity, type Model } from "./motion";
import { beginFrame, endFrame, recordPose } from "./motionStats";
import { buildReplay, sampleTraj, trajEnded, type ReplayInput, type ReplaySet, type Traj } from "./sim/replay";
import { isVirtualCam } from "@/lib/vcams";
import { useLive } from "./store";

const MAX_PEOPLE = 400;
const MAX_VEHICLES = 200;
const TRAIL_N = 5;
const TRAIL_STEP_S = 0.22;
const FADE_S = 0.6;
const BIKE_LENGTH = 2;
const BIKE_HEIGHT = 1.2;
/** Walking pace at which the figure's bob reaches full amplitude. */
const STRIDE_MS = 1.4;
const GLOW = { value: 0.4 };

/** Used when the YOLO server is older and sends no colour: a London bus is red, everything else neutral. */
const FALLBACK_COLOR: Record<TrackedClass, string> = {
  person: "#c9a27e",
  car: "#6f93c9",
  bus: "#c8102e",
  truck: "#d9d9d9",
  motorcycle: "#3a3a42",
  bicycle: "#3a3a42",
};

const _pose = new Float32Array(3);
const _frustum = new THREE.Frustum();
const _pv = new THREE.Matrix4();
const _ball = new THREE.Sphere();
/** A vehicle is never retired within this distance of the camera that tracked it. */
const KEEP_NEAR_M = 150;

/** True while any part of a vehicle is in the render camera's frustum or within KEEP_NEAR_M of the camera that tracked it. */
function inView(x: number, z: number, length: number, camX: number, camZ: number) {
  _ball.center.set(x, 1.2, z);
  _ball.radius = length;
  return Math.hypot(x - camX, z - camZ) < KEEP_NEAR_M || _frustum.intersectsSphere(_ball);
}

/** A compared camera's track, drawn 1:1 with the real video's clock (which loops). */
interface SyncTrack {
  camId: string;
  key: string;
  frame: CamFrame;
  kind: TrackedObject["kind"];
  cls: TrackedClass;
  color: THREE.Color;
  pts: TrackedObject["pts"];
  t0: number;
  t1: number;
  seed: number;
  model: Model;
  skin: THREE.Color;
}

/**
 * Vehicles drawn, with the checks the twin must pass: none off the asphalt, a bumper gap of at least
 * 2 m between vehicles in a line, and nobody vanishing while in view. Counts accumulate over frames;
 * read via `window.__vehicleAudit` with `?snapstats`.
 */
interface VehicleAudit {
  frames: number;
  vehicleFrames: number;
  offAsphalt: number;
  minBumperGap: number;
  minClearance: number;
  overlaps: number;
  vanished: number;
  vanishedInView: number;
  retired: number;
  /** A few offenders, for diagnosis: key and how far off the asphalt (m) or short of the gap. */
  offKeys: string[];
}
interface AuditPose {
  key: string;
  x: number;
  z: number;
  yaw: number;
  length: number;
  width: number;
  camX: number;
  camZ: number;
  frame: number;
}
const audit: VehicleAudit = { frames: 0, vehicleFrames: 0, offAsphalt: 0, minBumperGap: Infinity, minClearance: Infinity, overlaps: 0, vanished: 0, vanishedInView: 0, retired: 0, offKeys: [] };
const auditPoses = new Map<string, AuditPose>();
let auditSyncT = -1;
let auditSyncCam: string | null = null;
if (SNAP_STATS) (window as unknown as { __vehicleAudit: VehicleAudit }).__vehicleAudit = audit;

function noteDrawn(key: string, x: number, z: number, yaw: number, length: number, width: number, frame: CamFrame) {
  if (!SNAP_STATS) return;
  const e = auditPoses.get(key) ?? { key, x, z, yaw, length, width, camX: frame.x, camZ: frame.z, frame: 0 };
  Object.assign(e, { x, z, yaw, length, width, frame: audit.frames + 1 });
  auditPoses.set(key, e);
}

/** Checks this frame's drawn vehicles, and flags any drawn last frame that are now gone without being retired out of sight. */
function auditVehicles(snap: SnapIndex, retired: string[], syncT: number) {
  const frame = ++audit.frames;
  // A looped video or a switch of compared camera legitimately resets the replayed vehicles.
  const reset = syncClock.camId !== auditSyncCam || (syncT >= 0 && syncT < auditSyncT - 0.5);
  auditSyncT = syncT;
  auditSyncCam = syncClock.camId;
  const drawn: AuditPose[] = [];
  for (const [key, e] of auditPoses) {
    if (e.frame === frame) drawn.push(e);
    else if (e.frame === frame - 1 && !reset) {
      if (retired.includes(key)) audit.retired++;
      else {
        audit.vanished++;
        if (inView(e.x, e.z, e.length, e.camX, e.camZ)) audit.vanishedInView++;
      }
    }
  }
  audit.vehicleFrames += drawn.length;
  for (const a of drawn) {
    const sn = Math.sin(a.yaw);
    const cs = Math.cos(a.yaw);
    let worst = Infinity;
    for (const [lx, lz] of [[0, 0], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const x = a.x + (lx * a.width * cs) / 2 + (lz * a.length * sn) / 2;
      const z = a.z - (lx * a.width * sn) / 2 + (lz * a.length * cs) / 2;
      worst = Math.min(worst, asphaltMargin(snap, x, z));
    }
    if (worst < -0.1) {
      audit.offAsphalt++;
      if (audit.offKeys.length < 12 && !audit.offKeys.some((k) => k.startsWith(a.key))) audit.offKeys.push(`${a.key} ${worst.toFixed(2)} @${a.x.toFixed(0)},${a.z.toFixed(0)}`);
    }
  }
  for (let i = 0; i < drawn.length; i++) {
    for (let j = i + 1; j < drawn.length; j++) {
      const a = drawn[i];
      const b = drawn[j];
      const dx = b.x - a.x;
      const dz = b.z - a.z;
      if (Math.hypot(dx, dz) > 25) continue;
      const lon = dx * Math.sin(a.yaw) + dz * Math.cos(a.yaw);
      const lat = dx * Math.cos(a.yaw) - dz * Math.sin(a.yaw);
      if (Math.abs(lat) < (a.width + b.width) / 2 && Math.cos(a.yaw - b.yaw) > 0.7) {
        const gap = Math.abs(lon) - (a.length + b.length) / 2;
        if (gap < 1.9 && audit.offKeys.length < 12) audit.offKeys.push(`gap ${a.key} ${b.key} ${gap.toFixed(2)}`);
        audit.minBumperGap = Math.min(audit.minBumperGap, gap);
      }
      let sep = -Infinity;
      for (const [yaw, p, q] of [[a.yaw, a, b], [b.yaw, b, a]] as const) {
        for (const quarter of [0, Math.PI / 2]) {
          const ux = Math.sin(yaw + quarter);
          const uz = Math.cos(yaw + quarter);
          const reach = (o: AuditPose) =>
            (o.length / 2) * Math.abs(Math.sin(o.yaw) * ux + Math.cos(o.yaw) * uz) + (o.width / 2) * Math.abs(Math.cos(o.yaw) * ux - Math.sin(o.yaw) * uz);
          sep = Math.max(sep, Math.abs(dx * ux + dz * uz) - reach(p) - reach(q));
        }
      }
      audit.minClearance = Math.min(audit.minClearance, sep);
      if (sep < 0) audit.overlaps++;
    }
  }
}

/** Uniform scale that brings each model to its class's real length. */
const MODEL_SCALE: Record<Exclude<TrackedClass, "person">, number> = {
  car: DIMS.car[2] / CAR_LENGTH,
  truck: DIMS.truck[2] / VAN_LENGTH,
  bus: DIMS.bus[2] / BUS_L,
  motorcycle: DIMS.motorcycle[2] / BIKE_LENGTH,
  bicycle: DIMS.bicycle[2] / BIKE_LENGTH,
};
const BUS_WHITE = new THREE.Color("#ffffff");

const _m = new THREE.Matrix4();
const _p = new THREE.Vector3();
const _q = new THREE.Quaternion();
const _s = new THREE.Vector3();
const _uv = new Float32Array(2);
const _xz = new Float32Array(2);
const _prevXz = new Float32Array(2);
const UP = new THREE.Vector3(0, 1, 0);

const PERSON = new THREE.Color("#ffb37a");
const PERSON_DIM = new THREE.Color("#8a4a2a");
const CAR = new THREE.Color("#6f93c9");
const CAR_DIM = new THREE.Color("#2b3b5c");

interface Slot {
  /** Meshes sharing one instance matrix buffer: the first carries the colour and the matrix. */
  meshes: THREE.InstancedMesh[];
  n: number;
}

interface Scene {
  person: Slot;
  car: Slot;
  van: Slot;
  bus: Slot;
  bike: Slot;
  head: THREE.InstancedMesh;
  tl: THREE.LineSegments;
  pos: Float32Array;
  col: Float32Array;
  seg: number;
}

function placePerson(scene: Scene, x: number, z: number, yaw: number, fade: number, speed: number, seed: number, now: number, color: THREE.Color, skin: THREE.Color) {
  const slot = scene.person;
  if (slot.n >= MAX_PEOPLE) return false;
  const pace = Math.min(1, speed / STRIDE_MS);
  const bob = Math.abs(Math.sin(now * (4 + 6 * pace) + seed * 20)) * 0.04 * pace;
  _q.setFromAxisAngle(UP, yaw);
  _p.set(x, PAVEMENT_Y + bob * fade, z);
  _s.set(fade, fade, fade);
  slot.meshes[0].setMatrixAt(slot.n, _m.compose(_p, _q, _s));
  slot.meshes[0].setColorAt(slot.n, color);
  scene.head.setColorAt(slot.n, skin);
  slot.n++;
  return true;
}

function placeVehicle(scene: Scene, model: Model, cls: TrackedClass, x: number, z: number, yaw: number, fade: number, color: THREE.Color) {
  const slot = scene[model];
  if (slot.n >= MAX_VEHICLES) return false;
  _q.setFromAxisAngle(UP, yaw);
  _p.set(x, ROAD_Y, z);
  const sc = fade * (MODEL_SCALE[cls as keyof typeof MODEL_SCALE] ?? MODEL_SCALE.car);
  _s.set(sc, sc, sc);
  slot.meshes[0].setMatrixAt(slot.n, _m.compose(_p, _q, _s));
  // A London bus is red on the CCTV too; keep Arvin's livery rather than the camera's dim median.
  slot.meshes[0].setColorAt(slot.n, model === "bus" ? BUS_WHITE : color);
  slot.n++;
  return true;
}

function trailSegment(scene: Scene, isPerson: boolean, x0: number, z0: number, x1: number, z1: number, a0: number, a1: number) {
  const { pos, col } = scene;
  const base = isPerson ? PERSON : CAR;
  const dim = isPerson ? PERSON_DIM : CAR_DIM;
  const o = scene.seg * 6;
  const h = isPerson ? 0.9 : 0.6;
  pos[o] = x0;
  pos[o + 1] = h;
  pos[o + 2] = z0;
  pos[o + 3] = x1;
  pos[o + 4] = h;
  pos[o + 5] = z1;
  col[o] = dim.r + (base.r - dim.r) * a0;
  col[o + 1] = dim.g + (base.g - dim.g) * a0;
  col[o + 2] = dim.b + (base.b - dim.b) * a0;
  col[o + 3] = dim.r + (base.r - dim.r) * a1;
  col[o + 4] = dim.g + (base.g - dim.g) * a1;
  col[o + 5] = dim.b + (base.b - dim.b) * a1;
  scene.seg++;
}

/** Live entities: played once, dead-reckoned, never looped. The compared camera's entities are drawn by `drawSynced`. */
function drawEntities(scene: Scene, ents: Entity[], now: number, skipCam: string | null) {
  for (let i = 0; i < ents.length; i++) {
    const e = ents[i];
    if (e.camId === skipCam || e.fade <= 0) continue;
    const isPerson = e.kind === "person";
    const placed = isPerson
      ? placePerson(scene, e.x, e.z, e.yaw, e.fade, e.speed, e.seed, now, e.color, e.skin)
      : placeVehicle(scene, e.model, e.cls, e.x, e.z, e.yaw, e.fade, e.color);
    if (!placed) continue;
    if (!isPerson) noteDrawn(e.key, e.x, e.z, e.yaw, e.length, e.width, e.frame);
    recordPose(e.key, e.x, e.z, isPerson ? -1 : e.laneId, isPerson);
    if (e.state !== State.Tracked) continue;
    const t = now - e.clipStart;
    let px = e.x;
    let pz = e.z;
    for (let k = 1; k <= TRAIL_N; k++) {
      if (!sampleInto(e.pts, t - k * TRAIL_STEP_S, _uv)) break;
      projectInto(e.frame, _uv[0], _uv[1], _prevXz);
      trailSegment(scene, isPerson, px, pz, _prevXz[0], _prevXz[1], e.fade * (1 - (k - 1) / TRAIL_N), e.fade * (1 - k / TRAIL_N));
      px = _prevXz[0];
      pz = _prevXz[1];
    }
  }
}

/** The compared camera stays 1:1 with its (looping) video clock, unsnapped, so figures cross when the real ones do. */
/**
 * A compared vehicle replayed on the road network (see sim/replay.ts): on the asphalt, kept a bumper
 * gap from the rest, and driving on beyond the clip rather than fading out. It only leaves once its
 * route has ended and nobody can see it.
 */
function drawReplayed(scene: Scene, tr: SyncTrack, rep: Traj, syncT: number) {
  if (!sampleTraj(rep, syncT, _pose)) return;
  if (trajEnded(rep, syncT) && !inView(_pose[0], _pose[1], rep.length, rep.camX, rep.camZ)) return;
  if (!placeVehicle(scene, tr.model, tr.cls, _pose[0], _pose[1], _pose[2], 1, tr.color)) return;
  noteDrawn(tr.key, _pose[0], _pose[1], _pose[2], rep.length, rep.width, tr.frame);
  let px = _pose[0];
  let pz = _pose[1];
  for (let k = 1; k <= TRAIL_N; k++) {
    if (!sampleTraj(rep, syncT - k * TRAIL_STEP_S, _pose)) break;
    trailSegment(scene, false, px, pz, _pose[0], _pose[1], 1 - (k - 1) / TRAIL_N, 1 - k / TRAIL_N);
    px = _pose[0];
    pz = _pose[1];
  }
}

/** Compared figures that land inside a building footprint (pose error, or no lane to replay on) are not drawn. */
function drawSynced(scene: Scene, tracks: SyncTrack[], syncT: number, now: number, replay: ReplaySet | null, blds: BuildingIndex | null) {
  for (let i = 0; i < tracks.length; i++) {
    const tr = tracks[i];
    const rep = tr.kind === "person" ? undefined : replay?.byKey.get(tr.key);
    if (rep) {
      drawReplayed(scene, tr, rep, syncT);
      continue;
    }
    if (!sampleInto(tr.pts, syncT, _uv)) continue;
    projectInto(tr.frame, _uv[0], _uv[1], _xz);
    const fade = Math.min(1, (syncT - tr.t0) / FADE_S, (tr.t1 - syncT) / FADE_S);
    if (fade <= 0) continue;
    let yaw = 0;
    let speed = 0;
    let moving = false;
    if (sampleInto(tr.pts, syncT - 0.3, _uv)) {
      projectInto(tr.frame, _uv[0], _uv[1], _prevXz);
      const dx = _xz[0] - _prevXz[0];
      const dz = _xz[1] - _prevXz[1];
      const d2 = dx * dx + dz * dz;
      if (d2 > 1e-4) {
        yaw = Math.atan2(dx, dz);
        speed = Math.sqrt(d2) / 0.3;
        moving = true;
      }
    }
    const isPerson = tr.kind === "person";
    let cx = _xz[0];
    let cz = _xz[1];
    if (blds && inBuilding(blds, cx, cz)) continue;
    if (isPerson) {
      if (!placePerson(scene, cx, cz, yaw, fade, speed, tr.seed, now, tr.color, tr.skin)) continue;
    } else {
      const [w, , l] = DIMS[tr.cls as keyof typeof DIMS] ?? DIMS.car;
      const rx = cx - tr.frame.x;
      const rz = cz - tr.frame.z;
      const rl = Math.hypot(rx, rz) || 1;
      if (!moving) yaw = Math.atan2(rx, rz);
      const along = Math.abs((rx / rl) * Math.sin(yaw) + (rz / rl) * Math.cos(yaw));
      const push = 0.5 * (along * l + Math.sqrt(1 - Math.min(1, along * along)) * w);
      cx += (rx / rl) * push;
      cz += (rz / rl) * push;
      if (blds && inBuilding(blds, cx, cz)) continue;
      if (!placeVehicle(scene, tr.model, tr.cls, cx, cz, yaw, fade, tr.color)) continue;
    }
    let px = cx;
    let pz = cz;
    for (let k = 1; k <= TRAIL_N; k++) {
      if (!sampleInto(tr.pts, syncT - k * TRAIL_STEP_S, _uv)) break;
      projectInto(tr.frame, _uv[0], _uv[1], _prevXz);
      trailSegment(scene, isPerson, px, pz, _prevXz[0], _prevXz[1], fade * (1 - (k - 1) / TRAIL_N), fade * (1 - k / TRAIL_N));
      px = _prevXz[0];
      pz = _prevXz[1];
    }
  }
}

function flush(scene: Scene) {
  for (const slot of [scene.person, scene.car, scene.van, scene.bus, scene.bike]) {
    for (const m of slot.meshes) m.count = slot.n;
    const lead = slot.meshes[0];
    lead.instanceMatrix.needsUpdate = true;
    if (lead.instanceColor) lead.instanceColor.needsUpdate = true;
  }
  if (scene.head.instanceColor) scene.head.instanceColor.needsUpdate = true;
  scene.tl.geometry.setDrawRange(0, scene.seg * 2);
  (scene.tl.geometry.getAttribute("position") as THREE.BufferAttribute).needsUpdate = true;
  (scene.tl.geometry.getAttribute("color") as THREE.BufferAttribute).needsUpdate = true;
}

/** Instanced meshes that share the lead mesh's matrix buffer, so one write per object places every part. */
function makeSlot(parts: [THREE.BufferGeometry, THREE.Material][], capacity: number): Slot {
  const meshes = parts.map(([g, m]) => {
    const mesh = new THREE.InstancedMesh(g, m, capacity);
    mesh.frustumCulled = false;
    mesh.count = 0;
    return mesh;
  });
  for (let i = 1; i < meshes.length; i++) meshes[i].instanceMatrix = meshes[0].instanceMatrix;
  return { meshes, n: 0 };
}

export function Agents() {
  const cams = useLive((s) => s.cams);
  const clips = useLive((s) => s.clips);
  const poseVersion = usePoseVersion((s) => s.v);
  const snap = useSnapIndex();
  const geometry = useGeometryLoader();
  const blds = useMemo(() => (geometry.status === "ready" ? buildingIndexOf(geometry.data) : null), [geometry]);

  const sim = useMemo(() => new MotionSim(), []);
  const compareCamId = useCompare((s) => s.camId);

  useEffect(() => {
    sim.ingest(cams, clips, performance.now() / 1000);
  }, [sim, cams, clips]);

  useEffect(() => {
    if (poseVersion > 0) sim.reframe(cams);
  }, [sim, cams, poseVersion]);

  // Fit the compared camera's pose to the roads once per camera, so its cars land on lanes and the twin lines up with the video.
  const clipAt = compareCamId ? clips[compareCamId]?.at : undefined;
  useEffect(() => {
    if (!compareCamId || !snap || !blds || clipAt === undefined) return;
    const cam = cams.find((c) => c.id === compareCamId);
    const clip = clips[compareCamId];
    // Street-level virtual cams look at the horizon, where a degree of tilt moves a car tens of metres: keep their model pose.
    if (!cam || !clip || isVirtualCam(cam.id) || useAutofit.getState().byCam[cam.id]) return;
    const fit = fitPose(cam, clip, snap, blds, currentPose(cam));
    if (!fit) return;
    useAutofit.setState((s) => ({ byCam: { ...s.byCam, [cam.id]: fit } }));
    setPoseOverride(cam.id, fit.pose);
    useCameraStore.getState().setCctv(camFrame(cam), true);
    // cams/clips are read at fit time; the fit is keyed on the camera and its clip arriving.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareCamId, clipAt, snap, blds]);

  // The compared camera is drawn straight from its clip on the video's clock, so it is memoised here, not simulated.
  const synced = useMemo<SyncTrack[]>(() => {
    const cam = cams.find((c) => c.id === compareCamId);
    const clip = cam ? clips[cam.id] : null;
    if (!cam || !clip || clip.duration <= 0) return [];
    const frame = camFrame(cam);
    const out: SyncTrack[] = [];
    for (const tr of clip.tracks) {
      if (tr.pts.length < 2) continue;
      const cls: TrackedClass = tr.cls ?? (tr.kind === "person" ? "person" : "car");
      out.push({
        camId: cam.id,
        key: `${cam.id}:${tr.id}`,
        frame,
        kind: tr.kind,
        cls,
        color: new THREE.Color(tr.color ?? FALLBACK_COLOR[cls]),
        pts: tr.pts,
        t0: tr.pts[0][0],
        t1: tr.pts[tr.pts.length - 1][0],
        seed: (tr.id * 0.618) % 1,
        model: MODEL_OF[cls],
        skin: SKIN[tr.id % SKIN.length],
      });
    }
    return out;
    // poseVersion: re-project when a camera's pose is adjusted in the compare view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cams, clips, compareCamId, poseVersion]);

  // The compared camera's vehicles are replayed on the road network; a new analysis or pose edit rebuilds it.
  const replay = useMemo<ReplaySet | null>(() => {
    if (!snap) return null;
    const inputs: ReplayInput[] = [];
    for (const tr of synced) {
      if (tr.kind === "person") continue;
      const [width, , length] = DIMS[tr.cls as keyof typeof DIMS] ?? DIMS.car;
      inputs.push({ key: tr.key, frame: tr.frame, pts: tr.pts, length, width, bus: tr.cls === "bus" });
    }
    return buildReplay(snap, inputs);
  }, [snap, synced]);

  const trails = useRef<THREE.LineSegments>(null);

  const trailBuffers = useMemo(() => {
    const n = (MAX_PEOPLE + MAX_VEHICLES) * TRAIL_N * 2;
    return {
      pos: new Float32Array(n * 3),
      col: new Float32Array(n * 3),
    };
  }, []);

  const rig = useMemo(() => {
    const paint = paintMaterial();
    const lights = lampMaterial();
    const clothes = personMaterial(GLOW);
    const kit = getBusKit();
    const geos = {
      body: personBody(),
      head: personHead(),
      car: carBody(),
      carLamps: lamps(CAR_LENGTH, 1.8),
      van: vanBody(),
      vanLamps: lamps(VAN_LENGTH, 2),
      bike: bikeBody(BIKE_LENGTH, BIKE_HEIGHT),
    };
    const person = makeSlot(
      [
        [geos.body, clothes],
        [geos.head, clothes],
      ],
      MAX_PEOPLE,
    );
    const car = makeSlot(
      [
        [geos.car, paint],
        [geos.carLamps, lights],
      ],
      MAX_VEHICLES,
    );
    const van = makeSlot(
      [
        [geos.van, paint],
        [geos.vanLamps, lights],
      ],
      MAX_VEHICLES,
    );
    const bus = makeSlot(
      [
        [kit.body, kit.bodyMat],
        [kit.glass, kit.glassMat],
        [kit.lamps, kit.lampMat],
      ],
      MAX_VEHICLES,
    );
    const bike = makeSlot([[geos.bike, paint]], MAX_VEHICLES);
    return { paint, lights, clothes, geos, person, car, van, bus, bike };
  }, []);

  useEffect(() => {
    return () => {
      for (const s of [rig.person, rig.car, rig.van, rig.bus, rig.bike]) for (const m of s.meshes) m.dispose();
      for (const g of Object.values(rig.geos)) g.dispose();
      rig.paint.dispose();
      rig.lights.dispose();
      rig.clothes.dispose();
    };
  }, [rig]);

  const scene = useRef<Scene | null>(null);

  useFrame(({ clock, camera }, dt) => {
    if (!scene.current) {
      const tl = trails.current;
      if (!tl) return;
      scene.current = {
        person: rig.person,
        car: rig.car,
        van: rig.van,
        bus: rig.bus,
        bike: rig.bike,
        head: rig.person.meshes[1],
        tl,
        pos: trailBuffers.pos,
        col: trailBuffers.col,
        seg: 0,
      };
    }
    const sc = scene.current;
    const video = syncClock.video;
    const ended = video && video.ended && syncClock.endedAt !== null;
    const syncT = !video || video.readyState < 2 ? -1 : ended ? video.duration + (performance.now() - syncClock.endedAt!) / 1000 : video.currentTime;
    const night = ATMOS.windowGlow;
    rig.lights.color.setScalar(0.3 + 0.7 * night);
    GLOW.value = 0.1 + 0.35 * night;
    sc.person.n = sc.car.n = sc.van.n = sc.bus.n = sc.bike.n = 0;
    sc.seg = 0;
    beginFrame();
    _pv.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    _frustum.setFromProjectionMatrix(_pv);
    sim.step(performance.now() / 1000, Math.min(dt, 0.1), snap, (e) => inView(e.x, e.z, e.length, e.frame.x, e.frame.z));
    drawEntities(sc, sim.ents, performance.now() / 1000, syncClock.camId);
    if (syncT >= 0) drawSynced(sc, synced, syncT, clock.elapsedTime, replay, blds);
    if (SNAP_STATS && snap) auditVehicles(snap, sim.retired, syncT);
    endFrame();
    flush(sc);
  });

  return (
    <group>
      {[rig.person, rig.car, rig.van, rig.bus, rig.bike].map((s, i) =>
        s.meshes.map((m, j) => <primitive key={`${i}-${j}`} object={m} />),
      )}
      <lineSegments ref={trails} frustumCulled={false}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[trailBuffers.pos, 3]} />
          <bufferAttribute attach="attributes-color" args={[trailBuffers.col, 3]} />
        </bufferGeometry>
        <lineBasicMaterial
          vertexColors
          transparent
          opacity={0.75}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </lineSegments>
    </group>
  );
}
