import * as THREE from "three";
import { ATMOS } from "../palette";
import type { Layer, SignSpec } from "./kit";

function glowing() {
  return new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false });
}

export type SiteMaterials = Record<Layer, THREE.Material>;

export function makeSiteMaterials(): SiteMaterials {
  return {
    body: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.92, metalness: 0 }),
    glass: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.3, metalness: 0.1 }),
    lit: glowing(),
    strip: glowing(),
  };
}

/** Called every frame: windows and strip lights follow the day/night blend. */
export function updateSiteMaterials(m: SiteMaterials, signs: THREE.MeshBasicMaterial[]) {
  const g = ATMOS.windowGlow;
  (m.lit as THREE.MeshBasicMaterial).color.setScalar(g * 2.6);
  m.lit.visible = g > 0.04;
  (m.glass as THREE.MeshStandardMaterial).color.setScalar(1 + (1 - g) * 2.2);
  (m.strip as THREE.MeshBasicMaterial).color.setScalar(0.4 + g * 2.4);
  for (const s of signs) s.color.setScalar(0.6 + g * 1.6);
}

export function makeSignMaterial(spec: SignSpec) {
  const lines = spec.text.split("\n");
  const px = 160;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(spec.w * px);
  canvas.height = Math.round(spec.h * px);
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = spec.bg;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = spec.fg;
    ctx.globalAlpha = 0.55;
    ctx.lineWidth = Math.max(4, canvas.height * 0.03);
    ctx.strokeRect(ctx.lineWidth, ctx.lineWidth, canvas.width - ctx.lineWidth * 2, canvas.height - ctx.lineWidth * 2);
    ctx.globalAlpha = 1;
    ctx.fillStyle = spec.fg;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const lineH = (canvas.height * 0.78) / lines.length;
    let size = Math.min(lineH * 0.95, canvas.height * 0.62);
    ctx.font = `800 ${size}px system-ui, sans-serif`;
    const widest = Math.max(...lines.map((l) => ctx.measureText(l).width));
    const room = canvas.width * 0.88;
    if (widest > room) size *= room / widest;
    ctx.font = `800 ${size}px system-ui, sans-serif`;
    lines.forEach((line, i) => ctx.fillText(line, canvas.width / 2, canvas.height * 0.11 + lineH * (i + 0.5)));
  }
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 4;
  return new THREE.MeshBasicMaterial({ map, toneMapped: false });
}
