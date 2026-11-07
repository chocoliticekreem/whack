"use client";

import { useCallback, useEffect, useState } from "react";

export interface GpsFix {
  lat: number;
  lon: number;
  accuracyM: number;
  heading: number | null;
  speed: number | null;
  at: number;
}

export type GpsStatus = "off" | "searching" | "on" | "denied" | "unavailable";

/** Real device position with heading and speed; off until started. */
export function useStreamerGps() {
  const [wanted, setWanted] = useState(false);
  const [status, setStatus] = useState<GpsStatus>("off");
  const [fix, setFix] = useState<GpsFix | null>(null);

  useEffect(() => {
    if (!wanted) return;
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        const c = pos.coords;
        setFix({
          lat: c.latitude,
          lon: c.longitude,
          accuracyM: Math.round(c.accuracy),
          heading: c.heading !== null && Number.isFinite(c.heading) ? c.heading : null,
          speed: c.speed !== null && Number.isFinite(c.speed) ? c.speed : null,
          at: pos.timestamp,
        });
        setStatus("on");
      },
      (err) => {
        setFix(null);
        setStatus(err.code === err.PERMISSION_DENIED ? "denied" : "unavailable");
      },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 20_000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, [wanted]);

  const start = useCallback(() => {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      setStatus("unavailable");
      return;
    }
    setStatus("searching");
    setWanted(true);
  }, []);
  const stop = useCallback(() => {
    setWanted(false);
    setStatus("off");
    setFix(null);
  }, []);

  return { status, fix, start, stop };
}
