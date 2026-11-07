"use client";

import "./scenario.css";
import type { GeoJSONSource, MapMouseEvent, Map as MapLibreMap, Marker } from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getCityMap } from "./CityMapGL";
import type { Hotspot, Market } from "./city-types";
import {
  changes,
  fmtRel,
  marketMoves,
  simStations,
  suggestions,
  summarise,
  type Change,
  type HKey,
  type Intervention,
  type MarketMove,
  type ScenarioResponse,
  type SimState,
  type SimStation,
  type Suggestion,
} from "./scenario-model";

/** What a market card shows while a what-if is up: its options' what-if prices and the condition. */
export interface WhatIfOdds {
  cond: string;
  options: MarketMove["options"];
}

type LngLat = [number, number];
type Verb = "close" | "crowd" | "shut";

interface Target {
  lat: number;
  lon: number;
  street: { name: string; lines: LngLat[][] } | null;
  station: SimStation | null;
}

interface Run {
  verb: Verb | "suggested";
  action: string;
  cond: string;
  origin: LngLat;
  closed: LngLat[][];
  shut: LngLat | null;
  crowd: { at: LngLat; r: number } | null;
  build: (o: Opts) => Intervention[];
}

interface Opts {
  start: number;
  dur: number;
  crowd: number;
}

const DEFAULTS: Opts = { start: 0, dur: 30, crowd: 0.5 };
const SNAP_STREET_M = 30;
const SNAP_PX = 14;
const STREET_SPAN_M = 350;
const CLOSE_STEP_M = 100;
const CLOSE_R_M = 40;
const MAX_CLOSE_PTS = 5;
const NEAR_STATION_M = 250;
const CROWD_R_M = 250;
const SRC = "sc-src";
const LAYERS = ["sc-crowd", "sc-pick", "sc-closed", "sc-flow", "sc-shut"];

const metres = (aLat: number, aLon: number, bLat: number, bLon: number) => {
  const k = Math.cos((aLat * Math.PI) / 180);
  return Math.hypot((aLon - bLon) * 111_320 * k, (aLat - bLat) * 111_320);
};

/** Distance in metres from a point to a segment, in a local flat frame around the point. */
function segDist(lat: number, lon: number, a: LngLat, b: LngLat) {
  const k = Math.cos((lat * Math.PI) / 180) * 111_320;
  const ax = (a[0] - lon) * k;
  const ay = (a[1] - lat) * 111_320;
  const bx = (b[0] - lon) * k;
  const by = (b[1] - lat) * 111_320;
  const dx = bx - ax;
  const dy = by - ay;
  const l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / l2)) : 0;
  return Math.hypot(ax + t * dx, ay + t * dy);
}

function linesOf(g: GeoJSON.Geometry): LngLat[][] {
  if (g.type === "LineString") return [g.coordinates as LngLat[]];
  if (g.type === "MultiLineString") return g.coordinates as LngLat[][];
  return [];
}

/** Named road lines from the vector tiles already loaded, optionally only those with one name. */
function roads(map: MapLibreMap, name?: string): { name: string; line: LngLat[] }[] {
  const out: { name: string; line: LngLat[] }[] = [];
  for (const f of map.querySourceFeatures("omt", { sourceLayer: "transportation_name" })) {
    const n = f.properties?.name as string | undefined;
    if (!n || (name && n !== name)) continue;
    for (const line of linesOf(f.geometry)) out.push({ name: n, line });
  }
  return out;
}

/** Keeps the runs of a line that stay within `span` metres of a point. */
function clip(line: LngLat[], lat: number, lon: number, span: number): LngLat[][] {
  const runs: LngLat[][] = [];
  let cur: LngLat[] = [];
  for (const p of line) {
    if (metres(lat, lon, p[1], p[0]) <= span) cur.push(p);
    else if (cur.length) {
      if (cur.length > 1) runs.push(cur);
      cur = [];
    }
  }
  if (cur.length > 1) runs.push(cur);
  return runs;
}

