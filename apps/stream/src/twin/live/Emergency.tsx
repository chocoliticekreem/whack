"use client";

import { useFrame, type RootState } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { useDemoSettings, type TimeOfDay } from "@/platform/demo";
import { ROAD_Y } from "../network/layout";
import { useNetwork } from "../network/useNetwork";
import { ATMOS } from "../scene/palette";
import { useLive } from "./store";
import { writeYawMatrix } from "./sim/matrix";
import type { Helicopter } from "./sim/chase";
import { CAP, K, KIND_COUNT, LondonDirector, londonHour, parseLife, type Actor, type DirectorContext } from "./sim/london";

const SEED = 20261017;
const GROUND_Y = ROAD_Y;
const HALO_CAP = 260;
const SPILL_CAP = 40;
const ARMS_CAP = 16;
const HOUR_OVERRIDE: Record<TimeOfDay, number> = { day: 13, dusk: 18, night: 23.3 };

type RGB = [number, number, number];

const lin = (hex: number): RGB =>
  [((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255].map((v) => v ** 2.2) as RGB;

const WHITE: RGB = [1, 1, 1];
const GLASS = lin(0x0a1018);
const DARK = lin(0x0b0b0e);
const BLUE = lin(0x1a3fd0);
const YELLOW = lin(0xffd21a);
const HOT_YELLOW: RGB = [3, 2.2, 0.5];
const HEAD: RGB = [3, 2.7, 2.1];
const TAIL: RGB = [2.6, 0.12, 0.06];
const SKIN = lin(0xd9a07a);

function paint(g: THREE.BufferGeometry, [r, gr, b]: RGB) {
  const n = g.getAttribute("position").count;
  const c = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    c[i * 3] = r;
    c[i * 3 + 1] = gr;
    c[i * 3 + 2] = b;
  }
  g.setAttribute("color", new THREE.BufferAttribute(c, 3));
  return g;
}

/** Flat-shaded box; `top` narrows the roof, `nose` / `tail` pull the roof edge in at the front / back. */
function box(
  [w, h, l]: [number, number, number],
  [x, y, z]: [number, number, number],
  color: RGB,
  top = 1,
  nose = 1,
  tail = 1,
  rotX = 0,
) {
  const g = new THREE.BoxGeometry(w, h, l).toNonIndexed();
  const p = g.getAttribute("position") as THREE.BufferAttribute;
  if (top !== 1 || nose !== 1 || tail !== 1) {
    for (let i = 0; i < p.count; i++) {
      if (p.getY(i) > 0) {
        p.setX(i, p.getX(i) * top);
        p.setZ(i, p.getZ(i) * (p.getZ(i) > 0 ? nose : tail));
      }
    }
  }
  if (rotX) g.rotateX(rotX);
  g.translate(x, y, z);
  g.computeVertexNormals();
  return paint(g, color);
}

function cyl(rTop: number, rBot: number, h: number, seg: number, [x, y, z]: [number, number, number], color: RGB, axisX = false) {
  const g = new THREE.CylinderGeometry(rTop, rBot, h, seg).toNonIndexed();
  if (axisX) g.rotateZ(Math.PI / 2);
  g.translate(x, y, z);
  return paint(g, color);
}

function wheels(width: number, bases: number[], r = 0.33, w = 0.24) {
  const out: THREE.BufferGeometry[] = [];
  for (const sx of [-1, 1]) for (const z of bases) out.push(cyl(r, r, w, 10, [(sx * width) / 2, r, z], DARK, true));
  return out;
}

function merge(parts: THREE.BufferGeometry[]) {
  const g = mergeGeometries(parts)!;
  parts.forEach((p) => p.dispose());
  return g;
}

function tri(a: [number, number, number], b: [number, number, number], c: [number, number, number]) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([...a, ...b, ...c]), 3));
  g.setAttribute("normal", new THREE.BufferAttribute(new Float32Array([0, 1, 0, 0, 1, 0, 0, 1, 0]), 3));
  g.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(6), 2));
  return paint(g, WHITE);
}

interface Kit {
  body: THREE.BufferGeometry;
  lamp?: THREE.BufferGeometry;
  lampMode?: "fixed" | "glow" | "skin";
  barL?: THREE.BufferGeometry;
  barR?: THREE.BufferGeometry;
}

function bar(x: number, y: number, z: number, w = 0.5) {
  return merge([box([w, 0.1, 0.28], [x, y, z], WHITE)]);
}

function policeKit(): Kit {
  const panels: THREE.BufferGeometry[] = [];
  for (const sx of [-1, 1]) {
    for (let i = 0; i < 6; i++) panels.push(box([0.05, 0.3, 0.7], [sx * 0.93, 0.62, -1.85 + i * 0.74], i % 2 ? YELLOW : BLUE));
  }
  return {
    body: merge([
      box([1.85, 0.62, 4.6], [0, 0.6, 0], WHITE, 0.96, 0.92, 1),
      box([1.58, 0.55, 2.4], [0, 1.17, -0.25], WHITE, 0.8, 0.62, 0.84),
      box([1.6, 0.34, 2.25], [0, 1.12, -0.25], GLASS, 0.82, 0.64, 0.86),
      box([1.1, 0.08, 0.34], [0, 1.5, -0.2], DARK),
      box([1.82, 0.22, 0.14], [0, 0.4, 2.3], DARK),
      box([1.82, 0.22, 0.14], [0, 0.4, -2.3], DARK),
      ...panels,
      ...wheels(1.9, [-1.45, 1.45]),
    ]),
    barL: bar(0.29, 1.58, -0.2),
    barR: bar(-0.29, 1.58, -0.2),
  };
}

