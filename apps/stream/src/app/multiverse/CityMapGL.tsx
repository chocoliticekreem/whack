"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import "./city-map.css";
import type { Map as MapLibreMap, Marker } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { buildCityStyle, LOOK } from "./city-map-style";
// map-3d: deck.gl overlay mount (see CityLayers.tsx)
import { CityLayers } from "./CityLayers";
import type { Hotspot, HubsVsUsual } from "./city-types";

export interface Pop {
  key: number;
  hotspot: string;
  text: string;
  good: boolean;
}

// map-3d camera: steep pitch and an angled bearing so the extruded city reads as 3D at the overview zoom.
const PITCH = 70;
const BEARING = -50;
// A wider lens than MapLibre's 37° default: stronger perspective, and the dusk horizon shows at this pitch.
const FOV = 48;
const FIT_LIFT = 0.18;
const START = { center: [-0.122, 51.516] as [number, number], zoom: 13.7 };
const MAX_BOUNDS: [[number, number], [number, number]] = [
  [-0.32, 51.42],
  [0.08, 51.61],
];
const FIT_MAX_ZOOM = 15.2;
const FIT_MIN_ZOOM = 13.6;

// Why: City.tsx frames hubs through a plain function (the old twin contract), so the live map is held here.
let liveMap: MapLibreMap | null = null;
let pending: Hotspot[] | null = null;
let liveLoaded = false;

const isPhone = () => window.matchMedia("(max-width: 720px)").matches;

export type MapPick = (p: { lat: number; lon: number; hub?: string }) => void;
let picker: MapPick | null = null;

/** LocalMarket.tsx listens here: a click on empty map, or on a hub, picks that spot. */
export function onMapPick(fn: MapPick | null) {
  picker = fn;
}


/** Room for the UI over the map, measured from the real panels: sidebar + traders on desktop, strip + sheet on a phone. */
function padding() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const rect = (sel: string) => document.querySelector(sel)?.getBoundingClientRect() ?? null;
  const side = rect(".tl-markets");
  const ai = rect(".tl-ai");
  const bar = rect(".tl-strip") ?? rect(".tl-bar");
  // Tags sit ~40 px above their spot, so the top needs more room than the sides.
  const p = isPhone()
    ? { top: (ai?.bottom ?? bar?.bottom ?? 60) + 56, bottom: h - (side?.top ?? h * 0.5) + 24, left: 36, right: 36 }
    : { top: (bar?.bottom ?? 48) + 70, bottom: 48, left: (side?.right ?? 420) + 56, right: w - (ai?.left ?? w - 288) + 40 };
  // A short or narrow window would leave no room at all, and maplibre refuses to move then.
  const sx = Math.min(1, (w * 0.8) / (p.left + p.right));
  const sy = Math.min(1, (h * 0.8) / (p.top + p.bottom));
  return { top: p.top * sy, bottom: p.bottom * sy, left: p.left * sx, right: p.right * sx };
}

function fit(map: MapLibreMap, hubs: Hotspot[], duration: number) {
  const lons = hubs.map((h) => h.lon);
  const lats = hubs.map((h) => h.lat);
  const pad = padding();
  const bounds: [[number, number], [number, number]] = [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
  const cam = map.cameraForBounds(bounds, { padding: pad, maxZoom: FIT_MAX_ZOOM, pitch: PITCH, bearing: BEARING });
  // OpenMapTiles ships buildings from z13 only, so the overview never zooms out past where the city turns flat.
  const zoom = Math.max(cam?.zoom ?? FIT_MIN_ZOOM, FIT_MIN_ZOOM);
  // An offset, not padding: easeTo keeps padding on the map, and cameraForBounds would then count it twice.
  // map-3d: lift the hubs into the far half of the steep view, where perspective shrinks them enough to fit.
  const offset: [number, number] = [(pad.left - pad.right) / 2, (pad.top - pad.bottom) / 2 - (isPhone() ? 0 : window.innerHeight * FIT_LIFT)];
  const center: [number, number] = [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2];
  map.easeTo({ center, zoom, offset, pitch: PITCH, bearing: BEARING, duration, essential: true });
}

/** The loaded map, for overlays such as scenario mode; null until the style has loaded. */
export function getCityMap(): MapLibreMap | null {
  return liveLoaded ? liveMap : null;
}

/** Frames the given hubs, clear of the cards; safe to call before the map loads (applied once it does). */
export function flyToHotspots(hubs: Hotspot[]) {
  if (!hubs.length) return;
  if (!liveMap || !liveLoaded) {
    pending = hubs;
    return;
  }
  fit(liveMap, hubs, 1600);
}

interface Pin {
  marker: Marker;
  el: HTMLDivElement;
  pop: HTMLSpanElement;
  vs: HTMLSpanElement;
}

function makePin(lib: typeof import("maplibre-gl"), map: MapLibreMap, hub: Hotspot): Pin {
  const el = document.createElement("div");
  el.className = "cm-hub";
  el.setAttribute("role", "button");
  el.setAttribute("aria-label", hub.name);
  el.innerHTML = '<span class="cm-hub-halo"></span><span class="cm-hub-ring"></span><span class="cm-hub-dot"></span><span class="cm-hub-tag"><span class="cm-hub-tag-dot"></span><span class="cm-hub-name"></span><span class="cm-hub-vs"></span></span>';
  (el.querySelector(".cm-hub-name") as HTMLSpanElement).textContent = hub.name;
  const pop = document.createElement("span");
  pop.className = "cm-hub-pop";
  el.appendChild(pop);
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    map.easeTo({ center: [hub.lon, hub.lat], zoom: Math.max(map.getZoom(), 15.4), duration: 900 });
    picker?.({ lat: hub.lat, lon: hub.lon, hub: hub.id });
  });
  const marker = new lib.Marker({ element: el, anchor: "center" }).setLngLat([hub.lon, hub.lat]).addTo(map);
  return { marker, el, pop, vs: el.querySelector(".cm-hub-vs") as HTMLSpanElement };
}

