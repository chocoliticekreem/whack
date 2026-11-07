import { Kit, rng, windowRow, type SignSpec, type SiteModel } from "./kit";

/**
 * Falcon Point, Hopton Street (Wates, 1976-79): a nine-storey stepped brutalist riverside slab of pale
 * brick and concrete, continuous access balconies, an L-shaped wing, the Founders Arms and the Bankside
 * Gallery on the ground floor. Local frame: the river front faces -z (north), the slab runs along x.
 */
const L = 84;
const BACK_Z = 10;
const FLOORS = 9;
const FH = 3.1;
const STEP = 1.8;
const FRONT0 = -14;
const BAY = 3;
const BRICK = "#b8a088";
const CONC = "#d2cbbe";
const CONC_DARK = "#b3ab9e";
const PUB_BAYS = 7;
const GALLERY_BAYS = 4;

const frontZ = (floor: number) => FRONT0 + STEP * floor;

function riverFront(k: Kit, rand: () => number) {
  const bays = Math.floor(L / BAY);
  for (let f = 0; f < FLOORS; f++) {
    const y0 = f * FH;
    const zf = frontZ(f);
    k.box("body", 0, y0, (zf + BACK_Z) / 2, L, FH, BACK_Z - zf, f % 2 ? BRICK : "#c1ab93");
    k.box("body", 0, y0 + FH - 0.3, zf - 0.12, L + 0.4, 0.3, 0.3, CONC);
    for (let b = 0; b <= bays; b++) k.box("body", -L / 2 + b * BAY, y0 + 0.15, zf - 0.1, 0.35, FH - 0.5, 0.3, CONC);
    const first = f === 0 ? PUB_BAYS + GALLERY_BAYS : 0;
    windowRow(k, rand, {
      along: "x",
      from: -L / 2 + first * BAY,
      perp: zf - 0.12,
      dir: -1,
      y: y0 + 0.9,
      bays: bays - first,
      bay: BAY,
      paneW: BAY - 0.45,
      paneH: 1.6,
      litChance: 0.55,
    });
  }
  k.box("body", 0, 0, FRONT0 - 0.05, L + 0.2, 0.5, 0.3, "#6f6258");
  for (let level = 1; level <= FLOORS; level++) {
    const y = level * FH;
    const z = frontZ(level - 1) + 0.1;
    k.box("body", 0, y, z, L, 1.05, 0.22, CONC);
    k.box("strip", 0, y + 1.05, z, L - 1, 0.07, 0.14, "#ffe2b0");
  }
}

function accessDecks(k: Kit, rand: () => number) {
  for (let f = 0; f < FLOORS; f++) {
    const y0 = f * FH;
    k.box("body", 0, y0, BACK_Z + 0.8, L, 0.25, 1.6, CONC);
    k.box("body", 0, y0 + 0.25, BACK_Z + 1.55, L, 1, 0.15, CONC);
    k.box("strip", 0, y0 + 1.25, BACK_Z + 1.55, L - 1, 0.06, 0.1, "#ffe2b0");
    windowRow(k, rand, {
      along: "x",
      from: -L / 2,
      perp: BACK_Z,
      dir: 1,
      y: y0 + 1.0,
      bays: Math.floor(L / BAY),
      bay: BAY,
      paneW: 1.6,
      paneH: 1.1,
      litChance: 0.4,
    });
  }
  for (const cx of [-24, 22]) {
    const h = FLOORS * FH + 3.2;
    k.box("body", cx, 0, BACK_Z + 3.5, 5, h, 7, CONC_DARK);
    k.box("body", cx, h, BACK_Z + 3.5, 5.4, 0.3, 7.4, "#5b5750");
    for (let f = 0; f < FLOORS; f++) {
      for (const dx of [-1.2, 1.2]) k.box("lit", cx + dx, f * FH + 0.8, BACK_Z + 7.03, 0.7, 1.6, 0.06, "#ffd9a0");
    }
  }
}

