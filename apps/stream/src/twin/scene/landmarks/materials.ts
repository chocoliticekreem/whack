import * as THREE from "three";
import { ATMOS } from "../palette";

export type MatKey =
  | "stone"
  | "stoneLit"
  | "white"
  | "roof"
  | "steel"
  | "deck"
  | "concrete"
  | "glass"
  | "glassW"
  | "chain"
  | "lamp"
  | "gold"
  | "beacon"
  | "brick"
  | "ballast"
  | "track"
  | "platformLight"
  | "haze"
  | "hazeLit"
  | "farGlow"
  | "farWarm"
  | "hull";

/** One window texture tile covers COLS x ROWS cells of CELL_W x CELL_H metres. */
export const WINDOW_TILE = { cols: 8, rows: 16, cellW: 3.2, cellH: 3.6, w: 25.6, h: 57.6 };

function windowTexture(): THREE.CanvasTexture {
  const px = 16;
  const canvas = document.createElement("canvas");
  canvas.width = WINDOW_TILE.cols * px;
  canvas.height = WINDOW_TILE.rows * px;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  let s = 7;
  const rand = () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
  const colours = ["#ffc36e", "#ffb45a", "#ffd9a0", "#bcd4ff", "#fff1d6"];
  for (let r = 0; r < WINDOW_TILE.rows; r++) {
    for (let c = 0; c < WINDOW_TILE.cols; c++) {
      if (rand() > 0.42) continue;
      ctx.fillStyle = colours[Math.floor(rand() * colours.length)];
      ctx.fillRect(c * px + 4, r * px + 4, px - 8, px - 7);
    }
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

function latticeTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, 128, 128);
  ctx.strokeStyle = "#9ec8ff";
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(128, 128);
  ctx.moveTo(-64, 64);
  ctx.lineTo(64, 192);
  ctx.moveTo(64, -64);
  ctx.lineTo(192, 64);
  ctx.moveTo(128, 0);
  ctx.lineTo(0, 128);
  ctx.moveTo(192, 64);
  ctx.lineTo(64, 192);
  ctx.moveTo(64, -64);
  ctx.lineTo(-64, 64);
  ctx.stroke();
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export type Mats = Record<MatKey, THREE.Material> & { lattice: THREE.MeshStandardMaterial };

let cached: Mats | null = null;

export function getMats(): Mats {
  if (cached) return cached;
  const win = windowTexture();
  const winWorld = win.clone();
  winWorld.repeat.set(1 / WINDOW_TILE.w, 1 / WINDOW_TILE.h);
  winWorld.needsUpdate = true;
  const lattice = latticeTexture();
  lattice.repeat.set(10, 12);

  const std = (p: THREE.MeshStandardMaterialParameters) => new THREE.MeshStandardMaterial(p);
  const basic = (r: number, g: number, b: number, fog = true) =>
    new THREE.MeshBasicMaterial({ color: new THREE.Color(r, g, b), toneMapped: false, fog });

  cached = {
    stone: std({ color: "#8d8778", roughness: 0.9, metalness: 0.05, emissive: "#cdbf9f" }),
    stoneLit: std({ color: "#85806f", roughness: 0.9, metalness: 0.05, emissive: "#ffffff", emissiveMap: win }),
    white: std({ color: "#d6cdb6", roughness: 0.85, emissive: "#ffc890" }),
    roof: std({ color: "#3c4860", roughness: 0.55, metalness: 0.35, emissive: "#1c2c4c" }),
    steel: std({ color: "#2b5ca8", roughness: 0.45, metalness: 0.45, emissive: "#2f6fd6" }),
    deck: std({ color: "#17181e", roughness: 0.9, emissive: "#4a3322" }),
    concrete: std({ color: "#555860", roughness: 0.95, emissive: "#3a3026" }),
    glass: std({ color: "#26324a", roughness: 0.4, metalness: 0.1, emissive: "#ffffff", emissiveMap: win }),
    glassW: std({ color: "#26324a", roughness: 0.4, metalness: 0.1, emissive: "#ffffff", emissiveMap: winWorld }),
    chain: basic(0.7, 1.0, 2.2),
    lamp: basic(2.2, 1.4, 0.7),
    gold: basic(2.6, 1.9, 0.7),
    beacon: basic(3, 0.3, 0.2),
    brick: std({ color: "#503d36", roughness: 0.95, emissive: "#2a1810" }),
    ballast: std({ color: "#1d1f25", roughness: 1, emissive: "#202838" }),
    track: std({ color: "#7a8494", roughness: 0.4, metalness: 0.85, emissive: "#2a4472" }),
    platformLight: basic(1.1, 1.5, 2.2),
    haze: std({ color: "#141a33", roughness: 1, fog: false }),
    hazeLit: std({ color: "#141a33", roughness: 1, fog: false, emissive: "#ffffff", emissiveMap: win }),
    farGlow: basic(0.9, 1.6, 3.2, false),
    farWarm: basic(3, 2.2, 1.0, false),
    hull: std({ color: "#5a6672", roughness: 0.7, metalness: 0.3, emissive: "#202a38" }),
    lattice: std({ color: "#1d3a44", roughness: 0.4, metalness: 0.1, emissive: "#ffffff", emissiveMap: lattice }),
  };
  return cached;
}

const _c = new THREE.Color();
const _day = new THREE.Color();

/** Called once per frame: window / flood light levels follow the time of day and weather. */
export function updateLandmarkMaterials(time: number) {
  if (!cached) return;
  const g = ATMOS.windowGlow;
  const m = cached;
  const set = (key: MatKey | "lattice", intensity: number) => {
    (m[key] as THREE.MeshStandardMaterial).emissiveIntensity = intensity;
  };
  set("stone", 0.03 + 0.14 * g);
  set("stoneLit", 0.04 + 0.55 * g);
  set("white", 0.08 + 0.55 * g);
  set("roof", 0.1 + 0.4 * g);
  set("steel", 0.12 + 0.9 * g);
  set("deck", 0.2 + 0.5 * g);
  set("concrete", 0.05 + 0.25 * g);
  set("glass", 0.05 + 1.7 * g);
  set("glassW", 0.05 + 1.7 * g);
  set("lattice", 0.08 + 1.6 * g);
  set("brick", 0.1 + 0.4 * g);
  set("ballast", 0.2 + 0.8 * g);
  set("track", 0.2 + 0.9 * g);
  set("hull", 0.1 + 0.4 * g);
  set("hazeLit", 0.04 + 1.4 * g);

  const scale = (key: MatKey, base: [number, number, number], floor: number) => {
    const k = floor + (1 - floor) * Math.min(1, g * 1.2);
    (m[key] as THREE.MeshBasicMaterial).color.setRGB(base[0] * k, base[1] * k, base[2] * k);
  };
  scale("chain", [0.7, 1.0, 2.2], 0.3);
  scale("lamp", [2.2, 1.4, 0.7], 0.015);
  scale("gold", [2.6, 1.9, 0.7], 0.5);
  scale("platformLight", [1.1, 1.5, 2.2], 0.12);
  scale("farGlow", [0.9, 1.6, 3.2], 0.2);
  scale("farWarm", [3, 2.2, 1], 0.2);

  const blink = 0.35 + 0.65 * Math.max(0, Math.sin(time * 2.4));
  (m.beacon as THREE.MeshBasicMaterial).color.setRGB(3 * blink, 0.3 * blink, 0.2 * blink);

  _c.set("#26324a").lerp(_day.set("#8aa8cc"), 1 - Math.min(1, g * 1.5));
  (m.glass as THREE.MeshStandardMaterial).color.copy(_c);
  (m.glassW as THREE.MeshStandardMaterial).color.copy(_c);
  (m.lattice as THREE.MeshStandardMaterial).color.copy(_c);

  _c.copy(ATMOS.fogColor).lerp(ATMOS.skyTop, 0.3);
  (m.haze as THREE.MeshStandardMaterial).color.copy(_c).multiplyScalar(0.9);
  (m.hazeLit as THREE.MeshStandardMaterial).color.copy(_c).multiplyScalar(0.9);
}
