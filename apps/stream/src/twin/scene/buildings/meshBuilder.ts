import * as THREE from "three";
import type { P2 } from "./footprint";

export type V3 = [number, number, number];

/** What the facade shader needs to know about a surface. `style`: 0 solid, 1 brick, 2 glass, 3 roof, 4 roof light, 5 beacon, 6 stone, 7 awning, 8 concrete, 9 render; +0.5 = shopfront. */
export interface Surface {
  r: number;
  g: number;
  b: number;
  seed: number;
  lit: number;
  style: number;
}

const STRIDE = { position: 3, normal: 3, uv: 2, color: 3, twin: 3 };

/** Appends indexed triangles into growable typed arrays; one per spatial chunk. */
export class MeshBuilder {
  private capacity = 1 << 15;
  private indexCapacity = 1 << 16;
  private position = new Float32Array(this.capacity * STRIDE.position);
  private normal = new Float32Array(this.capacity * STRIDE.normal);
  private uv = new Float32Array(this.capacity * STRIDE.uv);
  private color = new Float32Array(this.capacity * STRIDE.color);
  private twin = new Float32Array(this.capacity * STRIDE.twin);
  private index = new Uint32Array(this.indexCapacity);
  vertexCount = 0;
  indexCount = 0;

  get triangles() {
    return this.indexCount / 3;
  }

  private grow(extraVertices: number, extraIndices: number) {
    if (this.vertexCount + extraVertices > this.capacity) {
      while (this.vertexCount + extraVertices > this.capacity) this.capacity *= 2;
      const expand = (src: Float32Array, stride: number) => {
        const next = new Float32Array(this.capacity * stride);
        next.set(src);
        return next;
      };
      this.position = expand(this.position, STRIDE.position);
      this.normal = expand(this.normal, STRIDE.normal);
      this.uv = expand(this.uv, STRIDE.uv);
      this.color = expand(this.color, STRIDE.color);
      this.twin = expand(this.twin, STRIDE.twin);
    }
    if (this.indexCount + extraIndices > this.indexCapacity) {
      while (this.indexCount + extraIndices > this.indexCapacity) this.indexCapacity *= 2;
      const next = new Uint32Array(this.indexCapacity);
      next.set(this.index);
      this.index = next;
    }
  }

  /** `ao` is baked ambient occlusion, a multiplier on the surface colour (1 = open, lower = darker). */
  vertex(p: V3, n: V3, u: number, v: number, s: Surface, ao = 1): number {
    this.grow(1, 0);
    const i = this.vertexCount++;
    this.position.set(p, i * 3);
    this.normal.set(n, i * 3);
    this.uv[i * 2] = u;
    this.uv[i * 2 + 1] = v;
    this.color[i * 3] = s.r * ao;
    this.color[i * 3 + 1] = s.g * ao;
    this.color[i * 3 + 2] = s.b * ao;
    this.twin[i * 3] = s.seed;
    this.twin[i * 3 + 1] = s.lit;
    this.twin[i * 3 + 2] = s.style;
    return i;
  }

  /** Quad whose winding is flipped as needed so its face normal agrees with `hint`. */
  quad(a: V3, b: V3, c: V3, d: V3, s: Surface, hint: V3, uvs?: [number, number][], ao: [number, number, number, number] = [1, 1, 1, 1]) {
    const e1x = b[0] - a[0];
    const e1y = b[1] - a[1];
    const e1z = b[2] - a[2];
    const e2x = c[0] - a[0];
    const e2y = c[1] - a[1];
    const e2z = c[2] - a[2];
    let nx = e1y * e2z - e1z * e2y;
    let ny = e1z * e2x - e1x * e2z;
    let nz = e1x * e2y - e1y * e2x;
    let len = Math.hypot(nx, ny, nz);
    if (len < 1e-9) {
      const f1x = d[0] - a[0];
      const f1y = d[1] - a[1];
      const f1z = d[2] - a[2];
      nx = e2y * f1z - e2z * f1y;
      ny = e2z * f1x - e2x * f1z;
      nz = e2x * f1y - e2y * f1x;
      len = Math.hypot(nx, ny, nz);
      if (len < 1e-9) return;
    }
    nx /= len;
    ny /= len;
    nz /= len;
    const flip = nx * hint[0] + ny * hint[1] + nz * hint[2] < 0;
    if (flip) {
      nx = -nx;
      ny = -ny;
      nz = -nz;
    }
    const n: V3 = [nx, ny, nz];
    const t = uvs ?? [
      [a[0], a[2]],
      [b[0], b[2]],
      [c[0], c[2]],
      [d[0], d[2]],
    ];
    const i0 = this.vertex(a, n, t[0][0], t[0][1], s, ao[0]);
    const i1 = this.vertex(b, n, t[1][0], t[1][1], s, ao[1]);
    const i2 = this.vertex(c, n, t[2][0], t[2][1], s, ao[2]);
    const i3 = this.vertex(d, n, t[3][0], t[3][1], s, ao[3]);
    this.grow(0, 6);
    const idx = this.index;
    let k = this.indexCount;
    if (flip) {
      idx[k++] = i0;
      idx[k++] = i2;
      idx[k++] = i1;
      idx[k++] = i0;
      idx[k++] = i3;
      idx[k++] = i2;
    } else {
      idx[k++] = i0;
      idx[k++] = i1;
      idx[k++] = i2;
      idx[k++] = i0;
      idx[k++] = i2;
      idx[k++] = i3;
    }
    this.indexCount = k;
  }