/** The nearest named street to a click and its stretch around it, snapped to the road geometry. */
function snapStreet(map: MapLibreMap, lat: number, lon: number): Target["street"] {
  let best: string | null = null;
  const perPx = (156_543 * Math.cos((lat * Math.PI) / 180)) / 2 ** map.getZoom();
  let d = Math.max(SNAP_STREET_M, SNAP_PX * perPx);
  for (const r of roads(map)) {
    for (let i = 1; i < r.line.length; i++) {
      const m = segDist(lat, lon, r.line[i - 1], r.line[i]);
      if (m < d) {
        d = m;
        best = r.name;
      }
    }
  }
  if (!best) return null;
  const lines = roads(map, best).flatMap((r) => clip(r.line, lat, lon, STREET_SPAN_M));
  return lines.length ? { name: best, lines } : null;
}

/** Points every ~100 m along the lines, at most five, each closed with a small radius. */
function samplePoints(lines: LngLat[][]): LngLat[] {
  const pts: LngLat[] = [];
  for (const line of lines) {
    let acc = CLOSE_STEP_M;
    for (let i = 1; i < line.length; i++) {
      acc += metres(line[i - 1][1], line[i - 1][0], line[i][1], line[i][0]);
      if (acc >= CLOSE_STEP_M) {
        pts.push(line[i]);
        acc = 0;
      }
    }
  }
  if (pts.length <= MAX_CLOSE_PTS) return pts.length ? pts : lines.flat().slice(0, 1);
  const step = pts.length / MAX_CLOSE_PTS;
  return Array.from({ length: MAX_CLOSE_PTS }, (_, i) => pts[Math.floor(i * step)]);
}

function circle([lon, lat]: LngLat, r: number): LngLat[] {
  const k = Math.cos((lat * Math.PI) / 180);
  return Array.from({ length: 49 }, (_, i) => {
    const t = (i / 48) * 2 * Math.PI;
    return [lon + (r * Math.cos(t)) / (111_320 * k), lat + (r * Math.sin(t)) / 111_320] as LngLat;
  });
}

function nearestStation(stations: SimStation[], lat: number, lon: number) {
  let best: SimStation | null = null;
  let d = Infinity;
  for (const s of stations) {
    const m = metres(lat, lon, s.lat, s.lon);
    if (m < d) {
      d = m;
      best = s;
    }
  }
  return d <= NEAR_STATION_M ? best : null;
}

function useMap() {
  const [map, setMap] = useState<MapLibreMap | null>(null);
  useEffect(() => {
    const id = window.setInterval(() => {
      const m = getCityMap();
      if (m) {
        setMap(m);
        window.clearInterval(id);
      }
    }, 300);
    return () => window.clearInterval(id);
  }, []);
  return map;
}

/** Map paint cannot read CSS variables, so the tokens are read once from the page. */
function tokens() {
  const css = getComputedStyle(document.querySelector(".tl-root") ?? document.body);
  const v = (n: string, f: string) => css.getPropertyValue(n).trim() || f;
  return { accent: v("--accent", "#d6246e"), no: v("--no", "#cf3341"), ink: v("--ink", "#151517") };
}

/** Stops the map from treating a click inside an overlay as a click on the city. */
function shield(el: HTMLElement) {
  for (const t of ["mousedown", "mouseup", "click", "dblclick", "touchstart", "touchend", "pointerdown", "pointerup", "wheel"]) {
    el.addEventListener(t, (e) => e.stopPropagation());
  }
}

const sizeLabel = (s: number) => `+${Math.round(s * 100)}%`;
const pct = (p: number) => `${Math.round(p * 100)}%`;