function quarryKit(): Kit {
  return {
    body: merge([
      box([1.8, 0.58, 4.3], [0, 0.58, 0], WHITE, 0.96, 0.92, 1),
      box([1.52, 0.5, 2.1], [0, 1.12, -0.35], WHITE, 0.8, 0.62, 0.9),
      box([1.55, 0.32, 1.95], [0, 1.08, -0.35], GLASS, 0.82, 0.64, 0.92),
      box([1.78, 0.2, 0.14], [0, 0.4, 2.2], DARK),
      box([1.78, 0.2, 0.14], [0, 0.4, -2.2], DARK),
      ...wheels(1.84, [-1.35, 1.35]),
    ]),
    lamp: merge([
      box([0.36, 0.14, 0.06], [0.62, 0.7, 2.17], HEAD),
      box([0.36, 0.14, 0.06], [-0.62, 0.7, 2.17], HEAD),
      box([0.34, 0.12, 0.06], [0.64, 0.74, -2.17], TAIL),
      box([0.34, 0.12, 0.06], [-0.64, 0.74, -2.17], TAIL),
    ]),
    lampMode: "fixed",
  };
}

function ambulanceKit(): Kit {
  return {
    body: merge([
      box([2.05, 0.9, 5.6], [0, 0.85, 0], WHITE, 1, 1, 1),
      box([2.05, 1.2, 3.5], [0, 1.9, -0.95], WHITE, 0.98, 1, 1),
      box([1.92, 0.75, 1.7], [0, 1.45, 1.95], WHITE, 0.9, 0.6, 1),
      box([1.94, 0.45, 1.5], [0, 1.58, 1.97], GLASS, 0.92, 0.62, 1),
      box([2.08, 0.26, 5.0], [0, 0.95, 0], YELLOW),
      box([2.1, 0.12, 5.0], [0, 0.72, 0], lin(0x1e9e4a)),
      box([2.04, 0.2, 0.14], [0, 0.5, 2.8], DARK),
      box([2.04, 0.2, 0.14], [0, 0.5, -2.8], DARK),
      ...wheels(2.05, [-1.9, 1.9], 0.38, 0.28),
    ]),
    barL: bar(0.55, 2.55, 0.8, 0.7),
    barR: bar(-0.55, 2.55, 0.8, 0.7),
  };
}

function fireKit(): Kit {
  const rails: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 9; i++) rails.push(box([0.6, 0.06, 0.06], [0, 2.4, -3.2 + i * 0.75], lin(0x8a8f96)));
  return {
    body: merge([
      box([2.5, 1.2, 7.2], [0, 1.15, -0.2], WHITE, 1, 1, 1),
      box([2.4, 1.35, 2.2], [0, 1.95, 2.55], WHITE, 0.94, 0.8, 1),
      box([2.42, 0.55, 2.0], [0, 2.1, 2.6], GLASS, 0.96, 0.82, 1),
      box([2.54, 0.14, 7.0], [0, 1.4, -0.2], lin(0xeeeeee)),
      box([0.56, 0.2, 6.6], [0, 2.3, -0.6], lin(0x8a8f96)),
      box([0.08, 0.2, 6.6], [0.26, 2.42, -0.6], lin(0xb0b4ba)),
      box([0.08, 0.2, 6.6], [-0.26, 2.42, -0.6], lin(0xb0b4ba)),
      ...rails,
      box([2.45, 0.22, 0.16], [0, 0.5, 3.7], DARK),
      ...wheels(2.4, [-2.4, 0.4, 2.7], 0.45, 0.3),
    ]),
    barL: bar(0.75, 2.88, 2.7, 0.7),
    barR: bar(-0.75, 2.88, 2.7, 0.7),
  };
}

function cabKit(): Kit {
  return {
    body: merge([
      box([1.76, 0.62, 4.6], [0, 0.6, 0], WHITE, 0.96, 0.94, 1),
      box([1.62, 0.95, 2.8], [0, 1.4, -0.4], WHITE, 0.92, 0.78, 0.94),
      box([1.64, 0.58, 2.65], [0, 1.4, -0.4], GLASS, 0.94, 0.8, 0.96),
      box([1.74, 0.22, 0.14], [0, 0.4, 2.3], DARK),
      box([1.74, 0.22, 0.14], [0, 0.4, -2.3], DARK),
      ...wheels(1.8, [-1.45, 1.45]),
    ]),
    lamp: merge([
      box([0.34, 0.14, 0.06], [0.62, 0.68, 2.33], HEAD),
      box([0.34, 0.14, 0.06], [-0.62, 0.68, 2.33], HEAD),
      box([0.3, 0.12, 0.06], [0.64, 0.7, -2.33], TAIL),
      box([0.3, 0.12, 0.06], [-0.64, 0.7, -2.33], TAIL),
      box([0.56, 0.17, 0.22], [0, 1.99, 0.35], HOT_YELLOW),
    ]),
    lampMode: "fixed",
  };
}

function vanKit(): Kit {
  return {
    body: merge([
      box([1.95, 0.55, 5.3], [0, 0.6, 0], WHITE, 0.98, 0.94, 1),
      box([1.95, 1.2, 3.5], [0, 1.5, -0.95], WHITE, 0.97, 1, 1),
      box([1.8, 0.7, 1.7], [0, 1.2, 1.5], WHITE, 0.86, 0.6, 1),
      box([1.82, 0.4, 1.5], [0, 1.28, 1.52], GLASS, 0.88, 0.62, 1),
      box([1.93, 0.22, 0.14], [0, 0.42, 2.65], DARK),
      box([1.93, 0.22, 0.14], [0, 0.42, -2.65], DARK),
      ...wheels(2.0, [-1.75, 1.75]),
    ]),
    lamp: merge([
      box([0.36, 0.14, 0.06], [0.7, 0.66, 2.67], HEAD),
      box([0.36, 0.14, 0.06], [-0.7, 0.66, 2.67], HEAD),
      box([0.3, 0.12, 0.06], [0.72, 0.7, -2.67], TAIL),
      box([0.3, 0.12, 0.06], [-0.72, 0.7, -2.67], TAIL),
    ]),
    lampMode: "fixed",
  };
}

