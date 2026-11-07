"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import type { Map as MapLibreMap, MapMouseEvent, Marker } from "maplibre-gl";
import {
  addAroundLayers,
  brightenStyle,
  PITCH,
  STYLE_URL,
  ZOOM,
  type Point,
} from "./mapLayers";

export interface DisruptionEvents {
  hover: (id: string | null) => void;
  click: (id: string) => void;
}

type MapLib = typeof import("maplibre-gl");

function hitDisruptionId(map: MapLibreMap, e: MapMouseEvent) {
  const id = map.queryRenderedFeatures(e.point, { layers: ["around-disruptions-hit"] })[0]?.properties
    ?.id;
  return typeof id === "string" ? id : null;
}

function makeStreamerMarker(lib: MapLib, map: MapLibreMap, point: Point) {
  const el = document.createElement("div");
  el.className = "relative grid size-6 place-items-center";
  // Above the cam and station pins, which are added later and would otherwise cover the streamer at a station.
  el.style.zIndex = "2";
  el.innerHTML =
    '<span class="absolute inset-0 animate-ping rounded-full bg-ember/50"></span><span class="relative size-3.5 rounded-full bg-ember ring-[3px] ring-bg shadow-[0_0_18px_rgba(255,77,46,0.9)]"></span>';
  return new lib.Marker({ element: el }).setLngLat([point.lon, point.lat]).addTo(map);
}

/** Creates the MapLibre map once and wires the static layers; `events.current` is read live. */
export function useAroundMap(
  containerRef: RefObject<HTMLDivElement | null>,
  initial: Point,
  events: RefObject<DisruptionEvents>,
) {
  const mapRef = useRef<MapLibreMap | null>(null);
  const libRef = useRef<MapLib | null>(null);
  const streamerRef = useRef<Marker | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let cancelled = false;
    let map: MapLibreMap | null = null;
    // Why: the container changes size when the PiP swaps, the stage goes fullscreen or the window resizes.
    const resizer = new ResizeObserver(() => map?.resize());
    resizer.observe(container);

    import("maplibre-gl").then((lib) => {
      if (cancelled) return;
      libRef.current = lib;
      // Turbopack can't bundle MapLibre's worker; serve the copy from public/ (see predev script).
      lib.setWorkerUrl("/maplibre-gl-worker.mjs");
      map = new lib.Map({
        container,
        style: STYLE_URL,
        center: [initial.lon, initial.lat],
        zoom: ZOOM,
        pitch: PITCH,
        attributionControl: { compact: true },
      });
      mapRef.current = map;
      (window as unknown as { __aroundMap: unknown }).__aroundMap = map;

      // Why: "load" also waits for the first tiles; the layers and DOM markers only need the style, which lands seconds earlier.
      map.once("style.load", () => {
        if (!map) return;
        brightenStyle(map);
        addAroundLayers(map, initial);
        streamerRef.current = makeStreamerMarker(lib, map, initial);

        map.on("mousemove", "around-disruptions-hit", (e) => {
          if (!map) return;
          const id = hitDisruptionId(map, e);
          map.getCanvas().style.cursor = id ? "pointer" : "";
          events.current.hover(id);
        });
        map.on("mouseleave", "around-disruptions-hit", () => {
          if (!map) return;
          map.getCanvas().style.cursor = "";
          events.current.hover(null);
        });
        map.on("click", "around-disruptions-hit", (e) => {
          if (!map) return;
          const id = hitDisruptionId(map, e);
          if (id) events.current.click(id);
        });
        setReady(true);
      });
    });

    return () => {
      cancelled = true;
      resizer.disconnect();
      streamerRef.current = null;
      map?.remove();
      mapRef.current = null;
      setReady(false);
    };
    // Why: the map is created once; later point changes fly the camera instead of rebuilding.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { mapRef, libRef, streamerRef, ready };
}
