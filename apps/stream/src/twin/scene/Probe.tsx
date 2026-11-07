"use client";

import { useThree } from "@react-three/fiber";
import { useEffect } from "react";
import { perf } from "./perf";

/** `window.__twin.bench()` for draw-call/triangle counts (msPerFrame is CPU submit time only); `window.__twin.camera` for the live camera. */
export function Probe() {
  const { gl, scene, camera } = useThree();
  useEffect(() => {
    const w = window as unknown as { __twin?: unknown };
    w.__twin = {
      gl,
      scene,
      camera,
      perf,
      bench(n = 20) {
        gl.info.autoReset = false;
        gl.info.reset();
        const t0 = performance.now();
        for (let i = 0; i < n; i++) gl.render(scene, camera);
        gl.getContext().finish();
        const ms = (performance.now() - t0) / n;
        const out = {
          msPerFrame: +ms.toFixed(2),
          drawCalls: Math.round(gl.info.render.calls / n),
          triangles: Math.round(gl.info.render.triangles / n),
          geometries: gl.info.memory.geometries,
          textures: gl.info.memory.textures,
          programs: gl.info.programs?.length,
          dpr: gl.getPixelRatio(),
          tier: perf.tier,
          renderer: perf.renderer,
        };
        gl.info.autoReset = true;
        return out;
      },
    };
    return () => {
      delete w.__twin;
    };
  }, [gl, scene, camera]);
  return null;
}
