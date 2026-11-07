"use client";

import { MapView, type Layer } from "@deck.gl/core";
import { ArcLayer, ColumnLayer, ScatterplotLayer } from "@deck.gl/layers";
import type { MapboxOverlay } from "@deck.gl/mapbox";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Hotspot, HubsVsUsual, MarketsResponse } from "./city-types";

type RGBA = [number, number, number, number];

const MAGENTA: RGBA = [255, 61, 168, 255];
const CYAN: RGBA = [61, 245, 255, 255];
const VIOLET: RGBA = [139, 92, 255, 255];
const AMBER: RGBA = [255, 181, 71, 255];

const ORBIT_DEG = 5;
const ORBIT_PERIOD_MS = 48_000;
const ORBIT_IDLE_MS = 20_000;
const PULSE_MS = 3200;
const PARTICLE_PERIOD_MS = 3200;
const ARC_HEIGHT = 0.35;
const MARKETS_POLL_MS = 10_000;

// Additive blending without depth writes: glows brighten what is behind them instead of punching holes in it.
const GLOW = {
  depthWriteEnabled: false,
  blend: true,
  blendColorOperation: "add",
  blendColorSrcFactor: "src-alpha",
  blendColorDstFactor: "one",
  blendAlphaOperation: "add",
  blendAlphaSrcFactor: "one",
  blendAlphaDstFactor: "one",
} as const;

const alpha = (c: RGBA, a: number): RGBA => [c[0], c[1], c[2], Math.round(a)];

interface Column {
  id: string;
  position: [number, number];
  height: number;
  color: RGBA;
}

interface Flow {
  from: Hotspot;
  to: Hotspot;
  /** Share of alive hub_surge futures that end on `to` (0..1). */
  weight: number;
  distM: number;
}

/** 1.0 = usual; the live spread is a few percent either way, so the deviation is stretched to be readable. */
function columnHeight(vsUsual: number | undefined): number {
  if (vsUsual == null) return 180;
  return Math.min(1100, Math.max(140, 560 * vsUsual ** 5));
}

function columnColor(vsUsual: number | undefined, isWinner: boolean): RGBA {
  if (isWinner) return AMBER;
  if (vsUsual == null) return VIOLET;
  return vsUsual >= 1 ? MAGENTA : CYAN;
}

function metres(a: Hotspot, b: Hotspot): number {
  const dx = (b.lon - a.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
  const dy = (b.lat - a.lat) * 110_574;
  return Math.hypot(dx, dy);
}

/** Same paraboloid as ArcLayer's shader for flat endpoints, so the particles ride the drawn arc. */
function onArc(f: Flow, r: number): [number, number, number] {
  return [f.from.lon + (f.to.lon - f.from.lon) * r, f.from.lat + (f.to.lat - f.from.lat) * r, Math.sqrt(r * (1 - r)) * f.distM * ARC_HEIGHT];
}

/**
 * Flow arcs are a stand-in until the sim exposes station-to-station flows: every hub sends an arc to the two hubs
 * the latest hub_surge market's futures favour most, weighted by their share of alive futures (byOption counts).
 */
function surgeFlows(hubs: Hotspot[], byOption: Record<string, number> | null): Flow[] {
  if (!byOption || hubs.length < 2) return [];
  const total = Object.values(byOption).reduce((s, n) => s + n, 0);
  if (total <= 0) return [];
  const byId = new Map(hubs.map((h) => [h.id, h]));
  const ranked = Object.entries(byOption)
    .filter(([id, n]) => n > 0 && byId.has(id))
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2);
  const flows: Flow[] = [];
  for (const [toId, n] of ranked) {
    const to = byId.get(toId)!;
    for (const from of hubs) {
      if (from.id === toId) continue;
      flows.push({ from, to, weight: n / total, distM: metres(from, to) });
    }
  }
  return flows;
}

