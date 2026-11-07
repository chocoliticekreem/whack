"use client";

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { ATMOS, CAMERA_FAR } from "./palette";
import { concreteTexture } from "./textures";

const RADIUS = CAMERA_FAR;
const TILE_M = 64;
const BLOCK_M = 46;

const DAY = new THREE.Color("#868e9a");
const NIGHT = new THREE.Color("#4a5470");

const wetUniform = { value: 0 };
const tileUniform = { value: TILE_M };

/**
 * Swaps the flat map lookup for three samples of the same tile: detail, a stretched macro layer that hides
 * the repeat, and a per-city-block hash tint. Wet puddles lower roughness where the noise is dark.
 */
const MAP_FRAGMENT = /* glsl */ `
vec4 gDetail = texture2D(map, vMapUv);
float gMacro = dot(texture2D(map, vMapUv * 0.0371 + vec2(0.27, 0.61)).rgb, vec3(0.3333));
float gMid = dot(texture2D(map, vMapUv * 0.5 + vec2(0.3, 0.1)).rgb, vec3(0.3333));
vec2 gBlock = floor(vMapUv * uGroundTile / ${BLOCK_M.toFixed(1)});
float gHash = fract(sin(dot(gBlock, vec2(127.1, 311.7))) * 43758.5453);
vec3 gPave = (0.95 + 0.1 * gHash) * mix(vec3(0.98, 0.99, 1.03), vec3(1.02, 1.01, 1.0), fract(gHash * 7.3));
float gPuddle = 1.0 - smoothstep(0.3, 0.38, gMid);
diffuseColor.rgb *= gDetail.rgb * mix(1.0, gMacro / 0.4, 0.3) * gPave;
diffuseColor.rgb *= mix(1.0, 0.72 - 0.22 * gPuddle, uGroundWet);
`;

export function Ground() {
  const material = useMemo(() => {
    const texture = concreteTexture({ tileM: TILE_M, uvScale: RADIUS * 2 });
    const material = new THREE.MeshStandardMaterial({
      map: texture,
      color: DAY,
      roughness: 0.95,
      metalness: 0,
    });
    material.customProgramCacheKey = () => "twin-ground";
    material.onBeforeCompile = (shader) => {
      shader.uniforms.uGroundWet = wetUniform;
      shader.uniforms.uGroundTile = tileUniform;
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", "#include <common>\nuniform float uGroundWet;\nuniform float uGroundTile;")
        .replace("#include <map_fragment>", MAP_FRAGMENT)
        .replace(
          "#include <roughnessmap_fragment>",
          "#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, mix(0.5, 0.1, gPuddle), uGroundWet);",
        );
    };
    return material;
  }, []);

  useFrame(() => {
    material.color.copy(NIGHT).lerp(DAY, 1 - ATMOS.windowGlow);
    material.emissive.copy(ATMOS.glow).multiplyScalar(0.025 * ATMOS.windowGlow);
    wetUniform.value = ATMOS.wet;
  });

  useEffect(() => () => material.dispose(), [material]);

  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.05, 0]} material={material}>
      <circleGeometry args={[RADIUS, 96]} />
    </mesh>
  );
}
