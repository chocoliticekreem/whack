import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

/** One merged mesh per layer; each layer gets its own material (see materials.ts). */
export type Layer = "body" | "glass" | "lit" | "strip";

export const LAYERS: Layer[] = ["body", "glass", "lit", "strip"];

export interface SignSpec {
  /** Lines separated by "\n". */
  text: string;
  x: number;
  y: number;
  z: number;
  w: number;
  h: number;
  /** Rotation about y; 0 faces +z (south), Math.PI faces north. */
  rotY: number;
  fg: string;
  bg: string;
  /** Adds a mirrored back face (flags). */
  double?: boolean;
}

export interface SiteModel {
  geometry: Record<Layer, THREE.BufferGeometry | null>;
  signs: SignSpec[];
  /** Roof height in site metres, before the skyline scale-up. */
  topM: number;
}

const tint = new THREE.Color();

function paint(g: THREE.BufferGeometry, color: THREE.ColorRepresentation) {
  tint.set(color);
  const n = g.attributes.position.count;
  const rgb = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    rgb[i * 3] = tint.r;
    rgb[i * 3 + 1] = tint.g;
    rgb[i * 3 + 2] = tint.b;
  }
  g.setAttribute("color", new THREE.BufferAttribute(rgb, 3));
  return g;
}

export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Collects coloured boxes and cylinders per layer, then merges each layer into a single geometry. */
export class Kit {
  private parts: Record<Layer, THREE.BufferGeometry[]> = { body: [], glass: [], lit: [], strip: [] };

  /** Axis-aligned box: centre (x, z), bottom y. */
  box(layer: Layer, x: number, y: number, z: number, w: number, h: number, d: number, color: THREE.ColorRepresentation) {
    const g = new THREE.BoxGeometry(w, h, d).translate(x, y + h / 2, z);
    this.parts[layer].push(paint(g, color));
  }

  /** A box long in `along`, centred at (cAlong, cPerp) in that axis' frame. */
  slab(
    layer: Layer,
    along: "x" | "z",
    cAlong: number,
    cPerp: number,
    y: number,
    len: number,
    h: number,
    thick: number,
    color: THREE.ColorRepresentation,
  ) {
    if (along === "x") this.box(layer, cAlong, y, cPerp, len, h, thick, color);
    else this.box(layer, cPerp, y, cAlong, thick, h, len, color);
  }

  cylinder(layer: Layer, x: number, y: number, z: number, r: number, h: number, color: THREE.ColorRepresentation) {
    const g = new THREE.CylinderGeometry(r, r, h, 10).translate(x, y + h / 2, z);
    this.parts[layer].push(paint(g, color));
  }

  build(): Record<Layer, THREE.BufferGeometry | null> {
    const out = {} as Record<Layer, THREE.BufferGeometry | null>;
    for (const layer of LAYERS) {
      out[layer] = this.parts[layer].length ? mergeGeometries(this.parts[layer], false) : null;
      this.parts[layer].forEach((g) => g.dispose());
    }
    return out;
  }
}

/** A row of window panes along one wall: lit panes glow at night, unlit ones are dark glass. */
export function windowRow(
  k: Kit,
  rand: () => number,
  o: {
    along: "x" | "z";
    from: number;
    perp: number;
    dir: 1 | -1;
    y: number;
    bays: number;
    bay: number;
    paneW: number;
    paneH: number;
    litChance: number;
    lit?: THREE.ColorRepresentation[];
    litLayer?: Layer;
  },
) {
  const warm = o.lit ?? ["#ffc477", "#ffd9a0", "#ffb05e", "#fff0cf"];
  for (let b = 0; b < o.bays; b++) {
    const c = o.from + (b + 0.5) * o.bay;
    const on = rand() < o.litChance;
    if (on) k.slab(o.litLayer ?? "lit", o.along, c, o.perp + o.dir * 0.05, o.y, o.paneW, o.paneH, 0.06, warm[Math.floor(rand() * warm.length)]);
    else k.slab("glass", o.along, c, o.perp + o.dir * 0.03, o.y, o.paneW, o.paneH, 0.06, "#1b2433");
  }
}

/** A tall ground-hugging street lamp: post plus a glowing head. */
export function lamp(k: Kit, x: number, z: number, height = 5.2) {
  k.cylinder("body", x, 0, z, 0.1, height, "#1d2026");
  k.box("strip", x, height - 0.1, z, 0.9, 0.18, 0.5, "#ffd9a0");
}