function riderParts(z: number, torsoY: number, lean: number): THREE.BufferGeometry[] {
  return [
    box([0.4, 0.6, 0.27], [0, torsoY, z], lin(0x17171c), 1, 1, 1, lean),
    box([0.22, 0.24, 0.24], [0, torsoY + 0.48, z + lean * 0.4], SKIN),
    box([0.14, 0.7, 0.16], [0.12, torsoY - 0.55, z + 0.1], lin(0x1a1a22)),
    box([0.14, 0.7, 0.16], [-0.12, torsoY - 0.55, z + 0.1], lin(0x1a1a22)),
  ];
}

function mopedKit(): Kit {
  return {
    body: merge([
      box([0.36, 0.5, 1.3], [0, 0.55, 0], WHITE),
      box([0.26, 0.75, 0.2], [0, 0.82, 0.55], WHITE),
      box([0.5, 0.05, 0.05], [0, 1.2, 0.55], DARK),
      cyl(0.27, 0.27, 0.12, 10, [0, 0.27, 0.6], DARK, true),
      cyl(0.27, 0.27, 0.12, 10, [0, 0.27, -0.55], DARK, true),
      ...riderParts(-0.05, 1.3, -0.12),
    ]),
    lamp: merge([box([0.52, 0.5, 0.52], [0, 1.2, -0.78], [1.4, 1.4, 1.4])]),
    lampMode: "glow",
  };
}

function bikeParts(rider: boolean): THREE.BufferGeometry[] {
  const parts = [
    cyl(0.34, 0.34, 0.05, 12, [0, 0.34, 0.55], DARK, true),
    cyl(0.34, 0.34, 0.05, 12, [0, 0.34, -0.55], DARK, true),
    box([0.05, 0.05, 1.0], [0, 0.72, 0], WHITE),
    box([0.05, 0.62, 0.05], [0, 0.55, -0.35], WHITE),
    box([0.05, 0.7, 0.05], [0, 0.7, 0.45], WHITE),
    box([0.5, 0.04, 0.04], [0, 1.06, 0.5], DARK),
    box([0.1, 0.05, 0.22], [0, 0.98, -0.38], DARK),
  ];
  if (rider) parts.push(...riderParts(-0.1, 1.38, 0.35));
  return parts;
}

function bikeKit(): Kit {
  return { body: merge([...bikeParts(true), box([0.4, 0.24, 0.3], [0, 1.0, 0.6], WHITE)]) };
}

function courierKit(): Kit {
  return {
    body: merge(bikeParts(true)),
    lamp: merge([box([0.46, 0.46, 0.46], [0, 1.28, -0.62], [1.4, 1.4, 1.4])]),
    lampMode: "glow",
  };
}

function scooterKit(): Kit {
  return {
    body: merge([
      box([0.2, 0.06, 0.95], [0, 0.16, -0.05], WHITE),
      box([0.04, 0.95, 0.04], [0, 0.65, 0.45], WHITE),
      box([0.5, 0.04, 0.04], [0, 1.1, 0.45], DARK),
      cyl(0.13, 0.13, 0.06, 8, [0, 0.13, 0.5], DARK, true),
      cyl(0.13, 0.13, 0.06, 8, [0, 0.13, -0.5], DARK, true),
      ...riderParts(-0.1, 1.26, -0.05),
    ]),
  };
}

function sweeperKit(): Kit {
  return {
    body: merge([
      box([2.0, 1.5, 4.0], [0, 1.15, -0.4], WHITE),
      box([1.9, 1.1, 1.5], [0, 1.0, 1.9], WHITE, 0.9, 0.7, 1),
      box([1.92, 0.5, 1.3], [0, 1.4, 1.92], GLASS, 0.92, 0.72, 1),
      box([0.3, 0.25, 2.2], [1.1, 0.25, 0.8], lin(0x16161a)),
      box([2.04, 0.2, 0.14], [0, 0.45, 2.7], DARK),
      ...wheels(1.9, [-1.6, 1.8], 0.42, 0.28),
    ]),
    lamp: merge([box([0.3, 0.16, 0.3], [0, 1.82, 1.8], [3, 1.4, 0.2])]),
    lampMode: "fixed",
  };
}

function excavatorKit(): Kit {
  const cab = lin(0xf2b705);
  return {
    body: merge([
      box([0.8, 0.7, 3.4], [1.25, 0.4, 0], DARK),
      box([0.8, 0.7, 3.4], [-1.25, 0.4, 0], DARK),
      box([2.7, 0.9, 2.8], [0, 1.15, -0.1], WHITE),
      box([1.3, 1.2, 1.3], [0.55, 2.0, 0.55], WHITE, 0.9, 0.8, 1),
      box([1.2, 0.7, 1.15], [0.57, 2.05, 0.6], GLASS, 0.92, 0.82, 1),
      box([0.4, 0.4, 3.6], [-0.35, 2.3, 2.4], cab, 1, 1, 1, -0.55),
      box([0.3, 0.3, 2.6], [-0.35, 2.4, 4.4], cab, 1, 1, 1, 0.5),
      box([0.8, 0.7, 0.8], [-0.35, 1.5, 5.5], lin(0x3a3a40)),
    ]),
  };
}

