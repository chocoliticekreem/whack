import { Kit, lamp, rng, type SignSpec, type SiteModel } from "./kit";

/**
 * Halkin, 1-2 Paris Garden: the hack venue. A refurbished four-floor warehouse of dark London brick with
 * big steel-framed windows, a glazed entrance, a set-back glass top floor and a lit rooftop sign.
 * Local frame: the entrance front faces -z, the long side runs along x.
 */
const W = 62;
const D = 34;
const FLOORS = 4;
const FH = 4.4;
const BAY = 4.1;
const BRICK = "#7a4a3b";
const BRICK_DARK = "#5e372c";
const STONE = "#b9b0a2";
const STEEL = "#232a33";
const ROOF_Y = FLOORS * FH;

const WARM = ["#ffc477", "#ffd9a0", "#ffb05e", "#fff0cf"];

interface Wall {
  along: "x" | "z";
  from: number;
  perp: number;
  dir: 1 | -1;
  bays: number;
}

function steelWindows(k: Kit, rand: () => number, wall: Wall, floor: number, skip?: (bay: number) => boolean) {
  const y0 = floor * FH + 0.7;
  const floorLit = floor === 3 ? 0.85 : floor === 0 ? 0.5 : 0.7;
  for (let b = 0; b < wall.bays; b++) {
    if (skip?.(b)) continue;
    const c = wall.from + (b + 0.5) * BAY;
    k.slab("body", wall.along, c, wall.perp + wall.dir * 0.03, y0 - 0.1, 3.4, 3.4, 0.14, STEEL);
    const office = rand() < floorLit;
    for (let col = 0; col < 2; col++) {
      for (let row = 0; row < 2; row++) {
        const lit = office && rand() < 0.88;
        k.slab(
          lit ? "lit" : "glass",
          wall.along,
          c + (col - 0.5) * 1.6,
          wall.perp + wall.dir * 0.1,
          y0 + 0.05 + row * 1.6,
          1.5,
          1.5,
          0.06,
          lit ? WARM[Math.floor(rand() * WARM.length)] : "#1b2433",
        );
      }
    }
  }
}

function warehouse(k: Kit, rand: () => number) {
  k.box("body", 0, 0, 0, W, ROOF_Y, D, BRICK);
  k.box("body", 0, 0, 0, W + 0.4, 1, D + 0.4, STONE);
  for (let f = 1; f <= FLOORS; f++) k.box("body", 0, f * FH - 0.35, 0, W + 0.5, 0.35, D + 0.5, f === FLOORS ? STONE : BRICK_DARK);
  k.box("body", 0, ROOF_Y, 0, W + 0.8, 0.9, D + 0.8, BRICK_DARK);

  const frontBays = Math.floor(W / BAY);
  const sideBays = Math.floor(D / BAY);
  const inEntrance = (b: number) => Math.abs(b - (frontBays - 1) / 2) < 1.6;
  const front: Wall = { along: "x", from: -(frontBays * BAY) / 2, perp: -D / 2, dir: -1, bays: frontBays };
  const back: Wall = { ...front, perp: D / 2, dir: 1 };
  const east: Wall = { along: "z", from: -(sideBays * BAY) / 2, perp: W / 2, dir: 1, bays: sideBays };
  const west: Wall = { ...east, perp: -W / 2, dir: -1 };
  for (let f = 0; f < FLOORS; f++) {
    steelWindows(k, rand, front, f, f === 0 ? inEntrance : undefined);
    steelWindows(k, rand, back, f);
    steelWindows(k, rand, east, f);
    steelWindows(k, rand, west, f);
  }
}