function eastWing(k: Kit, rand: () => number) {
  const x0 = L / 2 - 15;
  const floors = 6;
  const depth = 30;
  k.box("body", L / 2 - 7.5, 0, BACK_Z + depth / 2, 15, floors * FH, depth, BRICK);
  k.box("body", L / 2 - 7.5, floors * FH, BACK_Z + depth / 2, 15.4, 0.3, depth + 0.4, CONC);
  for (let f = 0; f < floors; f++) {
    const y = f * FH + 0.9;
    const bay = { bay: BAY, paneW: BAY - 0.5, paneH: 1.5, litChance: 0.5, y };
    windowRow(k, rand, { ...bay, along: "z", from: BACK_Z + 2, perp: L / 2, dir: 1, bays: 9 });
    windowRow(k, rand, { ...bay, along: "z", from: BACK_Z + 2, perp: x0, dir: -1, bays: 9 });
    windowRow(k, rand, { ...bay, along: "x", from: x0, perp: BACK_Z + depth, dir: 1, bays: 5 });
    k.slab("strip", "z", BACK_Z + 15, L / 2 + 0.3, f * FH + 0.15, depth - 2, 0.06, 0.1, "#ffe2b0");
  }
}

function groundFloor(k: Kit, signs: SignSpec[]) {
  const zf = FRONT0;
  const pubW = PUB_BAYS * BAY;
  const galW = GALLERY_BAYS * BAY;
  const pubX = -L / 2 + pubW / 2;
  const galX = -L / 2 + pubW + galW / 2;

  k.box("glass", pubX, 0.5, zf - 0.1, pubW - 1, 2.3, 0.15, "#1b2433");
  for (let i = 0; i < PUB_BAYS; i++) {
    const x = -L / 2 + (i + 0.5) * BAY;
    k.box("strip", x, 0.7, zf - 0.2, BAY - 0.6, 1.9, 0.06, "#ffb35c");
    k.box("body", x - BAY / 2, 0.3, zf - 0.15, 0.3, 2.6, 0.3, "#2a1a14");
  }
  k.box("glass", galX, 0.5, zf - 0.1, galW - 1, 2.3, 0.15, "#1b2433");
  for (let i = 0; i < GALLERY_BAYS; i++) {
    k.box("strip", -L / 2 + pubW + (i + 0.5) * BAY, 0.7, zf - 0.2, BAY - 0.7, 1.9, 0.06, "#e8f1ff");
  }

  k.box("body", pubX, 2.75, zf - 0.35, pubW - 3, 1.4, 0.3, "#2a1a14");
  signs.push({ text: "THE FOUNDERS ARMS", x: pubX, y: 3.45, z: zf - 0.52, w: pubW - 3.6, h: 1.1, rotY: Math.PI, fg: "#ffd9a0", bg: "#2a1a14" });
  k.box("body", galX, 2.85, zf - 0.35, galW - 1.5, 1.0, 0.3, "#20262e");
  signs.push({ text: "BANKSIDE GALLERY", x: galX, y: 3.35, z: zf - 0.52, w: galW - 2, h: 0.7, rotY: Math.PI, fg: "#e8f1ff", bg: "#20262e" });

  for (let i = 0; i < 6; i++) {
    const x = pubX - 8 + i * 3.2;
    k.box("body", x, 0.72, zf - 3.6, 1, 0.07, 1, "#3b2c22");
    k.cylinder("body", x, 0, zf - 3.6, 0.07, 0.72, "#1d2026");
    k.box("body", x, 0, zf - 4.8, 1.3, 0.45, 0.35, "#3b2c22");
  }
}

function roof(k: Kit) {
  const y = FLOORS * FH;
  k.box("body", 12, y, 5, 16, 2.8, 5.5, "#8f8a80");
  k.box("body", -2, y, 7, 6, 1.6, 4, "#8f8a80");
  k.cylinder("body", -34, y, 6, 0.12, 9, "#3b3d42");
  k.box("strip", -34, y + 9, 6, 0.4, 0.4, 0.4, "#ff5a4a");
}

export function buildFalconPoint(): SiteModel {
  const k = new Kit();
  const rand = rng(1976);
  const signs: SignSpec[] = [];
  riverFront(k, rand);
  accessDecks(k, rand);
  eastWing(k, rand);
  groundFloor(k, signs);
  roof(k);
  return { geometry: k.build(), signs, topM: FLOORS * FH + 3.5 };
}