/** Futures counts of the newest open hub_surge market, re-read on its own slow poll so CityMapGL's props stay unchanged. */
function useSurgeFutures(enabled: boolean): Record<string, number> | null {
  const [byOption, setByOption] = useState<Record<string, number> | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const mock = new URLSearchParams(window.location.search).get("mock") === "1";
    const read = async () => {
      try {
        const res = await fetch(`/api/city/markets${mock ? "?mock=1" : ""}`, { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as MarketsResponse;
        const surge = data.markets
          .filter((m) => m.id.startsWith("hub_surge") && m.futures && (m.status ?? "open") === "open")
          .sort((a, b) => b.settlesAt - a.settlesAt)[0];
        if (live) setByOption(surge?.futures?.byOption ?? null);
      } catch {
        // Arcs are decoration; a failed read keeps the last ones.
      }
    };
    void read();
    const id = window.setInterval(() => void read(), MARKETS_POLL_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [enabled]);
  return byOption;
}

const isPhone = () => window.matchMedia("(max-width: 720px)").matches;
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * deck.gl layers interleaved into the MapLibre map (depth-sorted with the building extrusions): a glowing column per hub
 * sized by live busyness vs usual, surge arcs with travelling particles, and a pulse ring at the winning hub on settle.
 * Also drives the slow idle orbit. On a phone only the columns (and the brief settle pulse) render, with no orbit.
 */
export function CityLayers({
  map,
  hotspots,
  vsUsual,
  winner,
}: {
  map: MapLibreMap | null;
  hotspots: Hotspot[];
  vsUsual: HubsVsUsual | null;
  winner: string | null;
}) {
  const overlay = useRef<MapboxOverlay | null>(null);
  const [overlayReady, setOverlayReady] = useState(false);
  const [lite, setLite] = useState(true);
  const [still, setStill] = useState(true);
  const pulse = useRef<{ hub: Hotspot; start: number } | null>(null);

  useEffect(() => {
    const phone = window.matchMedia("(max-width: 720px)");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => {
      setLite(isPhone());
      setStill(reducedMotion());
    };
    sync();
    phone.addEventListener("change", sync);
    motion.addEventListener("change", sync);
    return () => {
      phone.removeEventListener("change", sync);
      motion.removeEventListener("change", sync);
    };
  }, []);

  useEffect(() => {
    if (!map) return;
    let cancelled = false;
    let added: MapboxOverlay | null = null;
    const attach = () => {
      import("@deck.gl/mapbox").then(({ MapboxOverlay }) => {
        if (cancelled) return;
        // debt: MapLibre 6 moved `map.transform` onto an internal camera, and @deck.gl/mapbox 9.4 still reads
        // map.transform.height / _nearZ / elevation. ceiling: relies on the private `_camera` field.
        // upgrade: drop this once @deck.gl/mapbox supports maplibre-gl 6.
        const internal = map as unknown as { transform?: unknown; _camera?: { transform: unknown } };
        if (internal.transform === undefined && internal._camera)
          Object.defineProperty(map, "transform", { configurable: true, get: () => internal._camera!.transform });
        // "mapbox" is the view id the overlay syncs to the map; fovy must match the map's lens or the columns drift.
        const view = new MapView({ id: "mapbox", fovy: map.getVerticalFieldOfView() });
        // MapboxOverlayProps pins DeckProps' views generic to null, though the overlay reads a "mapbox" view at runtime.
        added = new MapboxOverlay({ interleaved: true, views: [view] as unknown as null, layers: [] });
        map.addControl(added);
        overlay.current = added;
        setOverlayReady(true);
      });
    };
    if (map.isStyleLoaded()) attach();
    else map.once("load", attach);
    return () => {
      cancelled = true;
      map.off("load", attach);
      if (added) map.removeControl(added);
      overlay.current = null;
      setOverlayReady(false);
    };
  }, [map]);

  const byOption = useSurgeFutures(!lite);
  const flows = useMemo(() => (lite ? [] : surgeFlows(hotspots, byOption)), [lite, hotspots, byOption]);

  const columns = useMemo<Column[]>(() => {
    const rows = new Map((vsUsual?.hubs ?? []).map((h) => [h.id, h.vsUsual]));
    return hotspots.map((h) => {
      const v = rows.get(h.id);
      const isWinner = winner === h.id;
      return { id: h.id, position: [h.lon, h.lat], height: columnHeight(v) * (isWinner ? 1.35 : 1), color: columnColor(v, isWinner) };
    });
  }, [hotspots, vsUsual, winner]);

  useEffect(() => {
    const hub = winner ? hotspots.find((h) => h.id === winner) : undefined;
    if (hub) pulse.current = { hub, start: performance.now() };
  }, [winner, hotspots]);

  useEffect(() => {
    const o = overlay.current;
    if (!overlayReady || !o || !map) return;
    const transitions = { getElevation: { duration: 1200 }, getFillColor: { duration: 900 } };
    const material = { ambient: 0.65, diffuse: 0.55, shininess: 48, specularColor: [255, 236, 210] as [number, number, number] };
    const staticLayers: Layer[] = [
      new ScatterplotLayer<Column>({
        id: "hub-floor-glow",
        data: columns,
        getPosition: (d) => d.position,
        getRadius: 170,
        getFillColor: (d) => alpha(d.color, 70),
        parameters: GLOW,
        transitions: { getFillColor: { duration: 900 } },
      }),
      new ColumnLayer<Column>({
        id: "hub-column-halo",
        data: columns,
        diskResolution: 24,
        radius: 95,
        extruded: true,
        getPosition: (d) => d.position,
        getElevation: (d) => d.height,
        getFillColor: (d) => alpha(d.color, 46),
        material: false,
        parameters: GLOW,
        transitions,
      }),
      new ColumnLayer<Column>({
        id: "hub-column",
        data: columns,
        diskResolution: 24,
        radius: 46,
        extruded: true,
        getPosition: (d) => d.position,
        getElevation: (d) => d.height,
        getFillColor: (d) => d.color,
        material,
        transitions,
      }),
    ];
    if (flows.length)
      staticLayers.push(
        new ArcLayer<Flow>({
          id: "surge-arcs",
          data: flows,
          getSourcePosition: (f) => [f.from.lon, f.from.lat],
          getTargetPosition: (f) => [f.to.lon, f.to.lat],
          getSourceColor: (f) => alpha(CYAN, 30 + 100 * f.weight),
          getTargetColor: (f) => alpha(MAGENTA, 60 + 150 * f.weight),
          getWidth: (f) => 1 + 4 * f.weight,
          getHeight: ARC_HEIGHT,
          widthUnits: "pixels",
          parameters: GLOW,
        }),
      );

    let raf = 0;
    const frame = (now: number) => {
      const layers = [...staticLayers];
      if (flows.length && !still) {
        const particles: { p: [number, number, number]; c: RGBA; r: number }[] = [];
        flows.forEach((f, i) => {
          for (let k = 0; k < 3; k++) {
            const r = (((now / PARTICLE_PERIOD_MS) + k / 3 + i * 0.137) % 1 + 1) % 1;
            particles.push({ p: onArc(f, r), c: alpha(r < 0.5 ? CYAN : MAGENTA, 140 + 115 * f.weight), r: 1.6 + 2.6 * f.weight });
          }
        });
        layers.push(
          new ScatterplotLayer<(typeof particles)[number]>({
            id: "surge-particles",
            data: particles,
            getPosition: (d) => d.p,
            getFillColor: (d) => d.c,
            getRadius: (d) => d.r,
            radiusUnits: "pixels",
            billboard: true,
            parameters: GLOW,
          }),
        );
      }
      const pl = pulse.current;
      if (pl) {
        const age = now - pl.start;
        if (age > PULSE_MS) pulse.current = null;
        else {
          const rings = [0, 0.22, 0.44].map((lag) => Math.max(0, age / PULSE_MS - lag)).filter((t) => t > 0 && t < 1);
          layers.push(
            new ScatterplotLayer<number>({
              id: "settle-pulse",
              data: rings,
              getPosition: () => [pl.hub.lon, pl.hub.lat],
              getRadius: (t) => 60 + 640 * Math.sqrt(t),
              getLineColor: (t) => alpha(AMBER, 255 * (1 - t)),
              getLineWidth: (t) => 9 - 6 * t,
              lineWidthUnits: "pixels",
              stroked: true,
              filled: false,
              // Drawn over the buildings, not behind them: the ring has to read across the whole block.
              parameters: { ...GLOW, depthCompare: "always" },
              updateTriggers: { getRadius: age, getLineColor: age, getLineWidth: age },
            }),
            new ColumnLayer<number>({
              id: "settle-beam",
              data: [Math.min(1, age / PULSE_MS)],
              diskResolution: 24,
              radius: 70,
              extruded: true,
              getPosition: () => [pl.hub.lon, pl.hub.lat],
              getElevation: (t) => 2400 * Math.sqrt(t),
              getFillColor: (t) => alpha(AMBER, 150 * (1 - t)),
              material: false,
              parameters: GLOW,
              updateTriggers: { getElevation: age, getFillColor: age },
            }),
          );
        }
      }
      o.setProps({ layers });
      if ((flows.length && !still) || pulse.current) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
    // `winner` restarts the loop so a settle pulse on an otherwise idle (phone) map gets frames.
  }, [overlayReady, map, columns, flows, still, winner]);

  // A slow sway of a few degrees around wherever the camera was framed; pauses while anything moves the camera
  // and for a while after the user touches the map, then resumes from the new bearing.
  useEffect(() => {
    if (!map || lite || still) return;
    const events = ["mousedown", "touchstart", "wheel", "dragstart"] as const;
    let raf = 0;
    let base = 0;
    let t0 = 0;
    let rebase = true;
    let lastTouch = -Infinity;
    const touched = () => {
      lastTouch = performance.now();
    };
    for (const ev of events) map.on(ev, touched);
    const frame = (now: number) => {
      if (map.isMoving() || now - lastTouch < ORBIT_IDLE_MS) rebase = true;
      else {
        if (rebase) {
          base = map.getBearing();
          t0 = now;
          rebase = false;
        }
        map.setBearing(base + ORBIT_DEG * Math.sin(((now - t0) / ORBIT_PERIOD_MS) * Math.PI * 2));
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      for (const ev of events) map.off(ev, touched);
    };
  }, [map, lite, still]);

  return null;
}
