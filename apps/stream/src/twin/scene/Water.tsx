"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { Ring } from "../config";
import { bearingToYaw, LANDMARKS, landmarkPos } from "../landmarks";
import { deriveRiver } from "../live/river";
import { ATMOS } from "./palette";
import { useGeometry } from "./useGeometry";
import { ringToShape } from "./shapes";

/** Murky Thames albedo: silty green-grey by day, cold blue-grey by night, darker and greyer in rain. */
const CALM = new THREE.Color("#2a3842");
const DAY = new THREE.Color("#747a58");
const RAINY = new THREE.Color("#2c3532");

const SHORE_RES = 1024;
const SHORE_MAX_M = 48;
const FLOW_RES = 128;
/** Surface current of the ebb (west -> east) at mid-river, metres per second. */
const FLOW_MPS = 0.5;

/**
 * The swell: a few long sine waves that are both drawn by the shader (as the base normal) and evaluated on the CPU,
 * so boats can bob in step with the surface they float on. Small on purpose, the river is sheltered.
 * `dx, dz` is the unit direction the phase advances along, `k` rad/m, `w` rad/s, `a` metres.
 */
const SWELL = [
  { dx: 0.96, dz: 0.28, k: 0.28, w: 0.8, a: 0.1 },
  { dx: -0.5, dz: 0.87, k: 0.51, w: 1.1, a: 0.06 },
  { dx: 0.2, dz: -0.98, k: 1.1, w: 1.6, a: 0.03 },
];

/** Water surface height above the water plane (y = 0.08 in the scene) at world x, z and `clock.elapsedTime` t. */
export function waveHeight(x: number, z: number, t: number) {
  let h = 0;
  for (const s of SWELL) h += s.a * Math.sin(s.k * (s.dx * x + s.dz * z) + s.w * t);
  return h;
}

/** Surface slope (dh/dx, dh/dz) at x, z, t; use it for boat pitch and roll. */
export function waveSlope(x: number, z: number, t: number, out: { x: number; z: number }) {
  out.x = 0;
  out.z = 0;
  for (const s of SWELL) {
    const c = s.a * s.k * Math.cos(s.k * (s.dx * x + s.dz * z) + s.w * t);
    out.x += c * s.dx;
    out.z += c * s.dz;
  }
  return out;
}

const f = (n: number) => n.toFixed(4);

const VERT = /* glsl */ `
varying vec3 vWorld;
#include <fog_pars_vertex>
void main() {
  vec4 world = modelMatrix * vec4(position, 1.0);
  vWorld = world.xyz;
  vec4 mvPosition = viewMatrix * world;
  gl_Position = projectionMatrix * mvPosition;
  #include <fog_vertex>
}`;