function humanKit(): Kit {
  return {
    body: merge([
      box([0.46, 0.64, 0.27], [0, 1.12, 0], WHITE),
      box([0.1, 0.56, 0.13], [0.29, 1.1, 0], WHITE),
      box([0.1, 0.56, 0.13], [-0.29, 1.1, 0], WHITE),
      box([0.17, 0.8, 0.21], [0.11, 0.4, 0], lin(0x15151a)),
      box([0.17, 0.8, 0.21], [-0.11, 0.4, 0], lin(0x15151a)),
    ]),
    lamp: merge([box([0.21, 0.25, 0.23], [0, 1.6, 0], WHITE)]),
    lampMode: "skin",
  };
}

function umbrellaKit(): Kit {
  const canopy = new THREE.ConeGeometry(0.66, 0.3, 10, 1, true).toNonIndexed();
  canopy.translate(0, 2.2, 0);
  const grey = lin(0x2a2a30);
  return { body: merge([paint(canopy, WHITE), cyl(0.015, 0.015, 0.8, 4, [0, 1.8, 0], grey)]) };
}

function birdKit(): Kit {
  const parts: THREE.BufferGeometry[] = [
    box([0.12, 0.1, 0.26], [0, 0.1, 0], WHITE),
    box([0.08, 0.08, 0.08], [0, 0.17, 0.15], WHITE),
    tri([0.05, 0.11, 0.1], [0.05, 0.11, -0.1], [0.4, 0.11, -0.02]),
    tri([-0.05, 0.11, 0.1], [-0.4, 0.11, -0.02], [-0.05, 0.11, -0.1]),
  ];
  return { body: merge(parts) };
}

function coneKit(): Kit {
  return {
    body: merge([
      box([0.42, 0.04, 0.42], [0, 0.02, 0], DARK),
      cyl(0.04, 0.17, 0.6, 8, [0, 0.34, 0], lin(0xff5a0a)),
      cyl(0.1, 0.125, 0.1, 8, [0, 0.3, 0], lin(0xf4f4f4)),
    ]),
  };
}

function barrierKit(): Kit {
  const parts: THREE.BufferGeometry[] = [];
  for (let i = 0; i < 6; i++) {
    const c = i % 2 ? lin(0xf4f4f4) : lin(0xd01a1a);
    parts.push(box([0.3, 0.2, 0.05], [-0.75 + i * 0.3, 0.9, 0], c));
    parts.push(box([0.3, 0.2, 0.05], [-0.75 + i * 0.3, 0.6, 0], i % 2 ? lin(0xd01a1a) : lin(0xf4f4f4)));
  }
  parts.push(box([0.06, 0.95, 0.4], [-0.8, 0.47, 0], DARK), box([0.06, 0.95, 0.4], [0.8, 0.47, 0], DARK));
  return { body: merge(parts) };
}

function buildKits(): Kit[] {
  const kits: Kit[] = new Array(KIND_COUNT);
  kits[K.POLICE] = policeKit();
  kits[K.QUARRY] = quarryKit();
  kits[K.AMBULANCE] = ambulanceKit();
  kits[K.FIRE] = fireKit();
  kits[K.CAB] = cabKit();
  kits[K.VAN] = vanKit();
  kits[K.MOPED] = mopedKit();
  kits[K.BIKE] = bikeKit();
  kits[K.COURIER] = courierKit();
  kits[K.SCOOTER] = scooterKit();
  kits[K.SWEEPER] = sweeperKit();
  kits[K.EXCAVATOR] = excavatorKit();
  kits[K.HUMAN] = humanKit();
  kits[K.UMBRELLA] = umbrellaKit();
  kits[K.BIRD] = birdKit();
  kits[K.CONE] = coneKit();
  kits[K.BARRIER] = barrierKit();
  return kits;
}

interface Light {
  x: number;
  y: number;
  z: number;
  /** 0 steady, 1 left flasher, 2 right flasher, 3 slow beacon. */
  mode: number;
  color: RGB;
  size: number;
  /** Only shown when it is dark enough. */
  night?: boolean;
}

const POLICE_BLUE: RGB = [0.1, 0.32, 1];
const POLICE_RED: RGB = [1, 0.1, 0.06];
const AMBER: RGB = [1, 0.55, 0.12];

const LIGHTS: Record<number, Light[]> = {
  [K.POLICE]: [
    { x: 0.29, y: 1.68, z: -0.2, mode: 1, color: POLICE_BLUE, size: 3.4 },
    { x: -0.29, y: 1.68, z: -0.2, mode: 2, color: POLICE_RED, size: 3.4 },
    { x: 0.55, y: 0.78, z: 2.3, mode: 2, color: POLICE_BLUE, size: 1.8 },
    { x: -0.55, y: 0.78, z: 2.3, mode: 1, color: POLICE_RED, size: 1.8 },
    { x: 0.5, y: 1.15, z: -2.2, mode: 2, color: POLICE_BLUE, size: 1.6 },
    { x: -0.5, y: 1.15, z: -2.2, mode: 1, color: POLICE_RED, size: 1.6 },
  ],
  [K.AMBULANCE]: [
    { x: 0.55, y: 2.65, z: 0.8, mode: 1, color: POLICE_BLUE, size: 3.6 },
    { x: -0.55, y: 2.65, z: 0.8, mode: 2, color: POLICE_BLUE, size: 3.6 },
    { x: 0.8, y: 0.85, z: 2.85, mode: 2, color: POLICE_BLUE, size: 1.8 },
    { x: -0.8, y: 0.85, z: 2.85, mode: 1, color: POLICE_BLUE, size: 1.8 },
    { x: 0.8, y: 2.4, z: -2.8, mode: 1, color: POLICE_RED, size: 1.8 },
    { x: -0.8, y: 2.4, z: -2.8, mode: 2, color: POLICE_RED, size: 1.8 },
  ],
  [K.FIRE]: [
    { x: 0.75, y: 2.98, z: 2.7, mode: 1, color: POLICE_BLUE, size: 3.8 },
    { x: -0.75, y: 2.98, z: 2.7, mode: 2, color: POLICE_BLUE, size: 3.8 },
    { x: 1.1, y: 2.0, z: -3.5, mode: 2, color: POLICE_BLUE, size: 2 },
    { x: -1.1, y: 2.0, z: -3.5, mode: 1, color: POLICE_BLUE, size: 2 },
  ],
  [K.SWEEPER]: [{ x: 0, y: 1.95, z: 1.8, mode: 3, color: AMBER, size: 2.6 }],
  [K.CAB]: [{ x: 0, y: 2.05, z: 0.35, mode: 0, color: [1, 0.78, 0.2], size: 1.1 }],
  [K.QUARRY]: [
    { x: 0.62, y: 0.7, z: 2.3, mode: 0, color: [1, 0.95, 0.8], size: 1.6, night: true },
    { x: -0.62, y: 0.7, z: 2.3, mode: 0, color: [1, 0.95, 0.8], size: 1.6, night: true },
  ],
};

