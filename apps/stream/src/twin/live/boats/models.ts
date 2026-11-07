import * as THREE from "three";
import { hull, Mesh } from "./mesh";

export type BoatKind = "clipper" | "sleek" | "cruise" | "tug" | "lighter" | "rib";

export interface BoatSpec {
  /** Length overall in world metres (model is built at real size then scaled by SCALE). */
  length: number;
  /** Too tall to pass under the closed bridge: waits for a lift. */
  tall: boolean;
  /** 0 = inner fast lane, 1 = middle, 2 = outer. */
  lane: 0 | 1 | 2;
  speed: [number, number];
  /** Wake length in metres at full speed, 0 for towed barges. */
  wake: number;
  /** How strongly the hull answers the waves (small, light craft more than heavy ones). */
  bob: number;
  /** How much the hull heels into turns. */
  lean: number;
}

/** Vessels are drawn slightly oversize so they read from the aerial camera. */
export const SCALE = 1.25;

const spec = (length: number, o: Omit<BoatSpec, "length">): BoatSpec => ({ length: length * SCALE, ...o });

export const SPECS: Record<BoatKind, BoatSpec> = {
  clipper: spec(36, { tall: false, lane: 0, speed: [10, 11.5], wake: 80, bob: 0.8, lean: 0.5 }),
  rib: spec(9, { tall: false, lane: 0, speed: [10, 13], wake: 50, bob: 1.6, lean: 1.4 }),
  sleek: spec(26, { tall: false, lane: 0, speed: [7.5, 9.5], wake: 55, bob: 1.2, lean: 0.8 }),
  cruise: spec(32, { tall: true, lane: 2, speed: [5.5, 7], wake: 45, bob: 0.9, lean: 0.4 }),
  tug: spec(15, { tall: false, lane: 1, speed: [3.2, 4.2], wake: 38, bob: 1.0, lean: 0.4 }),
  lighter: spec(30, { tall: false, lane: 1, speed: [3.2, 4.2], wake: 0, bob: 0.6, lean: 0.1 }),
};

export interface BoatParts {
  hull: Mesh;
  glass: Mesh;
  lamps: Mesh;
  strobe: Mesh;
}

const c = (hex: string) => new THREE.Color(hex);
const WHITE = c("#f1f3f6");
const OFFWHITE = c("#dfe4ea");
const NAVY = c("#10285c");
const DARK = c("#1b1d23");
const GREY = c("#7d8590");
const LIGHTGREY = c("#a9b0b9");
const TEAK = c("#9a7448");
const WOOD = c("#6d5033");
const LAMP_WHITE = c("#ffffff");
const LAMP_RED = c("#ff2020");
const LAMP_GREEN = c("#20ff70");
const STROBE = c("#3d7bff");
const GLASS = c("#ffffff");

function parts(): BoatParts {
  return { hull: new Mesh(), glass: new Mesh(), lamps: new Mesh(), strobe: new Mesh() };
}

/** Rows of lit cabin windows on both flanks. */
function windows(p: BoatParts, halfW: number, y0: number, h: number, z0: number, z1: number, n: number, fill = 0.68) {
  const pitch = (z1 - z0) / n;
  for (let i = 0; i < n; i++) {
    const z = z0 + pitch * (i + 0.5);
    p.glass.box(GLASS, halfW, y0, z, 0.14, h, pitch * fill);
    p.glass.box(GLASS, -halfW, y0, z, 0.14, h, pitch * fill);
  }
}

/** Sloped windscreen on a raked front: bottom edge at z = zBottom (y0), top edge raked back by `rake` over `h`. */
function windscreen(p: BoatParts, halfW: number, y0: number, h: number, zBottom: number, rake: number, fromFrac = 0.28, toFrac = 0.82) {
  const at = (f: number): [number, number] => [y0 + h * f, zBottom - rake * f + 0.05];
  const [ya, za] = at(fromFrac);
  const [yb, zb] = at(toFrac);
  p.glass.quad([-halfW, ya, za], [halfW, ya, za], [halfW, yb, zb], [-halfW, yb, zb], GLASS);
}

function nav(p: BoatParts, halfW: number, y: number, z: number, mastY: number, mastZ: number) {
  p.lamps.box(LAMP_RED, halfW, y, z, 0.4, 0.4, 0.4);
  p.lamps.box(LAMP_GREEN, -halfW, y, z, 0.4, 0.4, 0.4);
  p.lamps.box(LAMP_WHITE, 0, mastY, mastZ, 0.45, 0.45, 0.45);
}

