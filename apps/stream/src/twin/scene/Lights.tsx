"use client";

import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import * as THREE from "three";
import { ATMOS } from "./palette";
import { fxOff, perf } from "./perf";

const SUN_DISTANCE = 1500;
const FILL_DUSK = new THREE.Color("#ffb070");
const FILL_DAY = new THREE.Color("#ffe8cc");

/** The shadow box covers 15 % of the map size in metres per side half-width, so a texel stays about 0.3 m. */
const EXTENT_PER_TEXEL = 0.15;
const MIN_EXTENT = 100;
const SHADOW_SCAN_S = 0.5;

const _dir = new THREE.Vector3();
const _right = new THREE.Vector3();
const _up = new THREE.Vector3();
const _center = new THREE.Vector3();
const _ray = new THREE.Vector3();
const UP = new THREE.Vector3(0, 1, 0);

/**
 * Every lit solid mesh receives shadows; big, tall ones cast them. Flat layers (roads, parks, ground) only receive, and
 * instanced props (cars, people) only cast on the high tier. Modules that add meshes later are picked up by the next scan,
 * and `userData.noShadow` opts a mesh out.
 */
function flagShadows(scene: THREE.Scene) {
  const castInstanced = perf.tier === "high";
  scene.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh || mesh.userData.noShadow) return;
    const mat = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as THREE.Material & {
      isMeshStandardMaterial?: boolean;
      isMeshLambertMaterial?: boolean;
      isMeshPhongMaterial?: boolean;
    };
    if (!mat || mat.transparent || !(mat.isMeshStandardMaterial || mat.isMeshLambertMaterial || mat.isMeshPhongMaterial)) return;
    const geo = mesh.geometry;
    if (!geo.boundingBox) geo.computeBoundingBox();
    const box = geo.boundingBox;
    const instanced = (mesh as THREE.InstancedMesh).isInstancedMesh === true;
    const solid = !!box && box.max.y - box.min.y > (instanced ? 0.5 : 1.5);
    const cast = solid && (!instanced || castInstanced);
    if (mesh.castShadow !== cast) mesh.castShadow = cast;
    if (!mesh.receiveShadow) mesh.receiveShadow = true;
  });
}

export function Lights() {
  const hemi = useRef<THREE.HemisphereLight>(null);
  const sun = useRef<THREE.DirectionalLight>(null);
  const fill = useRef<THREE.DirectionalLight>(null);
  const scan = useRef(SHADOW_SCAN_S);
  const extent = useRef(0);

  useFrame(({ camera, scene, controls }, dt) => {
    const flash = ATMOS.lightning * 6;
    if (hemi.current) {
      hemi.current.color.copy(ATMOS.hemiSky);
      hemi.current.groundColor.copy(ATMOS.hemiGround);
      hemi.current.intensity = ATMOS.hemiIntensity + flash;
    }
    const s = sun.current;
    if (s) {
      s.color.copy(ATMOS.sunColor);
      s.intensity = ATMOS.sunIntensity;
      _dir.copy(ATMOS.sunDir);

      // The shadow box follows what the camera looks at: the controls target, or where the view ray meets the ground.
      const target = (controls as { target?: THREE.Vector3 } | null)?.target;
      if (target) _center.copy(target);
      else {
        camera.getWorldDirection(_ray);
        _center.copy(camera.position).addScaledVector(_ray, _ray.y < -0.05 ? -camera.position.y / _ray.y : 300);
      }
      _center.y = 0;
      const size = fxOff("noshadow") ? 0 : perf.shadowSize;
      const texelsToMetres = size * EXTENT_PER_TEXEL;
      const reach = Math.min(Math.max(Math.round((camera.position.distanceTo(_center) * 1.1) / 20) * 20, MIN_EXTENT), texelsToMetres);

      if (s.castShadow !== size > 0) s.castShadow = size > 0;
      if (size > 0) {
        if (s.shadow.mapSize.x !== size) {
          s.shadow.mapSize.set(size, size);
          s.shadow.map?.dispose();
          s.shadow.map = null;
        }
        if (extent.current !== reach) {
          extent.current = reach;
          const cam = s.shadow.camera;
          cam.left = -reach;
          cam.right = reach;
          cam.top = reach;
          cam.bottom = -reach;
          cam.near = 10;
          cam.far = SUN_DISTANCE * 2.2;
          cam.updateProjectionMatrix();
        }
        // Snap the box centre to whole shadow texels in light space so edges do not crawl while the camera moves.
        const texel = (2 * reach) / size;
        _right.crossVectors(UP, _dir).normalize();
        _up.crossVectors(_dir, _right);
        const a = _center.dot(_right);
        const b = _center.dot(_up);
        _center.addScaledVector(_right, Math.round(a / texel) * texel - a).addScaledVector(_up, Math.round(b / texel) * texel - b);
        s.shadow.bias = -0.0003;
        s.shadow.normalBias = texel * (1.2 + 0.5 / Math.max(_dir.y, 0.15));
      }
      s.position.copy(_center).addScaledVector(_dir, SUN_DISTANCE);
      s.target.position.copy(_center);
      s.target.updateMatrixWorld();
    }
    if (fill.current) {
      // The low sun is in front of the default cameras, so the faces they see are backlit; a daylight fill from behind lights them.
      fill.current.color.copy(FILL_DUSK).lerp(FILL_DAY, Math.min(ATMOS.sunIntensity / 4.2, 1));
      fill.current.intensity = 0.35 * (1 - ATMOS.cloud * 0.6) + 0.3 * ATMOS.sunIntensity * ATMOS.sunDisc;
    }
    scan.current += dt;
    if (scan.current >= SHADOW_SCAN_S) {
      scan.current = 0;
      flagShadows(scene);
    }
  });

  return (
    <>
      <hemisphereLight ref={hemi} args={["#4a5d96", "#141018", 2.4]} />
      <directionalLight ref={sun} position={[-600, 700, -500]} intensity={1.1} color="#8ea4d8" />
      <directionalLight ref={fill} position={[500, 300, 600]} intensity={0.35} color="#ffb070" />
    </>
  );
}
