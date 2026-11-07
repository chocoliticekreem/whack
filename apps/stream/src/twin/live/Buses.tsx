"use client";

import { Label } from "./Label";
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { ATMOS } from "../scene/palette";
import { BUS_H, BUS_L, blindMaterial, getBusKit, setBusGlow } from "./bus/busKit";
import type { AroundBusStop, BusArrival } from "@/platform/around/types";
import type { TwinGeometry } from "../config";
import { toLocal } from "../config";
import { ROAD_Y } from "../network/layout";
import { pathAt, SNAP_ENABLED, snapVehicle, useSnapIndex, walkPath, type Path, type SnapIndex, type VehicleSnap } from "../network/snap";
import type { XZ } from "../network/types";
import { useLive, useNow } from "./store";

const APPROACH_S = 90;
const APPROACH_M = 110;
/** Lane path traced upstream of the stop, so the eased approach never runs off its end. */
const PATH_M = 150;
const DWELL_S = 25;
/** After its dwell a bus pulls away along the road for this long before it is retired. */
const DEPART_S = 45;
const DEPART_ACCEL = 1.2;
const DEPART_MAX_MPS = 10;
/** Lane path traced downstream of the stop for the departure. */
const EXIT_M = 400;
/** Bumper-to-bumper room between buses queued at one stop: 2 m plus a share of their length. */
const BUS_GAP_M = BUS_L + 2 + 0.04 * 2 * BUS_L;
const _xz: XZ = [0, 0];
const _vs: VehicleSnap = { x: 0, z: 0, heading: 0, laneId: -1, s: 0, dist: 0 };
const _frustum = new THREE.Frustum();
const _pv = new THREE.Matrix4();
const _ball = new THREE.Sphere();

interface StopPlace {
  stop: AroundBusStop;
  x: number;
  z: number;
  dirX: number;
  dirZ: number;
  /** Keep-left lane path ending at the stop's kerb-side lane edge; null when the stop is off the network (straight-line fallback). */
  path: Path | null;
  /** Lane path leaving the stop, for the departure. */
  exit: Path | null;
}

/** Major-road lane nearest the stop (any road when none is in reach), with the approach and departure paths along it. */
function stopPaths(snap: SnapIndex, x: number, z: number): Pick<StopPlace, "path" | "exit"> {
  if (!snapVehicle(snap, x, z, NaN, _vs, 30, -1, true) && !snapVehicle(snap, x, z, NaN, _vs, 30)) return { path: null, exit: null };
  return {
    path: walkPath(snap, _vs.laneId, _vs.s, PATH_M, false, true),
    exit: walkPath(snap, _vs.laneId, _vs.s, EXIT_M, true, true),
  };
}

function nearestRoadDir(x: number, z: number, roads: TwinGeometry["roads"]): [number, number] {
  let best = Infinity;
  let dir: [number, number] = [1, 0];
  for (const road of roads) {
    const line = road.line;
    for (let i = 1; i < line.length; i++) {
      const [ax, az] = line[i - 1];
      const [bx, bz] = line[i];
      const vx = bx - ax;
      const vz = bz - az;
      const len2 = vx * vx + vz * vz;
      if (len2 < 1e-6) continue;
      let t = ((x - ax) * vx + (z - az) * vz) / len2;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const dx = ax + vx * t - x;
      const dz = az + vz * t - z;
      const d2 = dx * dx + dz * dz;
      if (d2 < best) {
        best = d2;
        const len = Math.sqrt(len2);
        dir = [vx / len, vz / len];
      }
    }
  }
  return dir;
}

function remainingSeconds(stop: AroundBusStop, a: BusArrival, nowMs: number) {
  return a.seconds - (nowMs - stop.at) / 1000;
}

