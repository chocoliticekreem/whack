"use client";

import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { KERB_W, KERB_Y, PATH_W, PATH_Y, PAVE_W, PAVEMENT_Y, layoutOf, underHeroLandmark } from "../network/layout";
import type { RoadNetwork } from "../network/types";
import { useNetwork } from "../network/useNetwork";
import { MeshBuilder, makeSurfaceMaterial, useSurfaceUniforms } from "./surfaces";

const SLAB = new THREE.Color("#9a9b9e");
const KERB = new THREE.Color("#62646a");

/**
 * Pavements are drawn UNDER the road: a wide slab ribbon and a slightly narrower kerb ribbon per edge,
 * rounded off by a disc at every node. The asphalt layer is drawn on top, so wherever roads meet,
 * cross or run close together the carriageway always wins and nothing overlaps or leaves a gap.
 */
function buildPavements(net: RoadNetwork): { slab: THREE.BufferGeometry; kerb: THREE.BufferGeometry } {
  const layout = layoutOf(net);
  const slab = new MeshBuilder();
  const kerb = new MeshBuilder();
  for (const edge of net.edges) {
    if (layout.hidden[edge.id]) continue;
    slab.ribbon(edge.pts, edge.width / 2 + PAVE_W, PAVEMENT_Y, SLAB, 1);
    kerb.ribbon(edge.pts, edge.width / 2 + KERB_W, KERB_Y, KERB);
  }
  for (const node of net.nodes) {
    const r = layout.nodeRadius[node.id];
    if (r <= 0 || node.edges.every((e) => layout.hidden[e])) continue;
    slab.disc(node.x, node.z, r + PAVE_W, PAVEMENT_Y, SLAB, 1);
    kerb.disc(node.x, node.z, r + KERB_W, KERB_Y, KERB);
  }
  for (const id of layout.pathIds) {
    const { pts, cum } = net.footways[id];
    if (!underHeroLandmark(pts, cum)) slab.ribbon(pts, PATH_W / 2, PATH_Y, SLAB, 1);
  }
  return { slab: slab.build(), kerb: kerb.build() };
}

export function Pavements() {
  const net = useNetwork();
  useSurfaceUniforms();
  const geometry = useMemo(() => buildPavements(net), [net]);
  const materials = useMemo(
    () => ({
      slab: makeSurfaceMaterial({ roughness: 0.88, layer: 1, paving: true }),
      kerb: makeSurfaceMaterial({ roughness: 0.9, layer: 2 }),
    }),
    [],
  );

  useEffect(
    () => () => {
      geometry.slab.dispose();
      geometry.kerb.dispose();
    },
    [geometry],
  );
  useEffect(
    () => () => {
      materials.slab.dispose();
      materials.kerb.dispose();
    },
    [materials],
  );

  return (
    <>
      <mesh geometry={geometry.slab} material={materials.slab} />
      <mesh geometry={geometry.kerb} material={materials.kerb} />
    </>
  );
}
