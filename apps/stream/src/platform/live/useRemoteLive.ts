"use client";

import { useEffect, useState } from "react";
import { DEFAULT_SESSION, type LiveState } from "./types";

export type SyncStatus = "connecting" | "connected" | "offline";

/** Subscribes to the server relay: the latest state from any device in the session. */
export function useRemoteLive(session = DEFAULT_SESSION) {
  const [state, setState] = useState<LiveState | null>(null);
  const [status, setStatus] = useState<SyncStatus>("connecting");

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource(`/api/live/stream?session=${encodeURIComponent(session)}`);
    es.onopen = () => setStatus("connected");
    es.onerror = () => setStatus("offline");
    es.onmessage = (m) => {
      try {
        setState(JSON.parse(m.data) as LiveState);
      } catch {
        // malformed frame; the next one replaces it
      }
    };
    return () => es.close();
  }, [session]);

  return { state, status };
}

export function sessionFromUrl() {
  if (typeof window === "undefined") return DEFAULT_SESSION;
  return new URLSearchParams(window.location.search).get("session") ?? DEFAULT_SESSION;
}

export function postLiveState(state: LiveState) {
  return fetch("/api/live/state", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(state),
    keepalive: true,
  }).then(
    (r) => r.ok,
    () => false,
  );
}

export function ageLabel(ts: number, now: number) {
  const s = Math.max(0, Math.round((now - ts) / 1000));
  return s < 60 ? `${s} s ago` : `${Math.floor(s / 60)} min ago`;
}