function entrance(k: Kit) {
  const z = -D / 2;
  k.box("body", 0, 0, z - 2.2, 14, 0.2, 4.4, "#34373d");
  k.box("body", 0, 4.4, z - 2.6, 15, 0.35, 5.2, STONE);
  for (const x of [-6.5, 6.5]) k.box("body", x, 0, z - 4.6, 0.4, 4.4, 0.4, STEEL);
  k.box("glass", 0, 0.2, z - 0.4, 13, 4.2, 0.2, "#1b2433");
  for (let i = 0; i < 4; i++) k.box("lit", -4.8 + i * 3.2, 0.5, z - 0.55, 2.8, 3.6, 0.06, "#ffd9a0");
  k.box("strip", 0, 4.3, z - 5.15, 14.6, 0.08, 0.1, "#ffe2b0");
}

function glassTopFloor(k: Kit, rand: () => number) {
  const w = W - 14;
  const d = D - 12;
  const h = 3.8;
  k.box("body", 0, ROOF_Y + 0.9, 0, w, h, d, STEEL);
  for (const [axis, perp, dir] of [
    ["x", -d / 2, -1],
    ["x", d / 2, 1],
  ] as const) {
    for (let b = 0; b < Math.floor(w / 3); b++) {
      const lit = rand() < 0.9;
      k.slab(lit ? "lit" : "glass", axis, -w / 2 + (b + 0.5) * 3, perp + dir * 0.05, ROOF_Y + 1.3, 2.7, 3, 0.06, lit ? WARM[b % WARM.length] : "#1b2433");
    }
  }
  for (const dir of [-1, 1] as const) {
    for (let b = 0; b < Math.floor(d / 3); b++) {
      k.slab("lit", "z", -d / 2 + (b + 0.5) * 3, (dir * w) / 2 + dir * 0.05, ROOF_Y + 1.3, 2.7, 3, 0.06, WARM[b % WARM.length]);
    }
  }
  k.box("body", 0, ROOF_Y + 0.9 + h, 0, w + 1.6, 0.35, d + 1.6, STONE);
  k.box("strip", 0, ROOF_Y + 0.9 + h + 0.35, -d / 2 - 0.8, w + 1.4, 0.07, 0.1, "#9fd3ff");
  k.box("body", 18, ROOF_Y + 5.05, 6, 8, 2.2, 5, "#8f8a80");
  k.box("body", -16, ROOF_Y + 5.05, 4, 5, 1.6, 4, "#8f8a80");
}

function signage(k: Kit, signs: SignSpec[]) {
  const z = -D / 2 + 0.9;
  k.box("body", 0, ROOF_Y + 0.9, z, 24, 4.6, 0.4, STEEL);
  for (const x of [-9, 9]) k.box("body", x, ROOF_Y + 0.9, z + 0.8, 0.3, 4.6, 1.6, STEEL);
  signs.push({ text: "HALKIN", x: 0, y: ROOF_Y + 3.2, z: z - 0.22, w: 22.4, h: 3.8, rotY: Math.PI, fg: "#ffffff", bg: "#161b22" });
  k.box("strip", 0, ROOF_Y + 5.5, z, 24, 0.08, 0.5, "#9fd3ff");

  const poleX = W / 2 - 5;
  const poleZ = -D / 2 - 7;
  k.cylinder("body", poleX, 0, poleZ, 0.14, 21, "#aeb3bb");
  k.box("strip", poleX, 21, poleZ, 0.4, 0.4, 0.4, "#9fd3ff");
  signs.push({ text: "VAST\nBUILDERS\nCHALLENGE", x: poleX - 3.8, y: 18.4, z: poleZ, w: 7.2, h: 4.4, rotY: Math.PI, fg: "#ffffff", bg: "#0b6f8a", double: true });
}

function street(k: Kit) {
  for (let x = -24; x <= 24; x += 16) lamp(k, x, -D / 2 - 4, 5.4);
}

export function buildHalkin(): SiteModel {
  const k = new Kit();
  const rand = rng(2026);
  const signs: SignSpec[] = [];
  warehouse(k, rand);
  entrance(k);
  glassTopFloor(k, rand);
  signage(k, signs);
  street(k);
  return { geometry: k.build(), signs, topM: ROOF_Y + 7.5 };
}
