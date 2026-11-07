import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

/**
 * London double-decker, shared by every <Bus>. Bus-local space: y up, +z is the front, +x the
 * nearside (door side). Geometry is merged per material and built lazily, so a bus costs four
 * draw calls: bodywork (vertex-coloured), window glass, lamps and the destination blinds.
 */
export const BUS_L = 10.5;
export const BUS_W = 2.5;
export const BUS_H = 4.4;

const HALF_L = 5.2;
const SIDE_PANEL_U = 0.34;

const RED = "#d3201b";
const DARK = "#26282b";
const ARCH = "#0b0b0d";
const TYRE = "#222326";
const HUB = "#9aa0a6";
const CREAM = "#ece7de";
const GREY = "#c9cbcd";
const WHITE = "#f2f2ee";
/** Glass and lamp parts take their colour from the material, so vertex colour is a placeholder. */
const UNTINTED = "#ffffff";
const PLATE_REAR = "#f2c230";

export interface BusKit {
  body: THREE.BufferGeometry;
  glass: THREE.BufferGeometry;
  lamps: THREE.BufferGeometry;
  blinds: THREE.BufferGeometry;
  bodyMat: THREE.MeshStandardMaterial;
  glassMat: THREE.MeshStandardMaterial;
  lampMat: THREE.MeshBasicMaterial;
}

function painted(geo: THREE.BufferGeometry, hex: string, gain = 1) {
  const c = new THREE.Color(hex).multiplyScalar(gain);
  const n = geo.getAttribute("position").count;
  const colors = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    colors[i * 3] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;
  }
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  return geo;
}

class Parts {
  readonly list: THREE.BufferGeometry[] = [];

  box(hex: string, w: number, h: number, l: number, x: number, y: number, z: number, gain = 1) {
    this.list.push(painted(new THREE.BoxGeometry(w, h, l).translate(x, y, z), hex, gain));
  }

  /** Cylinder whose axis runs along x (a wheel). */
  wheel(hex: string, r: number, width: number, x: number, y: number, z: number) {
    this.list.push(painted(new THREE.CylinderGeometry(r, r, width, 18).rotateZ(Math.PI / 2).translate(x, y, z), hex));
  }

  merge() {
    const merged = mergeGeometries(this.list, false);
    for (const g of this.list) g.dispose();
    return merged;
  }
}

function buildBody() {
  const p = new Parts();
  const door = { z0: 2.1, z1: 3.5 };

  // Underbody, skirt and bumpers: two stacked boxes give the chamfered plan.
  p.box(DARK, 2.46, 0.5, 10.0, 0, 0.55, 0);
  p.box(DARK, 2.2, 0.5, 10.4, 0, 0.55, 0);
  for (const s of [1, -1]) p.box(DARK, 2.34, 0.3, 0.34, 0, 0.58, s * 5.1);

  // Red bodywork: lower belt, solid core behind the window bands, mid belt between the decks.
  for (const [y, h, w] of [[1.05, 0.5, 2.5], [1.8, 1.0, 2.46], [2.55, 0.5, 2.5], [3.3, 1.0, 2.46]] as const) {
    p.box(RED, w, h, 10.0, 0, y, 0);
    p.box(RED, 2.2, h, 10.4, 0, y, 0);
  }

  // Roof: red cap in two steps, cream roof slab, rooftop AC units.
  p.box(RED, 2.46, 0.25, 10.2, 0, 3.925, 0);
  p.box(RED, 2.2, 0.25, 10.42, 0, 3.925, 0);
  p.box(CREAM, 2.2, 0.1, 9.8, 0, 4.1, 0);
  p.box(GREY, 1.2, 0.22, 2.4, 0, 4.26, -2.0);
  p.box(GREY, 0.6, 0.1, 0.9, 0, 4.2, 1.4);

  // Window pillars every ~1.1 m, standing slightly proud of the glass.
  for (let i = 0; i < 9; i++) {
    const z = -4.4 + i * 1.1;
    for (const s of [1, -1]) {
      p.box(RED, 0.08, 1.0, 0.12, s * 1.26, 3.3, z);
      if (s < 0 || z < door.z0 - 0.15 || z > door.z1 + 0.15) p.box(RED, 0.08, 1.0, 0.12, s * 1.26, 1.8, z);
    }
  }

  // Nearside door frame.
  p.box(RED, 0.08, 1.5, 0.1, 1.27, 1.55, door.z0);
  p.box(RED, 0.08, 1.5, 0.1, 1.27, 1.55, door.z1);
  p.box(RED, 0.08, 1.5, 0.08, 1.27, 1.55, (door.z0 + door.z1) / 2);
  p.box(DARK, 0.06, 0.1, door.z1 - door.z0, 1.27, 0.84, (door.z0 + door.z1) / 2);

  // Front/rear panels: windscreen centre pillars, plates, grille.
  for (const y of [1.8, 3.3]) p.box(RED, 0.1, 0.96, 10.46, 0, y, 0);
  p.box(DARK, 1.3, 0.5, 0.04, 0, 1.8, -HALF_L - 0.005);
  p.box(WHITE, 0.62, 0.16, 0.04, 0, 0.95, HALF_L + 0.02);
  p.box(PLATE_REAR, 0.62, 0.16, 0.04, 0, 0.95, -HALF_L - 0.02);

  // Wheels with arches, wing mirrors.
  for (const z of [3.3, -2.9]) {
    p.wheel(ARCH, 0.6, 2.56, 0, 0.5, z);
    for (const s of [1, -1]) {
      p.wheel(TYRE, 0.5, 0.4, s * 1.12, 0.5, z);
      p.wheel(HUB, 0.28, 0.46, s * 1.12, 0.5, z);
    }
  }
  for (const s of [1, -1]) {
    p.box(DARK, 0.55, 0.04, 0.04, s * 1.5, 2.1, 4.95);
    p.box(DARK, 0.06, 0.5, 0.22, s * 1.78, 2.1, 4.95);
  }
  return p.merge();
}