function halo(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.12, "rgba(255,255,255,0.85)");
  g.addColorStop(0.35, "rgba(255,255,255,0.25)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

function instanced(geo: THREE.BufferGeometry, mat: THREE.Material, cap: number, matrixFrom?: THREE.InstancedMesh) {
  const m = new THREE.InstancedMesh(geo, mat, Math.max(1, cap));
  if (matrixFrom) m.instanceMatrix = matrixFrom.instanceMatrix;
  else m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  m.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(1, cap) * 3), 3);
  m.instanceColor.setUsage(THREE.DynamicDrawUsage);
  m.count = 0;
  m.frustumCulled = false;
  return m;
}

interface KindMeshes {
  body: THREE.InstancedMesh;
  lamp?: THREE.InstancedMesh;
  lampMode?: Kit["lampMode"];
  barL?: THREE.InstancedMesh;
  barR?: THREE.InstancedMesh;
}

function buildMeshes(kits: Kit[]) {
  const paintMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.38, metalness: 0.18 });
  const birdMat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
  birdMat.onBeforeCompile = (sh) => {
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float aFlap;")
      .replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\nif (abs(position.x) > 0.06) transformed.y += abs(position.x) * aFlap * 1.5;",
      );
  };
  const lit = new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false });
  const glowMat = new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false });
  const skinMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.8 });
  const barMat = new THREE.MeshBasicMaterial({ toneMapped: false });
  const kinds: (KindMeshes | undefined)[] = new Array(KIND_COUNT);
  for (let k = 0; k < KIND_COUNT; k++) {
    const kit = kits[k];
    const cap = CAP[k];
    const body = instanced(kit.body, k === K.BIRD ? birdMat : paintMat, cap);
    const km: KindMeshes = { body };
    if (kit.lamp) {
      const mat = kit.lampMode === "skin" ? skinMat : kit.lampMode === "glow" ? glowMat : lit;
      km.lamp = instanced(kit.lamp, mat, cap, body);
      km.lampMode = kit.lampMode;
    }
    if (kit.barL && kit.barR) {
      km.barL = instanced(kit.barL, barMat, cap, body);
      km.barR = instanced(kit.barR, barMat, cap, body);
    }
    kinds[k] = km;
  }
  const flap = new THREE.InstancedBufferAttribute(new Float32Array(CAP[K.BIRD]), 1);
  flap.setUsage(THREE.DynamicDrawUsage);
  kits[K.BIRD].body.setAttribute("aFlap", flap);

  const armsGeo = merge([
    box([0.1, 0.6, 0.13], [0.26, 1.72, 0], WHITE),
    box([0.1, 0.6, 0.13], [-0.26, 1.72, 0], WHITE),
  ]);
  const arms = instanced(armsGeo, paintMat, ARMS_CAP);

  const tex = halo();
  const haloMat = new THREE.MeshBasicMaterial({
    map: tex,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    toneMapped: false,
  });
  const haloMesh = instanced(new THREE.PlaneGeometry(1, 1), haloMat, HALO_CAP);
  haloMesh.renderOrder = 3;
  const spillGeo = new THREE.PlaneGeometry(1, 1);
  spillGeo.rotateX(-Math.PI / 2);
  const spillMat = new THREE.MeshBasicMaterial({
    map: tex,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    toneMapped: false,
    polygonOffset: true,
    polygonOffsetFactor: -3,
  });
  const spill = instanced(spillGeo, spillMat, SPILL_CAP);
  spill.renderOrder = 2;
  return {
    kinds,
    arms,
    haloMesh,
    spill,
    flap,
    mats: { paintMat, birdMat, lit, glowMat, skinMat, barMat, haloMat, spillMat },
    tex,
    armsGeo,
    spillGeo,
  };
}

const _m = new THREE.Matrix4();
const _p = new THREE.Vector3();
const _s = new THREE.Vector3();

function flashState(clock: number, phase: number) {
  const f = (clock * 6 + phase * 3) % 1;
  return (f < 0.34 ? 1 : 0) | ((f >= 0.5 && f < 0.84 ? 1 : 0) << 1);
}

interface HeliParts {
  hull: THREE.MeshStandardMaterial;
  stripe: THREE.MeshStandardMaterial;
  beamGeo: THREE.ConeGeometry;
  beamMat: THREE.MeshBasicMaterial;
  poolGeo: THREE.CircleGeometry;
  poolMat: THREE.MeshBasicMaterial;
}

interface HeliRefs {
  group: THREE.Group;
  rotor: THREE.Object3D;
  beam: THREE.Mesh;
  pool: THREE.Mesh;
}

