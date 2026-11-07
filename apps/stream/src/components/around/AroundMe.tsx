"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
import { Car, ChevronDown, LocateFixed, ShieldAlert, User, Waves, Wind } from "lucide-react";
import { PipStage } from "@/components/stream/PipStage";
import type { StreamLocation } from "@/lib/types";
import { useFlowLayer } from "@/platform/around/useFlowLayer";
import type { PaddingOptions } from "maplibre-gl";
import { distanceM } from "@/platform/around/geo";
import type { AroundBusStop } from "@/platform/around/types";
import { BusStopCard, CamCard, DisruptionCard, LiveCamCard, StationCard } from "./cards";
import { MapLegend, type LayerKind } from "./MapLegend";
import { RoutePicker } from "./RoutePicker";
import {
  circlePolygon,
  crimeCollection,
  disruptionCollection,
  FIT_MAX_ZOOM,
  FIT_MIN_ZOOM,
  PITCH,
  setData,
  setLayersVisible,
  WALK_RADIUS_M,
  ZOOM,
  type Point,
} from "./mapLayers";
import { useAroundData, useNow } from "./useAroundData";
import { useAroundMap, type DisruptionEvents } from "./useAroundMap";
import { useCamCounts } from "./useCamCounts";
import { useGeolocation } from "./useGeolocation";
import { useCamMarkers, useLiveCamMarkers, useStationMarkers, useStopMarkers } from "./useMarkers";

// Fit the opening view to the streamer plus this many of the nearest cams and stations.
const FIT_NEAREST = 8;
// Anything further than this is left over from the previous location and must not stretch the fit.
const FIT_REACH_M = 1000;
const BUS_TICK_MS = 15_000;
const CARD_TICK_MS = 1_000;

const AIR_TONE = {
  good: "text-ok",
  fair: "text-ok",
  moderate: "text-warn",
  poor: "text-live",
  "very poor": "text-live",
} as const;

function monthLabel(ym: string) {
  const [y, m] = ym.split("-").map(Number);
  return y && m ? new Date(y, m - 1, 1).toLocaleDateString("en-GB", { month: "short", year: "numeric" }) : ym;
}

interface AroundMeProps {
  location: StreamLocation;
  activeCamUrl: string | null;
  /** The stream, shown as a swappable picture-in-picture on top of the map. */
  feed: ReactNode | null;
}

interface TileProps {
  icon: ReactNode;
  orb: string;
  value: string | number;
  label: string;
  tone?: string;
  title?: string;
  wide?: boolean;
}

