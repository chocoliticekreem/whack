"use client";

import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { MARK_Y, ROAD_Y, layoutOf, offsetPolyline, subPolyline } from "../network/layout";
import type { RoadNetwork, XZ } from "../network/types";
import { useNetwork } from "../network/useNetwork";
import { MeshBuilder, makeSurfaceMaterial, useSurfaceUniforms } from "./surfaces";

const ASPHALT = new THREE.Color("#32363f");
const PAINT = new THREE.Color("#d6dae2");

const DASH = { major: { on: 3.5, off: 7 }, minor: { on: 2, off: 5 } };
const CELL = 20;
const EDGE_LINE_PIECE = 2.5;

/** True where a point lies on another road's carriageway, so paint there would run across foreign asphalt. */
function makeCoverTest(net: RoadNetwork) {
  const cells = new Map<number, [number, number, number, number, number, number][]>();
  const key = (cx: number, cz: number) => (cx + 1024) * 4096 + (cz + 1024);
  for (const edge of net.edges) {
    for (let i = 0; i < edge.pts.length - 1; i++) {
      const [ax, az] = edge.pts[i];
      const [bx, bz] = edge.pts[i + 1];
      const seg: [number, number, number, number, number, number] = [ax, az, bx, bz, edge.width / 2 + 0.4, edge.id];
      for (let cx = Math.floor((Math.min(ax, bx) - 6) / CELL); cx <= Math.floor((Math.max(ax, bx) + 6) / CELL); cx++) {
        for (let cz = Math.floor((Math.min(az, bz) - 6) / CELL); cz <= Math.floor((Math.max(az, bz) + 6) / CELL); cz++) {
          const list = cells.get(key(cx, cz));
          if (list) list.push(seg);
          else cells.set(key(cx, cz), [seg]);
        }
      }
    }
  }
  return (x: number, z: number, self: number): boolean => {
    for (const [ax, az, bx, bz, reach, id] of cells.get(key(Math.floor(x / CELL), Math.floor(z / CELL))) ?? []) {
      if (id === self) continue;
      const ex = bx - ax;
      const ez = bz - az;
      const u = Math.min(1, Math.max(0, ((x - ax) * ex + (z - az) * ez) / (ex * ex + ez * ez || 1)));
      if (Math.hypot(x - ax - ex * u, z - az - ez * u) < reach) return true;
    }
    return false;
  };
}

function paintPiece(b: MeshBuilder, line: XZ[], half: number, covered: (x: number, z: number) => boolean) {
  const mid = line[line.length >> 1];
  const [first, last] = [line[0], line[line.length - 1]];
  if (covered(mid[0], mid[1]) || covered(first[0], first[1]) || covered(last[0], last[1])) return;
  b.ribbon(line, half, MARK_Y, PAINT);
}

function paintDashes(
  b: MeshBuilder,
  pts: XZ[],
  cum: number[],
  from: number,
  to: number,
  on: number,
  off: number,
  covered: (x: number, z: number) => boolean,
) {
  const span = to - from;
  if (span < on) return;
  const count = Math.max(1, Math.floor((span + off) / (on + off)));
  const pitch = count > 1 ? (span - on) / (count - 1) : 0;
  const start = count > 1 ? from : from + (span - on) / 2;
  for (let i = 0; i < count; i++) {
    const s0 = start + pitch * i;
    paintPiece(b, subPolyline(pts, cum, s0, s0 + on), 0.08, covered);
  }
}

/** One flat layer: a ribbon per edge, with a disc at every junction and bend so the joins are round and gap-free. */
function buildAsphalt(net: RoadNetwork): THREE.BufferGeometry {
  const layout = layoutOf(net);
  const b = new MeshBuilder();
  for (const edge of net.edges) if (!layout.hidden[edge.id]) b.ribbon(edge.pts, edge.width / 2, ROAD_Y, ASPHALT);
  for (const node of net.nodes) {
    const r = layout.nodeRadius[node.id];
    if (r > 0 && !node.edges.every((e) => layout.hidden[e])) b.disc(node.x, node.z, r, ROAD_Y, ASPHALT);
  }
  return b.build();
}

