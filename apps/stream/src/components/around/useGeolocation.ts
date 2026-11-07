"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { distanceM } from "@/platform/around/geo";
import type { Point } from "./mapLayers";

const REQUERY_MOVE_M = 75;

export interface Fix extends Point {
  accuracyM: number;
}

export function useGeolocation() {
  const [active, setActive] = useState(false);
  const [fix, setFix] = useState<Fix | null>(null);
  const [queryPoint, setQueryPoint] = useState<Point | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastQueryRef = useRef<Point | null>(null);

  const supported = typeof navigator !== "undefined" && "geolocation" in navigator;

  useEffect(() => {
    if (!active || !supported) return;
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        const next = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracyM: Math.round(pos.coords.accuracy),
        };
        setFix(next);
        setError(null);
        const last = lastQueryRef.current;
        if (!last || distanceM(last.lat, last.lon, next.lat, next.lon) > REQUERY_MOVE_M) {
          lastQueryRef.current = { lat: next.lat, lon: next.lon };
          setQueryPoint({ lat: next.lat, lon: next.lon });
        }
      },
      (err) => {
        setError(
          err.code === err.PERMISSION_DENIED
            ? "Location permission denied. Allow it in your browser to map your real surroundings."
            : "Could not get a location fix.",
        );
      },
      { enableHighAccuracy: true, maximumAge: 5_000, timeout: 20_000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, [active, supported]);

  const toggle = useCallback(() => {
    if (!supported) {
      setError("Location is not available in this browser.");
      return;
    }
    setActive((on) => {
      if (on) {
        setFix(null);
        setQueryPoint(null);
        setError(null);
        lastQueryRef.current = null;
      }
      return !on;
    });
  }, [supported]);

  return { active, fix, queryPoint, error, toggle };
}
