"use client";

import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { Ring } from "../../config";
import { railHeight } from "../../landmarks";
import { useGeometry } from "../useGeometry";
import { getMats } from "./materials";

const STEP = 8;
const TRACK_W = 4.2;
const DECK_DEPTH = 1.5;
const PIER_STEP = 9;

function densify(line: Ring): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i < line.length - 1; i++) {
    const [ax, az] = line[i];
    const [bx, bz] = line[i + 1];
    const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, bz - az) / STEP));
    for (let k = 0; k < n; k++) out.push([ax + ((bx - ax) * k) / n, az + ((bz - az) * k) / n]);
  }
  out.push(line[line.length - 1]);
  return out;
}

type V3 = [number, number, number];

function quad(out: number[], a: V3, b: V3, c: V3, d: V3) {
  out.push(...a, ...b, ...c, ...b, ...d, ...c);
}

interface RailLayers {
  deck: THREE.BufferGeometry;
  track: THREE.BufferGeometry;
  piers: THREE.BufferGeometry | null;
}

function toGeometry(v: number[]) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(v, 3));
  g.computeVertexNormals();
  return g;
}

function buildRail(lines: Ring[]): RailLayers | null {
  const deck: number[] = [];
  const track: number[] = [];
  const piers: THREE.BufferGeometry[] = [];
  const used = new Set<string>();
  const xAxis = new THREE.Vector3(1, 0, 0);
  const dir = new THREE.Vector3();
  const q = new THREE.Quaternion();
  const m = new THREE.Matrix4();

  for (const raw of lines) {
    const pts = densify(raw);
    const hs = pts.map(([x, z]) => railHeight(x, z));
    if (Math.max(...hs) < 1) continue;
    let sincePier = PIER_STEP;
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, az] = pts[i];
      const [bx, bz] = pts[i + 1];
      const ha = hs[i];
      const hb = hs[i + 1];
      if (ha < 0.3 && hb < 0.3) continue;
      const len = Math.hypot(bx - ax, bz - az);
      if (len < 0.01) continue;
      const ux = -(bz - az) / len;
      const uz = (bx - ax) / len;
      const A = (lat: number, dy: number): V3 => [ax + ux * lat, ha + dy, az + uz * lat];
      const B = (lat: number, dy: number): V3 => [bx + ux * lat, hb + dy, bz + uz * lat];
      const hw = TRACK_W / 2;
      quad(deck, A(-hw, 0.35), A(hw, 0.35), B(-hw, 0.35), B(hw, 0.35));
      quad(deck, A(-hw, 0.35), B(-hw, 0.35), A(-hw, 0.35 - DECK_DEPTH), B(-hw, 0.35 - DECK_DEPTH));
      quad(deck, B(hw, 0.35), A(hw, 0.35), B(hw, 0.35 - DECK_DEPTH), A(hw, 0.35 - DECK_DEPTH));
      for (const off of [-0.75, 0.75]) quad(track, A(off - 0.1, 0.5), A(off + 0.1, 0.5), B(off - 0.1, 0.5), B(off + 0.1, 0.5));
      sincePier += len;
      if (sincePier >= PIER_STEP && ha > 1.5) {
        sincePier = 0;
        const key = `${Math.round(ax / 3)},${Math.round(az / 3)}`;
        if (used.has(key)) continue;
        used.add(key);
        q.setFromUnitVectors(xAxis, dir.set(bx - ax, 0, bz - az).normalize());
        const ph = ha - DECK_DEPTH + 0.2;
        m.compose(new THREE.Vector3(ax, ph / 2, az), q, new THREE.Vector3(1.3, ph, TRACK_W * 0.85));
        const g = new THREE.BoxGeometry(1, 1, 1).toNonIndexed();
        g.deleteAttribute("uv");
        g.applyMatrix4(m);
        piers.push(g);
      }
    }
  }
  if (!deck.length) return null;
  const pierGeo = piers.length ? mergeGeometries(piers) : null;
  piers.forEach((g) => g.dispose());
  return { deck: toGeometry(deck), track: toGeometry(track), piers: pierGeo };
}

/** Brick viaducts for the elevated rail (c2c into Fenchurch Street, the DLR, London Bridge approaches). */
export function Rail() {
  const { roads } = useGeometry();
  const layers = useMemo(() => buildRail(roads.filter((r) => r.kind === "rail" && !r.tunnel).map((r) => r.line)), [roads]);
  useEffect(
    () => () => {
      layers?.deck.dispose();
      layers?.track.dispose();
      layers?.piers?.dispose();
    },
    [layers],
  );
  if (!layers) return null;
  const mats = getMats();
  return (
    <>
      <mesh geometry={layers.deck} material={mats.ballast} />
      <mesh geometry={layers.track} material={mats.track} />
      {layers.piers && <mesh geometry={layers.piers} material={mats.brick} />}
    </>
  );
}
