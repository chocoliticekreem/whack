import * as THREE from "three";
import type { TimeOfDay, WeatherKind } from "@/platform/demo";

/** Night-time London look. Fog colour must match the sky horizon or the world looks pasted on. */
export const SKY_TOP = "#02040a";
export const SKY_HORIZON = "#1c2546";
export const CITY_GLOW = "#4a3424";
export const FOG_NEAR = 650;
export const FOG_FAR = 2800;
export const CAMERA_FAR = 6000;

/**
 * Everything in the scene that depends on weather + time of day reads from one blended state.
 * `ATMOS` is the current (lerped) value, `ATMOS_TARGET` is where it is heading. Both are mutated
 * in place from useFrame — never reallocated — so every module can read them with zero garbage.
 */
export interface Atmosphere {
  skyTop: THREE.Color;
  skyHorizon: THREE.Color;
  glow: THREE.Color;
  fogColor: THREE.Color;
  sunColor: THREE.Color;
  hemiSky: THREE.Color;
  hemiGround: THREE.Color;
  sunDir: THREE.Vector3;
  sunIntensity: number;
  sunDisc: number;
  hemiIntensity: number;
  stars: number;
  cloud: number;
  fogNear: number;
  fogFar: number;
  windowGlow: number;
  bloom: number;
  rain: number;
  wet: number;
  lightning: number;
  brightness: number;
  saturation: number;
}

function make(): Atmosphere {
  return {
    skyTop: new THREE.Color(SKY_TOP),
    skyHorizon: new THREE.Color(SKY_HORIZON),
    glow: new THREE.Color(CITY_GLOW),
    fogColor: new THREE.Color(SKY_HORIZON),
    sunColor: new THREE.Color("#8ea4d8"),
    hemiSky: new THREE.Color("#4a5d96"),
    hemiGround: new THREE.Color("#141018"),
    sunDir: new THREE.Vector3(-600, 700, -500).normalize(),
    sunIntensity: 1.1,
    sunDisc: 0,
    hemiIntensity: 2.4,
    stars: 1,
    cloud: 0,
    fogNear: FOG_NEAR,
    fogFar: FOG_FAR,
    windowGlow: 1,
    bloom: 1.1,
    rain: 0,
    wet: 0,
    lightning: 0,
    brightness: 0.035,
    saturation: 0.1,
  };
}

export const ATMOS: Atmosphere = make();
export const ATMOS_TARGET: Atmosphere = make();

interface TimeLook {
  skyTop: string;
  skyHorizon: string;
  glow: string;
  sunColor: string;
  hemiSky: string;
  hemiGround: string;
  sunIntensity: number;
  sunDisc: number;
  hemiIntensity: number;
  stars: number;
  windowGlow: number;
  bloom: number;
  brightness: number;
  saturation: number;
  fogNear: number;
  fogFar: number;
}

const TIME_LOOKS: Record<TimeOfDay, TimeLook> = {
  night: {
    skyTop: SKY_TOP,
    skyHorizon: SKY_HORIZON,
    glow: CITY_GLOW,
    sunColor: "#8ea4d8",
    hemiSky: "#4a5d96",
    hemiGround: "#141018",
    sunIntensity: 1.1,
    sunDisc: 0,
    hemiIntensity: 2.4,
    stars: 1,
    windowGlow: 1,
    bloom: 1.1,
    brightness: 0.035,
    saturation: 0.1,
    fogNear: FOG_NEAR,
    fogFar: FOG_FAR,
  },
  dusk: {
    skyTop: "#141a3a",
    skyHorizon: "#c86a3c",
    glow: "#ff9a4a",
    sunColor: "#ff9b5a",
    hemiSky: "#6a5b8e",
    hemiGround: "#2a1d1a",
    sunIntensity: 2.0,
    sunDisc: 1,
    hemiIntensity: 2.0,
    stars: 0.25,
    windowGlow: 0.7,
    bloom: 0.9,
    brightness: 0.03,
    saturation: 0.18,
    fogNear: FOG_NEAR,
    fogFar: FOG_FAR,
  },
  day: {
    skyTop: "#1d6fd8",
    skyHorizon: "#a9d6f2",
    glow: "#ffb56a",
    sunColor: "#fff0cf",
    hemiSky: "#b4d4f2",
    hemiGround: "#9a8b73",
    sunIntensity: 4.2,
    sunDisc: 1,
    hemiIntensity: 3.4,
    stars: 0,
    windowGlow: 0.02,
    bloom: 0.7,
    brightness: 0.03,
    saturation: 0.38,
    // Crisp daylight: the city stays sharp to the horizon on a clear day.
    fogNear: 800,
    fogFar: 4200,
  },
};

interface WeatherMod {
  cloud: number;
  dim: number;
  sun: number;
  grey: number;
  fogNear: number;
  fogFar: number;
  rain: number;
  wet: number;
  lightning: number;
}

const WEATHER_MODS: Record<WeatherKind, WeatherMod> = {
  clear: { cloud: 0.05, dim: 1, sun: 1, grey: 0, fogNear: FOG_NEAR, fogFar: FOG_FAR, rain: 0, wet: 0, lightning: 0 },
  cloudy: { cloud: 0.85, dim: 0.8, sun: 0.35, grey: 0.55, fogNear: 500, fogFar: 2600, rain: 0, wet: 0, lightning: 0 },
  rain: { cloud: 0.95, dim: 0.6, sun: 0.2, grey: 0.75, fogNear: 300, fogFar: 1800, rain: 1, wet: 1, lightning: 0 },
  fog: { cloud: 0.9, dim: 0.75, sun: 0.3, grey: 0.9, fogNear: 30, fogFar: 320, rain: 0, wet: 0.5, lightning: 0 },
  storm: { cloud: 1, dim: 0.42, sun: 0.1, grey: 0.8, fogNear: 260, fogFar: 1500, rain: 1.8, wet: 1, lightning: 1 },
};