function buildMarkings(net: RoadNetwork): THREE.BufferGeometry {
  const layout = layoutOf(net);
  const b = new MeshBuilder();
  const covered = makeCoverTest(net);
  net.edges.forEach((edge) => {
    const strip = layout.strips[edge.id];
    if (!strip || strip.length < 8 || layout.hidden[edge.id]) return;
    const here = (x: number, z: number) => covered(x, z, edge.id);
    const margin = (atStart: boolean) => (net.nodes[atStart ? edge.from : edge.to].junction ? 2.5 : 0.5);
    if (!edge.oneway) {
      const { on, off } = DASH[edge.kind];
      paintDashes(b, strip.pts, strip.cum, margin(true), strip.length - margin(false), on, off, here);
    }
    if (edge.kind !== "major") return;
    for (let s = 0; s < strip.length - 0.5; s += EDGE_LINE_PIECE) {
      const piece = subPolyline(strip.pts, strip.cum, s, Math.min(strip.length, s + EDGE_LINE_PIECE));
      for (const side of [1, -1]) paintPiece(b, offsetPolyline(piece, side * (edge.width / 2 - 0.5)), 0.06, here);
    }
  });

  for (const items of layout.items) {
    for (const it of items) {
      if (!it.crossing || layout.hidden[it.edge]) continue;
      const edge = net.edges[it.edge];
      const strip = layout.strips[it.edge];
      if (!strip) continue;
      const n = strip.pts.length;
      const c = it.atStart ? strip.pts[0] : strip.pts[n - 1];
      const nb = it.atStart ? strip.pts[1] : strip.pts[n - 2];
      const dl = Math.hypot(nb[0] - c[0], nb[1] - c[1]) || 1;
      const dx = (nb[0] - c[0]) / dl;
      const dz = (nb[1] - c[1]) / dl;
      const reach = edge.width / 2 - 0.7;
      for (let lat = -reach; lat <= reach + 0.01; lat += 1) {
        const x0 = c[0] - dz * (lat - 0.25) - dx * 1.5;
        const z0 = c[1] + dx * (lat - 0.25) - dz * 1.5;
        const x1 = c[0] - dz * (lat + 0.25) - dx * 1.5;
        const z1 = c[1] + dx * (lat + 0.25) - dz * 1.5;
        b.flatQuad([x0, z0], [x1, z1], [x0 + dx * 3, z0 + dz * 3], [x1 + dx * 3, z1 + dz * 3], MARK_Y, PAINT);
      }
    }
  }
  return b.build();
}

const NODE_COLOURS = [new THREE.Color(2.6, 0.2, 2.2), new THREE.Color(0.2, 1.8, 2.4), new THREE.Color(2.8, 1.3, 0.2)];

function NodeDebug({ net }: { net: RoadNetwork }) {
  const { mesh, lines } = useMemo(() => {
    const inst = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1.1, 10, 8),
      new THREE.MeshBasicMaterial({ toneMapped: false }),
      net.nodes.length,
    );
    const m = new THREE.Matrix4();
    net.nodes.forEach((n, i) => {
      const deg = n.edges.length;
      const scale = n.junction ? 1.6 : 1;
      m.makeScale(scale, scale, scale).setPosition(n.x, 1.6, n.z);
      inst.setMatrixAt(i, m);
      inst.setColorAt(i, NODE_COLOURS[deg <= 1 ? 0 : deg === 2 ? 1 : 2]);
    });
    inst.frustumCulled = false;
    const verts: number[] = [];
    for (const e of net.edges) {
      for (let i = 0; i < e.pts.length - 1; i++) {
        verts.push(e.pts[i][0], 1, e.pts[i][1], e.pts[i + 1][0], 1, e.pts[i + 1][1]);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
    const seg = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: new THREE.Color(0.4, 2.2, 0.6), toneMapped: false }));
    seg.frustumCulled = false;
    return { mesh: inst, lines: seg };
  }, [net]);

  useEffect(
    () => () => {
      mesh.geometry.dispose();
      (mesh.material as THREE.Material).dispose();
      mesh.dispose();
      lines.geometry.dispose();
      (lines.material as THREE.Material).dispose();
    },
    [mesh, lines],
  );

  return (
    <>
      <primitive object={mesh} />
      <primitive object={lines} />
    </>
  );
}

export function Roads() {
  const net = useNetwork();
  useSurfaceUniforms();

  const { asphalt, markings } = useMemo(
    () => ({ asphalt: buildAsphalt(net), markings: buildMarkings(net) }),
    [net],
  );
  const materials = useMemo(() => {
    const paint = makeSurfaceMaterial({ roughness: 0.55, layer: 5 });
    paint.emissive.set("#ffffff");
    paint.emissiveIntensity = 0.12;
    return { asphalt: makeSurfaceMaterial({ roughness: 0.92, layer: 3, grain: 0.35 }), paint };
  }, []);
  const showNodes = useMemo(() => new URLSearchParams(window.location.search).get("nodes") === "1", []);

  useEffect(
    () => () => {
      asphalt.dispose();
      markings.dispose();
    },
    [asphalt, markings],
  );
  useEffect(
    () => () => {
      materials.asphalt.dispose();
      materials.paint.dispose();
    },
    [materials],
  );

  return (
    <>
      <mesh geometry={asphalt} material={materials.asphalt} receiveShadow />
      <mesh geometry={markings} material={materials.paint} />
      {showNodes && <NodeDebug net={net} />}
    </>
  );
}
