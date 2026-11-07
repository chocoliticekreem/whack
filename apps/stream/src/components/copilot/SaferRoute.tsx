"use client";

import { useEffect, useState } from "react";
import { Route } from "lucide-react";
import type { StreamLocation } from "@/lib/types";
import type { AdviceLevel } from "@/platform/agent/types";
import { publish } from "@/platform/bus";
import { DESTINATIONS, type RouteResponse } from "@/platform/route/types";

const ROUTE_TIMEOUT_MS = 10_000;
const ARRIVED_M = 30;

/** Where the calmer route goes when the advisor says the spot is busy: the next stop of the walk. */
function nextStop(location: StreamLocation) {
  return location.id === "tower-bridge" ? "Tower Hill station" : "Tower Bridge";
}

interface SaferRouteProps {
  location: StreamLocation;
  level: AdviceLevel | null;
}

/** Destination chips and the calmer route's summary; publishes `route.suggested` for the twin and the map. */
export function SaferRoute({ location, level }: SaferRouteProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [result, setResult] = useState<{ to: string; route: RouteResponse | null } | null>(null);
  const crowded = level === "busy" || level === "danger";
  const to = picked ?? (crowded ? nextStop(location) : null);
  const route = to && result?.to === to ? result.route : null;
  const loading = to !== null && result?.to !== to;

  useEffect(() => {
    if (!to) return;
    const ctrl = new AbortController();
    let cancelled = false;
    // Why: a hung or failed request must end in "No route found", never an endless "Working out…".
    const signal = AbortSignal.any([ctrl.signal, AbortSignal.timeout(ROUTE_TIMEOUT_MS)]);
    fetch(`/api/route?from=${location.lat},${location.lon}&to=${encodeURIComponent(to)}&mode=calm`, { signal })
      .then((r) => (r.ok ? (r.json() as Promise<RouteResponse>) : null))
      .catch(() => null)
      .then((r) => {
        if (cancelled) return;
        setResult({ to, route: r });
        if (!r) return;
        publish({
          type: "route.suggested",
          to: r.to.name,
          xz: r.calmest.xz,
          polyline: r.calmest.polyline,
          lengthM: r.calmest.lengthM,
          etaMin: r.calmest.etaMin,
          extraMin: r.extraMin,
          lessCrowd: r.lessCrowd,
        });
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [location, to]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1.5">
        {DESTINATIONS.map((d) => {
          const active = to === d.name;
          return (
            <button
              key={d.name}
              type="button"
              onClick={() => setPicked((cur) => (cur === d.name ? null : d.name))}
              aria-pressed={active}
              className={`rounded-md border px-2.5 py-1 text-[12px] font-medium transition-colors ${
                active ? "border-ember/60 bg-ember-soft text-text" : "border-line text-muted hover:border-line-strong hover:text-text"
              }`}
            >
              {d.name}
            </button>
          );
        })}
      </div>
      {to ? (
        route && route.calmest.lengthM < ARRIVED_M ? (
          <p className="text-[12px] text-dim">You&apos;re here</p>
        ) : route ? (
          <div className="flex items-start gap-2.5 rounded-lg border border-ember/30 bg-ember-soft px-3 py-2.5">
            <Route className="mt-0.5 size-4 shrink-0 text-ember" aria-hidden />
            <p
              className="text-[13px] leading-5 text-text"
              title={`${route.calmest.lengthM} m${route.extraMin > 0 ? ` · +${route.extraMin} min` : ""}${route.avoids.length > 0 ? ` · avoids ${route.avoids.join(", ")}` : ""}`}
            >
              <span className="font-semibold">{route.to.name}</span> · {Math.round(route.calmest.etaMin)} min
              {route.lessCrowd > 0 ? ` · −${Math.round(route.lessCrowd * 100)}% crowd` : ""}
            </p>
          </div>
        ) : (
          <p className="text-[12px] text-dim">{loading ? "Routing…" : "No route"}</p>
        )
      ) : (
        <p className="text-[12px] text-dim" title="The advisor picks one when it gets busy">Pick a spot</p>
      )}
    </div>
  );
}