function clipper(): BoatParts {
  const p = parts();
  for (const x0 of [-3.5, 3.5]) {
    hull(p.hull, { length: 36, beam: 2.8, top: 1.7, draft: 0.8, upper: WHITE, lower: NAVY, deck: OFFWHITE, stripeY: 1.0, x0, bowFrac: 0.55, sternW: 0.9, sheer: 0.4 });
  }
  p.hull.box(OFFWHITE, 0, 1.45, -1, 9.8, 0.5, 30, 0, 0);
  p.hull.box(WHITE, 0, 1.95, -2, 9.2, 2.1, 27, 4, 0);
  p.hull.box(NAVY, 0, 1.95, -2, 9.3, 0.5, 27.05);
  p.hull.box(WHITE, 0, 4.05, 2, 6.4, 1.7, 9, 2.5, 0);
  p.hull.box(DARK, 0, 5.75, 1.6, 6.6, 0.12, 8.2);
  windows(p, 4.62, 2.65, 0.95, -14, 9, 14);
  windows(p, 3.22, 4.5, 0.9, -1.5, 3.5, 4);
  windscreen(p, 2.7, 4.05, 1.7, 6.5, 2.5);
  p.hull.rod(GREY, [0, 5.8, 0.5], [0, 8.2, 0.5], 0.12, 0.08, 5);
  p.hull.box(DARK, 0, 7.6, 0.5, 1.8, 0.1, 0.18);
  nav(p, 3.35, 5.2, 4.0, 8.35, 0.5);
  return p;
}

interface YachtSpec {
  length: number;
  beam: number;
  top: number;
  draft: number;
  upper: THREE.Color;
  lower: THREE.Color;
  super: THREE.Color;
  stripeY: number;
  decks: number;
  deckH: number;
  mast: number;
}

function yacht(o: YachtSpec): BoatParts {
  const p = parts();
  const L = o.length;
  hull(p.hull, { length: L, beam: o.beam, top: o.top, draft: o.draft, upper: o.upper, lower: o.lower, deck: TEAK, stripeY: o.stripeY, bowFrac: 0.62, sternW: 0.85, sheer: 0.7 });
  let roofY = o.top;
  let zMast = 0;
  let frontZ = 0;
  let rake = 0;
  let topHalfW = 0;
  for (let k = 0; k < o.decks; k++) {
    const w = o.beam * (0.84 - 0.12 * k);
    const zBack = -L * (0.42 - 0.04 * k);
    const zFront = L * (0.2 - 0.1 * k);
    const zc = (zBack + zFront) / 2;
    const y0 = o.top + k * o.deckH - 0.05;
    rake = L * 0.1;
    p.hull.box(o.super, 0, y0, zc, w, o.deckH, zFront - zBack, rake, 0, 0.96);
    p.hull.box(DARK, 0, y0 + o.deckH - 0.12, zc, w + 0.1, 0.14, zFront - zBack - rake * 0.5, 0, 0);
    const n = Math.max(3, Math.round((zFront - zBack - rake - 3) / (o.deckH * 1.15)));
    windows(p, w / 2 + 0.02, y0 + o.deckH * 0.3, o.deckH * 0.42, zBack + 2, zFront - rake - 1, n);
    windscreen(p, w / 2 - 0.5, y0, o.deckH, zFront, rake);
    roofY = y0 + o.deckH;
    zMast = zc - (zFront - zBack) * 0.12;
    frontZ = zFront - rake;
    topHalfW = w / 2;
  }
  p.hull.box(OFFWHITE, 0, o.top, L * 0.3, o.beam * 0.4, 0.45, L * 0.1, 0, 0, 0.85);
  p.hull.rod(LIGHTGREY, [0, roofY, zMast], [0, roofY + o.mast, zMast], 0.2, 0.09, 6);
  p.hull.box(LIGHTGREY, 0, roofY + o.mast * 0.62, zMast, 3.4, 0.13, 0.16);
  p.hull.box(LIGHTGREY, 0, roofY + o.mast * 0.82, zMast, 2.2, 0.13, 0.16);
  p.hull.rod(WHITE, [0, roofY, zMast - 3.2], [0, roofY + 1.3, zMast - 3.2], 0.95, 0.95, 8);
  p.hull.rod(WHITE, [0, roofY + 1.3, zMast - 3.2], [0, roofY + 1.7, zMast - 3.2], 0.5, 0.05, 8);
  p.hull.rod(LIGHTGREY, [0, o.top, L * 0.47], [0, o.top + 1.8, L * 0.49], 0.07, 0.05, 4, false);
  nav(p, topHalfW - 0.2, roofY - o.deckH * 0.25, frontZ - 0.2, roofY + o.mast + 0.3, zMast);
  p.lamps.box(LAMP_WHITE, 0, o.top + 1.85, L * 0.49, 0.35, 0.35, 0.35);
  return p;
}