  /** Horizontal polygon at height y, facing up. */
  cap(ring: P2[], y: number, s: Surface) {
    const contour = ring.map(([x, z]) => new THREE.Vector2(x, z));
    const faces = THREE.ShapeUtils.triangulateShape(contour, []);
    const n: V3 = [0, 1, 0];
    const base = this.vertexCount;
    for (const [x, z] of ring) this.vertex([x, y, z], n, x, z, s);
    this.grow(0, faces.length * 3);
    for (const [a, b, c] of faces) {
      const area = (ring[b][1] - ring[a][1]) * (ring[c][0] - ring[a][0]) - (ring[b][0] - ring[a][0]) * (ring[c][1] - ring[a][1]);
      this.index[this.indexCount++] = base + a;
      this.index[this.indexCount++] = area > 0 ? base + b : base + c;
      this.index[this.indexCount++] = area > 0 ? base + c : base + b;
    }
  }

  /** Axis-aligned-to-(ax, az) box standing on y0. Top is `top` surface, sides are `s`. */
  box(cx: number, cz: number, ax: number, az: number, hw: number, hd: number, y0: number, y1: number, s: Surface, top: Surface = s) {
    const bx = -az;
    const bz = ax;
    const corner = (sx: number, sz: number, y: number): V3 => [
      cx + ax * hw * sx + bx * hd * sz,
      y,
      cz + az * hw * sx + bz * hd * sz,
    ];
    const sides: [number, number, number, number][] = [
      [1, -1, 1, 1],
      [1, 1, -1, 1],
      [-1, 1, -1, -1],
      [-1, -1, 1, -1],
    ];
    for (const [s0x, s0z, s1x, s1z] of sides) {
      const hint: V3 = [(s0x + s1x) * ax * 0.5 + (s0z + s1z) * bx * 0.5, 0, (s0x + s1x) * az * 0.5 + (s0z + s1z) * bz * 0.5];
      this.quad(corner(s0x, s0z, y0), corner(s0x, s0z, y1), corner(s1x, s1z, y1), corner(s1x, s1z, y0), s, hint, [
        [0, y0],
        [0, y1],
        [1, y1],
        [1, y0],
      ]);
    }
    this.quad(corner(1, -1, y1), corner(1, 1, y1), corner(-1, 1, y1), corner(-1, -1, y1), top, [0, 1, 0]);
  }

  build(): THREE.BufferGeometry {
    const geo = new THREE.BufferGeometry();
    const n = this.vertexCount;
    geo.setAttribute("position", new THREE.BufferAttribute(this.position.slice(0, n * 3), 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(this.normal.slice(0, n * 3), 3));
    geo.setAttribute("uv", new THREE.BufferAttribute(this.uv.slice(0, n * 2), 2));
    geo.setAttribute("color", new THREE.BufferAttribute(this.color.slice(0, n * 3), 3));
    geo.setAttribute("aTwin", new THREE.BufferAttribute(this.twin.slice(0, n * 3), 3));
    geo.setIndex(new THREE.BufferAttribute(this.index.slice(0, this.indexCount), 1));
    geo.computeBoundingSphere();
    geo.computeBoundingBox();
    return geo;
  }
}
