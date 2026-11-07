"use client";

import { create } from "zustand";
import { useEffect } from "react";
import type { AroundBusStop, AroundCam, AroundDisruption, CamClipTracks, YoloStatus } from "@/platform/around/types";
import { fetchJson, startPoller, type Poller } from "@/lib/client/poll";
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
  /**
   * YOLO behind `clips`. "loading" until the first answer; "unavailable" means the service is down and
   * nothing was ever tracked, so camera counts are unknown (show that, not 0). "stale" keeps the last clips.
   */
  yolo: "loading" | "live" | "stale" | "unavailable";
  /** When `clips` last changed from a successful poll, epoch ms; 0 before the first. */
  clipsAt: number;
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
  yolo: "loading",
  clipsAt: 0,
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

/** Polls a JSON route; a failed poll keeps the last good value (see lib/client/poll). */
function poll<T>(url: () => string | null, everyMs: number, apply: (data: T) => void): Poller {
  return startPoller(async (signal) => {
    const u = url();
    if (!u) return true;
    const r = await fetchJson<T>(u, signal);
    if (!r.ok) return r;
    apply(r.data);
    useLive.setState({ updatedAt: Date.now() });
    return true;
  }, everyMs);
}

interface TracksReply {
  clips: CamClipTracks[];
  pending: string[];
  yolo: YoloStatus;
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
  const areaPolls: Poller[] = [
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
  const tracks = startPoller(async (signal) => {
    const ids = useLive.getState().cams.map((c) => c.id);
    if (!ids.length) return true;
    const r = await fetchJson<TracksReply>(`/api/around/tracks?ids=${ids.join(",")}`, signal, 45_000);
    // A 503 still says why ("unavailable"); anything else without a body leaves the last state alone.
    const body = r.data as Partial<TracksReply> | null;
    if (!body?.yolo) return r;
    const prev = useLive.getState();
    const clips: Record<string, CamClipTracks> = { ...prev.clips };
    let changed = false;
    for (const clip of body.clips ?? []) {
      if (!clip.tracks?.length || prev.clips[clip.camId]?.at === clip.at) continue;
      clips[clip.camId] = clip;
      changed = true;
    }
    const known = Object.keys(clips).length > 0;
    // Clips already on screen outlive a YOLO outage: they are the last thing the cameras saw.
    const yolo = body.yolo === "unavailable" && known ? "stale" : body.yolo;
    useLive.setState({
      ...(changed ? { clips, clipsAt: Date.now() } : {}),
      yolo,
      ...(r.ok ? { updatedAt: Date.now() } : {}),
    });
    return r.ok ? true : r;
  }, TRACKS_MS);
  // Cams arrive after the first tracks tick; ask for their tracks as soon as the list is known.
  const unsubCams = useLive.subscribe((s, prev) => {
    if (s.cams !== prev.cams && s.cams.length && !prev.cams.length) tracks.tick();
  });
  const stops: (() => void)[] = [
    ...areaPolls.map((p) => p.stop),
    unsubCams,
    useOrigin.subscribe((s, prev) => {
      if (s.version === prev.version) return;
      useLive.setState({ cams: [], clips: {}, stops: [], disruptions: [], yolo: "loading", clipsAt: 0 });
      for (const p of areaPolls) p.tick();
    }),
    tracks.stop,
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
