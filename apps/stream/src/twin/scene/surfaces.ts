import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { offsetPolyline } from "../network/layout";

export { KERB_Y, MARK_Y, PATH_Y, PAVEMENT_Y, ROAD_Y } from "../network/layout";
import type { XZ } from "../network/types";
import { ATMOS } from "./palette";

/** Accumulates flat-shaded triangles into one non-indexed BufferGeometry (positions, normals, colours, paving mask). */
export class MeshBuilder {
  private pos: number[] = [];
  private nrm: number[] = [];
  private col: number[] = [];
  private aux: number[] = [];

  /** Triangle wound so its normal faces along `hint` (a direction, not necessarily unit). */
  tri(
    a: [number, number, number],
    b: [number, number, number],
    c: [number, number, number],
    hint: [number, number, number],
    color: THREE.Color,
    paving = 0,
  ) {
    const ux = b[0] - a[0];
    const uy = b[1] - a[1];
    const uz = b[2] - a[2];
    const vx = c[0] - a[0];
    const vy = c[1] - a[1];
    const vz = c[2] - a[2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz);
    if (len < 1e-9) return;
    nx /= len;
    ny /= len;
    nz /= len;
    const flip = nx * hint[0] + ny * hint[1] + nz * hint[2] < 0;
    const order = flip ? [a, c, b] : [a, b, c];
    const s = flip ? -1 : 1;
    for (const p of order) {
      this.pos.push(p[0], p[1], p[2]);
      this.nrm.push(nx * s, ny * s, nz * s);
      this.col.push(color.r, color.g, color.b);
      this.aux.push(paving);
    }
  }

  /** Horizontal quad a-b-d-c where a,b are one edge and c,d the opposite one. */
  flatQuad(a: XZ, b: XZ, c: XZ, d: XZ, y: number, color: THREE.Color, paving = 0) {
    this.tri([a[0], y, a[1]], [b[0], y, b[1]], [c[0], y, c[1]], UP, color, paving);
    this.tri([b[0], y, b[1]], [d[0], y, d[1]], [c[0], y, c[1]], UP, color, paving);
  }

  /** Strip between two equally long polylines. */
  flatStrip(left: XZ[], right: XZ[], y: number, color: THREE.Color, paving = 0) {
    for (let i = 0; i < left.length - 1; i++) this.flatQuad(left[i], left[i + 1], right[i], right[i + 1], y, color, paving);
  }

  /** Flat ribbon along `pts`, `half` metres either side, mitred at the bends. */
  ribbon(pts: XZ[], half: number, y: number, color: THREE.Color, paving = 0) {
    if (pts.length < 2) return;
    this.flatStrip(offsetPolyline(pts, half), offsetPolyline(pts, -half), y, color, paving);
  }

  /** Flat disc, used to round off junctions and bends where ribbons meet. */
  disc(x: number, z: number, r: number, y: number, color: THREE.Color, paving = 0, segments = 16) {
    for (let i = 0; i < segments; i++) {
      const a0 = (i / segments) * Math.PI * 2;
      const a1 = ((i + 1) / segments) * Math.PI * 2;
      this.tri(
        [x, y, z],
        [x + Math.cos(a0) * r, y, z + Math.sin(a0) * r],
        [x + Math.cos(a1) * r, y, z + Math.sin(a1) * r],
        UP,
        color,
        paving,
      );
    }
  }

  get empty() {
    return this.pos.length === 0;
  }

  build(): THREE.BufferGeometry {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(this.pos, 3));
    geo.setAttribute("normal", new THREE.Float32BufferAttribute(this.nrm, 3));
    geo.setAttribute("color", new THREE.Float32BufferAttribute(this.col, 3));
    geo.setAttribute("aPave", new THREE.Float32BufferAttribute(this.aux, 1));
    geo.computeBoundingSphere();
    return geo;
  }
}

const UP: [number, number, number] = [0, 1, 0];

const wetUniform = { value: 0 };
const skyUniform = { value: new THREE.Color() };

/** Drives the shared wet-road uniforms from the atmosphere; call once from each surface component. */
export function useSurfaceUniforms() {
  useFrame(() => {
    wetUniform.value = ATMOS.wet;
    skyUniform.value.copy(ATMOS.skyHorizon);
  });
}

const NOISE = /* glsl */ `
float twinHash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}
`;

interface SurfaceOptions {
  roughness: number;
  /** Stacking order of flat layers: higher wins the depth test over lower ones at the same spot. */
  layer: number;
  /** Flagstone grid on faces with aPave = 1. */
  paving?: boolean;
  /** Strength of the fine asphalt grain (0 = none). */
  grain?: number;
}

/** Vertex-coloured standard material with wet-road reflections and an optional procedural paving or asphalt look. */
export function makeSurfaceMaterial({ roughness, layer, paving = false, grain = 0 }: SurfaceOptions) {
  const mat = new THREE.MeshStandardMaterial({
    color: "#ffffff",
    roughness,
    metalness: 0.05,
    vertexColors: true,
    polygonOffset: true,
    polygonOffsetFactor: -layer,
    polygonOffsetUnits: -layer,
  });
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uTwinWet = wetUniform;
    shader.uniforms.uTwinSky = skyUniform;
    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        "#include <common>\nattribute float aPave;\nvarying float vPave;\nvarying vec3 vWPos;",
      )
      .replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\nvPave = aPave;\nvWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;",
      );
    const body = paving
      ? /* glsl */ `
vec2 g = vWPos.xz / 0.62;
vec2 id = floor(g);
vec2 f = fract(g);
vec2 fw = fwidth(g);
vec2 d = min(f, 1.0 - f);
float joint = 1.0 - smoothstep(0.012, 0.05 + max(fw.x, fw.y) * 1.2, min(d.x, d.y));
float fade = 1.0 - clamp(max(fw.x, fw.y) * 2.2, 0.0, 1.0);
float tone = 0.9 + 0.2 * twinHash(id);
diffuseColor.rgb *= mix(1.0, tone * (1.0 - 0.38 * joint), vPave * fade);
`
      : grain > 0
        ? /* glsl */ `
vec2 gp = vWPos.xz * 1.7;
float gfade = 1.0 - clamp(max(fwidth(gp.x), fwidth(gp.y)) * 1.5, 0.0, 1.0);
float speck = twinHash(floor(gp)) * 0.6 + twinHash(floor(gp * 0.23)) * 0.4;
diffuseColor.rgb *= 1.0 + (speck - 0.5) * ${grain.toFixed(2)} * gfade;
`
        : "";
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>\nuniform float uTwinWet;\nuniform vec3 uTwinSky;\nvarying float vPave;\nvarying vec3 vWPos;\n${NOISE}`,
      )
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>\n${body}\ndiffuseColor.rgb *= mix(1.0, 0.7, uTwinWet);`,
      )
      .replace(
        "#include <roughnessmap_fragment>",
        "#include <roughnessmap_fragment>\nroughnessFactor *= mix(1.0, 0.3, uTwinWet);",
      )
      .replace(
        "#include <emissivemap_fragment>",
        `#include <emissivemap_fragment>
float twinFres = pow(1.0 - clamp(dot(normalize(vViewPosition), normal), 0.0, 1.0), 3.0);
totalEmissiveRadiance += uTwinSky * twinFres * uTwinWet * 0.55;`,
      );
  };
  return mat;
}