function Helicopter({ director, refs }: { director: LondonDirector; refs: React.RefObject<HeliRefs | null> }) {
  const parts = useMemo(() => {
    const hull = new THREE.MeshStandardMaterial({ color: "#10151f", roughness: 0.4, metalness: 0.4 });
    const stripe = new THREE.MeshStandardMaterial({ color: "#e8c020", roughness: 0.5 });
    const beamGeo = new THREE.ConeGeometry(1, 1, 24, 1, true);
    beamGeo.translate(0, -0.5, 0);
    beamGeo.rotateX(-Math.PI / 2);
    const beamMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(0.7, 0.82, 1),
      transparent: true,
      opacity: 0.1,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      toneMapped: false,
    });
    const poolGeo = new THREE.CircleGeometry(1, 28);
    poolGeo.rotateX(-Math.PI / 2);
    const poolMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(0.8, 0.9, 1),
      transparent: true,
      opacity: 0.3,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      toneMapped: false,
      polygonOffset: true,
      polygonOffsetFactor: -4,
    });
    return { hull, stripe, beamGeo, beamMat, poolGeo, poolMat };
  }, []);

  useEffect(
    () => () => {
      Object.values(parts).forEach((v) => v.dispose());
    },
    [parts],
  );

  const group = useRef<THREE.Group>(null);
  const rotor = useRef<THREE.Group>(null);
  const beam = useRef<THREE.Mesh>(null);
  const pool = useRef<THREE.Mesh>(null);

  useFrame((_, dt) => {
    const g = group.current;
    const b = beam.current;
    const p = pool.current;
    const r = rotor.current;
    if (g && b && p && r) drawHeli(director.heli(), { g, b, p, r }, parts, dt);
  });
  void refs;

  return (
    <>
      <group ref={group} visible={false}>
        <mesh material={parts.hull} scale={[1.1, 1.05, 2.1]} position={[0, 0, 0.3]}>
          <sphereGeometry args={[1, 12, 8]} />
        </mesh>
        <mesh material={parts.hull} position={[0, 0.25, -3.4]}>
          <boxGeometry args={[0.3, 0.35, 4.6]} />
        </mesh>
        <mesh material={parts.stripe} position={[0, 0.2, -2.2]}>
          <boxGeometry args={[0.34, 0.1, 1.6]} />
        </mesh>
        <mesh material={parts.hull} position={[0, 1.1, -5.4]}>
          <boxGeometry args={[0.1, 1.3, 0.8]} />
        </mesh>
        <mesh material={parts.hull} position={[0, 1.15, 0]}>
          <cylinderGeometry args={[0.12, 0.12, 0.5, 6]} />
        </mesh>
        <group ref={rotor} position={[0, 1.45, 0]}>
          <mesh material={parts.hull}>
            <boxGeometry args={[11, 0.04, 0.3]} />
          </mesh>
          <mesh material={parts.hull}>
            <boxGeometry args={[0.3, 0.04, 11]} />
          </mesh>
        </group>
        <mesh material={parts.hull} position={[0.9, -1.1, 0.3]}>
          <boxGeometry args={[0.08, 0.08, 3]} />
        </mesh>
        <mesh material={parts.hull} position={[-0.9, -1.1, 0.3]}>
          <boxGeometry args={[0.08, 0.08, 3]} />
        </mesh>
      </group>
      <mesh ref={beam} geometry={parts.beamGeo} material={parts.beamMat} visible={false} frustumCulled={false} renderOrder={4} />
      <mesh ref={pool} geometry={parts.poolGeo} material={parts.poolMat} visible={false} frustumCulled={false} renderOrder={4} />
    </>
  );
}

function drawHeli(
  h: Helicopter | null,
  { g, b, p, r }: { g: THREE.Group; b: THREE.Mesh; p: THREE.Mesh; r: THREE.Object3D },
  parts: HeliParts,
  dt: number,
) {
  g.visible = b.visible = p.visible = !!h;
    if (!h) return;
    g.position.set(h.x, h.y, h.z);
    g.rotation.y = h.yaw;
    r.rotation.y += dt * 38;
    const night = 0.35 + 0.65 * ATMOS.windowGlow;
    b.position.set(h.x, h.y - 0.9, h.z);
    const dx = h.lx - h.x;
    const dz = h.lz - h.z;
    const dist = Math.hypot(dx, dz, h.y);
    b.lookAt(h.lx, 0.2, h.lz);
    const radius = dist * 0.13;
    b.scale.set(radius, radius, dist);
    parts.beamMat.opacity = 0.05 + 0.13 * night;
    p.position.set(h.lx, 0.12, h.lz);
    p.scale.setScalar(radius * 1.05);
    parts.poolMat.opacity = 0.12 + 0.3 * night;
}

type LifeKit = ReturnType<typeof buildMeshes> & { kits: Kit[] };

