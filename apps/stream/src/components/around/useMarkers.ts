"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";
import type { Map as MapLibreMap, Marker } from "maplibre-gl";
import type { AroundBusStop, AroundCam, AroundLiveCam, AroundStation, CamReading } from "@/platform/around/types";
import { busStopHtml, camPinClass, camPinHtml, liveCamPinHtml, stationPinHtml } from "./markers";
import { liveSeconds } from "./cards";

type MapLib = typeof import("maplibre-gl");

interface Refs {
  mapRef: RefObject<MapLibreMap | null>;
  libRef: RefObject<MapLib | null>;
  ready: boolean;
}

interface Placed {
  marker: Marker;
  /** The pin itself; the marker's own element carries MapLibre's positioning classes and must keep them. */
  pin: HTMLButtonElement;
  lat: number;
  lon: number;
  className: string;
  html: string;
}

interface Item {
  id: string;
  lat: number;
  lon: number;
}

const PIN_CLASS = "cursor-pointer transition-transform hover:scale-110";

/**
 * One DOM marker per item, keyed by id. Markers are created once and patched in place: a poll that
 * returns the same items, a new YOLO count or a selection change only rewrites the pins that changed.
 */
function useKeyedMarkers<T extends Item>(
  { mapRef, libRef, ready }: Refs,
  kind: string,
  items: T[],
  render: (item: T) => { className: string; html: string },
  onSelect: (id: string) => void,
  visible: boolean,
) {
  const placedRef = useRef<Map<string, Placed>>(new Map());
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    const placed = placedRef.current;
    return () => {
      placed.forEach((p) => p.marker.remove());
      placed.clear();
    };
  }, [ready]);

  useEffect(() => {
    const map = mapRef.current;
    const lib = libRef.current;
    if (!ready || !map || !lib) return;
    const placed = placedRef.current;
    const seen = new Set<string>();
    for (const item of items) {
      seen.add(item.id);
      const { className, html } = render(item);
      let p = placed.get(item.id);
      if (!p) {
        const pin = document.createElement("button");
        pin.type = "button";
        pin.dataset.kind = kind;
        const id = item.id;
        pin.addEventListener("click", (e) => {
          e.stopPropagation();
          onSelectRef.current(id);
        });
        const el = document.createElement("div");
        el.append(pin);
        const marker = new lib.Marker({ element: el }).setLngLat([item.lon, item.lat]).addTo(map);
        p = { marker, pin, lat: item.lat, lon: item.lon, className: "", html: "" };
        placed.set(id, p);
      } else if (p.lat !== item.lat || p.lon !== item.lon) {
        p.marker.setLngLat([item.lon, item.lat]);
        p.lat = item.lat;
        p.lon = item.lon;
      }
      if (p.className !== className) p.pin.className = p.className = className;
      if (p.html !== html) p.pin.innerHTML = p.html = html;
    }
    for (const [id, p] of placed) {
      if (seen.has(id)) continue;
      p.marker.remove();
      placed.delete(id);
    }
  }, [mapRef, libRef, ready, kind, items, render]);

  useEffect(() => {
    for (const p of placedRef.current.values()) p.marker.getElement().style.display = visible ? "" : "none";
  }, [visible, items, ready]);
}

interface CamMarkerInput {
  cams: AroundCam[];
  readings: Map<string, CamReading>;
  activeCamId: string | null;
  selectedCamId: string | null;
  onSelect: (id: string) => void;
  visible: boolean;
}

export function useCamMarkers(refs: Refs, input: CamMarkerInput) {
  const { cams, readings, activeCamId, selectedCamId, onSelect, visible } = input;
  const render = useCallback(
    (cam: AroundCam) => {
      const active = cam.id === activeCamId;
      return { className: camPinClass(active, selectedCamId === cam.id), html: camPinHtml(cam, active, readings.get(cam.id)) };
    },
    [readings, activeCamId, selectedCamId],
  );
  useKeyedMarkers(refs, "cam", cams, render, onSelect, visible);
}

interface PinMarkerInput<T extends Item> {
  items: T[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  visible: boolean;
}

export function useStationMarkers(refs: Refs, { items, selectedId, onSelect, visible }: PinMarkerInput<AroundStation>) {
  const render = useCallback(
    (s: AroundStation) => ({ className: PIN_CLASS, html: stationPinHtml(s, selectedId === s.id) }),
    [selectedId],
  );
  useKeyedMarkers(refs, "station", items, render, onSelect, visible);
}

export function useLiveCamMarkers(refs: Refs, { items, selectedId, onSelect, visible }: PinMarkerInput<AroundLiveCam>) {
  const render = useCallback(
    (c: AroundLiveCam) => ({ className: PIN_CLASS, html: liveCamPinHtml(c, selectedId === c.id) }),
    [selectedId],
  );
  useKeyedMarkers(refs, "livecam", items, render, onSelect, visible);
}

interface StopMarkerInput extends PinMarkerInput<AroundBusStop> {
  now: number;
}

/** Bus-stop flags; the countdown counts down from when TfL reported it (`stop.at`). */
export function useStopMarkers(refs: Refs, { items, selectedId, onSelect, visible, now }: StopMarkerInput) {
  const render = useCallback(
    (stop: AroundBusStop) => {
      const next = stop.arrivals
        .map((a) => liveSeconds(a.seconds, stop.at, now))
        .filter((s) => s > -60)
        .sort((a, b) => a - b)[0];
      return { className: PIN_CLASS, html: busStopHtml(stop, next ?? null, selectedId === stop.id) };
    },
    [selectedId, now],
  );
  useKeyedMarkers(refs, "bus", items, render, onSelect, visible);
}
