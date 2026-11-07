"use client";

import { create } from "zustand";
import { useEffect } from "react";
import type { AroundBusStop, AroundCam, AroundDisruption, CamClipTracks } from "@/platform/around/types";
import { subscribe } from "@/platform/bus";
import type { LiveState as LiveRelayState } from "@/platform/live/types";
import { currentOrigin, type TwinGeometry } from "../config";
import { useOrigin } from "../origin";

export interface LiveState {
  cams: AroundCam[];
  clips: Record<string, CamClipTracks>;
  stops: AroundBusStop[];
  disruptions: AroundDisruption[];
  roads: TwinGeometry["roads"];
  streamer: { lat: number; lon: number };
  /** Where the streamer position came from; "phone" is the cross-device relay (/api/live). */
  streamerSource: "demo" | "gps" | "phone";
  streamerHeading: number | null;
  streamerSpeed: number | null;
  streamerAccuracyM: number | null;
  streamerTs: number;
  updatedAt: number;
  /** Wall clock, ticked once a second for render-time countdowns; 0 before the feeds start. */
  now: number;
  screenMode: "video" | "frame" | null;
  gps: "off" | "on" | "error";
}

export const useLive = create<LiveState>(() => ({
  cams: [],
  clips: {},
  stops: [],
  disruptions: [],
  roads: [],
  streamer: { ...useOrigin.getState().origin },
  streamerSource: "demo",
  streamerHeading: null,
  streamerSpeed: null,
  streamerAccuracyM: null,
  streamerTs: 0,
  updatedAt: 0,
  now: 0,
  screenMode: null,
  gps: "off",
}));

let gpsWatch: number | null = null;
/** Auto-start happens once per page load; after that only the user turns GPS on or off. */
let gpsArmed = false;
/** A chosen demo position (?lat/lon, Dev "Jump to", the stream page's picker) wins until the user asks for GPS. */
let demoPinned = false;

function stopGpsWatch() {
  if (gpsWatch !== null) navigator.geolocation.clearWatch(gpsWatch);
  gpsWatch = null;
}

export function setDemoStreamer(lat: number, lon: number) {
  demoPinned = true;
  stopGpsWatch();
  useLive.setState({ streamer: { lat, lon }, streamerSource: "demo", streamerTs: Date.now(), gps: "off" });
}

/** Browser geolocation drives the streamer position until stopped. */
export function toggleGps() {
  if (gpsWatch !== null) {
    stopGpsWatch();
    useLive.setState({ gps: "off" });
    return;
  }
  if (!("geolocation" in navigator)) {
    useLive.setState({ gps: "error" });
    return;
  }
  demoPinned = false;
  gpsWatch = navigator.geolocation.watchPosition(
    (pos) => {
      if (demoPinned) return;
      useLive.setState({
        streamer: { lat: pos.coords.latitude, lon: pos.coords.longitude },
        streamerSource: "gps",
        streamerHeading: pos.coords.heading,
        streamerSpeed: pos.coords.speed,
        streamerAccuracyM: Math.round(pos.coords.accuracy),
        streamerTs: pos.timestamp,
        gps: "on",
      });
    },
    () => {
      stopGpsWatch();
      useLive.setState({ gps: "error" });
    },
    { enableHighAccuracy: true, maximumAge: 1000, timeout: 15_000 },
  );
  useLive.setState({ gps: "on" });
}

const CAMS_MS = 60_000;
const DISRUPTIONS_MS = 60_000;
const BUSES_MS = 30_000;
const TRACKS_MS = 15_000;

interface Poll {
  stop(): void;
  /** Re-fetch now (the origin moved). */
  tick(): void;
}

function poll<T>(url: () => string | null, everyMs: number, apply: (data: T) => void): Poll {
  let controller: AbortController | null = null;
  let stopped = false;
  const tick = async () => {
    const u = url();
    if (!u) return;
    controller?.abort();
    controller = new AbortController();
    try {
      const res = await fetch(u, { signal: controller.signal, cache: "no-store" });
      if (!res.ok) return;
      const data = (await res.json()) as T;
      if (!stopped) {
        apply(data);
        useLive.setState({ updatedAt: Date.now() });
      }
    } catch {
      // aborted or network error; the next tick retries
    }
  };
  void tick();
  const timer = setInterval(() => void tick(), everyMs);
  return {
    stop: () => {
      stopped = true;
      controller?.abort();
      clearInterval(timer);
    },
    tick: () => void tick(),
  };
}

