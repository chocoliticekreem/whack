import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

/**
 * Arvin's low-poly road vehicles. Vehicle-local space: y up, +z is the nose, ground at y = 0.
 * Body panels are vertex-coloured white so an instance colour (paint or the real colour seen on
 * CCTV) tints them; glass, trim and wheels keep their own dark vertex colours.
 */

type Rgb = [number, number, number];

export const GLASS: Rgb = [0.03, 0.05, 0.08];
export const DARK: Rgb = [0.05, 0.05, 0.06];
export const WHITE: Rgb = [1, 1, 1];
const HEAD: Rgb = [3, 2.7, 2.1];
const TAIL: Rgb = [2.6, 0.12, 0.06];
const SIGN: Rgb = [3, 2, 0.5];

export const CAR_LENGTH = 4.3;
export const VAN_LENGTH = 5.3;
export const CAB_LENGTH = 4.6;

export function tint(g: THREE.BufferGeometry, [r, gr, b]: Rgb) {
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

/** A flat-shaded box whose roof is narrowed (`top`) and whose nose / tail roof edges are pulled in. */
export function block(
  [w, h, l]: Rgb,
  [x, y, z]: Rgb,
  color: Rgb,
  top = 1,
  nose = 1,
  tail = 1,
) {
  const g = new THREE.BoxGeometry(w, h, l).toNonIndexed();
  const p = g.getAttribute("position") as THREE.BufferAttribute;
  for (let i = 0; i < p.count; i++) {
    if (p.getY(i) > 0) {
      p.setX(i, p.getX(i) * top);
      p.setZ(i, p.getZ(i) * (p.getZ(i) > 0 ? nose : tail));
    }
  }
  g.translate(x, y, z);
  g.computeVertexNormals();
  return tint(g, color);
}

function wheels(width: number, wheelbase: number, shift = 0) {
  const parts: THREE.BufferGeometry[] = [];
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      const g = new THREE.CylinderGeometry(0.33, 0.33, 0.24, 10).toNonIndexed();
      g.rotateZ(Math.PI / 2);
      g.translate((sx * width) / 2, 0.33, sz * wheelbase + shift);
      parts.push(tint(g, DARK));
    }
  }
  return parts;
}

export function carBody() {
  return mergeGeometries([
    block([1.78, 0.6, 4.3], [0, 0.58, 0], WHITE, 0.96, 0.92, 1),
    block([1.55, 0.5, 2.2], [0, 1.13, -0.2], WHITE, 0.8, 0.62, 0.84),
    block([1.58, 0.32, 2.05], [0, 1.08, -0.2], GLASS, 0.82, 0.64, 0.86),
    block([1.76, 0.22, 0.14], [0, 0.4, 2.2], DARK),
    block([1.76, 0.22, 0.14], [0, 0.4, -2.2], DARK),
    ...wheels(1.82, 1.4),
  ])!;
}

export function vanBody() {
  return mergeGeometries([
    block([1.95, 0.55, 5.3], [0, 0.6, 0], WHITE, 0.98, 0.94, 1),
    block([1.95, 1.05, 3.5], [0, 1.4, -0.95], WHITE, 0.97, 1, 1),
    block([1.8, 0.7, 1.7], [0, 1.2, 1.5], WHITE, 0.86, 0.6, 1),
    block([1.82, 0.4, 1.5], [0, 1.28, 1.52], GLASS, 0.88, 0.62, 1),
    block([1.93, 0.22, 0.14], [0, 0.42, 2.65], DARK),
    block([1.93, 0.22, 0.14], [0, 0.42, -2.65], DARK),
    ...wheels(2.0, 1.75),
  ])!;
}

export function cabBody() {
  return mergeGeometries([
    block([1.76, 0.6, 4.6], [0, 0.6, 0], WHITE, 0.96, 0.94, 1),
    block([1.6, 0.9, 2.75], [0, 1.35, -0.45], WHITE, 0.9, 0.78, 0.92),
    block([1.63, 0.55, 2.6], [0, 1.32, -0.45], GLASS, 0.92, 0.8, 0.94),
    block([1.74, 0.22, 0.14], [0, 0.4, 2.3], DARK),
    block([1.74, 0.22, 0.14], [0, 0.4, -2.3], DARK),
    ...wheels(1.8, 1.45),
  ])!;
}

/** A single-track two-wheeler: frame block, saddle and two wheels. Body tinted by instance colour. */
export function bikeBody(length: number, height: number) {
  const r = 0.3;
  const parts: THREE.BufferGeometry[] = [];
  for (const sz of [-1, 1]) {
    const g = new THREE.CylinderGeometry(r, r, 0.08, 10).toNonIndexed();
    g.rotateZ(Math.PI / 2);
    g.translate(0, r, sz * (length / 2 - r));
    parts.push(tint(g, DARK));
  }
  parts.push(block([0.2, height * 0.35, length * 0.6], [0, r + height * 0.2, 0], WHITE, 0.6));
  parts.push(block([0.5, 0.1, 0.3], [0, height * 0.8, -length * 0.1], DARK));
  return mergeGeometries(parts)!;
}

/** Head- and tail-lamps for a body of the given footprint; `sign` adds a taxi roof light at that height. */
export function lamps(length: number, width: number, sign?: number) {
  const parts: THREE.BufferGeometry[] = [];
  for (const sx of [-1, 1]) {
    parts.push(block([0.34, 0.14, 0.06], [sx * width * 0.34, 0.66, length / 2 + 0.03], HEAD));
    parts.push(block([0.3, 0.12, 0.06], [sx * width * 0.36, 0.7, -length / 2 - 0.03], TAIL));
  }
  if (sign !== undefined) parts.push(block([0.5, 0.14, 0.22], [0, sign, 0.1], SIGN));
  return mergeGeometries(parts)!;
}

/** A pool of light on the road ahead of the car; additive, fading to black at the far edge. */
export function beam() {
  const g = new THREE.BufferGeometry();
  g.setAttribute(
    "position",
    new THREE.BufferAttribute(
      new Float32Array([-0.7, 0, 2.5, 0.7, 0, 2.5, 2.1, 0, 12, -0.7, 0, 2.5, 2.1, 0, 12, -2.1, 0, 12]),
      3,
    ),
  );
  const near = [0.3, 0.26, 0.16];
  g.setAttribute(
    "color",
    new THREE.BufferAttribute(new Float32Array([...near, ...near, 0, 0, 0, ...near, 0, 0, 0, 0, 0, 0]), 3),
  );
  return g;
}

export function paintMaterial() {
  return new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.3, metalness: 0.2 });
}

export function lampMaterial() {
  return new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false });
}
