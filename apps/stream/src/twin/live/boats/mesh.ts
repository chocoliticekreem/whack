import * as THREE from "three";

export type V3 = [number, number, number];

/** Tiny flat-shaded, vertex-coloured mesh builder: every face owns its vertices, so low-poly edges stay crisp. */
export class Mesh {
  private readonly pos: number[] = [];
  private readonly col: number[] = [];

  tri(a: V3, b: V3, c: V3, colour: THREE.Color) {
    this.pos.push(...a, ...b, ...c);
    for (let i = 0; i < 3; i++) this.col.push(colour.r, colour.g, colour.b);
  }

  quad(a: V3, b: V3, c: V3, d: V3, colour: THREE.Color) {
    this.tri(a, b, c, colour);
    this.tri(a, c, d, colour);
  }

  /** Box centred on x/z, standing on y0. `fz` / `bz` pull the top edge back at the front / rear, `tw` narrows the top. */
  box(colour: THREE.Color, x: number, y0: number, z: number, w: number, h: number, d: number, fz = 0, bz = 0, tw = 1) {
    const hw = w / 2;
    const hd = d / 2;
    const tl = hw * tw;
    const y1 = y0 + h;
    const bl: V3 = [x - hw, y0, z - hd];
    const br: V3 = [x + hw, y0, z - hd];
    const fr: V3 = [x + hw, y0, z + hd];
    const fl: V3 = [x - hw, y0, z + hd];
    const tbl: V3 = [x - tl, y1, z - hd + bz];
    const tbr: V3 = [x + tl, y1, z - hd + bz];
    const tfr: V3 = [x + tl, y1, z + hd - fz];
    const tfl: V3 = [x - tl, y1, z + hd - fz];
    this.quad(tbl, tbr, tfr, tfl, colour);
    this.quad(bl, fl, fr, br, colour);
    this.quad(fl, fr, tfr, tfl, colour);
    this.quad(br, bl, tbl, tbr, colour);
    this.quad(bl, fl, tfl, tbl, colour);
    this.quad(fr, br, tbr, tfr, colour);
  }

  /** Prism / cone / mast between two points. */
  rod(colour: THREE.Color, a: V3, b: V3, r0: number, r1 = r0, seg = 6, cap = true) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const dz = b[2] - a[2];
    const len = Math.hypot(dx, dy, dz) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const uz = dz / len;
    const refUp = Math.abs(uy) < 0.9;
    let n1x = refUp ? -uz : 0;
    let n1y = refUp ? 0 : uz;
    let n1z = refUp ? ux : -uy;
    const nl = Math.hypot(n1x, n1y, n1z) || 1;
    n1x /= nl;
    n1y /= nl;
    n1z /= nl;
    const n2x = uy * n1z - uz * n1y;
    const n2y = uz * n1x - ux * n1z;
    const n2z = ux * n1y - uy * n1x;
    const ring = (p: V3, r: number, k: number): V3 => {
      const th = (k / seg) * Math.PI * 2;
      const c = Math.cos(th) * r;
      const s = Math.sin(th) * r;
      return [p[0] + n1x * c + n2x * s, p[1] + n1y * c + n2y * s, p[2] + n1z * c + n2z * s];
    };
    for (let k = 0; k < seg; k++) {
      this.quad(ring(a, r0, k), ring(a, r0, k + 1), ring(b, r1, k + 1), ring(b, r1, k), colour);
      if (cap) {
        if (r0 > 0) this.tri(a, ring(a, r0, k + 1), ring(a, r0, k), colour);
        if (r1 > 0) this.tri(b, ring(b, r1, k), ring(b, r1, k + 1), colour);
      }
    }
  }

  /** Bilinear sail patch between four corners (in order around the sail), bulged along `bulge`. */
  sail(colour: THREE.Color, p0: V3, p1: V3, p2: V3, p3: V3, bulge: V3, nu = 3, nv = 3) {
    const at = (u: number, v: number): V3 => {
      const k = 4 * u * (1 - u) * v * (1 - v) * 1.6;
      const out: V3 = [0, 0, 0];
      for (let i = 0; i < 3; i++) {
        const bottom = p0[i] + (p1[i] - p0[i]) * u;
        const top = p3[i] + (p2[i] - p3[i]) * u;
        out[i] = bottom + (top - bottom) * v + bulge[i] * k;
      }
      return out;
    };
    for (let i = 0; i < nu; i++) {
      for (let j = 0; j < nv; j++) {
        const u0 = i / nu;
        const u1 = (i + 1) / nu;
        const v0 = j / nv;
        const v1 = (j + 1) / nv;
        this.quad(at(u0, v0), at(u1, v0), at(u1, v1), at(u0, v1), colour);
      }
    }
  }

  geometry(scale: number) {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(this.pos.length);
    for (let i = 0; i < pos.length; i++) pos[i] = this.pos[i] * scale;
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(this.col), 3));
    g.computeVertexNormals();
    return g;
  }

  get empty() {
    return this.pos.length === 0;
  }
}

export interface HullSpec {
  length: number;
  beam: number;
  /** Deck height above the waterline. */
  top: number;
  draft: number;
  /** Colour above `stripeY`, below it, and on deck. */
  upper: THREE.Color;
  lower: THREE.Color;
  deck: THREE.Color;
  stripeY: number;
  /** Lateral offset of the hull centre (catamaran hulls). */
  x0?: number;
  /** Fraction of the length where the bow starts to narrow. */
  bowFrac?: number;
  /** Beam at the transom as a fraction of the full beam. */
  sternW?: number;
  /** Extra deck height at the bow. */
  sheer?: number;
}

/** Lofted displacement hull with a pointed, raked bow. Bow points to +z, waterline is y = 0. */
export function hull(m: Mesh, o: HullSpec) {
  const stations = 12;
  const bowFrac = o.bowFrac ?? 0.6;
  const sternW = o.sternW ?? 0.8;
  const sheer = o.sheer ?? 0.5;
  const x0 = o.x0 ?? 0;
  const sections: V3[][] = [];
  for (let i = 0; i <= stations; i++) {
    const u = i / stations;
    const z = (u - 0.5) * o.length;
    let w = 1;
    if (u < 0.2) w = sternW + (1 - sternW) * (u / 0.2);
    let k = 0;
    if (u > bowFrac) {
      k = (u - bowFrac) / (1 - bowFrac);
      w = Math.pow(1 - k, 0.7);
    }
    const hw = (o.beam / 2) * w;
    const topY = o.top + sheer * u * u * u;
    const botY = -o.draft * (1 - 0.75 * k);
    const midY = Math.min(o.stripeY, topY - 0.05);
    sections.push([
      [x0 - hw, topY, z],
      [x0 - hw * 0.97, midY, z],
      [x0 - hw * 0.62, botY, z],
      [x0 + hw * 0.62, botY, z],
      [x0 + hw * 0.97, midY, z],
      [x0 + hw, topY, z],
    ]);
  }
  for (let i = 0; i < stations; i++) {
    const a = sections[i];
    const b = sections[i + 1];
    m.quad(a[0], a[5], b[5], b[0], o.deck);
    for (let j = 0; j < 5; j++) {
      const col = j === 0 || j === 4 ? o.upper : o.lower;
      m.quad(a[j], a[j + 1], b[j + 1], b[j], col);
    }
  }
  const s = sections[0];
  m.quad(s[0], s[5], s[4], s[1], o.upper);
  m.quad(s[1], s[4], s[3], s[2], o.lower);
}