const GREY = new THREE.Color();
const scratch = new THREE.Color();

function greyOf(c: THREE.Color, amount: number, dim: number) {
  const l = c.r * 0.3 + c.g * 0.59 + c.b * 0.11;
  GREY.setRGB(l, l, l * 1.08);
  return c.lerp(GREY, amount).multiplyScalar(dim);
}

export interface AtmosphereInput {
  weather: WeatherKind;
  time: TimeOfDay;
  /** Unit vector toward the sun (or moon at night); y < 0 when below the horizon. */
  sunDir: THREE.Vector3;
  /** Live Open-Meteo visibility in metres; only matters for fog. */
  visibilityM: number | null;
}

/** Writes the target look for the given conditions into ATMOS_TARGET (no allocation). */
export function setAtmosphereTarget(input: AtmosphereInput) {
  const look = TIME_LOOKS[input.time];
  const mod = WEATHER_MODS[input.weather];
  const t = ATMOS_TARGET;
  greyOf(t.skyTop.set(look.skyTop), mod.grey, mod.dim);
  greyOf(t.skyHorizon.set(look.skyHorizon), mod.grey, mod.dim);
  t.glow.set(look.glow).multiplyScalar(mod.dim);
  if (input.weather === "fog") {
    scratch.copy(t.skyHorizon).lerp(t.skyTop, 0.2);
    t.fogColor.copy(scratch);
    t.skyHorizon.copy(scratch);
    t.skyTop.lerp(scratch, 0.85);
  } else {
    t.fogColor.copy(t.skyHorizon);
  }
  t.sunColor.set(look.sunColor);
  greyOf(t.hemiSky.set(look.hemiSky), mod.grey * 0.6, mod.dim);
  t.hemiGround.set(look.hemiGround).multiplyScalar(mod.dim);
  t.sunDir.copy(input.sunDir);
  if (t.sunDir.y < 0.05) {
    // Below the horizon: a dusk sun rests on the horizon at its azimuth, night keeps a high moon-light.
    if (look.sunDisc > 0) t.sunDir.setY(0.07).normalize();
    else t.sunDir.set(-0.52, 0.6, -0.43).normalize();
  }
  t.sunIntensity = look.sunIntensity * mod.sun;
  t.sunDisc = look.sunDisc * (1 - mod.cloud);
  t.hemiIntensity = look.hemiIntensity * (0.75 + 0.25 * mod.dim);
  t.stars = look.stars * (1 - mod.cloud);
  t.cloud = mod.cloud;
  // Weather pulls the time-of-day distances in by the same ratio it pulls the night defaults;
  // fog is absolute so daylight never thins it.
  const foggy = input.weather === "fog";
  t.fogNear = foggy ? mod.fogNear : look.fogNear * (mod.fogNear / FOG_NEAR);
  if (!foggy) t.fogFar = look.fogFar * (mod.fogFar / FOG_FAR);
  else t.fogFar = input.visibilityM !== null ? THREE.MathUtils.clamp(input.visibilityM, 150, 1200) : mod.fogFar;
  t.windowGlow = Math.min(1, look.windowGlow + (1 - mod.dim) * 0.5);
  t.bloom = look.bloom + mod.rain * 0.1;
  t.rain = mod.rain;
  t.wet = mod.wet;
  t.lightning = mod.lightning;
  t.brightness = look.brightness;
  t.saturation = look.saturation;
}

/** Exponential approach: ~95 % of the way after `tau * 3` seconds. */
export function lerpAtmosphere(dt: number, tau = 0.5) {
  const k = 1 - Math.exp(-dt / tau);
  const a = ATMOS;
  const t = ATMOS_TARGET;
  a.skyTop.lerp(t.skyTop, k);
  a.skyHorizon.lerp(t.skyHorizon, k);
  a.glow.lerp(t.glow, k);
  a.fogColor.lerp(t.fogColor, k);
  a.sunColor.lerp(t.sunColor, k);
  a.hemiSky.lerp(t.hemiSky, k);
  a.hemiGround.lerp(t.hemiGround, k);
  a.sunDir.lerp(t.sunDir, k).normalize();
  a.sunIntensity += (t.sunIntensity - a.sunIntensity) * k;
  a.sunDisc += (t.sunDisc - a.sunDisc) * k;
  a.hemiIntensity += (t.hemiIntensity - a.hemiIntensity) * k;
  a.stars += (t.stars - a.stars) * k;
  a.cloud += (t.cloud - a.cloud) * k;
  a.fogNear += (t.fogNear - a.fogNear) * k;
  a.fogFar += (t.fogFar - a.fogFar) * k;
  a.windowGlow += (t.windowGlow - a.windowGlow) * k;
  a.bloom += (t.bloom - a.bloom) * k;
  a.rain += (t.rain - a.rain) * k;
  a.wet += (t.wet - a.wet) * k;
  a.brightness += (t.brightness - a.brightness) * k;
  a.saturation += (t.saturation - a.saturation) * k;
}
