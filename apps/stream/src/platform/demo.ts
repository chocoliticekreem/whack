"use client";

import { useEffect, useState } from "react";

/**
 * Demo/dev settings shared by the stream page and the 3D twin (same origin), synced across tabs.
 * Default is live: real weather, real London time, real crowds. Overrides exist only for demo testing.
 */

export type WeatherKind = "clear" | "cloudy" | "rain" | "fog" | "storm";
export type TimeOfDay = "day" | "dusk" | "night";

export interface LiveWeather {
  kind: WeatherKind;
  temperatureC: number;
  cloudCover: number;
  precipitationMm: number;
  windKph: number;
  isDay: boolean;
  visibilityM: number;
  at: number;
}

export interface DemoSettings {
  /** null = live from Open-Meteo. */
  weather: WeatherKind | null;
  /** null = live London time. */
  time: TimeOfDay | null;
  /** Multiplies simulated crowds (1 = mirror reality). */
  crowd: 1 | 3 | 10;
  /** Draw the inferred crowd field as simulated people and vehicles (an estimate, not real entities). */
  estimate: boolean;
  /** Draw illustrative river traffic (not live data). */
  boats: boolean;
}

export const LIVE_SETTINGS: DemoSettings = { weather: null, time: null, crowd: 1, estimate: false, boats: false };

const KEY = "classroom-47:demo";
const CHANNEL = "classroom-47-demo";

function read(): DemoSettings {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...LIVE_SETTINGS, ...(JSON.parse(raw) as Partial<DemoSettings>) } : LIVE_SETTINGS;
  } catch {
    return LIVE_SETTINGS;
  }
}

export function isLive(s: DemoSettings) {
  return s.weather === null && s.time === null && s.crowd === 1 && !s.estimate && !s.boats;
}

export function setDemoSettings(next: DemoSettings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Private mode: settings still apply to this tab via the channel.
  }
  if (typeof BroadcastChannel !== "undefined") new BroadcastChannel(CHANNEL).postMessage(next);
  window.dispatchEvent(new CustomEvent<DemoSettings>(CHANNEL, { detail: next }));
}

export function useDemoSettings(): DemoSettings {
  // Lazy init is safe: every consumer is client-only (ssr: false).
  const [settings, setSettings] = useState<DemoSettings>(() =>
    typeof window === "undefined" ? LIVE_SETTINGS : read(),
  );
  useEffect(() => {
    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(CHANNEL) : null;
    const onRemote = (e: MessageEvent<DemoSettings>) => setSettings(e.data);
    const onLocal = (e: Event) => setSettings((e as CustomEvent<DemoSettings>).detail);
    channel?.addEventListener("message", onRemote);
    window.addEventListener(CHANNEL, onLocal);
    return () => {
      channel?.close();
      window.removeEventListener(CHANNEL, onLocal);
    };
  }, []);
  return settings;
}

/** London time-of-day bucket from the real clock. */
export function liveTimeOfDay(now = new Date()): TimeOfDay {
  const hour = Number(now.toLocaleString("en-GB", { hour: "numeric", hour12: false, timeZone: "Europe/London" }));
  if (hour >= 7 && hour < 17) return "day";
  if (hour >= 17 && hour < 19) return "dusk";
  return "night";
}

/**
 * Real solar elevation/azimuth (degrees, azimuth clockwise from north) for the London clock.
 * Standard declination + hour-angle approximation; good to ~1° which is all a sky needs.
 */
export function sunAngles(point: { lat: number; lon: number }, now = new Date()) {
  const start = Date.UTC(now.getUTCFullYear(), 0, 0);
  const dayOfYear = (now.getTime() - start) / 86_400_000;
  const decl = (23.44 * Math.PI) / 180 * Math.sin(((2 * Math.PI) / 365) * (dayOfYear - 81));
  const solarHours = now.getUTCHours() + now.getUTCMinutes() / 60 + point.lon / 15;
  const hourAngle = ((solarHours - 12) * 15 * Math.PI) / 180;
  const lat = (point.lat * Math.PI) / 180;
  const sinEl = Math.sin(lat) * Math.sin(decl) + Math.cos(lat) * Math.cos(decl) * Math.cos(hourAngle);
  const elevation = Math.asin(sinEl);
  const azimuth =
    Math.atan2(Math.sin(hourAngle), Math.cos(hourAngle) * Math.sin(lat) - Math.tan(decl) * Math.cos(lat)) +
    Math.PI;
  return { elevation: (elevation * 180) / Math.PI, azimuth: ((azimuth * 180) / Math.PI) % 360 };
}

export interface Conditions {
  weather: WeatherKind;
  time: TimeOfDay;
  crowd: number;
  live: LiveWeather | null;
  /** True when nothing is overridden. */
  isLive: boolean;
}

/** Effective conditions for the twin: live weather/time unless a dev override is set. */
export function useConditions(point: { lat: number; lon: number }): Conditions {
  const settings = useDemoSettings();
  const [live, setLive] = useState<LiveWeather | null>(null);
  const { lat, lon } = point;
  useEffect(() => {
    const ctrl = new AbortController();
    const load = () =>
      fetch(`/api/around/weather?lat=${lat}&lon=${lon}`, { signal: ctrl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((w: LiveWeather | null) => w && setLive(w))
        .catch(() => {});
    load();
    const id = setInterval(load, 5 * 60_000);
    return () => {
      ctrl.abort();
      clearInterval(id);
    };
  }, [lat, lon]);
  return {
    weather: settings.weather ?? live?.kind ?? "clear",
    time: settings.time ?? liveTimeOfDay(),
    crowd: settings.crowd,
    live,
    isLive: isLive(settings),
  };
}