function cruise(): BoatParts {
  const p = parts();
  hull(p.hull, { length: 32, beam: 7.4, top: 1.4, draft: 1.1, upper: WHITE, lower: c("#1d4d8c"), deck: GREY, stripeY: 0.8, bowFrac: 0.66, sternW: 0.9, sheer: 0.45 });
  p.hull.box(WHITE, 0, 1.35, -1.5, 6.8, 2.3, 24);
  p.hull.box(c("#c9d2dc"), 0, 3.65, -1.5, 7.1, 0.2, 24.6);
  windows(p, 3.42, 1.95, 1.2, -12.5, 9.5, 11);
  for (let z = -12.5; z <= 10; z += 2.25) {
    for (const x of [-3.5, 3.5]) p.hull.rod(WHITE, [x, 3.85, z], [x, 4.85, z], 0.05, 0.05, 4, false);
  }
  for (const x of [-3.5, 3.5]) p.hull.rod(WHITE, [x, 4.85, -12.8], [x, 4.85, 10.8], 0.06, 0.06, 4, false);
  for (let z = -11; z <= 5; z += 2.2) {
    for (const x of [-1.8, 1.8]) p.hull.box(WOOD, x, 3.85, z, 2.2, 0.55, 0.75);
  }
  p.hull.box(WHITE, 0, 3.85, 7.8, 4.2, 2.2, 4.2, 1, 0);
  p.hull.box(DARK, 0, 6.05, 7.5, 4.5, 0.15, 4.5);
  windscreen(p, 1.9, 3.85, 2.2, 9.9, 1);
  windows(p, 2.12, 4.6, 0.95, 6, 9.6, 3);
  p.hull.rod(DARK, [0, 3.85, -6], [0, 6.1, -6.3], 0.6, 0.5, 8);
  p.hull.rod(c("#c9362f"), [0, 5.5, -6.2], [0, 5.9, -6.3], 0.62, 0.55, 8);
  p.hull.rod(GREY, [0, 6.2, 7.2], [0, 8.7, 7.2], 0.1, 0.07, 5);
  p.hull.box(DARK, 0, 7.9, 7.2, 1.5, 0.1, 0.18);
  nav(p, 2.2, 5.6, 9.2, 8.8, 7.2);
  return p;
}

function tug(): BoatParts {
  const p = parts();
  hull(p.hull, { length: 15, beam: 5.6, top: 1.6, draft: 1.9, upper: c("#8f2323"), lower: c("#14141a"), deck: c("#2a2b30"), stripeY: 0.5, bowFrac: 0.7, sternW: 0.9, sheer: 0.4 });
  p.hull.box(WHITE, 0, 1.55, -1.5, 4.0, 2.4, 5, 0.5, 0);
  p.hull.box(WHITE, 0, 3.95, -0.8, 3.2, 1.7, 3.2, 0.5, 0);
  p.hull.box(DARK, 0, 5.65, -0.8, 3.4, 0.12, 3.4);
  windows(p, 2.02, 2.4, 0.9, -3.5, 0.8, 3);
  windows(p, 1.62, 4.45, 0.85, -2.2, 0.6, 3);
  p.glass.box(GLASS, 0, 4.45, 0.84, 2.7, 0.85, 0.08);
  p.hull.rod(DARK, [0, 5.65, -3.1], [0, 7.2, -3.0], 0.75, 0.6, 8);
  p.hull.rod(c("#c9362f"), [0, 6.5, -3.05], [0, 6.9, -3.0], 0.77, 0.72, 8);
  for (const x of [-2.85, 2.85]) p.hull.rod(DARK, [x, 1.15, -4.6], [x, 1.15, 3.2], 0.3, 0.3, 6);
  p.hull.rod(DARK, [-1.1, 1.0, 6.4], [1.1, 1.0, 6.4], 0.34, 0.34, 6);
  p.hull.rod(DARK, [0, 1.6, -5.4], [0, 2.6, -5.4], 0.32, 0.32, 6);
  p.hull.rod(GREY, [0, 5.75, -0.8], [0, 7.2, -0.8], 0.07, 0.05, 4, false);
  nav(p, 1.75, 5.2, 0.4, 7.3, -0.8);
  return p;
}

