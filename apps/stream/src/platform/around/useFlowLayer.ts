"use client";

import { useEffect, useMemo, useState, type RefObject } from "react";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import { fetchJson, startPoller } from "@/lib/client/poll";
import { camHeading, flowFrame, viewCone } from "./flows";
import type { AroundCam, CamClipTracks, CamReading } from "./types";

const POLL_MS = 15_000;
// ~10fps is smooth enough for walking pace and keeps the map responsive.
const FRAME_MS = 100;
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const PERSON = "#ffd166";
const VEHICLE = "#5ec8ff";

/** Top-down car silhouette, pointing north, drawn once into the map's sprite. */
function carImage(): ImageData {
  const w = 22;
  const h = 40;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const g = c.getContext("2d")!;
  g.fillStyle = VEHICLE;
  g.strokeStyle = "#07070a";
  g.lineWidth = 2;
  g.beginPath();
  g.roundRect(3, 2, w - 6, h - 4, 6);
  g.fill();
  g.stroke();
  g.fillStyle = "rgba(7,7,10,0.55)";
  g.fillRect(6, 9, w - 12, 7);
  g.fillRect(6, h - 13, w - 12, 5);
  return g.getImageData(0, 0, w, h);
}

function ensureLayers(map: MapLibreMap) {
  if (map.getSource("flow-people")) return;
  if (!map.hasImage("flow-car")) map.addImage("flow-car", carImage(), { pixelRatio: 2 });
  map.addSource("flow-cones", { type: "geojson", data: EMPTY });
  map.addSource("flow-people", { type: "geojson", data: EMPTY });
  map.addSource("flow-vehicles", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "flow-cones",
    type: "fill",
    source: "flow-cones",
    paint: { "fill-color": "#ffffff", "fill-opacity": 0.03 },
  });
  map.addLayer({
    id: "flow-vehicles",
    type: "symbol",
    source: "flow-vehicles",
    layout: {
      "icon-image": "flow-car",
      "icon-rotate": ["get", "bearing"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-size": ["interpolate", ["linear"], ["zoom"], 15, 0.5, 18, 1],
    },
  });
  map.addLayer({
    id: "flow-people",
    type: "circle",
    source: "flow-people",
    paint: {
      "circle-color": PERSON,
      "circle-opacity": ["case", [">", ["get", "count"], 1], 0.85, 1],
      "circle-radius": ["interpolate", ["linear"], ["get", "count"], 1, 3.5, 3, 7, 8, 11, 20, 16],
      "circle-stroke-color": "#07070a",
      "circle-stroke-width": 1.2,
    },
  });
  map.addLayer({
    id: "flow-people-count",
    type: "symbol",
    source: "flow-people",
    filter: [">", ["get", "count"], 2],
    layout: {
      "text-field": ["to-string", ["get", "count"]],
      "text-font": ["Noto Sans Bold"],
      "text-size": 11,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: { "text-color": "#07070a" },
  });
}

/**
 * Animates real YOLO tracks from nearby JamCam clips: people as dots (groups merged, with a count)
 * and vehicles as car icons pointing the way they move. Returns totals summed from the latest YOLO
 * count per nearby camera (live or stale), the same readings the camera card shows, so the two agree
 * even when no clip is animating.
 */
export function useFlowLayer(
  mapRef: RefObject<MapLibreMap | null>,
  ready: boolean,
  cams: AroundCam[],
  centre: { lat: number; lon: number },
) {
  const [clips, setClips] = useState<CamClipTracks[]>([]);
  const [readings, setReadings] = useState<ReadonlyMap<string, CamReading>>(() => new Map());
  const ids = cams.map((c) => c.id).join(",");

  useEffect(() => {
    if (!ids) return;
    // Same merge as the card's useCamCounts: each cam keeps its last reading until a newer one lands.
    const poller = startPoller(async (signal) => {
      const r = await fetchJson<{ readings?: CamReading[] }>(`/api/around/counts?ids=${encodeURIComponent(ids)}`, signal);
      if (!r.ok) return r;
      const got = r.data.readings ?? [];
      if (got.length) {
        setReadings((prev) => {
          const next = new Map(prev);
          for (const reading of got) next.set(reading.camId, reading);
          return next;
        });
      }
      return true;
    }, POLL_MS);
    return () => poller.stop();
  }, [ids]);

  const totals = useMemo(() => {
    const sum = { people: 0, vehicles: 0 };
    for (const id of ids.split(",")) {
      const r = readings.get(id);
      if (!r) continue;
      sum.people += r.people;
      sum.vehicles += r.vehicles;
    }
    return sum;
  }, [ids, readings]);

  useEffect(() => {
    if (!ids) return;
    // A failed or empty poll keeps the clips already animating; the server keeps each cam's last good clip too.
    const poller = startPoller(async (signal) => {
      const r = await fetchJson<{ clips?: CamClipTracks[] }>(`/api/around/tracks?ids=${encodeURIComponent(ids)}`, signal, 45_000);
      if (!r.ok) return r;
      const got = (r.data.clips ?? []).filter((c) => c.tracks?.length);
      if (got.length) {
        setClips((prev) => {
          const byId = new Map(prev.map((c) => [c.camId, c]));
          let changed = false;
          for (const c of got) {
            if (byId.get(c.camId)?.at === c.at) continue;
            byId.set(c.camId, c);
            changed = true;
          }
          return changed ? [...byId.values()] : prev;
        });
      }
      return true;
    }, POLL_MS);
    return () => poller.stop();
  }, [ids]);

  const { lat, lon } = centre;
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const byId = new Map(cams.map((c) => [c.id, c]));
    const placed = clips.flatMap((clip) => {
      const cam = byId.get(clip.camId);
      return cam ? [{ cam, heading: camHeading(cam, { lat, lon }), clip }] : [];
    });
    let raf = 0;
    let last = 0;
    let conesDrawn = false;
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      if (now - last < FRAME_MS || !map.isStyleLoaded()) return;
      last = now;
      ensureLayers(map);
      if (!conesDrawn) {
        conesDrawn = true;
        (map.getSource("flow-cones") as GeoJSONSource).setData({
          type: "FeatureCollection",
          features: placed.map((p) => viewCone(p.cam, p.heading)),
        });
      }
      const frame = flowFrame(placed, Date.now());
      (map.getSource("flow-people") as GeoJSONSource).setData(frame.people);
      (map.getSource("flow-vehicles") as GeoJSONSource).setData(frame.vehicles);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [mapRef, ready, clips, cams, lat, lon]);

  return { totals, clips: clips.length };
}