/** MapLibre city view: OpenFreeMap vector tiles, pale extruded buildings, hotspots as DOM markers. */
export function CityMapGL({
  hotspots,
  active,
  winner,
  pop,
  vsUsual,
}: {
  hotspots: Hotspot[];
  active: string[];
  winner: string | null;
  pop: Pop | null;
  /** From GET /signals; null hides the % and the laggard ring. */
  vsUsual: HubsVsUsual | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const lib = useRef<typeof import("maplibre-gl") | null>(null);
  const pins = useRef(new Map<string, Pin>());
  const [ready, setReady] = useState(false);
  // map-3d: the map instance for CityLayers.
  const [glMap, setGlMap] = useState<MapLibreMap | null>(null);

  useEffect(() => {
    const el = container.current;
    if (!el) return;
    let cancelled = false;
    let map: MapLibreMap | null = null;
    const resizer = new ResizeObserver(() => map?.resize());
    resizer.observe(el);
    const current = pins.current;

    // maplibre touches `window` on import, so it loads inside the effect.
    import("maplibre-gl").then((maplibregl) => {
      if (cancelled) return;
      lib.current = maplibregl;
      // Turbopack can't bundle MapLibre's worker; serve the copy from public/ (see predev script).
      maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");
      map = new maplibregl.Map({
        container: el,
        style: buildCityStyle(),
        ...START,
        pitch: PITCH,
        bearing: BEARING,
        maxPitch: 75,
        minZoom: 11.5,
        maxBounds: MAX_BOUNDS,
        canvasContextAttributes: { antialias: true },
        attributionControl: { compact: true },
      });
      map.setVerticalFieldOfView(FOV); // map-3d
      liveMap = map;
      if (process.env.NODE_ENV !== "production") Object.assign(window, { __cityMap: map });
      map.once("load", () => {
        // Compact attribution opens expanded by default, which covers the map on a phone.
        el.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
        // Moving the camera before the style loads crashes MapLibre's first render, so framing waits for load.
        liveLoaded = true;
        if (map && pending) fit(map, pending, 0);
        pending = null;
      });
      map.on("click", (e) => picker?.({ lat: e.lngLat.lat, lon: e.lngLat.lng }));
      setReady(true);
      setGlMap(map); // map-3d
    });

    return () => {
      cancelled = true;
      resizer.disconnect();
      current.forEach((p) => p.marker.remove());
      current.clear();
      map?.remove();
      if (liveMap === map) {
        liveMap = null;
        liveLoaded = false;
      }
      setReady(false);
      setGlMap(null); // map-3d
    };
  }, []);

  useEffect(() => {
    const map = liveMap;
    if (!ready || !map || !lib.current) return;
    const ids = new Set(hotspots.map((h) => h.id));
    for (const [id, p] of pins.current) {
      if (!ids.has(id)) {
        p.marker.remove();
        pins.current.delete(id);
      }
    }
    for (const hub of hotspots) {
      const p = pins.current.get(hub.id);
      if (p) p.marker.setLngLat([hub.lon, hub.lat]);
      else pins.current.set(hub.id, makePin(lib.current, map, hub));
    }
  }, [ready, hotspots]);

  const activeKey = active.join(",");
  useEffect(() => {
    const on = new Set(activeKey ? activeKey.split(",") : []);
    for (const [id, p] of pins.current) {
      const isActive = on.has(id);
      const isWin = winner === id;
      p.el.toggleAttribute("data-active", isActive);
      p.el.toggleAttribute("data-win", isWin);
      p.el.toggleAttribute("data-dim", on.size > 0 && !isActive && !isWin);
    }
  }, [ready, hotspots, activeKey, winner]);

  useEffect(() => {
    const rows = new Map((vsUsual?.hubs ?? []).map((h) => [h.id, h]));
    for (const [id, p] of pins.current) {
      const row = rows.get(id);
      const vs = row ? `${Math.round(row.vsUsual * 100)}%` : "";
      const name = p.el.querySelector(".cm-hub-name")?.textContent ?? "";
      p.vs.replaceChildren(vs);
      p.el.toggleAttribute("data-laggard", !!row && vsUsual?.laggard === id);
      p.el.setAttribute("aria-label", row ? `${name}, ${vs} of a usual ${vsUsual?.day}` : name);
    }
  }, [ready, hotspots, vsUsual]);

  useEffect(() => {
    if (!pop) return;
    const p = pins.current.get(pop.hotspot);
    if (!p) return;
    // Re-inserting the node restarts the rise-and-fade animation for back-to-back settles.
    const fresh = p.pop.cloneNode() as HTMLSpanElement;
    fresh.textContent = pop.text;
    fresh.toggleAttribute("data-good", pop.good);
    fresh.toggleAttribute("data-show", true);
    p.pop.replaceWith(fresh);
    p.pop = fresh;
  }, [pop]);

  return (
    <>
      <div ref={container} className="cm-map" style={{ background: LOOK.land }} />
      {/* map-3d: deck.gl columns, surge arcs and settle pulse */}
      <CityLayers map={glMap} hotspots={hotspots} vsUsual={vsUsual} winner={winner} />
    </>
  );
}