// Why: no planar reflection pass. The river is one flat plane, so it is shaded as a single cheap draw:
//  - murky body colour lit by the current sky, shifting from silty/light in the shallows to dark/blue mid-river
//    (distance-to-bank texture),
//  - normals from the shared swell plus layered value-noise ripple octaves that drift with the current (flow-map style
//    two-phase advection, so the pattern never shears) and are faded by pixel footprint (fwidth) => no moire,
//  - Schlick fresnel mixing in the same sky gradient Sky.tsx draws, sun glitter, sparse night lamp glints,
//  - foam where the water meets a bank or a Tower Bridge pier.
const FRAG = /* glsl */ `
uniform vec3 uBody;
uniform vec3 uTop;
uniform vec3 uHorizon;
uniform vec3 uGlow;
uniform vec3 uSunDir;
uniform vec3 uSunColor;
uniform float uTime;
uniform float uWet;
uniform float uCloud;
uniform float uLights;
uniform sampler2D uShore;
uniform sampler2D uFlow;
uniform vec4 uShoreRect;
uniform vec4 uPier;
uniform vec4 uPierPos;
varying vec3 vWorld;
#include <fog_pars_fragment>

const mat2 ID = mat2(1.0, 0.0, 0.0, 1.0);
const mat2 R1 = mat2(0.8, 0.6, -0.6, 0.8);
const mat2 R2 = mat2(0.6, -0.8, 0.8, 0.6);

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

// Value noise with analytic derivatives: x = value, yz = d/dp.
vec3 vnoised(vec2 x) {
  vec2 i = floor(x);
  vec2 fr = fract(x);
  vec2 u = fr * fr * fr * (fr * (fr * 6.0 - 15.0) + 10.0);
  vec2 du = 30.0 * fr * fr * (fr * (fr - 2.0) + 1.0);
  float a = hash21(i);
  float b = hash21(i + vec2(1.0, 0.0));
  float c = hash21(i + vec2(0.0, 1.0));
  float d = hash21(i + vec2(1.0, 1.0));
  return vec3(a + (b - a) * u.x + (c - a) * u.y + (a - b - c + d) * u.x * u.y,
              du * (vec2(b - a, c - a) + (a - b - c + d) * u.yx));
}

float fade(float k, float fp) {
  return 1.0 - smoothstep(0.3, 0.8, k * fp);
}

// Two noise samples advected by the current, cross-faded every half period so the drift never accumulates shear.
vec3 flowNoise(vec2 p, mat2 R, float k, vec2 adv, float t) {
  float ph0 = fract(t * 0.14);
  float ph1 = fract(t * 0.14 + 0.5);
  float w0 = 1.0 - abs(2.0 * ph0 - 1.0);
  vec3 a = vnoised(R * (p - adv * (ph0 - 0.5) * 7.14) * k);
  vec3 b = vnoised(R * (p - adv * (ph1 - 0.5) * 7.14) * k);
  return mix(b, a, w0);
}

// Slope contribution (world space) of one ripple octave.
vec2 octave(vec2 p, mat2 R, float k, float amp, vec2 adv, float t, float fp) {
  return (flowNoise(p, R, k, adv, t).yz * R) * (k * amp * fade(k, fp));
}

vec2 swell(vec2 p, float t, float fp) {
  vec2 s = vec2(0.0);
  ${SWELL.map(
    (w) =>
      `s += vec2(${f(w.dx)}, ${f(w.dz)}) * cos(dot(vec2(${f(w.dx)}, ${f(w.dz)}), p) * ${f(w.k)} + t * ${f(w.w)}) * ${f(w.a * w.k)} * fade(${f(w.k)}, fp);`,
  ).join("\n  ")}
  return s;
}

float pierDistance(vec2 p, vec2 c) {
  vec2 d = p - c;
  vec2 l = vec2(d.x * uPier.x - d.y * uPier.y, d.x * uPier.y + d.y * uPier.x);
  vec2 q = abs(l) - uPier.zw;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0);
}

void main() {
  vec3 toCam = cameraPosition - vWorld;
  float dist = length(toCam);
  vec3 V = toCam / dist;
  vec2 p = vWorld.xz;
  float fp = max(length(dFdx(p)), length(dFdy(p)));

  vec2 uv = (p - uShoreRect.xy) / uShoreRect.z;
  float shore = texture2D(uShore, uv).r * uShoreRect.w;
  vec4 fl = texture2D(uFlow, uv);
  vec2 adv = (fl.xy * 2.0 - 1.0) * (fl.z * ${f(FLOW_MPS)});
  float edge = min(shore, max(min(pierDistance(p, uPierPos.xy), pierDistance(p, uPierPos.zw)), 0.0));

  // Normal: swell + drifting ripple octaves (each ~0.04 slope) + small chop and rain dimples.
  vec2 s = swell(p, uTime, fp);
  s += octave(p, ID, 0.1, 0.6, adv, uTime, fp);
  s += octave(p, R1, 0.27, 0.2, adv * 0.9, uTime, fp);
  s += octave(p, R2, 0.7, 0.075, adv * 0.8, uTime, fp) * (0.8 + 0.8 * uWet);
  s += (vnoised(R1 * (p * 1.9 + vec2(uTime * 0.35, -uTime * 0.2))).yz * R1) * (1.9 * 0.03 * fade(1.9, fp) * (0.5 + 1.5 * uWet));
  vec2 rc = floor(p / 1.1);
  vec2 rf = fract(p / 1.1) - 0.5;
  float rseed = hash21(rc);
  float rph = fract(uTime * 0.9 + rseed);
  float rr = max(length(rf), 1e-3);
  float ringX = (rr - rph * 0.45) * 14.0;
  s += (rf / rr) * exp(-ringX * ringX) * (1.0 - rph) * 0.14 * uWet * step(0.55, rseed) * (1.0 - smoothstep(0.1, 0.3, fp / 1.1));
  vec3 N = normalize(vec3(-s.x, 1.0, -s.y));

  float cosT = clamp(dot(N, V), 0.0, 1.0);
  float fresnel = 0.035 + 0.965 * pow(1.0 - cosT, 4.0);

  vec3 R = reflect(-V, N);
  R.y = abs(R.y);
  float h = clamp(R.y, 0.0, 1.0);
  vec3 sky = mix(uHorizon, uTop, pow(h, 0.45));
  sky += uGlow * 0.3 * exp(-h * 5.0);
  sky = mix(sky, vec3(dot(sky, vec3(0.3, 0.59, 0.11))) * vec3(1.0, 1.02, 0.96), 0.3);

  // Body: murky albedo lit by sky + sun; silty/brighter in the shallows, darker and bluer in the channel.
  float deep = smoothstep(2.0, 38.0, shore);
  vec3 amb = uHorizon * 0.6 + uTop * 0.15 + uSunColor * (0.25 * clamp(uSunDir.y, 0.0, 1.0) * (1.0 - uCloud * 0.7));
  vec3 albedo = mix(uBody * vec3(1.3, 1.15, 0.85), uBody * vec3(0.6, 0.78, 0.78), deep);
  vec3 col = mix(albedo * amb, sky, fresnel);

  // Sun / moon glitter: smooth lobe from the ripple normals plus fine flickering facets that fade out once sub-pixel.
  float cosSun = max(dot(R, uSunDir), 0.0);
  float clear = 1.0 - uCloud * 0.85;
  col += uSunColor * (pow(cosSun, 700.0) * 8.0 + pow(cosSun, 90.0) * 0.7 + pow(cosSun, 10.0) * 0.03) * clear * (0.3 + 0.7 * fresnel);
  vec2 gc = floor(p / 0.9);
  float gs = hash21(gc);
  vec2 micro = (vec2(hash21(gc + floor(uTime * 0.8 + gs * 3.0)), hash21(gc + 7.0 + floor(uTime * 0.8 + gs * 3.0))) - 0.5) * 0.4;
  vec3 Rg = reflect(-V, normalize(N + vec3(micro.x, 0.0, micro.y)));
  Rg.y = abs(Rg.y);
  col += uSunColor * pow(max(dot(Rg, uSunDir), 0.0), 1600.0) * 14.0 * clear * (1.0 - smoothstep(0.1, 0.4, fp / 0.9));

  // Night: sparse warm/cool lamp glints (footprint-faded) and a soft far glow so distant water is not dead.
  vec2 lq = (p + N.xz * 10.0) / 6.0;
  vec2 lf = fract(lq) - 0.5;
  float lseed = hash21(floor(lq) + 17.0);
  vec2 vd = normalize(V.xz + 1e-4);
  vec2 lfr = vec2(dot(lf, vd) * 0.55, dot(lf, vec2(-vd.y, vd.x)) * 1.6);
  float lamp = step(0.93, lseed) * smoothstep(0.5, 0.0, length(lfr)) * (1.0 - smoothstep(0.1, 0.35, fp / 6.0));
  vec3 lampCol = mix(vec3(1.0, 0.62, 0.3), vec3(0.55, 0.75, 1.0), step(0.6, fract(lseed * 7.0)));
  float twinkle = 0.6 + 0.4 * sin(uTime * 1.6 + lseed * 40.0);
  col += lampCol * lamp * lamp * twinkle * uLights * uLights * (0.5 + 1.5 * fresnel) * 1.5;
  float farGlow = 0.5 + 0.5 * sin(p.x * 0.045 + uTime * 0.3) * sin(p.y * 0.06 - uTime * 0.2);
  col += uGlow * uLights * 0.22 * fresnel * farGlow;

  // Lacy foam along banks and around the bridge piers, drifting with the current.
  float fn = flowNoise(p, R1, 0.55, adv * 0.5, uTime).x;
  float foam = (1.0 - smoothstep(0.1, 1.2 + 2.6 * fn, edge)) * (0.3 + 0.7 * smoothstep(0.3, 0.7, fn));
  foam *= 1.0 - 0.5 * smoothstep(1.0, 4.0, fp);
  vec3 foamCol = uHorizon * 0.55 + uSunColor * (0.35 * clamp(uSunDir.y, 0.0, 1.0)) * clear + vec3(0.03);
  col = mix(col, foamCol, foam * 0.7 * (1.0 - uWet * 0.3));

  gl_FragColor = vec4(col, 1.0);
  #include <fog_fragment>
}`;

