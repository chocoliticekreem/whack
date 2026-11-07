"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { ATMOS, CITY_GLOW, FOG_FAR, FOG_NEAR, SKY_HORIZON, SKY_TOP } from "./palette";

const VERT = /* glsl */ `
varying vec3 vDir;
void main() {
  vDir = normalize(position);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const FRAG = /* glsl */ `
uniform vec3 uTop;
uniform vec3 uHorizon;
uniform vec3 uGlow;
uniform vec2 uGlowDir;
uniform float uTime;
uniform float uStars;
uniform vec3 uSunDir;
uniform vec3 uSunColor;
uniform float uSunDisc;
uniform float uCloud;
uniform float uFlash;
varying vec3 vDir;

float hash(vec3 p) {
  p = fract(p * vec3(443.897, 441.423, 437.195));
  p += dot(p, p.yzx + 19.19);
  return fract((p.x + p.y) * p.z);
}

float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash(vec3(i, 1.0));
  float b = hash(vec3(i + vec2(1.0, 0.0), 1.0));
  float c = hash(vec3(i + vec2(0.0, 1.0), 1.0));
  float d = hash(vec3(i + vec2(1.0, 1.0), 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

void main() {
  float h = clamp(vDir.y, -0.05, 1.0);
  float t = pow(max(h, 0.0), mix(0.45, 0.28, uSunDisc));
  vec3 col = mix(uHorizon, uTop, t);
  float toward = max(dot(normalize(vDir.xz), uGlowDir), 0.0);
  float lift = smoothstep(0.0, 0.1, h) * exp(-max(h, 0.0) * 5.0);
  float glow = pow(toward, 2.0) * lift;
  // The fixed city glow only dominates when there is no sun; a lit sky gets its warmth from the sun band.
  col += uGlow * glow * 0.8 * (1.0 - uCloud * 0.5) * (1.0 - uSunDisc * 0.75);
  col += vec3(0.05, 0.06, 0.12) * lift;

  // Sun: bright HDR disc (above the bloom threshold) inside a tight corona and a wide halo, plus a
  // horizon band lit toward the sun and faint rays. Everything fades with cloud cover via uSunDisc.
  float cosSun = dot(vDir, uSunDir);
  float sunAbove = max(cosSun, 0.0);
  float disc = smoothstep(0.9972, 0.9984, cosSun);
  float corona = pow(sunAbove, 320.0) * 1.0 + pow(sunAbove, 48.0) * 0.35;
  float halo = pow(sunAbove, 7.0) * 0.1 + pow(sunAbove, 2.0) * 0.02;
  vec3 warm = mix(uSunColor, vec3(1.0, 0.62, 0.28), 0.5);
  col += uSunColor * disc * 3.2 * uSunDisc + warm * corona * uSunDisc;
  col += mix(warm, uGlow, 0.35) * halo * (0.25 + uSunDisc);

  vec2 sunFlat = normalize(uSunDir.xz + 1e-4);
  float sunSide = max(dot(normalize(vDir.xz + 1e-4), sunFlat), 0.0);
  float band = exp(-max(h, 0.0) * 9.0) * smoothstep(-0.04, 0.02, vDir.y);
  col += mix(uGlow, uSunColor, 0.3) * band * pow(sunSide, 3.0) * 0.3 * uSunDisc;

  if (cosSun > 0.35 && uSunDisc > 0.05) {
    vec3 right = normalize(cross(vec3(0.0, 1.0, 0.0), uSunDir));
    vec3 up = cross(uSunDir, right);
    vec2 p = vec2(dot(vDir, right), dot(vDir, up));
    float ang = atan(p.y, p.x);
    float streak = (0.5 + 0.5 * sin(ang * 14.0 + uTime * 0.05)) * (0.5 + 0.5 * sin(ang * 5.0 - uTime * 0.03 + 1.3));
    col += uSunColor * pow(streak, 3.0) * exp(-length(p) * 3.2) * smoothstep(0.35, 0.8, cosSun) * 0.16 * uSunDisc;
  }

  // Cloud deck projected on the dome; thickens towards the horizon. A clear sunny sky still gets
  // a scatter of cumulus; under the sun they are white on top with golden undersides.
  float cl = max(uCloud, 0.22 * uSunDisc);
  if (cl > 0.01 && h > 0.0) {
    vec2 uv = vDir.xz / (h + 0.12) * 1.6 + vec2(uTime * 0.004, 0.0);
    float n = vnoise(uv) * 0.5 + vnoise(uv * 2.3 + 7.0) * 0.3 + vnoise(uv * 5.1 + 3.0) * 0.2;
    float cover = smoothstep(0.55 - cl * 0.5, 0.9 - cl * 0.35, n);
    vec3 greyCol = mix(uHorizon, uTop, 0.35) * (0.85 + 0.35 * n);
    vec3 shade = mix(vec3(0.8, 0.88, 1.0), vec3(1.0, 0.78, 0.5), pow(sunSide, 3.0));
    vec3 lit = vec3(1.02, 1.0, 0.97) * (0.92 + 0.3 * n);
    vec3 sunnyCol = mix(shade, lit, smoothstep(0.35, 0.8, n)) * (0.55 + 0.45 * smoothstep(0.05, 0.5, uTop.b));
    sunnyCol += uSunColor * pow(sunAbove, 14.0) * (1.0 - cover * 0.5) * 0.5;
    vec3 cloudCol = mix(greyCol, sunnyCol, smoothstep(0.0, 0.9, uSunDisc));
    float opacity = smoothstep(0.0, 0.4, cl) * (0.55 + 0.45 * min(cl, 1.0));
    col = mix(col, cloudCol, cover * opacity * smoothstep(0.0, 0.08, h));
  }

  vec3 cell = floor(vDir * 700.0);
  float star = step(0.9965, hash(cell));
  float d = length(fract(vDir * 700.0) - 0.5);
  star *= smoothstep(0.32, 0.12, d);
  float twinkle = 0.6 + 0.4 * sin(uTime * 1.7 + hash(cell + 1.0) * 6.283);
  col += vec3(0.8, 0.85, 1.0) * star * twinkle * smoothstep(0.08, 0.35, h) * 0.9 * uStars;

  col += vec3(0.9, 0.95, 1.2) * uFlash * (0.35 + 0.65 * smoothstep(0.0, 0.5, h));
  gl_FragColor = vec4(col, 1.0);
}`;

export function Sky() {
  const mesh = useRef<THREE.Mesh>(null);
  const material = useRef<THREE.ShaderMaterial>(null);
  const fog = useRef<THREE.Fog>(null);
  const uniforms = useMemo(
    () => ({
      uTop: { value: new THREE.Color(SKY_TOP) },
      uHorizon: { value: new THREE.Color(SKY_HORIZON) },
      uGlow: { value: new THREE.Color(CITY_GLOW) },
      uGlowDir: { value: new THREE.Vector2(-0.65, -0.76) },
      uTime: { value: 0 },
      uStars: { value: 1 },
      uSunDir: { value: new THREE.Vector3(0, 1, 0) },
      uSunColor: { value: new THREE.Color("#ffffff") },
      uSunDisc: { value: 0 },
      uCloud: { value: 0 },
      uFlash: { value: 0 },
    }),
    [],
  );

  useFrame(({ camera, clock, scene }) => {
    if (mesh.current) mesh.current.position.copy(camera.position);
    if (material.current) {
      const u = material.current.uniforms;
      u.uTime.value = clock.elapsedTime;
      u.uTop.value.copy(ATMOS.skyTop);
      u.uHorizon.value.copy(ATMOS.skyHorizon);
      u.uGlow.value.copy(ATMOS.glow);
      u.uStars.value = ATMOS.stars;
      u.uSunDir.value.copy(ATMOS.sunDir);
      u.uSunColor.value.copy(ATMOS.sunColor);
      u.uSunDisc.value = ATMOS.sunDisc;
      u.uCloud.value = ATMOS.cloud;
      u.uFlash.value = ATMOS.lightning;
    }
    if (fog.current) {
      fog.current.color.copy(ATMOS.fogColor);
      fog.current.near = ATMOS.fogNear;
      fog.current.far = ATMOS.fogFar;
    }
    if (scene.background instanceof THREE.Color) scene.background.copy(ATMOS.fogColor);
  });

  return (
    <>
      <fog ref={fog} attach="fog" args={[SKY_HORIZON, FOG_NEAR, FOG_FAR]} />
      <color attach="background" args={[SKY_HORIZON]} />
      <mesh ref={mesh} frustumCulled={false} renderOrder={-10}>
        <sphereGeometry args={[4500, 48, 24]} />
        <shaderMaterial
          ref={material}
          uniforms={uniforms}
          vertexShader={VERT}
          fragmentShader={FRAG}
          side={THREE.BackSide}
          depthWrite={false}
          fog={false}
        />
      </mesh>
    </>
  );
}