const q = (radius: number) => {
  const o = currentOrigin();
  return `lat=${o.lat}&lon=${o.lon}&radius=${radius}`;
};

/** The phone's relayed GPS (another device) drives the streamer unless this tab's own GPS is on. */
function followRelay() {
  if (typeof EventSource === "undefined") return () => {};
  const session = new URLSearchParams(window.location.search).get("session") ?? "default";
  const es = new EventSource(`/api/live/stream?session=${encodeURIComponent(session)}`);
  es.onmessage = (m) => {
    let s: LiveRelayState;
    try {
      s = JSON.parse(m.data) as LiveRelayState;
    } catch {
      return;
    }
    if (!s.live || demoPinned || useLive.getState().gps === "on") return;
    useLive.setState({
      streamer: { lat: s.lat, lon: s.lon },
      streamerSource: "phone",
      streamerHeading: s.heading,
      streamerSpeed: s.speed,
      streamerAccuracyM: s.accuracyM,
      streamerTs: s.ts,
    });
  };
  return () => es.close();
}

let refs = 0;
let stopAll: (() => void) | null = null;

function start() {
  const areaPolls: Poll[] = [
    poll<{ cams: AroundCam[] }>(() => `/api/around/cams?${q(700)}`, CAMS_MS, (d) =>
      useLive.setState({ cams: d.cams ?? [] }),
    ),
    poll<{ items: AroundDisruption[] }>(() => `/api/around/disruptions?${q(700)}`, DISRUPTIONS_MS, (d) =>
      useLive.setState({ disruptions: d.items ?? [] }),
    ),
    poll<{ stops: AroundBusStop[] }>(() => `/api/around/buses?${q(400)}`, BUSES_MS, (d) =>
      useLive.setState({ stops: d.stops ?? [] }),
    ),
    poll<TwinGeometry>(
      () => {
        const o = currentOrigin();
        return `/api/twin/osm?lat=${o.lat}&lon=${o.lon}`;
      },
      10 * 60_000,
      (d) => useLive.setState({ roads: (d.roads ?? []).filter((r) => r.kind === "major" || r.kind === "minor") }),
    ),
  ];
  const stops: (() => void)[] = [
    ...areaPolls.map((p) => p.stop),
    useOrigin.subscribe((s, prev) => {
      if (s.version === prev.version) return;
      useLive.setState({ cams: [], clips: {}, stops: [], disruptions: [] });
      for (const p of areaPolls) p.tick();
    }),
    poll<{ clips: CamClipTracks[] }>(
      () => {
        const ids = useLive.getState().cams.map((c) => c.id);
        return ids.length ? `/api/around/tracks?ids=${ids.join(",")}` : null;
      },
      TRACKS_MS,
      (d) => {
        const clips: Record<string, CamClipTracks> = { ...useLive.getState().clips };
        for (const clip of d.clips ?? []) if (clip.tracks?.length) clips[clip.camId] = clip;
        useLive.setState({ clips });
      },
    ).stop,
    subscribe((e) => {
      if (e.type !== "stream.location") return;
      if (e.location.id !== "gps") {
        setDemoStreamer(e.location.lat, e.location.lon);
        return;
      }
      if (demoPinned || useLive.getState().gps === "on") return;
      useLive.setState({
        streamer: { lat: e.location.lat, lon: e.location.lon },
        streamerSource: "phone",
        streamerTs: Date.now(),
      });
    }),
    followRelay(),
  ];
  useLive.setState({ now: Date.now() });
  const clock = setInterval(() => useLive.setState({ now: Date.now() }), 1000);
  stops.push(() => clearInterval(clock));
  return () => stops.forEach((s) => s());
}

function pinFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  if (params.has("lat") && params.has("lon") && Number.isFinite(lat) && Number.isFinite(lon)) {
    setDemoStreamer(lat, lon);
  }
}

/** Starts the polling loops once, shared by every component that mounts it. */
export function useLiveFeeds() {
  useEffect(() => {
    if (!gpsArmed) {
      gpsArmed = true;
      pinFromUrl();
      // Real GPS by default, unless a demo position was asked for; the user owns the toggle from here on.
      if (!demoPinned && useLive.getState().gps === "off") toggleGps();
    }
    if (refs++ === 0) stopAll = start();
    return () => {
      if (--refs === 0) {
        stopAll?.();
        stopAll = null;
      }
    };
  }, []);
}

export function useNow() {
  return useLive((s) => s.now);
}