function lighter(): BoatParts {
  const p = parts();
  hull(p.hull, { length: 30, beam: 7.2, top: 1.1, draft: 0.7, upper: c("#2c2520"), lower: c("#16161a"), deck: c("#3a342f"), stripeY: 0.3, bowFrac: 0.84, sternW: 0.96, sheer: 0.25 });
  p.hull.box(DARK, 0, 1.1, -1, 5.6, 0.85, 22);
  p.hull.box(c("#7e7468"), 0, 1.95, -1, 5.2, 1.35, 21.5, 3.5, 3.5, 0.5);
  for (const z of [-14, 13]) {
    p.hull.rod(DARK, [0, 1.1, z], [0, 1.9, z], 0.28, 0.28, 6);
    p.hull.rod(GREY, [0, 1.9, z], [0, 3.4, z], 0.06, 0.05, 4, false);
    p.lamps.box(LAMP_WHITE, 0, 3.5, z, 0.4, 0.4, 0.4);
  }
  return p;
}

function rib(): BoatParts {
  const p = parts();
  hull(p.hull, { length: 9, beam: 3, top: 0.55, draft: 0.3, upper: c("#e8ecf2"), lower: c("#16346e"), deck: c("#2b2f38"), stripeY: 0.25, bowFrac: 0.62, sternW: 0.9, sheer: 0.15 });
  for (const s of [-1, 1]) {
    p.hull.rod(c("#23262c"), [s * 1.2, 0.75, -3.8], [s * 0.5, 0.85, 3.6], 0.52, 0.3, 6);
    p.hull.rod(c("#e9c817"), [s * 1.2, 0.75, -3.8], [s * 1.12, 0.78, -1.4], 0.53, 0.53, 6, false);
  }
  p.hull.box(c("#1a2a55"), 0, 0.5, -0.3, 1.1, 0.95, 1.0, 0.3, 0);
  p.glass.quad([-0.5, 1.25, 0.08], [0.5, 1.25, 0.08], [0.5, 1.5, -0.22], [-0.5, 1.5, -0.22], GLASS);
  p.hull.box(c("#2b2f38"), 0, 0.5, -1.7, 1.0, 0.5, 0.7);
  for (const x of [-0.55, 0.55]) p.hull.rod(LIGHTGREY, [x, 0.6, -2.4], [x, 2.0, -2.4], 0.05, 0.05, 4, false);
  p.hull.rod(LIGHTGREY, [-0.55, 2.0, -2.4], [0.55, 2.0, -2.4], 0.05, 0.05, 4, false);
  p.strobe.box(STROBE, -0.35, 2.02, -2.4, 0.5, 0.2, 0.3);
  p.strobe.box(STROBE, 0.35, 2.02, -2.4, 0.5, 0.2, 0.3);
  for (const x of [-0.55, 0.55]) p.hull.box(DARK, x, -0.15, -4.6, 0.6, 1.0, 0.8);
  p.hull.rod(GREY, [0, 0.5, -2.8], [0, 2.9, -2.8], 0.04, 0.03, 4, false);
  nav(p, 0.62, 2.2, -2.4, 3.0, -2.8);
  return p;
}

const BUILDERS: Record<BoatKind, () => BoatParts> = {
  clipper,
  rib,
  sleek: () => yacht({ length: 26, beam: 6, top: 1.5, draft: 1.0, upper: WHITE, lower: NAVY, super: WHITE, stripeY: 0.7, decks: 2, deckH: 1.9, mast: 2.4 }),
  cruise,
  tug,
  lighter,
};

/** Merged, scaled geometries for one vessel type. `strobe` is null unless the model has flashing lights. */
export function buildBoat(kind: BoatKind) {
  const p = BUILDERS[kind]();
  return {
    hull: p.hull.geometry(SCALE),
    glass: p.glass.geometry(SCALE),
    lamps: p.lamps.geometry(SCALE),
    strobe: p.strobe.empty ? null : p.strobe.geometry(SCALE),
  };
}
