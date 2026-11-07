"use client";

import { useEffect, useMemo, useState, type RefObject } from "react";
import type { Map as MapLibreMap, PaddingOptions } from "maplibre-gl";
import { Route } from "lucide-react";
import { publish } from "@/platform/bus";
import { distanceM } from "@/platform/around/geo";
import type { AroundStation } from "@/platform/around/types";
import type { RouteDestination, RouteResponse } from "@/platform/route/types";
import { insideGeometryBounds, projection, snapOrigin } from "@/twin/config";
import { lineFeature, setData, type Point } from "./mapLayers";

// The planner's street graph covers ~650 m around the streamer; keep a margin for snapping to it.
const REACH_M = 600;
const MIN_TRIP_M = 80;
const MAX_DESTINATIONS = 6;
// A picked destination is dropped once the streamer is this far from it (moved or switched location).
const DROP_M = 900;

interface RoutePickerProps {
  mapRef: RefObject<MapLibreMap | null>;
  ready: boolean;
  from: Point;
  /** Real stations near the streamer (TfL), offered as destinations. */
  stations: AroundStation[];
  fitPadding: () => PaddingOptions;
}

/** Stations within walking reach of wherever the streamer is, nearest first. */
function nearbyDestinations(from: Point, stations: AroundStation[]): RouteDestination[] {
  const { toLocal } = projection(snapOrigin(from.lat, from.lon));
  return stations
    .map((s) => ({ name: s.name, lat: s.lat, lon: s.lon, d: distanceM(from.lat, from.lon, s.lat, s.lon) }))
    .filter((s) => s.d >= MIN_TRIP_M && s.d <= REACH_M && insideGeometryBounds(...toLocal(s.lat, s.lon)))
    .sort((a, b) => a.d - b.d)
    // TfL lists one stop per mode, e.g. Bond Street (Underground) and Bond Street (Elizabeth line).
    .filter((s, i, all) => all.findIndex((o) => o.name === s.name) === i)
    .slice(0, MAX_DESTINATIONS)
    .map(({ name, lat, lon }) => ({ name, lat, lon }));
}

/** Destination chips in the info drawer; draws the fastest (grey dashed) and calmer (ember) routes from the streamer. */
export function RoutePicker({ mapRef, ready, from, stations, fitPadding }: RoutePickerProps) {
  const destinations = useMemo(() => nearbyDestinations(from, stations), [from, stations]);
  const [picked, setPicked] = useState<RouteDestination | null>(null);
  const to = picked && distanceM(from.lat, from.lon, picked.lat, picked.lon) <= DROP_M ? picked : null;
  const [fetched, setRoute] = useState<RouteResponse | null>(null);
  const route = to ? fetched : null;
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function pick(d: RouteDestination) {
    setPicked((cur) => (cur?.name === d.name ? null : d));
    setRoute(null);
    setError(null);
    setLoading(true);
  }

  useEffect(() => {
    if (!to) return;
    const ctrl = new AbortController();
    const q = `from=${from.lat},${from.lon}&to=${to.lat},${to.lon}&toName=${encodeURIComponent(to.name)}&mode=calm`;
    fetch(`/api/route?${q}`, { signal: ctrl.signal })
      .then(async (r) => {
        const data = (await r.json()) as RouteResponse | { error: string };
        if (ctrl.signal.aborted) return;
        if (!r.ok || "error" in data) {
          setRoute(null);
          setError("error" in data ? data.error : "Route unavailable");
        } else {
          setRoute(data);
          setError(null);
          publish({
            type: "route.suggested",
            to: data.to.name,
            xz: data.calmest.xz,
            polyline: data.calmest.polyline,
            lengthM: data.calmest.lengthM,
            etaMin: data.calmest.etaMin,
            extraMin: data.extraMin,
            lessCrowd: data.lessCrowd,
          });
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [to, from.lat, from.lon]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    setData(map, "around-route-fast", lineFeature(route?.fastest.polyline ?? []));
    setData(map, "around-route-calm", lineFeature(route?.calmest.polyline ?? []));
    if (route) {
      const pts = [...route.fastest.polyline, ...route.calmest.polyline];
      const lats = pts.map((p) => p[0]);
      const lons = pts.map((p) => p[1]);
      map.fitBounds(
        [
          [Math.min(...lons), Math.min(...lats)],
          [Math.max(...lons), Math.max(...lats)],
        ],
        { padding: fitPadding(), duration: 900, maxZoom: 17 },
      );
    }
    // Why: fit once per new route, not again when the panel resizes the padding.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapRef, ready, route]);

  return (
    <div className="flex flex-col gap-3 px-4 pb-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 flex items-center gap-1.5 text-[14px] font-extrabold text-text">
          <Route className="size-4 text-candy-orange" aria-hidden />
          Walk to
        </span>
        {destinations.length === 0 && !to && (
          <span className="text-[12px] font-semibold text-white/45">No stations within {REACH_M} m yet</span>
        )}
        {(to && !destinations.some((d) => d.name === to.name) ? [to, ...destinations] : destinations).map((d) => {
          const active = to?.name === d.name;
          return (
            <button
              key={d.name}
              type="button"
              data-route-dest
              onClick={() => pick(d)}
              aria-pressed={active}
              className={`pill pill-sm ${active ? "pill-candy" : ""}`}
            >
              {d.name}
            </button>
          );
        })}
      </div>
      {to && loading && !route && <p className="text-[13px] font-semibold text-white/60">Planning on live crowd density…</p>}
      {to && error && <p className="text-[13px] font-semibold text-warn">{error}</p>}
      {route && (
        <div className="flex flex-col gap-2 rounded-[22px] bg-white/[0.06] px-3.5 py-3 text-[13px] font-semibold shadow-[inset_0_2px_3px_rgb(255_255_255/0.1)]">
          <p className="flex items-center gap-2.5 text-white/60">
            <span className="h-0 w-5 shrink-0 border-t-2 border-dashed border-[#9a99a6]" aria-hidden />
            Fastest: {route.fastest.lengthM} m · {Math.round(route.fastest.etaMin)} min · passes ~{Math.round(route.fastest.exposure)}{" "}
            people
          </p>
          <p className="flex items-center gap-2.5 text-text">
            <span className="h-0 w-5 shrink-0 border-t-[3px] border-ember" aria-hidden />
            Calmer: {route.calmest.lengthM} m · {Math.round(route.calmest.etaMin)} min
            {route.extraMin > 0 ? ` (+${route.extraMin} min)` : ""} ·{" "}
            {route.lessCrowd > 0 ? `${Math.round(route.lessCrowd * 100)}% less crowd` : "same crowd either way"}
          </p>
          {route.avoids.length > 0 && (
            <p className="text-[12px] text-white/45">Steers around {route.avoids.join(", ")}</p>
          )}
        </div>
      )}
    </div>
  );
}
