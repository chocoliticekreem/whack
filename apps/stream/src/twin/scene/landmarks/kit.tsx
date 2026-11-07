"use client";

import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { getMats, WINDOW_TILE, type MatKey } from "./materials";

const _m = new THREE.Matrix4();
const _q = new THREE.Quaternion();
const _e = new THREE.Euler();
const _p = new THREE.Vector3();
const _s = new THREE.Vector3();

/** Collects transformed geometry per material so a whole landmark merges into a few draw calls. */
export class Kit {
  private groups = new Map<MatKey | "lattice", THREE.BufferGeometry[]>();

  add(
    key: MatKey | "lattice",
    geo: THREE.BufferGeometry,
    x = 0,
    y = 0,
    z = 0,
    ry = 0,
    rx = 0,
    rz = 0,
    scale: [number, number, number] = [1, 1, 1],
  ) {
    const g = geo.index ? geo.toNonIndexed() : geo;
    if (g !== geo) geo.dispose();
    for (const name of Object.keys(g.attributes)) {
      if (name !== "position" && name !== "normal" && name !== "uv") g.deleteAttribute(name);
    }
    if (!g.attributes.uv) g.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
    _e.set(rx, ry, rz, "YXZ");
    _q.setFromEuler(_e);
    _m.compose(_p.set(x, y, z), _q, _s.set(scale[0], scale[1], scale[2]));
    g.applyMatrix4(_m);
    const list = this.groups.get(key) ?? [];
    list.push(g);
    this.groups.set(key, list);
  }

  /** Box whose base sits at y. */
  box(key: MatKey, w: number, h: number, d: number, x: number, y: number, z: number, ry = 0) {
    this.add(key, new THREE.BoxGeometry(w, h, d), x, y + h / 2, z, ry);
  }

  /** Lit-window box: UVs follow real size so windows stay 3.2 x 3.6 m everywhere. */
  winBox(key: MatKey, w: number, h: number, d: number, x: number, y: number, z: number, ry = 0, seed = 0) {
    this.add(key, winBoxGeometry(w, h, d, seed), x, y + h / 2, z, ry);
  }

  /** Cylinder or cone frustum, base at y. */
  cyl(key: MatKey, rTop: number, rBot: number, h: number, segs: number, x: number, y: number, z: number, ry = 0) {
    this.add(key, new THREE.CylinderGeometry(rTop, rBot, h, segs), x, y + h / 2, z, ry);
  }

  build(): { key: MatKey | "lattice"; geo: THREE.BufferGeometry }[] {
    return [...this.groups.entries()].map(([key, list]) => {
      const geo = mergeGeometries(list)!;
      list.forEach((g) => g.dispose());
      return { key, geo };
    });
  }
}

export function winBoxGeometry(w: number, h: number, d: number, seed = 0) {
  const g = new THREE.BoxGeometry(w, h, d);
  const uv = g.attributes.uv as THREE.BufferAttribute;
  const faceW = [d, d, 0, 0, w, w];
  for (let f = 0; f < 6; f++) {
    for (let i = 0; i < 4; i++) {
      const k = f * 4 + i;
      if (f === 2 || f === 3) uv.setXY(k, 0.01, 0.01);
      else uv.setXY(k, (uv.getX(k) * faceW[f]) / WINDOW_TILE.w + seed * 0.37 + f * 0.21, (uv.getY(k) * h) / WINDOW_TILE.h);
    }
  }
  return g;
}

export function scaleUV(g: THREE.BufferGeometry, su: number, sv: number) {
  const uv = g.attributes.uv as THREE.BufferAttribute;
  for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * su, uv.getY(i) * sv);
  return g;
}

/** Open-ended cylinder with window UVs; add a roof cap separately. */
export function winCylinder(rTop: number, rBot: number, h: number, segs: number) {
  const g = new THREE.CylinderGeometry(rTop, rBot, h, segs, 1, true);
  const circ = Math.PI * (rTop + rBot);
  const uv = g.attributes.uv as THREE.BufferAttribute;
  for (let i = 0; i < uv.count; i++) uv.setXY(i, (uv.getX(i) * circ) / WINDOW_TILE.w, (uv.getY(i) * h) / WINDOW_TILE.h);
  return g;
}

export function KitMeshes({ kit }: { kit: Kit }) {
  const built = useMemo(() => kit.build(), [kit]);
  useEffect(() => () => built.forEach((b) => b.geo.dispose()), [built]);
  const mats = getMats();
  return (
    <>
      {built.map(({ key, geo }) => (
        <mesh key={key} geometry={geo} material={mats[key]} />
      ))}
    </>
  );
}
