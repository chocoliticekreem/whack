"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { sunAngles, type Conditions, type TimeOfDay } from "@/platform/demo";
import { currentOrigin } from "../config";
import { ATMOS, ATMOS_TARGET, lerpAtmosphere, setAtmosphereTarget } from "./palette";

const SUN_DIR = new THREE.Vector3();
const DEG = Math.PI / 180;

/**
 * Fixed sun for overridden time buckets; live dusk/night use the real solar position.
 * Day is a stylised golden-hour sun, live or overridden: every camera preset looks north-west
 * (aerial pitches ~15 deg down, so a sun above ~11 deg clips the 50 deg vertical FOV), and the real
 * midday sun sits due south behind the camera. Azimuth 316 lines up with the sky's city-glow direction.
 */
const OVERRIDE_SUN: Record<TimeOfDay, { elevation: number; azimuth: number }> = {
  day: { elevation: 8, azimuth: 316 },
  dusk: { elevation: 4, azimuth: 252 },
  night: { elevation: -20, azimuth: 300 },
};

function toSceneDir(elevation: number, azimuth: number, out: THREE.Vector3) {
  const el = elevation * DEG;
  const az = azimuth * DEG;
  // North is -z, east is +x.
  return out.set(Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el)).normalize();
}

/**
 * Drives ATMOS from the effective conditions: sets the target when props change, lerps every frame
 * (priority -1 so Sky/Lights/Post read a settled value), and fires lightning during storms.
 */
export function Atmosphere({ conditions, timeOverridden }: { conditions: Conditions; timeOverridden: boolean }) {
  const { weather, time, live } = conditions;
  const visibilityM = live?.visibilityM ?? null;
  const nextFlash = useRef(0);

  useEffect(() => {
    const apply = () => {
      const angles = timeOverridden || time === "day" ? OVERRIDE_SUN[time] : sunAngles(currentOrigin());
      setAtmosphereTarget({ weather, time, sunDir: toSceneDir(angles.elevation, angles.azimuth, SUN_DIR), visibilityM });
    };
    apply();
    const id = setInterval(apply, 60_000);
    return () => clearInterval(id);
  }, [weather, time, timeOverridden, visibilityM]);

  useFrame(({ clock }, dt) => {
    lerpAtmosphere(Math.min(dt, 0.1));
    const t = clock.elapsedTime;
    if (ATMOS_TARGET.lightning > 0 && ATMOS.rain > 1) {
      if (t > nextFlash.current) {
        ATMOS.lightning = 1;
        nextFlash.current = t + 2.5 + Math.random() * 5;
      }
    }
    ATMOS.lightning = Math.max(0, ATMOS.lightning - dt * 6);
  }, -1);

  return null;
}

const DROPS = 7000;

function hash(n: number) {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}
const BOX = 140;
const HEIGHT = 90;

const RAIN_VERT = /* glsl */ `
attribute vec3 aOffset;
attribute float aSeed;
attribute float aEnd;
uniform float uTime;
uniform float uAmount;
varying float vAlpha;
void main() {
  float speed = 55.0 + aSeed * 25.0;
  float y = mod(aOffset.y - uTime * speed, ${HEIGHT.toFixed(1)});
  float len = (0.9 + aSeed * 0.9) * aEnd * min(uAmount, 1.0);
  vec3 p = vec3(aOffset.x, y - len, aOffset.z);
  vAlpha = (0.14 + 0.18 * aSeed) * (0.6 + 0.4 * aEnd) * clamp(uAmount, 0.0, 1.0);
  // Drops past the visible share fade out so rain ramps smoothly.
  vAlpha *= step(aSeed, clamp(uAmount, 0.0, 1.0) * 1.05);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;

const RAIN_FRAG = /* glsl */ `
varying float vAlpha;
void main() {
  gl_FragColor = vec4(0.72, 0.8, 0.95, vAlpha);
}`;

/** Rain as line segments in a box that follows the camera; density/opacity come from ATMOS.rain. */
export function Rain() {
  const lines = useRef<THREE.LineSegments>(null);
  const material = useRef<THREE.ShaderMaterial>(null);
  const uniforms = useMemo(() => ({ uTime: { value: 0 }, uAmount: { value: 0 } }), []);
  const geometry = useMemo(() => {
    const offset = new Float32Array(DROPS * 2 * 3);
    const seed = new Float32Array(DROPS * 2);
    const end = new Float32Array(DROPS * 2);
    for (let i = 0; i < DROPS; i++) {
      const x = (hash(i * 4) - 0.5) * BOX;
      const y = hash(i * 4 + 1) * HEIGHT;
      const z = (hash(i * 4 + 2) - 0.5) * BOX;
      const s = hash(i * 4 + 3);
      for (let v = 0; v < 2; v++) {
        const k = i * 2 + v;
        offset[k * 3] = x;
        offset[k * 3 + 1] = y;
        offset[k * 3 + 2] = z;
        seed[k] = s;
        end[k] = v;
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(offset, 3));
    g.setAttribute("aOffset", new THREE.BufferAttribute(offset, 3));
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    g.setAttribute("aEnd", new THREE.BufferAttribute(end, 1));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, HEIGHT / 2, 0), BOX);
    return g;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame(({ camera, clock }) => {
    const l = lines.current;
    if (!l) return;
    l.visible = ATMOS.rain > 0.02;
    l.position.set(camera.position.x, camera.position.y - HEIGHT * 0.55, camera.position.z);
    if (material.current) {
      material.current.uniforms.uTime.value = clock.elapsedTime;
      material.current.uniforms.uAmount.value = ATMOS.rain;
    }
  });

  return (
    <lineSegments ref={lines} geometry={geometry} frustumCulled={false} renderOrder={5}>
      <shaderMaterial
        ref={material}
        uniforms={uniforms}
        vertexShader={RAIN_VERT}
        fragmentShader={RAIN_FRAG}
        transparent
        depthWrite={false}
        fog={false}
      />
    </lineSegments>
  );
}
