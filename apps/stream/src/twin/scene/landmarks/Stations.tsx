"use client";

import { Billboard } from "@react-three/drei";
import { useMemo } from "react";
import * as THREE from "three";
import { insideGeometryBounds, toLocal } from "../../config";
import { railHeight, STATION_FALLBACKS, type StationNetwork } from "../../landmarks";
import { Label } from "../../live/Label";
import { useGeometry } from "../useGeometry";
import { Kit, KitMeshes } from "./kit";

interface Station {
  name: string;
  x: number;
  z: number;
  network: StationNetwork;
  yaw: number;
}

const NETWORK_LABEL: Record<StationNetwork, string> = {
  tube: "Underground",
  dlr: "DLR",
  rail: "National Rail",
  overground: "Overground",
};

const RING: Record<StationNetwork, string> = { tube: "#e1251b", dlr: "#00afad", rail: "#e1251b", overground: "#ee7c0e" };
const BAR: Record<StationNetwork, string> = { tube: "#0019a8", dlr: "#00686a", rail: "#ffffff", overground: "#0019a8" };

const textures = new Map<string, THREE.CanvasTexture>();

function signTexture(network: StationNetwork, name: string): THREE.CanvasTexture {
  const key = `${network}:${name}`;
  const hit = textures.get(key);
  if (hit) return hit;
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 320;
  const ctx = canvas.getContext("2d")!;
  ctx.clearRect(0, 0, 512, 320);
  if (network === "rail") {
    ctx.fillStyle = "#e1251b";
    ctx.beginPath();
    ctx.roundRect(96, 8, 320, 220, 28);
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 22;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const [y, dir] of [[88, 1], [148, -1]] as const) {
      const x0 = dir === 1 ? 150 : 362;
      const x1 = dir === 1 ? 362 : 150;
      ctx.beginPath();
      ctx.moveTo(x0, y);
      ctx.lineTo(x1, y);
      ctx.moveTo(x1 - dir * 32, y - 26);
      ctx.lineTo(x1, y);
      ctx.lineTo(x1 - dir * 32, y + 26);
      ctx.stroke();
    }
  } else {
    ctx.strokeStyle = RING[network];
    ctx.lineWidth = 34;
    ctx.beginPath();
    ctx.arc(256, 118, 100, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = BAR[network];
    ctx.fillRect(40, 84, 432, 68);
  }
  ctx.fillStyle = network === "rail" ? "#fff" : "#fff";
  ctx.font = "bold 44px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  if (network !== "rail") ctx.fillText(name.toUpperCase().slice(0, 18), 256, 119, 410);
  else {
    ctx.fillStyle = "#0b1020";
    ctx.fillRect(20, 242, 472, 66);
    ctx.fillStyle = "#fff";
    ctx.fillText(name.toUpperCase().slice(0, 18), 256, 275, 450);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  textures.set(key, tex);
  return tex;
}

function networkOf(kind: "rail" | "subway" | "light_rail", name: string): StationNetwork {
  if (kind === "subway") return "tube";
  if (kind === "light_rail") return "dlr";
  return /shadwell|wapping|whitechapel|shoreditch|rotherhithe|hoxton|canada water/i.test(name) ? "overground" : "rail";
}

function buildStation(kit: Kit, s: Station, base: number) {
  if (s.network === "tube") {
    kit.box("stone", 10, 5, 7, 0, 0, 0);
    kit.winBox("glass", 8, 3, 0.4, 0, 0.8, 3.6);
    kit.box("roof", 11, 0.6, 8, 0, 5, 0);
    kit.box("lamp", 9, 0.3, 0.3, 0, 4.6, 3.8);
    return;
  }
  const len = s.network === "dlr" ? 46 : 78;
  const y = base + 0.9;
  kit.box("concrete", len, 0.9, 7.5, 0, base, 0, 0);
  kit.box("roof", len + 2, 0.7, 14, 0, y + 5.4, 0);
  kit.box("platformLight", len - 2, 0.25, 9, 0, y + 5.1, 0);
  for (let d = -len / 2 + 4; d <= len / 2 - 3; d += 9) {
    for (const sz of [-1, 1]) kit.box("steel", 0.6, 5.4, 0.6, d, y, sz * 5.2);
  }
  if (s.network !== "dlr") {
    kit.box("brick", 20, 14, 16, -len / 2 - 8, 0, 0);
    kit.winBox("glass", 20.4, 14, 16.4, -len / 2 - 8, 0, 0);
  }
}

function StationMarker({ s }: { s: Station }) {
  const base = railHeight(s.x, s.z);
  const kit = useMemo(() => {
    const k = new Kit();
    buildStation(k, s, base);
    return k;
  }, [s, base]);
  const tex = useMemo(() => signTexture(s.network, s.name), [s.network, s.name]);
  const top = base + (s.network === "tube" ? 17 : 20);
  const size = s.network === "rail" ? 11 : 14;
  return (
    <group position={[s.x, 0, s.z]}>
      <group rotation={[0, s.yaw, 0]}>
        <KitMeshes kit={kit} />
      </group>
      <mesh position={[0, (base + top) / 2 - 2, 0]}>
        <boxGeometry args={[0.5, top - base + 2, 0.5]} />
        <meshBasicMaterial color="#9aa6c0" />
      </mesh>
      <Billboard position={[0, top, 0]}>
        <mesh>
          <planeGeometry args={[size, size * 0.625]} />
          <meshBasicMaterial map={tex} transparent alphaTest={0.2} toneMapped={false} side={THREE.DoubleSide} color={new THREE.Color(1.5, 1.5, 1.5)} />
        </mesh>
      </Billboard>
      <Label position={[0, top + 5, 0]} priority={40}>
        <div
          style={{
            fontFamily: "var(--font-figtree), system-ui, sans-serif",
            background: "rgba(12,12,16,0.78)",
            border: `1px solid ${RING[s.network]}`,
            borderRadius: 8,
            padding: "3px 8px",
            color: "#fff",
            whiteSpace: "nowrap",
            fontSize: 11,
            lineHeight: 1.3,
            backdropFilter: "blur(8px)",
          }}
        >
          <b>{s.name}</b>
          <span style={{ color: "#9aa6c0" }}> · {NETWORK_LABEL[s.network]}</span>
        </div>
      </Label>
    </group>
  );
}

export function Stations() {
  const { stations, roads } = useGeometry();
  const list = useMemo<Station[]>(() => {
    const rails = roads.filter((r) => r.kind === "rail").map((r) => r.line);
    const raw: Omit<Station, "yaw">[] = (stations ?? []).map((s) => ({
      name: s.name,
      x: s.pos[0],
      z: s.pos[1],
      network: networkOf(s.kind, s.name),
    }));
    for (const f of STATION_FALLBACKS) {
      const [x, z] = toLocal(f.lat, f.lon);
      if (!insideGeometryBounds(x, z)) continue;
      const dup = raw.some((r) => r.network === f.network && Math.hypot(r.x - x, r.z - z) < 150);
      if (!dup) raw.push({ name: f.name, x, z, network: f.network });
    }
    return raw.map((s) => {
      let best = Infinity;
      let yaw = 0;
      for (const line of rails) {
        for (let i = 0; i < line.length - 1; i++) {
          const [ax, az] = line[i];
          const [bx, bz] = line[i + 1];
          const d = Math.hypot((ax + bx) / 2 - s.x, (az + bz) / 2 - s.z);
          if (d < best && d < 160) {
            best = d;
            yaw = -Math.atan2(bz - az, bx - ax);
          }
        }
      }
      return { ...s, yaw };
    });
  }, [stations, roads]);
  return (
    <>
      {list.map((s) => (
        <StationMarker key={`${s.network}:${s.name}`} s={s} />
      ))}
    </>
  );
}
