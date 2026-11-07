import * as THREE from "three";
import { mulberry32 } from "../live/sim/rng";

/**
 * Cheap procedural tiling canvas textures, shared by every scene module.
 *
 * - Client only: call from inside useMemo/useEffect of a "use client" component, never at module scope.
 * - All textures are sRGB, seamless (periodic noise), mipmapped, anisotropy 8, RepeatWrapping.
 * - `tileM` = metres covered by one tile. `uvScale` = metres per UV unit of the target geometry
 *   (1 for geometry whose UVs are already metres, e.g. ShapeGeometry parks; 12000 for a 12 km disc with 0..1 UVs).
 *   repeat = uvScale / tileM, so the texture keeps its real-world size on any mesh.
 * - Neutral textures are painted mid-bright (~sRGB 170) so `material.color` carries the real albedo (multiplicative).
 * - Results are cached by (kind, options) in a small LRU (16 entries). Don't dispose them yourself.
 */
export interface TextureOptions {
  /** Metres per tile. Each kind has its own sensible default. */
  tileM?: number;
  /** Metres per UV unit of the geometry the texture is applied to (default 1). */
  uvScale?: number;
  /** Canvas resolution in px (default 512). */
  size?: number;
  seed?: number;
}

type Kind = "concrete" | "asphalt" | "paver" | "grass" | "brick" | "glass";

const DEFAULT_TILE_M: Record<Kind, number> = {
  concrete: 64,
  asphalt: 10,
  paver: 4,
  grass: 18,
  brick: 6,
  glass: 8,
};

const CACHE_LIMIT = 16;
const cache = new Map<string, THREE.CanvasTexture>();

function cached(kind: Kind, opts: TextureOptions, paint: (size: number, rnd: () => number) => HTMLCanvasElement) {
  const size = opts.size ?? 512;
  const tileM = opts.tileM ?? DEFAULT_TILE_M[kind];
  const uvScale = opts.uvScale ?? 1;
  const seed = opts.seed ?? 1;
  const key = `${kind}:${size}:${tileM}:${uvScale}:${seed}`;
  const hit = cache.get(key);
  if (hit) {
    cache.delete(key);
    cache.set(key, hit);
    return hit;
  }
  const tex = new THREE.CanvasTexture(paint(size, mulberry32(seed * 7919 + kind.charCodeAt(0) * 131 + kind.length)));
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.repeat.set(uvScale / tileM, uvScale / tileM);
  tex.needsUpdate = true;
  cache.set(key, tex);
  if (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) {
      cache.get(oldest)?.dispose();
      cache.delete(oldest);
    }
  }
  return tex;
}

/** Periodic value noise, summed over octaves and normalised to 0..1 (seamless at the tile edge). */
function fbm(size: number, rnd: () => number, octaves: [cells: number, amp: number][]) {
  const out = new Float32Array(size * size);
  let total = 0;
  for (const [cells, amp] of octaves) {
    total += amp;
    const lattice = new Float32Array(cells * cells);
    for (let i = 0; i < lattice.length; i++) lattice[i] = rnd();
    const i0 = new Int32Array(size);
    const i1 = new Int32Array(size);
    const t = new Float32Array(size);
    for (let p = 0; p < size; p++) {
      const f = (p / size) * cells;
      const c = Math.floor(f);
      const k = f - c;
      i0[p] = c % cells;
      i1[p] = (c + 1) % cells;
      t[p] = k * k * (3 - 2 * k);
    }
    for (let y = 0; y < size; y++) {
      const ra = i0[y] * cells;
      const rb = i1[y] * cells;
      const ty = t[y];
      for (let x = 0; x < size; x++) {
        const tx = t[x];
        const a = lattice[ra + i0[x]] + (lattice[ra + i1[x]] - lattice[ra + i0[x]]) * tx;
        const b = lattice[rb + i0[x]] + (lattice[rb + i1[x]] - lattice[rb + i0[x]]) * tx;
        out[y * size + x] += (a + (b - a) * ty) * amp;
      }
    }
  }
  for (let i = 0; i < out.length; i++) out[i] /= total;
  return out;
}

function hash2(a: number, b: number, seed: number) {
  const s = Math.sin(a * 127.1 + b * 311.7 + seed * 74.7) * 43758.5453;
  return s - Math.floor(s);
}

type Painter = (x: number, y: number, u: number, v: number) => [number, number, number];

/** Per-pixel painter, `rgb` in 0..255. */
function pixels(size: number, paint: Painter) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const img = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const [r, g, b] = paint(x, y, x / size, y / size);
      const i = (y * size + x) * 4;
      img.data[i] = r;
      img.data[i + 1] = g;
      img.data[i + 2] = b;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return { canvas, ctx };
}

