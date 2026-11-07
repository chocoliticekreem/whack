import type { ExpressionSpecification, LayerSpecification, SkySpecification, StyleSpecification } from "maplibre-gl";

/** Pitch-deck palette (ink, magenta, cyan, violet, amber, lime). */
const DECK = {
  ink: "#070812",
  magenta: "#FF3DA8",
  cyan: "#3DF5FF",
  violet: "#8B5CFF",
  amber: "#FFB547",
  lime: "#B6FF5C",
} as const;

/** Dusk over London: an ink-violet ground so white tags and cards carry the contrast, colour lives in the buildings. */
export const LOOK = {
  land: "#16142b",
  park: "#1f3326",
  parkEdge: "#2c4a2b",
  water: "#0b4250",
  waterGlow: "#11687a",
  road: "#2f2c52",
  roadMid: "#4a4677",
  roadMajor: "#8783bd",
  rail: "#3a3563",
  buildingLow: "#3d3c6b",
  buildingMid: DECK.violet,
  buildingHigh: DECK.magenta,
  buildingTop: DECK.amber,
  label: "#c9c6ee",
  waterLabel: "#7fe8f2",
  halo: "#16142b",
} as const;

const SKY: SkySpecification = {
  "sky-color": "#241650",
  "horizon-color": "#e8588f",
  "fog-color": "#2a1d4a",
  "sky-horizon-blend": 0.3,
  "horizon-fog-blend": 1,
  "fog-ground-blend": 0.5,
  "atmosphere-blend": 0,
};

const FONT = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];
const FONT_ITALIC = ["Noto Sans Italic"];
const RAW_HEIGHT: ExpressionSpecification = ["coalesce", ["get", "render_height"], 10];
const RAW_BASE: ExpressionSpecification = ["coalesce", ["get", "render_min_height"], 0];
const MAJOR: ExpressionSpecification = ["in", ["get", "class"], ["literal", ["primary", "trunk", "motorway"]]];
const SECONDARY: ExpressionSpecification = ["in", ["get", "class"], ["literal", ["secondary", "tertiary"]]];

// Why: central London is mostly 5-8 storeys, so true heights read flat at the overview; exaggerate there, more on zoom-in.
const scaled = (expr: ExpressionSpecification): ExpressionSpecification => [
  "interpolate",
  ["linear"],
  ["zoom"],
  13,
  ["*", expr, 1.6],
  15,
  ["*", expr, 2],
  16.5,
  ["*", expr, 1.4],
];

function roads(): LayerSpecification[] {
  const line = (id: string, filter: ExpressionSpecification, color: string, w: [number, number], opacity = 1): LayerSpecification => ({
    id,
    type: "line",
    source: "omt",
    "source-layer": "transportation",
    filter,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": color,
      "line-opacity": opacity,
      "line-width": ["interpolate", ["exponential", 1.6], ["zoom"], 12, w[0], 17, w[1]],
    },
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
    line("roads-secondary", SECONDARY, LOOK.roadMid, [0.8, 10]),
    line("roads-major-glow", MAJOR, DECK.violet, [4, 30], 0.18),
    line("roads-major", MAJOR, LOOK.roadMajor, [1.2, 14]),
  ];
}

/** Overview: the Thames and district names only. From z15 the street names and neighbourhoods fill in. */
function labels(): LayerSpecification[] {
  const halo = { "text-halo-color": LOOK.halo, "text-halo-width": 1.4, "text-halo-blur": 0.5 };
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
        "text-size": 13,
        "text-letter-spacing": 0.14,
        "symbol-spacing": 500,
      },
      paint: { "text-color": LOOK.waterLabel, "text-halo-color": LOOK.water, "text-halo-width": 1.2 },
    },
    {
      id: "district-name",
      type: "symbol",
      source: "omt",
      "source-layer": "place",
      filter: ["in", ["get", "class"], ["literal", ["suburb", "quarter"]]],
      layout: {
        "text-field": ["upcase", ["get", "name"]],
        "text-font": FONT_BOLD,
        "text-size": ["interpolate", ["linear"], ["zoom"], 13, 10, 16, 13],
        "text-letter-spacing": 0.18,
        "text-padding": ["interpolate", ["linear"], ["zoom"], 13, 120, 15, 30],
      },
      paint: { "text-color": LOOK.label, "text-opacity": 0.7, ...halo },
    },
    {
      id: "neighbourhood-name",
      type: "symbol",
      source: "omt",
      "source-layer": "place",
      minzoom: 15,
      filter: ["==", ["get", "class"], "neighbourhood"],
      layout: { "text-field": ["get", "name"], "text-font": FONT, "text-size": 11, "text-padding": 20 },
      paint: { "text-color": LOOK.label, "text-opacity": 0.75, ...halo },
    },
    {
      id: "road-name",
      type: "symbol",
      source: "omt",
      "source-layer": "transportation_name",
      minzoom: 15,
      filter: ["any", MAJOR, SECONDARY],
      layout: {
        "symbol-placement": "line",
        "text-field": ["get", "name"],
        "text-font": FONT,
        "text-size": 10.5,
        "symbol-spacing": 600,
        "text-padding": 30,
      },
      paint: { "text-color": LOOK.label, "text-opacity": 0.85, ...halo },
    },
  ];
}

export function buildCityStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    // Low warm sun from the south-west: lit faces glow amber, the far sides fall into violet.
    light: { anchor: "map", color: "#ffd6b8", intensity: 0.45, position: [1.4, 235, 55] },
    sky: SKY,
    sources: { omt: { type: "vector", url: "https://tiles.openfreemap.org/planet" } },
    layers: [
      { id: "land", type: "background", paint: { "background-color": LOOK.land } },
      { id: "park", type: "fill", source: "omt", "source-layer": "park", paint: { "fill-color": LOOK.park, "fill-outline-color": LOOK.parkEdge } },
      {
        id: "landcover",
        type: "fill",
        source: "omt",
        "source-layer": "landcover",
        filter: ["in", ["get", "class"], ["literal", ["grass", "wood"]]],
        paint: { "fill-color": LOOK.park },
      },
      { id: "water", type: "fill", source: "omt", "source-layer": "water", paint: { "fill-color": LOOK.water, "fill-outline-color": LOOK.waterGlow } },
      ...roads(),
      {
        id: "buildings",
        type: "fill-extrusion",
        source: "omt",
        "source-layer": "building",
        minzoom: 13,
        paint: {
          "fill-extrusion-color": [
            "interpolate",
            ["linear"],
            RAW_HEIGHT,
            0,
            LOOK.buildingLow,
            22,
            "#5b4c8f",
            40,
            LOOK.buildingMid,
            75,
            LOOK.buildingHigh,
            140,
            LOOK.buildingTop,
          ],
          "fill-extrusion-height": scaled(RAW_HEIGHT),
          "fill-extrusion-base": scaled(RAW_BASE),
          "fill-extrusion-vertical-gradient": true,
        },
      },
      ...labels(),
    ],
  };
}