/** Rasterises the water rings and turns them into a distance-to-bank field (0 at the bank, 1 at SHORE_MAX_M). */
function buildShore(water: Ring[]) {
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (const ring of water) {
    for (const [x, z] of ring) {
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minZ = Math.min(minZ, z);
      maxZ = Math.max(maxZ, z);
    }
  }
  const pad = 8;
  const size = Math.max(maxX - minX, maxZ - minZ) + pad * 2;
  const x0 = minX - pad;
  const z0 = minZ - pad;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = SHORE_RES;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.fillStyle = "#fff";
  for (const ring of water) {
    if (ring.length < 3) continue;
    ctx.beginPath();
    ring.forEach(([x, z], i) => {
      const px = ((x - x0) / size) * SHORE_RES;
      const py = ((z - z0) / size) * SHORE_RES;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.closePath();
    ctx.fill();
  }
  const rgba = ctx.getImageData(0, 0, SHORE_RES, SHORE_RES).data;
  const dist = new Float32Array(SHORE_RES * SHORE_RES);
  for (let i = 0; i < dist.length; i++) dist[i] = rgba[i * 4] > 127 ? 1e6 : 0;
  const D = Math.SQRT2;
  const at = (x: number, y: number) => (x < 0 || y < 0 || x >= SHORE_RES || y >= SHORE_RES ? 0 : dist[y * SHORE_RES + x]);
  for (let y = 0; y < SHORE_RES; y++) {
    for (let x = 0; x < SHORE_RES; x++) {
      const i = y * SHORE_RES + x;
      dist[i] = Math.min(dist[i], at(x - 1, y) + 1, at(x, y - 1) + 1, at(x - 1, y - 1) + D, at(x + 1, y - 1) + D);
    }
  }
  for (let y = SHORE_RES - 1; y >= 0; y--) {
    for (let x = SHORE_RES - 1; x >= 0; x--) {
      const i = y * SHORE_RES + x;
      dist[i] = Math.min(dist[i], at(x + 1, y) + 1, at(x, y + 1) + 1, at(x + 1, y + 1) + D, at(x - 1, y + 1) + D);
    }
  }
  const metresPerTexel = size / SHORE_RES;
  const data = new Uint8Array(dist.length);
  for (let i = 0; i < dist.length; i++) data[i] = Math.min(255, Math.round(((dist[i] * metresPerTexel) / SHORE_MAX_M) * 255));
  const texture = new THREE.DataTexture(data, SHORE_RES, SHORE_RES, THREE.RedFormat, THREE.UnsignedByteType);
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return { texture, rect: new THREE.Vector4(x0, z0, size, SHORE_MAX_M) };
}

/**
 * Current field over the same rectangle: rg = unit flow direction (the centreline's west -> east tangent), b = speed
 * (parabolic across the channel, zero outside it, so docks stay still). Falls back to a plain eastward drift.
 */
function buildFlow(water: Ring[], rect: THREE.Vector4) {
  const path = deriveRiver(water);
  const data = new Uint8Array(FLOW_RES * FLOW_RES * 4);
  for (let j = 0; j < FLOW_RES; j++) {
    for (let i = 0; i < FLOW_RES; i++) {
      const x = rect.x + ((i + 0.5) / FLOW_RES) * rect.z;
      const z = rect.y + ((j + 0.5) / FLOW_RES) * rect.z;
      let tx = 1;
      let tz = 0;
      let speed = 1;
      if (path) {
        let best = Infinity;
        let k = 0;
        for (let n = 0; n < path.count; n++) {
          const d = (path.xs[n] - x) ** 2 + (path.zs[n] - z) ** 2;
          if (d < best) {
            best = d;
            k = n;
          }
        }
        tx = path.tx[k];
        tz = path.tz[k];
        const lateral = Math.sqrt(best) / Math.max(path.hw[k], 1);
        const inChannel = 1 - Math.min(Math.max((lateral - 1.1) / 0.5, 0), 1);
        speed = Math.max(0, 1 - 0.6 * Math.min(lateral, 1.4) ** 2) * inChannel;
      }
      const o = (j * FLOW_RES + i) * 4;
      data[o] = Math.round((tx * 0.5 + 0.5) * 255);
      data[o + 1] = Math.round((tz * 0.5 + 0.5) * 255);
      data[o + 2] = Math.round(speed * 255);
      data[o + 3] = 255;
    }
  }
  const texture = new THREE.DataTexture(data, FLOW_RES, FLOW_RES, THREE.RGBAFormat, THREE.UnsignedByteType);
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

/** Tower Bridge pier footprints in world space (mirrors TowerBridge.tsx: towers at +-41.5 m, 40 x 23 m bases, x1.1 scale). */
function pierUniforms() {
  const [bx, bz] = landmarkPos(LANDMARKS.towerBridge);
  const yaw = bearingToYaw(LANDMARKS.towerBridge.headingDeg);
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  const along = 41.5 * 1.1;
  return {
    frame: new THREE.Vector4(c, s, 20 * 1.1, 11.5 * 1.1),
    pos: new THREE.Vector4(bx + along * s, bz + along * c, bx - along * s, bz - along * c),
  };
}

export function Water() {
  const { water } = useGeometry();
  const material = useRef<THREE.ShaderMaterial>(null);

  const field = useMemo(() => {
    const rings = water.filter((ring) => ring.length >= 3);
    const shore = buildShore(rings);
    return { shore, flow: buildFlow(rings, shore.rect) };
  }, [water]);
  useEffect(
    () => () => {
      field.shore.texture.dispose();
      field.flow.dispose();
    },
    [field],
  );

  const uniforms = useMemo(() => {
    const pier = pierUniforms();
    return {
      ...THREE.UniformsUtils.merge([
        THREE.UniformsLib.fog,
        {
          uBody: { value: new THREE.Color(CALM) },
          uTop: { value: new THREE.Color() },
          uHorizon: { value: new THREE.Color() },
          uGlow: { value: new THREE.Color() },
          uSunDir: { value: new THREE.Vector3(0, 1, 0) },
          uSunColor: { value: new THREE.Color() },
          uTime: { value: 0 },
          uWet: { value: 0 },
          uCloud: { value: 0 },
          uLights: { value: 1 },
          uShoreRect: { value: field.shore.rect },
          uPier: { value: pier.frame },
          uPierPos: { value: pier.pos },
        },
      ]),
      uShore: { value: field.shore.texture },
      uFlow: { value: field.flow },
    };
  }, [field]);

  useFrame(({ clock }) => {
    const m = material.current;
    if (!m) return;
    const u = m.uniforms;
    u.uBody.value
      .copy(CALM)
      .lerp(DAY, ATMOS.windowGlow < 0.5 ? 1 - ATMOS.windowGlow : 0)
      .lerp(RAINY, ATMOS.wet * 0.7);
    u.uTop.value.copy(ATMOS.skyTop);
    u.uHorizon.value.copy(ATMOS.skyHorizon);
    u.uGlow.value.copy(ATMOS.glow);
    u.uSunDir.value.copy(ATMOS.sunDir);
    u.uSunColor.value.copy(ATMOS.sunColor);
    u.uTime.value = clock.elapsedTime;
    u.uWet.value = ATMOS.wet;
    u.uCloud.value = ATMOS.cloud;
    u.uLights.value = ATMOS.windowGlow;
  });

  const geometry = useMemo(() => {
    const parts = water
      .filter((ring) => ring.length >= 3)
      .map((ring) => {
        const g = new THREE.ShapeGeometry(ringToShape(ring));
        g.rotateX(-Math.PI / 2);
        return g;
      });
    return parts.length ? mergeGeometries(parts) : null;
  }, [water]);
  useEffect(() => () => geometry?.dispose(), [geometry]);

  if (!geometry) return null;
  return (
    <mesh geometry={geometry} position={[0, 0.08, 0]}>
      <shaderMaterial ref={material} uniforms={uniforms} vertexShader={VERT} fragmentShader={FRAG} fog />
    </mesh>
  );
}