function Life({ director }: { director: LondonDirector }) {
  const settings = useDemoSettings();
  const disruptions = useLive((s) => s.disruptions);
  const settingsRef = useRef(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);
  const clock = useRef({ hour: 12, at: -1e9 });
  const ctx = useMemo<DirectorContext>(() => ({ hour: 12, rain: 0, focus: [0, 0] }), []);
  const heliRefs = useRef<HeliRefs | null>(null);

  const kit = useMemo(() => {
    const kits = buildKits();
    return { kits, ...buildMeshes(kits) };
  }, []);

  useEffect(() => {
    director.setDisruptions(disruptions);
  }, [director, disruptions]);

  useEffect(
    () => () => {
      for (const km of kit.kinds) {
        if (!km) continue;
        for (const m of [km.body, km.lamp, km.barL, km.barR]) m?.dispose();
      }
      kit.arms.dispose();
      kit.haloMesh.dispose();
      kit.spill.dispose();
      kit.kits.forEach((k) => {
        k.body.dispose();
        k.lamp?.dispose();
        k.barL?.dispose();
        k.barR?.dispose();
      });
      Object.values(kit.mats).forEach((m) => m.dispose());
      kit.tex.dispose();
      kit.armsGeo.dispose();
      kit.spillGeo.dispose();
    },
    [kit],
  );

  useFrame((state, dtRaw) => drawLife(director, kit, ctx, clock.current, settingsRef.current.time, state, dtRaw));

  return (
    <group>
      {kit.kinds.map((km, k) =>
        km ? (
          <group key={k}>
            <primitive object={km.body} />
            {km.lamp && <primitive object={km.lamp} />}
            {km.barL && <primitive object={km.barL} />}
            {km.barR && <primitive object={km.barR} />}
          </group>
        ) : null,
      )}
      <primitive object={kit.arms} />
      <primitive object={kit.spill} />
      <primitive object={kit.haloMesh} />
      <Helicopter director={director} refs={heliRefs} />
    </group>
  );
}

function drawLife(
  director: LondonDirector,
  kit: LifeKit,
  ctx: DirectorContext,
  ck: { hour: number; at: number },
  override: TimeOfDay | null,
  state: RootState,
  dtRaw: number,
) {
  const dt = Math.min(dtRaw, 0.05);
  const now = state.clock.elapsedTime;
  if (override) ck.hour = HOUR_OVERRIDE[override];
  else if (now - ck.at > 5) {
    ck.at = now;
    ck.hour = londonHour();
  }
  const cam = state.camera.position;
  ctx.hour = ck.hour;
  ctx.rain = ATMOS.rain;
  ctx.focus[0] = cam.x;
  ctx.focus[1] = cam.z;
  director.update(dt, ctx);

  const night = ATMOS.windowGlow;
  const dark = 0.35 + 0.65 * night;
  const pool = director.pool;
  const q = state.camera.quaternion;
  const haloArr = kit.haloMesh.instanceMatrix.array as Float32Array;
  const haloCol = kit.haloMesh.instanceColor!.array as Float32Array;
  const spillArr = kit.spill.instanceMatrix.array as Float32Array;
  const spillCol = kit.spill.instanceColor!.array as Float32Array;
  let halos = 0;
  let spills = 0;
  let arms = 0;
  const armsArr = kit.arms.instanceMatrix.array as Float32Array;
  const armsCol = kit.arms.instanceColor!.array as Float32Array;
  const flapArr = kit.flap.array as Float32Array;

  const pushHalo = (x: number, y: number, z: number, size: number, r: number, g: number, b: number) => {
    if (halos >= HALO_CAP) return;
    _p.set(x, y, z);
    _s.set(size, size, 1);
    _m.compose(_p, q, _s);
    _m.toArray(haloArr, halos * 16);
    haloCol[halos * 3] = r;
    haloCol[halos * 3 + 1] = g;
    haloCol[halos * 3 + 2] = b;
    halos++;
  };

  kit.mats.lit.color.setScalar(dark);
  kit.mats.glowMat.color.setScalar(0.5 + 0.5 * night);

  for (let k = 0; k < KIND_COUNT; k++) {
    const km = kit.kinds[k]!;
    const list = pool[k];
    const lights = LIGHTS[k];
    const bodyArr = km.body.instanceMatrix.array as Float32Array;
    const bodyCol = km.body.instanceColor!.array as Float32Array;
    const lampCol = km.lamp?.instanceColor!.array as Float32Array | undefined;
    const barLCol = km.barL?.instanceColor!.array as Float32Array | undefined;
    const barRCol = km.barR?.instanceColor!.array as Float32Array | undefined;
    let n = 0;
    for (let i = 0; i < list.length; i++) {
      const a: Actor = list[i];
      if (!a.live) continue;
      let y = a.y + GROUND_Y;
      if (k === K.HUMAN && a.walk > 0) y += Math.abs(Math.sin(now * 8 + a.phase * 6.28)) * 0.05 * a.walk;
      else if (k === K.BIRD) y = a.y;
      writeYawMatrix(bodyArr, n * 16, a.x, y, a.z, a.yaw, a.scale, a.scale);
      bodyCol[n * 3] = a.tr;
      bodyCol[n * 3 + 1] = a.tg;
      bodyCol[n * 3 + 2] = a.tb;
      if (lampCol) {
        if (km.lampMode === "fixed") {
          lampCol[n * 3] = lampCol[n * 3 + 1] = lampCol[n * 3 + 2] = 1;
        } else {
          const boost = km.lampMode === "glow" ? 2.2 : 1;
          lampCol[n * 3] = a.gr * boost;
          lampCol[n * 3 + 1] = a.gg * boost;
          lampCol[n * 3 + 2] = a.gb * boost;
        }
      }
      if (k === K.BIRD) flapArr[n] = a.flap;
      if (k === K.HUMAN && a.hands && arms < ARMS_CAP) {
        armsArr.set(bodyArr.subarray(n * 16, n * 16 + 16), arms * 16);
        armsCol[arms * 3] = a.tr;
        armsCol[arms * 3 + 1] = a.tg;
        armsCol[arms * 3 + 2] = a.tb;
        arms++;
      }
      const c = Math.cos(a.yaw) * a.scale;
      const s = Math.sin(a.yaw) * a.scale;
      if (a.siren && barLCol && barRCol) {
        const f = flashState(now, a.phase);
        const polOn = k === K.POLICE;
        const lc: RGB = POLICE_BLUE;
        const rc: RGB = polOn ? POLICE_RED : POLICE_BLUE;
        const l = f & 1 ? 4 : 0.03;
        const r = f & 2 ? 4 : 0.03;
        barLCol[n * 3] = lc[0] * l;
        barLCol[n * 3 + 1] = lc[1] * l;
        barLCol[n * 3 + 2] = lc[2] * l;
        barRCol[n * 3] = rc[0] * r;
        barRCol[n * 3 + 1] = rc[1] * r;
        barRCol[n * 3 + 2] = rc[2] * r;
        if (spills < SPILL_CAP) {
          const lit = f & 1 ? lc : f & 2 ? rc : null;
          if (lit) {
            writeYawMatrix(spillArr, spills * 16, a.x, 0.1, a.z, 0, 15 * a.scale, 1);
            const gain = (0.12 + 0.5 * night) * a.scale;
            spillCol[spills * 3] = lit[0] * gain;
            spillCol[spills * 3 + 1] = lit[1] * gain;
            spillCol[spills * 3 + 2] = lit[2] * gain;
            spills++;
          }
        }
      } else if (barLCol && barRCol) {
        barLCol[n * 3] = barLCol[n * 3 + 1] = barLCol[n * 3 + 2] = 0.02;
        barRCol[n * 3] = barRCol[n * 3 + 1] = barRCol[n * 3 + 2] = 0.02;
      }
      if (lights && a.scale > 0.05) {
        const f = a.siren ? flashState(now, a.phase) : 0;
        for (let j = 0; j < lights.length; j++) {
          const L = lights[j];
          if (L.night && night < 0.4) continue;
          let on = 1;
          if (L.mode === 1) on = f & 1;
          else if (L.mode === 2) on = f & 2 ? 1 : 0;
          else if (L.mode === 3) on = Math.sin(now * 7 + a.phase * 6) > 0 ? 1 : 0;
          if (L.mode !== 0 && !a.siren && k !== K.SWEEPER) on = 0;
          if (!on) continue;
          const gain = L.mode === 0 ? 0.35 + 0.65 * night : 0.55 + 0.6 * night;
          const lx = a.x + c * L.x + s * L.z;
          const lz = a.z - s * L.x + c * L.z;
          pushHalo(lx, y + L.y * a.scale, lz, L.size * a.scale, L.color[0] * gain, L.color[1] * gain, L.color[2] * gain);
        }
      }
      if ((k === K.MOPED || k === K.COURIER) && night > 0.15) {
        const lx = a.x + s * -0.7;
        const lz = a.z + c * -0.7;
        pushHalo(lx, y + 1.25, lz, 1.5, a.gr * 2.2 * night, a.gg * 2.2 * night, a.gb * 2.2 * night);
      }
      n++;
    }
    km.body.count = n;
    km.body.instanceMatrix.needsUpdate = true;
    km.body.instanceColor!.needsUpdate = true;
    if (km.lamp) {
      km.lamp.count = n;
      km.lamp.instanceColor!.needsUpdate = true;
    }
    if (km.barL && km.barR) {
      km.barL.count = km.barR.count = n;
      km.barL.instanceColor!.needsUpdate = true;
      km.barR.instanceColor!.needsUpdate = true;
    }
    if (k === K.BIRD) kit.flap.needsUpdate = true;
  }

  for (const g of director.glows) {
    if (!g.on) continue;
    const flicker = 0.85 + 0.15 * Math.sin(now * 9 + g.x);
    pushHalo(g.x, g.y, g.z, g.size, g.r * night * flicker, g.g * night * flicker, g.b * night * flicker);
  }

  kit.haloMesh.count = halos;
  kit.haloMesh.instanceMatrix.needsUpdate = true;
  kit.haloMesh.instanceColor!.needsUpdate = true;
  kit.spill.count = spills;
  kit.spill.instanceMatrix.needsUpdate = true;
  kit.spill.instanceColor!.needsUpdate = true;
  kit.arms.count = arms;
  kit.arms.instanceMatrix.needsUpdate = true;
  kit.arms.instanceColor!.needsUpdate = true;

  const h = director.heli();
  if (h) {
    const blink = Math.sin(now * 5) > 0.2;
    pushHaloHeli(h.x, h.y, h.z, h.yaw, blink, now, night, pushHalo);
    kit.haloMesh.count = halos;
    kit.haloMesh.instanceMatrix.needsUpdate = true;
    kit.haloMesh.instanceColor!.needsUpdate = true;
  }
}

