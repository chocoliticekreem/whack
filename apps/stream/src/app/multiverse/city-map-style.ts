import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from "maplibre-gl";

/** Light editorial look adapted from RAISE's "chalk" view: one pale material, depth comes from the light. */
export const LOOK = {
  land: "#ebebe5",
  park: "#e0e3d8",
  water: "#cfd9de",
  road: "#f7f6f3",
  roadMajor: "#fdfdfb",
  rail: "#d6d3cc",
  buildingLow: "#ffffff",
  buildingMid: "#f7f6f2",
  buildingHigh: "#ecebe5",
  label: "#6b6a66",
  water_label: "#7f939c",
  halo: "#ebebe5",
} as const;

const FONT = ["Noto Sans Regular"];
const FONT_ITALIC = ["Noto Sans Italic"];
const HEIGHT: ExpressionSpecification = ["coalesce", ["get", "render_height"], 10];
const MAJOR: ExpressionSpecification = ["in", ["get", "class"], ["literal", ["primary", "trunk", "motorway"]]];
const SECONDARY: ExpressionSpecification = ["in", ["get", "class"], ["literal", ["secondary", "tertiary"]]];

function roads(): LayerSpecification[] {
  const line = (id: string, filter: ExpressionSpecification, color: string, w: [number, number]): LayerSpecification => ({
    id,
    type: "line",
    source: "omt",
    "source-layer": "transportation",
    filter,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": color, "line-width": ["interpolate", ["exponential", 1.6], ["zoom"], 12, w[0], 17, w[1]] },
  });
  return [
    {
      id: "rail",
      type: "line",
      source: "omt",
      "source-layer": "transportation",
      filter: ["==", ["get", "class"], "rail"],
      paint: { "line-color": LOOK.rail, "line-width": 1 },
    },
    line("roads-minor", ["in", ["get", "class"], ["literal", ["minor", "service"]]], LOOK.road, [0.4, 6]),
    line("roads-secondary", SECONDARY, LOOK.road, [0.8, 10]),
    line("roads-major", MAJOR, LOOK.roadMajor, [1.4, 16]),
  ];
}

/** Only the Thames and a handful of major road names: the hotspots carry the story. */
function labels(): LayerSpecification[] {
  return [
    {
      id: "water-name",
      type: "symbol",
      source: "omt",
      "source-layer": "water_name",
      filter: ["==", ["geometry-type"], "LineString"],
      layout: {
        "symbol-placement": "line",
        "text-field": ["get", "name"],
        "text-font": FONT_ITALIC,
        "text-size": 12,
        "text-letter-spacing": 0.12,
        "symbol-spacing": 500,
      },
      paint: { "text-color": LOOK.water_label, "text-halo-color": LOOK.water, "text-halo-width": 1 },
    },
    {
      id: "road-name",
      type: "symbol",
      source: "omt",
      "source-layer": "transportation_name",
      minzoom: 14,
      filter: MAJOR,
      layout: {
        "symbol-placement": "line",
        "text-field": ["get", "name"],
        "text-font": FONT,
        "text-size": 10.5,
        "symbol-spacing": 900,
        "text-padding": 40,
      },
      paint: { "text-color": LOOK.label, "text-opacity": 0.75, "text-halo-color": LOOK.halo, "text-halo-width": 1.25 },
    },
  ];
}

export function buildCityStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    light: { anchor: "viewport", color: "#ffffff", intensity: 0.4, position: [1.3, 210, 35] },
    sources: { omt: { type: "vector", url: "https://tiles.openfreemap.org/planet" } },
    layers: [
      { id: "land", type: "background", paint: { "background-color": LOOK.land } },
      { id: "park", type: "fill", source: "omt", "source-layer": "park", paint: { "fill-color": LOOK.park } },
      {
        id: "landcover",
        type: "fill",
        source: "omt",
        "source-layer": "landcover",
        filter: ["in", ["get", "class"], ["literal", ["grass", "wood"]]],
        paint: { "fill-color": LOOK.park },
      },
      { id: "water", type: "fill", source: "omt", "source-layer": "water", paint: { "fill-color": LOOK.water } },
      ...roads(),
      {
        id: "buildings",
        type: "fill-extrusion",
        source: "omt",
        "source-layer": "building",
        minzoom: 13,
        paint: {
          "fill-extrusion-color": ["interpolate", ["linear"], HEIGHT, 0, LOOK.buildingLow, 50, LOOK.buildingMid, 150, LOOK.buildingHigh],
          "fill-extrusion-height": HEIGHT,
          "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        },
      },
      ...labels(),
    ],
  };
}