/** Signed distance short of the stop (negative once it has pulled away) of a bus with `rem` seconds to go. */
function stopDistance(rem: number) {
  if (rem > 0) {
    const k = Math.min(1, rem / APPROACH_S);
    return k * k * APPROACH_M;
  }
  const t = Math.max(0, -DWELL_S - rem);
  const ramp = DEPART_MAX_MPS / DEPART_ACCEL;
  return -(t < ramp ? 0.5 * DEPART_ACCEL * t * t : 0.5 * DEPART_MAX_MPS * ramp + DEPART_MAX_MPS * (t - ramp));
}

/** Where a bus sits once the buses due before it (`ahead`, soonest first) have taken their places: never closer than BUS_GAP_M behind the one in front. */
function queuedDistance(stop: AroundBusStop, ahead: BusArrival[], rem: number, nowMs: number) {
  let floor = -Infinity;
  for (const a of ahead) floor = Math.max(stopDistance(remainingSeconds(stop, a, nowMs)), floor) + BUS_GAP_M;
  return Math.max(stopDistance(rem), floor);
}

function formatDue(seconds: number) {
  if (seconds <= 30) return "due";
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

function Bus({ place, arrival, ahead }: { place: StopPlace; arrival: BusArrival; ahead: BusArrival[] }) {
  const group = useRef<THREE.Group>(null);
  const body = useRef<THREE.Group>(null);
  const kit = getBusKit();
  const blind = blindMaterial(arrival.line, arrival.destination);
  const phase = place.x * 0.37;
  const [label, setLabel] = useState("");
  const lastLabel = useRef(0);
  const straightYaw = Math.atan2(place.dirX, place.dirZ);

  useFrame(() => {
    const g = group.current;
    if (!g) return;
    const now = Date.now();
    const rem = remainingSeconds(place.stop, arrival, now);
    const k = Math.min(1, Math.max(0, rem / APPROACH_S));
    const dist = queuedDistance(place.stop, ahead, rem, now);
    const lane = dist >= 0 ? place.path : place.exit;
    let inFrame = true;
    if (lane) {
      g.rotation.y = pathAt(lane, dist >= 0 ? lane.length - dist : -dist, _xz);
      g.position.set(_xz[0], ROAD_Y, _xz[1]);
      // Past the end of its exit road a bus is retired, but only once nobody can see it.
      if (dist < 0 && -dist >= lane.length) {
        _ball.center.set(_xz[0], 2, _xz[1]);
        _ball.radius = BUS_L;
        inFrame = _frustum.intersectsSphere(_ball);
      }
    } else {
      g.rotation.y = straightYaw;
      g.position.set(place.x - place.dirX * dist, ROAD_Y, place.z - place.dirZ * dist);
    }
    g.visible = inFrame;
    const b = body.current;
    if (b) {
      // Speed is proportional to k (the approach is eased as k^2), so every motion fades out as the bus stops.
      const moving = rem > 0 ? Math.min(1, k * 3) : rem < -DWELL_S ? 1 : 0;
      const brake = rem > 0 ? Math.sin(Math.min(1, k * 5) * Math.PI) : 0;
      b.position.y = Math.sin(now * 0.008 + phase) * 0.012 * moving + Math.sin(now * 0.038 + phase) * 0.0025 * (1 - moving);
      b.rotation.x = Math.sin(now * 0.0061 + phase * 1.7) * 0.0018 * moving + brake * 0.012;
      b.rotation.z = Math.sin(now * 0.0093 + phase * 0.6) * 0.0012 * moving;
    }
    if (now - lastLabel.current > 1000) {
      lastLabel.current = now;
      setLabel(formatDue(rem));
    }
  });

  return (
    <group ref={group}>
      <group ref={body}>
        <mesh geometry={kit.body} material={kit.bodyMat} castShadow />
        <mesh geometry={kit.glass} material={kit.glassMat} />
        <mesh geometry={kit.lamps} material={kit.lampMat} />
        <mesh geometry={kit.blinds} material={blind} />
      </group>
      <Label position={[0, BUS_H + 2.4, 0]} priority={60}>
        <div
          style={{
            fontFamily: "var(--font-figtree), system-ui, sans-serif",
            background: "rgba(200,16,46,0.9)",
            color: "#fff",
            borderRadius: 8,
            padding: "4px 9px",
            fontSize: 12,
            fontWeight: 600,
            whiteSpace: "nowrap",
            boxShadow: "0 0 14px rgba(200,16,46,0.5)",
          }}
        >
          {arrival.line} · {label}
        </div>
      </Label>
    </group>
  );
}

function StopMarker({ place, now }: { place: StopPlace; now: number }) {
  const next = place.stop.arrivals[0];
  return (
    <group position={[place.x, 0, place.z]}>
      <mesh position={[0, 1.6, 0]}>
        <cylinderGeometry args={[0.08, 0.08, 3.2, 6]} />
        <meshStandardMaterial color="#2c2c36" metalness={0.5} />
      </mesh>
      <mesh position={[0, 3.3, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.7, 0.7, 0.12, 20]} />
        <meshStandardMaterial color="#c8102e" emissive="#c8102e" emissiveIntensity={0.6} />
      </mesh>
      <mesh position={[0, 3.3, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.34, 0.34, 0.14, 20]} />
        <meshStandardMaterial color="#f4f3f0" emissive="#f4f3f0" emissiveIntensity={0.4} />
      </mesh>
      <Label position={[0, 4.6, 0]} priority={60}>
        <div
          style={{
            fontFamily: "var(--font-figtree), system-ui, sans-serif",
            background: "rgba(12,12,16,0.72)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "3px 8px",
            color: "var(--text)",
            fontSize: 11,
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontWeight: 600 }}>{place.stop.indicator || "Stop"}</span>
          {next ? (
            <span style={{ color: "var(--muted)" }}>
              {" · "}
              {next.line} {formatDue(remainingSeconds(place.stop, next, now))}
            </span>
          ) : null}
        </div>
      </Label>
    </group>
  );
}

export function Buses() {
  useFrame(({ camera }) => {
    setBusGlow(ATMOS.windowGlow);
    _pv.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    _frustum.setFromProjectionMatrix(_pv);
  });
  const stops = useLive((s) => s.stops);
  const roads = useLive((s) => s.roads);
  const now = useNow();
  const snap = useSnapIndex();

  const places = useMemo<StopPlace[]>(
    () =>
      stops.map((stop) => {
        const [x, z] = toLocal(stop.lat, stop.lon);
        const [dirX, dirZ] = nearestRoadDir(x, z, roads);
        const paths = snap && SNAP_ENABLED ? stopPaths(snap, x, z) : { path: null, exit: null };
        return { stop, x, z, dirX, dirZ, ...paths };
      }),
    [stops, roads, snap],
  );

  const due = useMemo(() => {
    if (!now) return [];
    const out: { place: StopPlace; arrival: BusArrival; ahead: BusArrival[]; key: string }[] = [];
    for (const place of places) {
      // A stop can list the same line and destination twice (back-to-back buses), so the key counts repeats.
      const seen = new Map<string, number>();
      const here: BusArrival[] = [];
      for (const a of [...place.stop.arrivals].sort((p, q) => p.seconds - q.seconds)) {
        const rem = remainingSeconds(place.stop, a, now);
        if (rem < APPROACH_S && rem > -(DWELL_S + DEPART_S)) {
          const base = `${place.stop.id}:${a.line}:${a.destination}`;
          const n = seen.get(base) ?? 0;
          seen.set(base, n + 1);
          out.push({ place, arrival: a, ahead: [...here], key: `${base}:${n}` });
          here.push(a);
        }
      }
    }
    return out;
  }, [places, now]);

  return (
    <group>
      {places.map((p) => (
        <StopMarker key={p.stop.id} place={p} now={now} />
      ))}
      {due.map((d) => (
        <Bus key={d.key} place={d.place} arrival={d.arrival} ahead={d.ahead} />
      ))}
    </group>
  );
}
