import * as THREE from "three";

/** Deterministic PRNG so the same footprint always gets the same look. */
export function seededRandom(seed: number) {
  let a = (seed * 2654435761) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const c = (hex: string) => new THREE.Color(hex);

const BRICK = ["#8a5a44", "#7d4f3c", "#9c6b50", "#6b4636", "#755244", "#8c6246"].map(c);
/** London stock: the pale yellow-brown brick of Georgian and Victorian terraces. */
const YELLOW_STOCK = ["#b09a72", "#a8946c", "#9d8a68", "#b7a47c"].map(c);
const SOOT = ["#6a6058", "#756c64", "#5f5851"].map(c);
const PORTLAND = ["#d2cab6", "#c9c1ac", "#d8d1c0", "#c2baa6"].map(c);
const RENDER = ["#e2ddd0", "#d6cfbe", "#cfc8b8", "#e6dcc6"].map(c);
const CONCRETE = ["#7c7f84", "#9a9c9e", "#8a8c8e", "#a6a49e"].map(c);
const GLASS = ["#41607f", "#4a6a86", "#4d6872", "#3f6c70", "#3a5878"].map(c);
const ROOF = ["#6d7176", "#7a7e82", "#62666b"].map(c);
const GRAVEL = ["#8d8a83", "#9a968c"].map(c);
const LEAD = ["#7f8a92", "#8a949b"].map(c);
const GREEN_ROOF = ["#587a43", "#668650", "#4f703e"].map(c);
const SLATE = ["#5d636d", "#535963", "#666c78"].map(c);
const TERRACOTTA = ["#8a5545", "#7c4d3f", "#8f5f4a", "#96624c"].map(c);

export type Material = "brick" | "stone" | "concrete" | "glass" | "render";

/** Shader style code for the wall surface of each material. */
export const WALL_STYLE: Record<Material, number> = { brick: 1, glass: 2, stone: 6, concrete: 8, render: 9 };

/** Darkest day albedo (linear luminance) a wall may have, so nothing reads black in sunlight. */
const MIN_ALBEDO = 0.1;

export const STONE_TRIM = c("#b2aa9a");
export const METAL_TRIM = c("#46515b");
export const MAST = c("#a4acb3");
export const PLINTH_STONE = c("#5d5a56");
export const PLANT = c("#8a8d92");
export const BITUMEN = c("#2c2f33");
export const GLASS_PLINTH = c("#34424f");

function pick<T>(list: T[], r: number): T {
  return list[Math.min(list.length - 1, Math.floor(r * list.length))];
}

/** Material family from height and a roll; tall buildings skew to glass, small ones to brick, civic mid-rise to Portland stone. */
export function chooseMaterial(height: number, roll: number): Material {
  if (height > 60) return roll < 0.55 ? "glass" : roll < 0.8 ? "concrete" : roll < 0.9 ? "stone" : "brick";
  if (height > 25) return roll < 0.15 ? "glass" : roll < 0.4 ? "concrete" : roll < 0.65 ? "stone" : roll < 0.75 ? "render" : "brick";
  return roll < 0.62 ? "brick" : roll < 0.78 ? "render" : roll < 0.88 ? "stone" : "concrete";
}

/** Linear-space wall albedo with per-building variation, blending any tagged OSM colour in. */
export function wallColour(material: Material, tagged: string | undefined, rnd: () => number, out: THREE.Color): THREE.Color {
  const roll = rnd();
  if (material === "glass") out.copy(pick(GLASS, roll));
  else if (material === "concrete") out.copy(pick(CONCRETE, roll));
  else if (material === "render") out.copy(pick(RENDER, roll));
  else if (material === "stone") out.copy(pick(PORTLAND, roll));
  else out.copy(roll < 0.5 ? pick(BRICK, rnd()) : roll < 0.78 ? pick(YELLOW_STOCK, rnd()) : pick(SOOT, rnd()));
  if (tagged && material !== "glass") {
    try {
      out.lerp(new THREE.Color(tagged), 0.5);
    } catch {
      // unparsable OSM colour: keep the palette pick
    }
  }
  out.offsetHSL((rnd() - 0.5) * 0.03, (rnd() - 0.5) * 0.1, (rnd() - 0.5) * 0.08);
  const lum = 0.2126 * out.r + 0.7152 * out.g + 0.0722 * out.b;
  if (lum < MIN_ALBEDO) out.multiplyScalar(MIN_ALBEDO / Math.max(lum, 1e-3));
  return out;
}

/** Roof surface: slate or terracotta when pitched; flat roofs are bitumen, gravel, lead or (on low-rise) green. */
export function roofColour(pitched: boolean, height: number, rnd: () => number, out: THREE.Color): THREE.Color {
  if (pitched) return out.copy(rnd() < 0.55 ? pick(SLATE, rnd()) : pick(TERRACOTTA, rnd()));
  const roll = rnd();
  if (height < 45 && roll < 0.14) return out.copy(pick(GREEN_ROOF, rnd()));
  if (roll < 0.4) return out.copy(pick(GRAVEL, rnd()));
  if (roll < 0.6) return out.copy(pick(LEAD, rnd()));
  return out.copy(pick(ROOF, rnd()));
}