/** Draws at x±size, y±size so blotches that cross the tile edge wrap (seamless). */
function wrapped(size: number, x: number, y: number, r: number, draw: (x: number, y: number) => void) {
  for (const dx of [-size, 0, size]) {
    for (const dy of [-size, 0, size]) {
      if (x + dx + r < 0 || x + dx - r > size || y + dy + r < 0 || y + dy - r > size) continue;
      draw(x + dx, y + dy);
    }
  }
}

const clamp255 = (n: number) => (n < 0 ? 0 : n > 255 ? 255 : n);

/**
 * Cool neutral-grey London concrete, large mottling + fine speckle + faint hairline joints. Default tile 64 m.
 * Mean ~sRGB 168, tint with `material.color`. Used by Ground.
 */
export function concreteTexture(opts: TextureOptions = {}) {
  return cached("concrete", opts, (size, rnd) => {
    const n = fbm(size, rnd, [[3, 1], [7, 0.8], [16, 0.55], [48, 0.3]]);
    const { canvas, ctx } = pixels(size, (x, y) => {
      const m = n[y * size + x];
      const grain = (rnd() - 0.5) * 22;
      const l = 168 + (m - 0.5) * 120 + grain;
      return [clamp255(l - 2), clamp255(l), clamp255(l + 4)];
    });
    ctx.strokeStyle = "rgba(32,36,44,0.16)";
    ctx.lineWidth = 1;
    const step = size / 4;
    for (let i = 0; i < 4; i++) {
      ctx.beginPath();
      ctx.moveTo(i * step, 0);
      ctx.lineTo(i * step, size);
      ctx.moveTo(0, i * step);
      ctx.lineTo(size, i * step);
      ctx.stroke();
    }
    return canvas;
  });
}

/**
 * Dark tarmac: mottled, bright aggregate flecks, hairline cracks, a few oil stains. Default tile 10 m.
 * Roads/Pavements: `new THREE.MeshStandardMaterial({ map: asphaltTexture({ tileM: 10 }), color: "#b8bcc4" })`.
 */
