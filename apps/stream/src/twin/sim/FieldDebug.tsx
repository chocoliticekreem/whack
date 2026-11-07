"use client";

import { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import { getLastField, useFieldStore } from "./useField";

/** `?field=1`: road segments coloured by estimated load; observed solid, inferred translucent. */

const COLD = new THREE.Color("#2a6f8f");
const HOT = new THREE.Color("#ff5a1f");
const _c = new THREE.Color();

function enabled() {
  return typeof window !== "undefined" && new URLSearchParams(window.location.search).get("field") === "1";
}

export function FieldDebug() {
  const [on] = useState(enabled);
  const at = useFieldStore((s) => s?.at ?? 0);
  const geo = useMemo(() => {
    if (!on || !at) return null;
    const f = getLastField();
    if (!f) return null;
    const build = (observed: boolean) => {
      const pos: number[] = [];
      const col: number[] = [];
      for (const s of f.segments) {
        if ((s.source === "observed") !== observed) continue;
        const load = Math.min(1, (s.peds + s.vehicles * 1.5) / 20);
        _c.copy(COLD).lerp(HOT, load);
        pos.push(s.a[0], 0.6, s.a[1], s.b[0], 0.6, s.b[1]);
        col.push(_c.r, _c.g, _c.b, _c.r, _c.g, _c.b);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
      return g;
    };
    return { solid: build(true), faint: build(false) };
  }, [on, at]);
  useEffect(() => {
    return () => {
      geo?.solid.dispose();
      geo?.faint.dispose();
    };
  }, [geo]);

  const mats = useMemo(
    () => ({
      solid: new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.95, depthWrite: false }),
      faint: new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.35, depthWrite: false }),
    }),
    [],
  );
  useEffect(() => {
    return () => {
      mats.solid.dispose();
      mats.faint.dispose();
    };
  }, [mats]);

  if (!on || !geo) return null;
  return (
    <group>
      <lineSegments geometry={geo.solid} material={mats.solid} frustumCulled={false} />
      <lineSegments geometry={geo.faint} material={mats.faint} frustumCulled={false} />
    </group>
  );
}
