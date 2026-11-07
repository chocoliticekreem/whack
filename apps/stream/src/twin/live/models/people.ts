import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

/**
 * Arvin's pedestrian figure: a body (legs, torso, arms) and a separate head so the two can carry
 * different instance colours (clothes vs skin). Person-local space: y up, feet at y = 0.
 */

export const CLOTHES = [
  "#1a1d24",
  "#2b303a",
  "#3a2a2a",
  "#5a6070",
  "#c23b3b",
  "#e0a53a",
  "#2f7f8f",
  "#d8d2c4",
  "#7a4fa3",
  "#1f3a6b",
  "#4c7a3c",
  "#e8e8ea",
].map((c) => new THREE.Color(c));
export const SKIN = ["#f1c9a5", "#d9a577", "#b57b50", "#8a5a3a", "#5e3b26", "#f6d7bd"].map((c) => new THREE.Color(c));

type Rgb = [number, number, number];

function part(g: THREE.BufferGeometry, [r, gr, b]: Rgb, x: number, y: number, z: number) {
  const out = g.toNonIndexed();
  out.translate(x, y, z);
  const n = out.getAttribute("position").count;
  const c = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    c[i * 3] = r;
    c[i * 3 + 1] = gr;
    c[i * 3 + 2] = b;
  }
  out.setAttribute("color", new THREE.BufferAttribute(c, 3));
  return out;
}

export function personBody() {
  const leg = new THREE.BoxGeometry(0.16, 0.82, 0.18);
  const arm = new THREE.BoxGeometry(0.1, 0.56, 0.13);
  return mergeGeometries([
    part(leg, [0.22, 0.24, 0.3], -0.1, 0.41, 0),
    part(leg, [0.22, 0.24, 0.3], 0.1, 0.41, 0),
    part(new THREE.BoxGeometry(0.46, 0.64, 0.27), [1, 1, 1], 0, 1.14, 0),
    part(arm, [0.85, 0.85, 0.85], -0.29, 1.1, 0),
    part(arm, [0.85, 0.85, 0.85], 0.29, 1.1, 0),
  ])!;
}

export function personHead() {
  return part(new THREE.SphereGeometry(0.12, 8, 6), [1, 1, 1], 0, 1.6, 0);
}

/** Clothes material whose glow follows the scene's night level through the shared `glow` uniform. */
export function personMaterial(glow: { value: number }) {
  const material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85, metalness: 0 });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uGlow = glow;
    shader.fragmentShader = shader.fragmentShader
      .replace("void main() {", "uniform float uGlow;\nvoid main() {")
      .replace(
        "#include <emissivemap_fragment>",
        "#include <emissivemap_fragment>\n  totalEmissiveRadiance += diffuseColor.rgb * uGlow;",
      );
  };
  return material;
}