function buildGlass() {
  const p = new Parts();
  p.box(UNTINTED, 2.5, 1.0, 9.0, 0, 1.8, -0.1);
  p.box(UNTINTED, 2.5, 1.0, 9.4, 0, 3.3, 0);
  p.box(UNTINTED, 2.0, 0.9, 0.6, 0, 1.8, 4.92);
  p.box(UNTINTED, 2.0, 0.92, 10.44, 0, 3.3, 0);
  // Nearside door glass, split by the centre pillar.
  p.box(UNTINTED, 0.05, 1.4, 1.3, 1.27, 1.55, 2.8);
  return p.merge();
}

function buildLamps() {
  const p = new Parts();
  for (const s of [1, -1]) {
    p.box("#ffffff", 0.46, 0.22, 0.06, s * 0.78, 1.05, HALF_L + 0.03, 1.4);
    p.box("#ffa21a", 0.14, 0.22, 0.06, s * 1.02, 1.05, HALF_L + 0.03, 1.1);
    p.box("#ff1a0f", 0.36, 0.24, 0.06, s * 0.9, 1.05, -HALF_L - 0.03, 1.2);
    p.box("#ff1a0f", 0.1, 0.8, 0.06, s * 1.06, 1.85, -HALF_L - 0.03, 1.0);
    p.box("#ffa21a", 0.14, 0.14, 0.06, s * 1.02, 1.35, -HALF_L - 0.03, 1.1);
  }
  return p.merge();
}

function buildBlinds() {
  const list: THREE.BufferGeometry[] = [];
  list.push(new THREE.PlaneGeometry(1.7, 0.4).translate(0, 2.55, HALF_L + 0.015));
  list.push(new THREE.PlaneGeometry(1.7, 0.4).rotateY(Math.PI).translate(0, 2.55, -HALF_L - 0.015));
  for (const s of [1, -1]) {
    const panel = new THREE.PlaneGeometry(0.56, 0.4);
    const uv = panel.getAttribute("uv");
    for (let i = 0; i < uv.count; i++) uv.setX(i, uv.getX(i) * SIDE_PANEL_U);
    list.push(panel.rotateY((s * Math.PI) / 2).translate(s * 1.265, 2.55, 3.9));
  }
  const merged = mergeGeometries(list, false);
  for (const g of list) g.dispose();
  return merged;
}

let kit: BusKit | null = null;

export function getBusKit(): BusKit {
  if (kit) return kit;
  kit = {
    body: buildBody(),
    glass: buildGlass(),
    lamps: buildLamps(),
    blinds: buildBlinds(),
    bodyMat: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.45, metalness: 0.12 }),
    glassMat: new THREE.MeshStandardMaterial({
      color: "#1a2736",
      roughness: 0.15,
      metalness: 0.15,
      emissive: new THREE.Color("#ffc980"),
      emissiveIntensity: 0,
    }),
    lampMat: new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false }),
  };
  return kit;
}

/** Shared by every bus: window glass, lamps and blinds light up with the scene's night level. */
export function setBusGlow(glow: number) {
  const { glassMat, lampMat } = getBusKit();
  glassMat.emissiveIntensity = glow * 1.0;
  lampMat.color.setScalar(0.7 + glow * 2.6);
  for (let i = 0; i < blindMaterials.length; i++) blindMaterials[i].color.setScalar(0.85 + glow * 0.9);
}

function fitFont(g: CanvasRenderingContext2D, text: string, maxW: number, startPx: number) {
  let px = startPx;
  g.font = `bold ${px}px Arial, Helvetica, sans-serif`;
  while (px > 20 && g.measureText(text).width > maxW) {
    px -= 2;
    g.font = `bold ${px}px Arial, Helvetica, sans-serif`;
  }
}

/** Amber dot-matrix blind: route number on the left (the side panels crop to it), destination right. */
function blindTexture(line: string, destination: string) {
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 128;
  const g = c.getContext("2d")!;
  g.fillStyle = "#050505";
  g.fillRect(0, 0, 512, 128);
  g.fillStyle = "#ffb21a";
  g.textBaseline = "middle";
  g.textAlign = "center";
  fitFont(g, line.toUpperCase(), 150, 96);
  g.fillText(line.toUpperCase(), (SIDE_PANEL_U * 512) / 2, 68);
  g.fillRect(SIDE_PANEL_U * 512 + 2, 18, 4, 92);
  g.textAlign = "left";
  const dest = destination.toUpperCase();
  fitFont(g, dest, 512 - SIDE_PANEL_U * 512 - 36, 64);
  g.fillText(dest, SIDE_PANEL_U * 512 + 22, 68);
  g.fillStyle = "#000";
  for (let x = 0; x < 512; x += 4) g.fillRect(x, 0, 1.4, 128);
  for (let y = 0; y < 128; y += 4) g.fillRect(0, y, 512, 1.4);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

const blindCache = new Map<string, THREE.MeshBasicMaterial>();
/** Every blind material, so the single per-frame update can walk them without allocating. */
export const blindMaterials: THREE.MeshBasicMaterial[] = [];

export function blindMaterial(line: string, destination: string) {
  const key = `${line}|${destination}`;
  let mat = blindCache.get(key);
  if (!mat) {
    mat = new THREE.MeshBasicMaterial({ map: blindTexture(line, destination), toneMapped: false });
    blindCache.set(key, mat);
    blindMaterials.push(mat);
  }
  return mat;
}