export function Scenario({
  hotspots,
  markets,
  now,
  onOdds,
  onClose,
}: {
  hotspots: Hotspot[];
  markets: Market[];
  now: number;
  onOdds: (odds: Record<string, WhatIfOdds> | null) => void;
  onClose: () => void;
}) {
  const map = useMap();
  const [state, setState] = useState<SimState | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [opts, setOpts] = useState<Opts>(DEFAULTS);
  const [adjust, setAdjust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [res, setRes] = useState<ScenarioResponse | null>(null);
  const [viewPick, setView] = useState<HKey | null>(null);
  const [streetHits, setStreetHits] = useState<Record<string, LngLat[][]>>({});
  const pins = useRef<Marker[]>([]);
  const popMarker = useRef<Marker | null>(null);
  const [popEl, setPopEl] = useState<HTMLDivElement | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    let live = true;
    void fetch("/api/sim/state", { cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<SimState>) : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((s) => live && setState(s))
      .catch(() => live && setError("The central sim is not reachable, so what-ifs cannot run right now."));
    return () => {
      live = false;
    };
  }, []);

  const stations = useMemo(() => (state ? simStations(state, hotspots) : []), [state, hotspots]);

  // Street suggestions name a street; they show only when the loaded map can place it.
  const allChips = useMemo(() => (state ? suggestions(state, stations) : []), [state, stations]);
  useEffect(() => {
    if (!map) return;
    const place = () => {
      const hits: Record<string, LngLat[][]> = {};
      for (const c of allChips) {
        if (!c.street) continue;
        const lines = roads(map, c.street).map((r) => r.line);
        if (lines.length) hits[c.street] = lines;
      }
      setStreetHits(hits);
    };
    place();
    map.on("idle", place);
    return () => {
      map.off("idle", place);
    };
  }, [map, allChips]);
  const chips = allChips.filter((c) => !c.street || streetHits[c.street]);

  const execute = useCallback(async (r: Run, o: Opts) => {
    const id = ++seq.current;
    setRun(r);
    setView(null);
    setTarget(null);
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/sim/scenario", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ interventions: r.build(o).map((iv) => ({ ...iv, note: r.action })), horizons: [15, 60] }),
        cache: "no-store",
      });
      const data = (await resp.json()) as ScenarioResponse & { error?: string };
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      if (id !== seq.current) return;
      setRes(data);
    } catch (e) {
      if (id === seq.current) setError(e instanceof Error ? e.message : "Simulation failed");
    } finally {
      if (id === seq.current) setBusy(false);
    }
  }, []);

  const list = useMemo<Change[]>(() => (res ? changes(res, hotspots) : []), [res, hotspots]);
  const shown = useMemo(() => {
    const st = list.filter((c) => c.basis === "busyness");
    return st.length ? st : list;
  }, [list]);
  const view: HKey = useMemo(() => {
    if (viewPick) return viewPick;
    const peak = (k: "d15" | "d60") => Math.max(0, ...shown.map((c) => Math.abs(c[k] ?? 0)));
    return peak("d15") >= peak("d60") ? "+15" : "+60";
  }, [viewPick, shown]);

  const moves = useMemo(() => (res ? marketMoves(markets, res, now) : []), [res, markets, now]);
  useEffect(() => {
    if (!res || !run || busy) {
      onOdds(null);
      return;
    }
    onOdds(Object.fromEntries(moves.map((m) => [m.market.id, { cond: run.cond, options: m.options }])));
  }, [moves, res, run, busy, onOdds]);
  useEffect(() => () => onOdds(null), [onOdds]);

  const pick = useCallback(
    (lat: number, lon: number) => {
      const m = getCityMap();
      if (!m) return;
      setTarget({ lat, lon, street: snapStreet(m, lat, lon), station: nearestStation(stations, lat, lon) });
    },
    [stations],
  );

  useEffect(() => {
    if (!map) return;
    const onClick = (e: MapMouseEvent) => pick(e.lngLat.lat, e.lngLat.lng);
    map.on("click", onClick);
    map.getCanvas().style.cursor = "crosshair";
    return () => {
      map.off("click", onClick);
      map.getCanvas().style.cursor = "";
    };
  }, [map, pick]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setTarget(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The popover is a map marker at the click, with React content portalled into it.
  useEffect(() => {
    if (!map || !target) {
      popMarker.current?.remove();
      popMarker.current = null;
      return;
    }
    let cancelled = false;
    void import("maplibre-gl").then((lib) => {
      if (cancelled) return;
      popMarker.current?.remove();
      const el = document.createElement("div");
      el.className = "sc-pop-anchor";
      shield(el);
      popMarker.current = new lib.Marker({ element: el, anchor: "bottom", offset: [0, -10] }).setLngLat([target.lon, target.lat]).addTo(map);
      setPopEl(el);
    });
    return () => {
      cancelled = true;
    };
  }, [map, target]);

  useEffect(
    () => () => {
      popMarker.current?.remove();
    },
    [],
  );

  // One GeoJSON source for everything drawn: the picked street, closed streets, the crowd area, flows.
  const geo = useMemo(() => {
    const feats: GeoJSON.Feature[] = [];
    const line = (role: string, coords: LngLat[]) => feats.push({ type: "Feature", properties: { role }, geometry: { type: "LineString", coordinates: coords } });
    if (target?.street) target.street.lines.forEach((l) => line("pick", l));
    if (run) {
      run.closed.forEach((l) => line("closed", l));
      if (run.crowd) feats.push({ type: "Feature", properties: { role: "crowd" }, geometry: { type: "Polygon", coordinates: [circle(run.crowd.at, run.crowd.r)] } });
      if (run.shut) feats.push({ type: "Feature", properties: { role: "shut" }, geometry: { type: "Point", coordinates: run.shut } });
      if (res) {
        const k = view === "+15" ? "d15" : "d60";
        shown
          .filter((c) => (c[k] ?? 0) > 0 && c.lat != null && c.lon != null)
          .sort((a, b) => (b[k] ?? 0) - (a[k] ?? 0))
          .slice(0, 3)
          .forEach((c) => line("flow", [run.origin, [c.lon as number, c.lat as number]]));
      }
    }
    return { type: "FeatureCollection" as const, features: feats };
  }, [target, run, res, shown, view]);

  useEffect(() => {
    if (!map) return;
    const src = map.getSource(SRC) as GeoJSONSource | undefined;
    if (src) {
      src.setData(geo);
      return;
    }
    const t = tokens();
    map.addSource(SRC, { type: "geojson", data: geo });
    const role = (r: string) => ["==", ["get", "role"], r] as ["==", ["get", string], string];
    map.addLayer({ id: "sc-crowd", type: "fill", source: SRC, filter: role("crowd"), paint: { "fill-color": t.accent, "fill-opacity": 0.14 } });
    map.addLayer({ id: "sc-pick", type: "line", source: SRC, filter: role("pick"), layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": t.ink, "line-width": 7, "line-opacity": 0.35 } });
    map.addLayer({ id: "sc-closed", type: "line", source: SRC, filter: role("closed"), layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": t.no, "line-width": 6 } });
    map.addLayer({ id: "sc-flow", type: "line", source: SRC, filter: role("flow"), layout: { "line-cap": "round" }, paint: { "line-color": t.accent, "line-width": 2.5, "line-dasharray": [1.5, 1.5] } });
    map.addLayer({ id: "sc-shut", type: "circle", source: SRC, filter: role("shut"), paint: { "circle-radius": 14, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": t.no, "circle-stroke-width": 4 } });
  }, [map, geo]);

  useEffect(() => {
    if (!map) return;
    return () => {
      for (const id of LAYERS) if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(SRC)) map.removeSource(SRC);
    };
  }, [map]);

  // Result pins: a rise in the accent, a fall outlined; station busyness, or hub street crowds if no station moved.
  useEffect(() => {
    let cancelled = false;
    const clear = () => {
      pins.current.forEach((p) => p.remove());
      pins.current = [];
    };
    clear();
    if (!map || !res || busy) return clear;
    void import("maplibre-gl").then((lib) => {
      if (cancelled) return;
      for (const c of shown) {
        const d = view === "+15" ? c.d15 : c.d60;
        if (d == null || Math.abs(d) < 1 || c.lat == null || c.lon == null) continue;
        const el = document.createElement("div");
        el.className = "sc-pin";
        el.dataset.dir = d > 0 ? "up" : "down";
        el.style.setProperty("--w", String(Math.min(1, Math.abs(d) / 30)));
        el.innerHTML = '<span class="sc-pin-dot"></span><span class="sc-pin-tag"><span class="sc-pin-name"></span><b class="sc-pin-d"></b></span>';
        (el.querySelector(".sc-pin-name") as HTMLElement).textContent = c.basis === "people" ? `${c.name} streets` : c.name;
        (el.querySelector(".sc-pin-d") as HTMLElement).textContent = fmtRel(d);
        pins.current.push(new lib.Marker({ element: el, anchor: "center" }).setLngLat([c.lon, c.lat]).addTo(map));
      }
    });
    return () => {
      cancelled = true;
      clear();
    };
  }, [map, res, busy, shown, view]);

  const verb = (v: Verb) => {
    if (!target) return;
    const at: LngLat = [target.lon, target.lat];
    const near = target.station?.name;
    const street = target.street?.name;
    let r: Run;
    if (v === "close" && target.street) {
      const pts = samplePoints(target.street.lines);
      r = {
        verb: v,
        action: `Closing ${street}`,
        cond: `if ${street} closes`,
        origin: at,
        closed: target.street.lines,
        shut: null,
        crowd: null,
        build: (o) => pts.map((p) => ({ kind: "closure", where: { lat: p[1], lon: p[0], radiusM: CLOSE_R_M }, start: o.start, end: o.start + o.dur, magnitude: 0 })),
      };
    } else if (v === "shut" && target.station) {
      const s = target.station;
      r = {
        verb: v,
        action: `Shutting ${s.name}`,
        cond: `if ${s.name} shuts`,
        origin: [s.lon, s.lat],
        closed: [],
        shut: [s.lon, s.lat],
        crowd: null,
        build: (o) => [{ kind: "closure", where: { naptan: s.naptan }, start: o.start, end: o.start + o.dur, magnitude: 0 }],
      };
    } else {
      const place = street ? `on ${street}` : near ? `near ${near}` : "here";
      r = {
        verb: "crowd",
        action: `A crowd ${place}`,
        cond: `if a crowd gathers ${place}`,
        origin: at,
        closed: [],
        shut: null,
        crowd: { at, r: CROWD_R_M },
        build: (o) => [{ kind: "demand_surge", where: { lat: target.lat, lon: target.lon, radiusM: CROWD_R_M }, start: o.start, end: o.start + o.dur, magnitude: o.crowd }],
      };
    }
    void execute(r, opts);
  };

  const suggest = (c: Suggestion) => {
    let ivs = c.interventions;
    let closed: LngLat[][] = [];
    let origin: LngLat | null = null;
    if (c.street) {
      const lines = streetHits[c.street] ?? [];
      const pts = samplePoints(lines);
      closed = lines;
      origin = pts[Math.floor(pts.length / 2)] ?? null;
      ivs = pts.map((p) => ({ kind: "closure", where: { lat: p[1], lon: p[0], radiusM: CLOSE_R_M }, start: 0, end: 90, magnitude: 0 }));
    }
    const first = ivs[0];
    if (!first) return;
    if (!origin) {
      const w = first.where;
      const s = "naptan" in w ? stations.find((x) => x.naptan === w.naptan) : null;
      origin = "naptan" in w ? (s ? [s.lon, s.lat] : null) : [w.lon, w.lat];
    }
    if (!origin) return;
    const shutAt = first.kind === "capacity" ? origin : null;
    const crowd = first.kind === "demand_surge" && !("naptan" in first.where) ? { at: origin, r: first.where.radiusM } : null;
    const fixed = ivs;
    void execute(
      {
        verb: "suggested",
        action: c.action,
        cond: `if ${c.label.replace(/^What if /, "").replace(/\?$/, "")}`,
        origin,
        closed,
        shut: shutAt,
        crowd,
        build: () => fixed,
      },
      opts,
    );
  };

  const setOpt = (o: Partial<Opts>) => {
    const next = { ...opts, ...o };
    setOpts(next);
    if (run && run.verb !== "suggested") void execute(run, next);
  };

  const clearRun = () => {
    seq.current++;
    setRun(null);
    setRes(null);
    setBusy(false);
    setError(null);
    setTarget(null);
  };

  const sentence = res && run && !busy ? summarise(run.action, list) : null;

  return (
    <>
      <section className="sc" aria-label="What if" aria-live="polite">
        <div className="sc-row">
          <div className="sc-main">
            {busy && <p className="sc-lead">Simulating {run?.action.toLowerCase()}…</p>}
            {!busy && sentence && <p className="sc-lead">{sentence}</p>}
            {!busy && !sentence && <p className="sc-lead">Click any street or station to ask what if.</p>}
            <p className="sc-small">
              {res?.mock && !busy && <span className="sc-badge">Preview: simulated with mock</span>}
              This is a what-if, it doesn&apos;t change live prices.
              {run && !busy && run.verb !== "suggested" && (
                <button type="button" className="sc-link" aria-expanded={adjust} onClick={() => setAdjust((a) => !a)}>
                  Adjust
                </button>
              )}
            </p>
          </div>
          <div className="sc-actions">
            {run && (
              <button type="button" className="sc-ghost" onClick={clearRun}>
                Try another
              </button>
            )}
            <button type="button" className="sc-exit" onClick={onClose}>
              Back to live prices
            </button>
          </div>
        </div>

        {adjust && run && run.verb !== "suggested" && (
          <div className="sc-adjust">
            <Choice label="Starts" values={[0, 15]} value={opts.start} onChange={(v) => setOpt({ start: v })} fmt={(v) => (v === 0 ? "Now" : `In ${v} min`)} />
            <Choice label="Lasts" values={[30, 60]} value={opts.dur} onChange={(v) => setOpt({ dur: v })} fmt={(v) => `${v} min`} />
            {run.verb === "crowd" && <Choice label="Crowd" values={[0.25, 0.5, 1]} value={opts.crowd} onChange={(v) => setOpt({ crowd: v })} fmt={sizeLabel} />}
          </div>
        )}

        {!run && chips.length > 0 && (
          <div className="sc-chips" aria-label="Suggested from live disruptions">
            {chips.map((c) => (
              <button key={c.id} type="button" className="sc-chip" onClick={() => suggest(c)}>
                {c.label}
              </button>
            ))}
          </div>
        )}

        {res && !busy && shown.length > 0 && (
          <div className="sc-strip">
            <div className="sc-seg" role="radiogroup" aria-label="Horizon">
              {(["+15", "+60"] as HKey[]).map((k) => (
                <button key={k} type="button" role="radio" aria-checked={view === k} data-on={view === k || undefined} onClick={() => setView(k)}>
                  {k} min
                </button>
              ))}
            </div>
            {shown.filter((c) => (view === "+15" ? c.d15 : c.d60)).slice(0, 5).map((c) => {
              const d = view === "+15" ? c.d15 : c.d60;
              return (
                <span key={c.id} className="sc-chg" data-dir={d && d > 0 ? "up" : d && d < 0 ? "down" : undefined}>
                  {c.basis === "people" ? `${c.name} streets` : c.name} <b className="tl-data">{fmtRel(d)}</b>
                </span>
              );
            })}
          </div>
        )}

        {error && <div className="tl-error sc-error">{error}</div>}
      </section>

      {popEl &&
        target &&
        createPortal(
          <div className="sc-pop" role="dialog" aria-label="What if here">
            <b className="sc-pop-title">{target.street?.name ?? target.station?.name ?? "This spot"}</b>
            {target.station && target.street && <span className="sc-pop-sub">Near {target.station.name}</span>}
            <div className="sc-pop-verbs">
              {target.street && (
                <button type="button" onClick={() => verb("close")}>
                  Close this street
                </button>
              )}
              <button type="button" onClick={() => verb("crowd")}>
                Crowd here
              </button>
              {target.station && (
                <button type="button" onClick={() => verb("shut")}>
                  Shut {target.station.name}
                </button>
              )}
            </div>
          </div>,
          popEl,
        )}
    </>
  );
}

/** The what-if line on a market card: the two options that move most, live price → what-if price. */
export function WhatIfLine({ odds }: { odds: WhatIfOdds }) {
  const top = [...odds.options].sort((a, b) => Math.abs(b.to - b.from) - Math.abs(a.to - a.from)).slice(0, 2);
  const still = top.every((o) => Math.round(o.to * 100) === Math.round(o.from * 100));
  return (
    <div className="sc-card" onClick={(e) => e.stopPropagation()}>
      <span className="sc-card-tag">What-if</span>
      {still ? (
        <span className="sc-card-o">Barely moves {odds.cond}</span>
      ) : (
        <span className="sc-card-o">
          {top.map((o, i) => (
            <span key={o.id}>
              {i > 0 && ", "}
              {o.label}{" "}
              <span className="tl-data" data-up={o.to > o.from || undefined}>
                {pct(o.from)} → {pct(o.to)}
              </span>
            </span>
          ))}{" "}
          {odds.cond}
        </span>
      )}
      <span className="sc-card-note">approx.</span>
    </div>
  );
}

function Choice<T extends number>({ label, values, value, onChange, fmt }: { label: string; values: T[]; value: T; onChange: (v: T) => void; fmt: (v: T) => string }) {
  return (
    <div className="sc-field">
      <span>{label}</span>
      <div className="sc-seg" role="radiogroup" aria-label={label}>
        {values.map((v) => (
          <button key={v} type="button" role="radio" aria-checked={value === v} data-on={value === v || undefined} onClick={() => onChange(v)}>
            {fmt(v)}
          </button>
        ))}
      </div>
    </div>
  );
}
