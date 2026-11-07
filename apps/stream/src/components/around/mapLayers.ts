import type { Map as MapLibreMap } from "maplibre-gl";
import type { AroundDisruption } from "@/platform/around/types";

export const STYLE_URL = "https://tiles.openfreemap.org/styles/dark";
export const WALK_RADIUS_M = 500;
// Shows ~1 km across on a laptop, so the nearest cams and stations are on screen before the fit to them runs.
export const ZOOM = 15.5;
/** Fitting to nearby markers never zooms past this, so a lone cam next to the streamer keeps its streets in view. */
export const FIT_MAX_ZOOM = 16.5;
/** Below this street names vanish; a phone that cannot fit every nearby marker shows the closest ones instead. */
export const FIT_MIN_ZOOM = 14.5;
export const PITCH = 0;

export interface Point {
  lat: number;
  lon: number;
}




export function circlePolygon(p: Point, radiusM: number): GeoJSON.Feature<GeoJSON.Polygon> {
  const steps = 72;
  const dLat = radiusM / 111_320;
  const dLon = radiusM / (111_320 * Math.cos((p.lat * Math.PI) / 180));
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    ring.push([p.lon + Math.cos(a) * dLon, p.lat + Math.sin(a) * dLat]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

export function disruptionCollection(items: AroundDisruption[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: items
      .filter((d) => d.line.length >= 2)
      .map((d) => ({
        type: "Feature",
        id: d.id,
        properties: { id: d.id, severity: d.severity },
        geometry: { type: "LineString", coordinates: d.line },
      })),
  };
}

export function crimeCollection(points: [number, number][]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: points.map((p) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } })),
  };
}

export function lineFeature(polyline: [number, number][]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features:
      polyline.length >= 2
        ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: polyline.map(([lat, lon]) => [lon, lat]) } }]
        : [],
  };
}

export function setData(map: MapLibreMap, sourceId: string, data: GeoJSON.GeoJSON) {
  const src = map.getSource(sourceId);
  if (src && "setData" in src) (src as { setData: (d: GeoJSON.GeoJSON) => void }).setData(data);
}

export function setLayersVisible(map: MapLibreMap, layerIds: string[], visible: boolean) {
  for (const id of layerIds) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
  }
}

/** The OpenFreeMap dark style is near-black; lift roads, buildings and labels so streets read. */
export function brightenStyle(map: MapLibreMap) {
  const setPaint = map.setPaintProperty.bind(map) as (id: string, prop: string, value: unknown) => void;
  const set = (id: string, prop: string, value: unknown) => {
    if (map.getLayer(id)) setPaint(id, prop, value);
  };
  set("background", "background-color", "#0e0e13");
  set("water", "fill-color", "#161c28");
  set("building", "fill-color", "#17171e");
  set("building", "fill-outline-color", "#262630");
  set("landuse_park", "fill-color", "#14201a");
  set("highway_path", "line-color", "#2c2c36");
  set("highway_minor", "line-color", "#33333e");
  set("highway_major_casing", "line-color", "#1a1a21");
  set("highway_major_inner", "line-color", "#45454f");
  set("highway_major_subtle", "line-color", "#45454f");
  set("highway_motorway_inner", "line-color", "#55555f");
  set("railway", "line-color", "#3a3a46");
  set("railway_transit", "line-color", "#3a3a46");
  for (const layer of map.getStyle().layers) {
    if (layer.type !== "symbol") continue;
    set(layer.id, "text-color", "#b7b6c2");
    set(layer.id, "text-halo-color", "#0e0e13");
    set(layer.id, "text-halo-width", 1.2);
  }
}

export function addAroundLayers(map: MapLibreMap, point: Point) {
  map.addSource("around-walk", { type: "geojson", data: circlePolygon(point, WALK_RADIUS_M) });
  map.addLayer({
    id: "around-walk-fill",
    type: "fill",
    source: "around-walk",
    paint: { "fill-color": "#ff4d2e", "fill-opacity": 0.035 },
  });
  map.addLayer({
    id: "around-walk-line",
    type: "line",
    source: "around-walk",
    paint: {
      "line-color": "#ff4d2e",
      "line-opacity": 0.35,
      "line-width": 1,
      "line-dasharray": [2, 2],
    },
  });


  map.addSource("around-crime", { type: "geojson", data: crimeCollection([]) });
  map.addLayer({
    id: "around-crime-heat",
    type: "heatmap",
    source: "around-crime",
    paint: {
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 14, 14, 18, 40],
      "heatmap-intensity": 0.6,
      "heatmap-opacity": 0.45,
      "heatmap-color": [
        "interpolate",
        ["linear"],
        ["heatmap-density"],
        0,
        "rgba(255,45,85,0)",
        0.3,
        "rgba(255,45,85,0.25)",
        1,
        "rgba(255,120,60,0.8)",
      ],
    },
  });

  map.addSource("around-disruptions", { type: "geojson", data: disruptionCollection([]) });
  map.addLayer({
    id: "around-disruptions-hit",
    type: "line",
    source: "around-disruptions",
    paint: { "line-color": "#000", "line-opacity": 0, "line-width": 16 },
  });
  map.addLayer({
    id: "around-disruptions",
    type: "line",
    source: "around-disruptions",
    layout: { "line-cap": "round" },
    paint: {
      "line-color": ["match", ["get", "severity"], ["Serious", "Severe"], "#ff2d55", "#ffb020"],
      "line-width": 2,
      "line-dasharray": [1.4, 1.4],
      "line-opacity": 0.6,
    },
  });

  map.addSource("around-route-fast", { type: "geojson", data: lineFeature([]) });
  map.addLayer({
    id: "around-route-fast",
    type: "line",
    source: "around-route-fast",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#9a99a6", "line-width": 3, "line-dasharray": [1.5, 1.5], "line-opacity": 0.8 },
  });
  map.addSource("around-route-calm", { type: "geojson", data: lineFeature([]) });
  map.addLayer({
    id: "around-route-calm-glow",
    type: "line",
    source: "around-route-calm",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#ff4d2e", "line-width": 12, "line-opacity": 0.18, "line-blur": 4 },
  });
  map.addLayer({
    id: "around-route-calm",
    type: "line",
    source: "around-route-calm",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#ff4d2e", "line-width": 4, "line-opacity": 0.95 },
  });
}