function pushHaloHeli(
  x: number,
  y: number,
  z: number,
  yaw: number,
  blink: boolean,
  now: number,
  night: number,
  push: (x: number, y: number, z: number, size: number, r: number, g: number, b: number) => void,
) {
  const s = Math.sin(yaw);
  const c = Math.cos(yaw);
  const gain = 0.5 + 0.5 * night;
  if (blink) push(x + s * -5.4, y + 1.7, z + c * -5.4, 3.2, 2 * gain, 0.15 * gain, 0.1 * gain);
  if (Math.sin(now * 3.1) > 0.8) push(x, y + 1.5, z, 3.8, 2.4 * gain, 2.4 * gain, 2.6 * gain);
  push(x, y - 0.9, z, 4.5, 1.6 * gain, 1.9 * gain, 2.2 * gain);
}

/**
 * London life director: police chases (with a helicopter and searchlight), ambulances and fire engines
 * at the real TfL disruptions, roadworks, cabs, couriers, cyclists, scooters, birds, tour groups, street
 * performers and pub crowds. Toggle kinds with `?life=police,cabs,...` or `?life=off`.
 */
export function Emergency() {
  const net = useNetwork();
  const director = useMemo(() => new LondonDirector(net, SEED, parseLife(window.location.search)), [net]);
  return director.graph.empty ? null : <Life director={director} />;
}
