"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { StreamLocation } from "@/lib/types";
import { distanceM } from "@/platform/around/geo";
import { publish } from "@/platform/bus";
import { gpsLocation, labelFor } from "./nearest";
import { PUBLISH_MS, type LiveState } from "./types";
import { ageLabel, postLiveState, sessionFromUrl, useRemoteLive } from "./useRemoteLive";
import { useStreamerGps } from "./useStreamerGps";

const MOVE_M = 8;
// One id per tab so a page ignores its own relayed echoes.
const CLIENT_ID = `web-${Math.random().toString(36).slice(2, 8)}`;

export type PositionSource = "gps" | "phone" | "demo";

interface Args {
  live: boolean;
  viewers: number;
  /** The picker's location, used only when no GPS (local or relayed) is available. */
  demo: StreamLocation;
}

/**
 * Ties the real position into the stream page. Phone: GPS drives the location and is relayed
 * every 2 s while live. Laptop: follows the phone's relayed state when it is not streaming itself.
 */
export function useLiveLink({ live, viewers, demo }: Args) {
  const gps = useStreamerGps();
  const session = useMemo(() => sessionFromUrl(), []);
  const remote = useRemoteLive(session);
  const [now, setNow] = useState(() => Date.now());

  // GPS is the default source of truth: start it as soon as the page loads, not only when live.
  useEffect(() => {
    if (gps.status === "off") gps.start();
  }, [gps]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const fix = gps.fix;
  const peer = remote.state && remote.state.streamer !== CLIENT_ID ? remote.state : null;
  const followPhone = !fix && !live && !!peer && peer.live && now - peer.ts < 60_000;

  const source: PositionSource = fix ? "gps" : followPhone ? "phone" : "demo";

  // Keyed on primitives: every relay push is a new `peer` object and must not churn the location.
  const fixLat = fix?.lat, fixLon = fix?.lon, fixAcc = fix?.accuracyM;
  const peerLat = peer?.lat, peerLon = peer?.lon, peerAcc = peer?.accuracyM, peerLabel = peer?.label;
  const location = useMemo<StreamLocation>(() => {
    if (fixLat !== undefined && fixLon !== undefined)
      return gpsLocation(fixLat, fixLon, labelFor(fixLat, fixLon), `GPS ±${fixAcc} m`);
    if (followPhone && peerLat !== undefined && peerLon !== undefined)
      return gpsLocation(peerLat, peerLon, peerLabel ?? "Phone", `Phone ±${peerAcc} m`);
    return demo;
  }, [fixLat, fixLon, fixAcc, followPhone, peerLat, peerLon, peerAcc, peerLabel, demo]);

  // Re-broadcast on the in-browser bus only when the dot really moved.
  const lastPublished = useRef<{ lat: number; lon: number } | null>(null);
  useEffect(() => {
    if (source === "demo") return;
    const last = lastPublished.current;
    if (last && distanceM(last.lat, last.lon, location.lat, location.lon) < MOVE_M) return;
    lastPublished.current = { lat: location.lat, lon: location.lon };
    publish({ type: "stream.location", location });
  }, [source, location]);

  const latest = useRef({ live, viewers, fix, location });
  useEffect(() => {
    latest.current = { live, viewers, fix, location };
  });
  const [relayOk, setRelayOk] = useState<boolean | null>(null);
  useEffect(() => {
    if (!live || !fix) return;
    const tick = async () => {
      const cur = latest.current;
      if (!cur.fix) return;
      const state: LiveState = {
        streamer: CLIENT_ID,
        session,
        lat: cur.fix.lat,
        lon: cur.fix.lon,
        accuracyM: cur.fix.accuracyM,
        heading: cur.fix.heading,
        speed: cur.fix.speed,
        viewers: cur.viewers,
        live: cur.live,
        label: cur.location.name,
        ts: Date.now(),
      };
      setRelayOk(await postLiveState(state));
    };
    void tick();
    const id = setInterval(() => void tick(), PUBLISH_MS);
    return () => clearInterval(id);
    // Why: only (re)start the loop when streaming begins or the first fix lands; ticks read the ref.
  }, [live, !!fix, session]); // eslint-disable-line react-hooks/exhaustive-deps

  // Tell the relay the stream ended so followers stop tracking a stale dot.
  const wasLive = useRef(false);
  useEffect(() => {
    if (wasLive.current && !live && gps.fix) {
      void postLiveState({
        streamer: CLIENT_ID,
        session,
        lat: gps.fix.lat,
        lon: gps.fix.lon,
        accuracyM: gps.fix.accuracyM,
        heading: gps.fix.heading,
        speed: gps.fix.speed,
        viewers: 0,
        live: false,
        label: location.name,
        ts: Date.now(),
      });
    }
    wasLive.current = live;
  }, [live, gps.fix, session, location.name]);

  const badge =
    source === "gps" && fix
      ? `Phone GPS · ±${fix.accuracyM} m · ${ageLabel(fix.at, now)}`
      : source === "phone" && peer
        ? `Phone GPS · ±${peer.accuracyM} m · ${ageLabel(peer.ts, now)}`
        : gps.status === "searching"
          ? "GPS · searching…"
          : gps.status === "denied"
            ? "GPS denied · demo location"
            : "Demo location";

  const sync =
    remote.status === "connected"
      ? live && fix
        ? relayOk === false
          ? "Sync: relay error"
          : "Sync: connected"
        : followPhone
          ? "Sync: following phone"
          : "Sync: connected"
      : remote.status === "offline"
        ? "Sync: offline"
        : "Sync: connecting…";

  return {
    location,
    source,
    badge,
    sync,
    gps,
    peer,
    followPhone,
    viewers: followPhone && peer ? peer.viewers : viewers,
  };
}