export function asphaltTexture(opts: TextureOptions = {}) {
  return cached("asphalt", opts, (size, rnd) => {
    const n = fbm(size, rnd, [[4, 1], [12, 0.7], [40, 0.45]]);
    const { canvas, ctx } = pixels(size, (x, y) => {
      const m = n[y * size + x];
      const fleck = rnd() < 0.045 ? 40 + rnd() * 50 : 0;
      const l = 118 + (m - 0.5) * 70 + (rnd() - 0.5) * 26 + fleck;
      return [clamp255(l), clamp255(l), clamp255(l + 3)];
    });
    for (let i = 0; i < 6; i++) {
      const x = rnd() * size;
      const y = rnd() * size;
      const r = 30 + rnd() * 60;
      wrapped(size, x, y, r, (px, py) => {
        const g = ctx.createRadialGradient(px, py, 0, px, py, r);
        g.addColorStop(0, "rgba(10,10,14,0.22)");
        g.addColorStop(1, "rgba(10,10,14,0)");
        ctx.fillStyle = g;
        ctx.fillRect(px - r, py - r, r * 2, r * 2);
      });
    }
    ctx.strokeStyle = "rgba(20,20,24,0.45)";
    ctx.lineWidth = 1;
    for (let i = 0; i < 5; i++) {
      let x = rnd() * size;
      let y = rnd() * size;
      ctx.beginPath();
      ctx.moveTo(x, y);
      for (let s = 0; s < 14; s++) {
        x += (rnd() - 0.5) * 34;
        y += (rnd() - 0.2) * 22;
        ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    return canvas;
  });
}

/**
 * Pavement slabs laid in running bond: per-slab tint, dark joints, grit. 8 x 16 slabs per tile, default tile 4 m.
 * Pavements: pale warm grey, use `color: "#c9c3b8"`.
 */
export function paverTexture(opts: TextureOptions = {}) {
  return cached("paver", opts, (size, rnd) => {
    const cols = 8;
    const rows = 16;
    const seed = rnd() * 100;
    const n = fbm(size, rnd, [[8, 1], [32, 0.5]]);
    return pixels(size, (x, y, u, v) => {
      const row = Math.floor(v * rows);
      const uu = u * cols + (row % 2 ? 0.5 : 0);
      const col = Math.floor(uu) % cols;
      const fu = uu - Math.floor(uu);
      const fv = v * rows - row;
      const joint = fu < 0.05 || fu > 0.97 || fv < 0.08 || fv > 0.96;
      const tint = (hash2(col, row, seed) - 0.5) * 36;
      const l = (joint ? 78 : 176) + tint * (joint ? 0.2 : 1) + (n[y * size + x] - 0.5) * 36 + (rnd() - 0.5) * 18;
      return [clamp255(l + 3), clamp255(l + 1), clamp255(l - 4)];
    }).canvas;
  });
}

/**
 * Lush grass: tone noise, soft darker canopy-dapple blotches, light/dark blade strokes. Default tile 18 m.
 * Mean ~ mid green; tint with `material.color` (day #8fd05a-ish, night deep blue-green).
 */
export function grassTexture(opts: TextureOptions = {}) {
  return cached("grass", opts, (size, rnd) => {
    const n = fbm(size, rnd, [[3, 1], [8, 0.7], [24, 0.5], [64, 0.3]]);
    const { canvas, ctx } = pixels(size, (x, y) => {
      const m = n[y * size + x];
      const l = 0.62 + (m - 0.5) * 0.55 + (rnd() - 0.5) * 0.12;
      return [clamp255(150 * l * 1.05), clamp255(205 * l), clamp255(92 * l)];
    });
    for (let i = 0; i < 16; i++) {
      const x = rnd() * size;
      const y = rnd() * size;
      const r = 28 + rnd() * 64;
      wrapped(size, x, y, r, (px, py) => {
        const g = ctx.createRadialGradient(px, py, 0, px, py, r);
        g.addColorStop(0, "rgba(10,48,18,0.42)");
        g.addColorStop(0.6, "rgba(10,48,18,0.2)");
        g.addColorStop(1, "rgba(10,48,18,0)");
        ctx.fillStyle = g;
        ctx.fillRect(px - r, py - r, r * 2, r * 2);
      });
    }
    for (let i = 0; i < 2600; i++) {
      const x = rnd() * size;
      const y = rnd() * size;
      const len = 3 + rnd() * 5;
      const lean = (rnd() - 0.5) * 3;
      ctx.strokeStyle = rnd() < 0.5 ? "rgba(214,240,120,0.35)" : "rgba(16,70,24,0.35)";
      ctx.lineWidth = 1;
      wrapped(size, x, y, len + 3, (px, py) => {
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(px + lean, py - len);
        ctx.stroke();
      });
    }
    return canvas;
  });
}

/**
 * London stock brick in stretcher bond: 12 x 24 bricks per tile, varied warm red/brown/yellow, light mortar,
 * grain. Default tile 6 m (about 0.5 m wide bricks, so use tileM ~ 3 for a more realistic scale).
 * Buildings: `map: brickTexture({ tileM: 4, uvScale: 1 })` with metre UVs, tint with `color` ("#ffffff" keeps it as painted).
 */
export function brickTexture(opts: TextureOptions = {}) {
  return cached("brick", opts, (size, rnd) => {
    const cols = 12;
    const rows = 24;
    const seed = rnd() * 100;
    const n = fbm(size, rnd, [[6, 1], [24, 0.6]]);
    const palette: [number, number, number][] = [
      [158, 84, 62],
      [140, 72, 54],
      [172, 98, 70],
      [150, 106, 78],
      [122, 66, 52],
      [186, 140, 96],
    ];
    return pixels(size, (x, y, u, v) => {
      const row = Math.floor(v * rows);
      const uu = u * cols + (row % 2 ? 0.5 : 0);
      const col = Math.floor(uu) % cols;
      const fu = uu - Math.floor(uu);
      const fv = v * rows - row;
      const mortar = fu < 0.07 || fv < 0.14;
      if (mortar) {
        const l = 168 + (n[y * size + x] - 0.5) * 40 + (rnd() - 0.5) * 16;
        return [clamp255(l), clamp255(l - 3), clamp255(l - 10)];
      }
      const c = palette[Math.floor(hash2(col, row, seed) * palette.length)];
      const shade = 0.86 + (n[y * size + x] - 0.5) * 0.4 + (rnd() - 0.5) * 0.12 + (fv < 0.25 ? -0.05 : 0);
      return [clamp255(c[0] * shade), clamp255(c[1] * shade), clamp255(c[2] * shade)];
    }).canvas;
  });
}

/**
 * Curtain-wall glass: 8 x 16 panes with diagonal sky-reflection gradients, per-pane tint, dark mullions and
 * noise. Cool blue-grey. Default tile 8 m. Pair with low roughness / some metalness for a glassy look.
 */
export function glassTexture(opts: TextureOptions = {}) {
  return cached("glass", opts, (size, rnd) => {
    const cols = 8;
    const rows = 16;
    const seed = rnd() * 100;
    const n = fbm(size, rnd, [[4, 1], [16, 0.5]]);
    return pixels(size, (x, y, u, v) => {
      const pu = u * cols;
      const pv = v * rows;
      const col = Math.floor(pu);
      const row = Math.floor(pv);
      const fu = pu - col;
      const fv = pv - row;
      if (fu < 0.05 || fv < 0.05) return [34, 40, 48];
      const streak = Math.max(0, 1 - Math.abs(fu - fv * 0.7 - 0.25) * 3.2);
      const tint = hash2(col, row, seed);
      const base = 92 + tint * 46 + (n[y * size + x] - 0.5) * 30 + streak * 70 + (rnd() - 0.5) * 8;
      return [clamp255(base * 0.78), clamp255(base * 0.92), clamp255(base * 1.12)];
    }).canvas;
  });
}