function Tile({ icon, orb, value, label, tone = "text-text", title, wide }: TileProps) {
  return (
    <div className={`tile ${wide ? "col-span-2" : ""}`} title={title}>
      <span className={`grid size-10 shrink-0 place-items-center rounded-full shadow-[inset_0_2px_3px_rgb(255_255_255/0.45)] ${orb}`}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className={`block text-[28px] font-extrabold leading-none tracking-[-0.03em] ${tone}`}>{value}</span>
        <span className="mt-1 block truncate text-[12px] font-semibold text-white/60">{label}</span>
      </span>
    </div>
  );
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function BusStopPanel({ stop, onClose }: { stop: AroundBusStop; onClose: () => void }) {
  const now = useNow(CARD_TICK_MS);
  return <BusStopCard stop={stop} now={now} fetchedAt={stop.at} onClose={onClose} />;
}

interface FitItem {
  lat: number;
  lon: number;
}

/** Bounds around the streamer and the nearest markers, or null while nothing near has loaded yet. */
function nearbyBounds(centre: Point, items: FitItem[]): [[number, number], [number, number]] | null {
  const near = items
    .map((i) => ({ i, d: distanceM(centre.lat, centre.lon, i.lat, i.lon) }))
    .filter((x) => x.d <= FIT_REACH_M)
    .sort((a, b) => a.d - b.d)
    .slice(0, FIT_NEAREST)
    .map((x) => x.i);
  if (near.length === 0) return null;
  const lats = [centre.lat, ...near.map((i) => i.lat)];
  const lons = [centre.lon, ...near.map((i) => i.lon)];
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}

export function AroundMe({ location, activeCamUrl, feed }: AroundMeProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedId, setSelectedId] = useState<string | null | undefined>(undefined);
  const [disruptionId, setDisruptionId] = useState<string | null>(null);
  const [disruptionPinned, setDisruptionPinned] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [hidden, setHidden] = useState<ReadonlySet<LayerKind>>(() => new Set());

  const eventsRef = useRef<DisruptionEvents>({ hover: () => {}, click: () => {} });
  useEffect(() => {
    eventsRef.current = {
      hover: (id) => {
        if (!disruptionPinned) setDisruptionId(id);
      },
      click: (id) => {
        setDisruptionId(id);
        setDisruptionPinned(true);
      },
    };
  }, [disruptionPinned]);

  const geo = useGeolocation();
  const geoPoint = geo.active ? geo.queryPoint : null;
  const point = useMemo(
    () => geoPoint ?? { lat: location.lat, lon: location.lon },
    [geoPoint, location.lat, location.lon],
  );
  const dot = geo.active && geo.fix ? geo.fix : point;

  const { mapRef, libRef, streamerRef, ready } = useAroundMap(containerRef, point, eventsRef);
  const { cams, disruptions, stops, stations, crime, tide, air, liveCams, updatedAt, failed } = useAroundData(point);
  const camIds = useMemo(() => cams.map((c) => c.id), [cams]);
  const readings = useCamCounts(camIds);
  const flow = useFlowLayer(mapRef, ready, cams, point);
  const [stationId, setStationId] = useState<string | null>(null);
  const [liveCamId, setLiveCamId] = useState<string | null>(null);
  const [stopId, setStopId] = useState<string | null>(null);

  const activeCam = useMemo(() => cams.find((c) => c.videoUrl === activeCamUrl) ?? null, [cams, activeCamUrl]);
  const selectedCam =
    selectedId === undefined ? activeCam : cams.find((c) => c.id === selectedId) ?? null;
  const disruption = disruptions.find((d) => d.id === disruptionId) ?? null;
  const station = stations.find((s) => s.id === stationId) ?? null;
  const liveCam = liveCams.find((c) => c.id === liveCamId) ?? null;
  const stop = stops.find((s) => s.id === stopId) ?? null;

  // Left padding keeps fitted markers out from under the info panel when it is open on a wide map.
  const fitPadding = useCallback((): PaddingOptions => {
    const wide = (containerRef.current?.clientWidth ?? 0) >= 720;
    return { top: 96, bottom: 72, right: 48, left: wide && infoOpen ? 420 : 48 };
  }, [infoOpen]);

  const userMovedRef = useRef(false);
  const fittedRef = useRef(0);
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const onMove = (e: { originalEvent?: unknown }) => {
      if (e.originalEvent) userMovedRef.current = true;
    };
    map.on("dragstart", onMove);
    map.on("zoomstart", onMove);
    return () => {
      map.off("dragstart", onMove);
      map.off("zoomstart", onMove);
    };
  }, [mapRef, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    userMovedRef.current = false;
    fittedRef.current = 0;
    map.flyTo({ center: [point.lon, point.lat], zoom: ZOOM, pitch: PITCH, duration: 1200, essential: true });
    setData(map, "around-walk", circlePolygon(point, WALK_RADIUS_M));
  }, [mapRef, ready, point]);

  // Fit once nearby cams arrive and once more if stations add to them, until the streamer pans or zooms.
  const fitItems = useMemo(() => [...cams, ...stations], [cams, stations]);
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || userMovedRef.current || fitItems.length <= fittedRef.current) return;
    const bounds = nearbyBounds(point, fitItems);
    if (!bounds) return;
    fittedRef.current = fitItems.length;
    const camera = map.cameraForBounds(bounds, { padding: fitPadding(), maxZoom: FIT_MAX_ZOOM });
    if (!camera) return;
    // On a phone the nearest markers can span more than the screen; then keep the streamer centred instead.
    const tooWide = (camera.zoom ?? FIT_MIN_ZOOM) < FIT_MIN_ZOOM;
    map.easeTo({
      center: tooWide ? [point.lon, point.lat] : camera.center,
      zoom: Math.max(camera.zoom ?? FIT_MIN_ZOOM, FIT_MIN_ZOOM),
      pitch: PITCH,
      duration: 1000,
    });
  }, [mapRef, ready, point, fitItems, fitPadding]);

  useEffect(() => {
    streamerRef.current?.setLngLat([dot.lon, dot.lat]);
  }, [streamerRef, ready, dot.lon, dot.lat]);

  useEffect(() => {
    const map = mapRef.current;
    if (ready && map) setData(map, "around-disruptions", disruptionCollection(disruptions));
  }, [mapRef, ready, disruptions]);

  useEffect(() => {
    const map = mapRef.current;
    if (ready && map) setData(map, "around-crime", crimeCollection(crime?.points ?? []));
  }, [mapRef, ready, crime]);

  const closuresOn = !hidden.has("closure");
  const crimeOn = !hidden.has("crime");
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    setLayersVisible(map, ["around-disruptions", "around-disruptions-hit"], closuresOn);
    setLayersVisible(map, ["around-crime-heat"], crimeOn);
  }, [mapRef, ready, closuresOn, crimeOn]);

  const toggleLayer = useCallback((kind: LayerKind) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (!next.delete(kind)) next.add(kind);
      return next;
    });
  }, []);
  const counts = useMemo(
    () => ({
      cam: cams.length,
      livecam: liveCams.length,
      station: stations.length,
      bus: stops.length,
      closure: disruptions.length,
      crime: crime?.points.length ?? 0,
    }),
    [cams, liveCams, stations, stops, disruptions, crime],
  );

  // One detail card at a time, and the info panel folds away so the card does not stack on top of it.
  const selectCam = useCallback((id: string) => {
    setSelectedId(id);
    setLiveCamId(null);
    setStationId(null);
    setStopId(null);
    setInfoOpen(false);
  }, []);
  const selectLiveCam = useCallback((id: string) => {
    setLiveCamId(id);
    setSelectedId(null);
    setStationId(null);
    setStopId(null);
    setInfoOpen(false);
  }, []);
  const selectStation = useCallback((id: string) => {
    setStationId(id);
    setSelectedId(null);
    setLiveCamId(null);
    setStopId(null);
    setInfoOpen(false);
  }, []);
  const selectStop = useCallback((id: string) => {
    setStopId(id);
    setSelectedId(null);
    setLiveCamId(null);
    setStationId(null);
    setInfoOpen(false);
  }, []);

  const refs = useMemo(() => ({ mapRef, libRef, ready }), [mapRef, libRef, ready]);
  useCamMarkers(refs, {
    cams,
    readings,
    activeCamId: activeCam?.id ?? null,
    selectedCamId: selectedCam?.id ?? null,
    onSelect: selectCam,
    visible: !hidden.has("cam"),
  });
  useStationMarkers(refs, { items: stations, selectedId: stationId, onSelect: selectStation, visible: !hidden.has("station") });
  useLiveCamMarkers(refs, { items: liveCams, selectedId: liveCamId, onSelect: selectLiveCam, visible: !hidden.has("livecam") });
  const busNow = useNow(BUS_TICK_MS);
  useStopMarkers(refs, { items: stops, selectedId: stopId, onSelect: selectStop, visible: !hidden.has("bus"), now: busNow });

  const place = geo.active && geo.fix ? "Your real position" : `${location.name} · ${location.area}`;

  return (
    <PipStage
      className="min-h-[560px] flex-1"
      corner="top-right"
      primaryLabel="map"
      secondaryLabel="stream"
      secondary={feed}
      primary={
        <div
          ref={containerRef}
          className="absolute inset-0 h-full w-full [&_.maplibregl-ctrl-attrib]:!bg-bg/70 [&_.maplibregl-ctrl-attrib]:!text-[10px] [&_.maplibregl-ctrl-attrib_a]:!text-muted"
        />
      }
      overlay={({ swapped }) =>
        swapped ? null : (
          <>
            <aside className="glass pointer-events-auto absolute left-3 top-3 flex max-h-[calc(100%-1.5rem)] w-[min(360px,calc(100%-1.5rem))] max-sm:top-14 max-sm:max-h-[calc(100%-4.25rem)] flex-col overflow-hidden rounded-[28px]">
              <header className="flex items-center gap-3 px-4 pb-3 pt-3.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h2 className="text-[18px] font-extrabold leading-tight tracking-[-0.02em] text-text">Around you</h2>
                    <span className="live-badge">
                      <span className="animate-live size-1.5 rounded-full bg-white" />
                      Live
                    </span>
                  </div>
                  <p className="truncate text-[13px] font-semibold text-white/60">{place}</p>
                </div>
                {!infoOpen && (
                  <span className="flex items-center gap-2 text-[14px] font-extrabold text-text">
                    <User className="size-4 text-[#ffd166]" aria-hidden />
                    {flow.totals.people}
                    <Car className="size-4 text-[#5ec8ff]" aria-hidden />
                    {flow.totals.vehicles}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => setInfoOpen((o) => !o)}
                  aria-expanded={infoOpen}
                  aria-label={infoOpen ? "Collapse info" : "Expand info"}
                  className="pill glass !h-9 !w-9 !px-0"
                >
                  <ChevronDown className={`size-4 transition-transform duration-300 ${infoOpen ? "rotate-180" : ""}`} aria-hidden />
                </button>
              </header>

              <div
                inert={!infoOpen}
                className={`grid min-h-0 transition-[grid-template-rows,opacity] duration-300 ease-[var(--ease-out)] ${
                  infoOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                }`}
              >
                <div className="scrollbar-thin min-h-0 overflow-y-auto">
                  <p className="px-4 pb-2 text-[12px] font-semibold text-white/50">
                    from {plural(flow.clips, "live camera")}
                  </p>
                  <div className="grid grid-cols-2 gap-2 px-3 pb-3">
                    <Tile
                      icon={<User className="size-5 text-[#3a2600]" aria-hidden />}
                      orb="bg-[#ffd166]"
                      value={flow.totals.people}
                      label="people"
                    />
                    <Tile
                      icon={<Car className="size-5 text-[#00283d]" aria-hidden />}
                      orb="bg-[#5ec8ff]"
                      value={flow.totals.vehicles}
                      label="vehicles"
                    />
                    {air && (
                      <Tile
                        icon={<Wind className="size-5 text-[#04281a]" aria-hidden />}
                        orb="bg-ok"
                        value={air.eaqi}
                        tone={AIR_TONE[air.band]}
                        label={`Air ${air.band}`}
                        title={`PM2.5 ${air.pm25} · NO₂ ${air.no2} µg/m³`}
                      />
                    )}
                    {tide && (
                      <Tile
                        icon={<Waves className="size-5 text-[#00233d]" aria-hidden />}
                        orb="bg-[#4da3ff]"
                        value={`${tide.levelM.toFixed(1)} m`}
                        label={`Thames ${tide.trend}`}
                        title={`EA gauge at ${tide.station}`}
                      />
                    )}
                    {crime && crime.month && (
                      <Tile
                        wide
                        icon={<ShieldAlert className="size-5 text-[#3d0010]" aria-hidden />}
                        orb="bg-[#ff6b8a]"
                        value={crime.total}
                        label={`crimes within 600 m, ${monthLabel(crime.month)}`}
                        title={crime.streets.map((s) => `${s.name}: ${s.count}`).join("\n")}
                      />
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-2 px-4 pb-3">
                    <button
                      type="button"
                      onClick={geo.toggle}
                      aria-pressed={geo.active}
                      className={`pill pill-sm ${geo.active ? "pill-candy" : "glass"}`}
                    >
                      <LocateFixed className={`size-3.5 ${geo.active && !geo.fix ? "animate-pulse" : ""}`} aria-hidden />
                      {geo.active ? (geo.fix ? "Using my location" : "Locating…") : "Use my location"}
                    </button>
                    {geo.active && geo.fix && (
                      <span className="rounded-full bg-ok/15 px-2.5 py-1 text-[12px] font-bold text-ok">
                        GPS ±{geo.fix.accuracyM} m
                      </span>
                    )}
                    {updatedAt && (
                      <span className="ml-auto text-[12px] font-semibold text-white/45">
                        Updated {updatedAt.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    )}
                  </div>

                  {geo.error && (
                    <p className="mx-3 mb-3 rounded-2xl bg-warn/10 px-3 py-2 text-[12px] font-semibold text-warn">{geo.error}</p>
                  )}

                  <RoutePicker mapRef={mapRef} ready={ready} from={point} stations={stations} fitPadding={fitPadding} />
                </div>
              </div>
            </aside>

            {/* Bottom-left, above the chat button: the right edge stays clear for the Copilot panel. */}
            <div className="pointer-events-none absolute bottom-14 left-3 flex max-h-[calc(100%-5.5rem)] w-[min(320px,calc(100%-1.5rem))] flex-col items-start justify-end gap-2">
              {disruption && (
                <DisruptionCard
                  item={disruption}
                  pinned={disruptionPinned}
                  onClose={() => {
                    setDisruptionPinned(false);
                    setDisruptionId(null);
                  }}
                />
              )}
              {(liveCam || station || stop || (selectedCam && !liveCam)) && (
                <div className="scrollbar-thin pointer-events-auto flex min-h-0 w-full flex-col gap-2 overflow-y-auto">
                  {liveCam && <LiveCamCard cam={liveCam} onClose={() => setLiveCamId(null)} />}
                  {station && <StationCard station={station} onClose={() => setStationId(null)} />}
                  {stop && <BusStopPanel stop={stop} onClose={() => setStopId(null)} />}
                  {selectedCam && !liveCam && (
                    <CamCard
                      cam={selectedCam}
                      reading={readings.get(selectedCam.id)}
                      isYourFeed={selectedCam.id === activeCam?.id}
                      onClose={() => setSelectedId(null)}
                    />
                  )}
                </div>
              )}
              <MapLegend counts={counts} hidden={hidden} onToggle={toggleLayer} />
            </div>

            {failed && (
              <p className="glass absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full px-4 py-1.5 text-[12px] font-bold text-white/70">
                Live data unavailable
              </p>
            )}
          </>
        )
      }
    />
  );
}
