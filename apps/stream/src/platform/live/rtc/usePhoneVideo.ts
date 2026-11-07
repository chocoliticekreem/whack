"use client";

import { useEffect, useState } from "react";
import { newViewerId, openSignalStream, postSignal } from "./signalClient";
import { ICE_SERVERS, type SignalMessage } from "./types";

export type PhoneVideoStatus = "off" | "connecting" | "connected" | "failed";

export interface PhoneVideo {
  stream: MediaStream | null;
  status: PhoneVideoStatus;
  /** One-way delay estimate in seconds (RTT/2 + jitter buffer), null until stats arrive. */
  latencyS: number | null;
}

// Re-send "join" while no offer has arrived: the phone may not be listening yet.
const JOIN_RETRY_MS = 5000;
// After this long without a connection, the UI shows the fallback (joins keep going).
const FAIL_AFTER_MS = 20_000;
const STATS_MS = 1000;
const DISCONNECT_GRACE_MS = 4000;

/**
 * Laptop side: while enabled, join the session's phone over WebRTC and expose its video.
 * Reconnects on drop and keeps re-joining until the phone answers.
 */
export function usePhoneVideo(session: string, enabled: boolean): PhoneVideo {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [status, setStatus] = useState<PhoneVideoStatus>("connecting");
  const [latencyS, setLatencyS] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const viewerId = newViewerId();
    let pc: RTCPeerConnection | null = null;
    let joinTimer: ReturnType<typeof setInterval> | null = null;
    let failTimer: ReturnType<typeof setTimeout> | null = null;
    let statsTimer: ReturnType<typeof setInterval> | null = null;
    let lastJb = { delay: 0, count: 0 };
    let stopped = false;

    const stopJoining = () => {
      if (joinTimer) clearInterval(joinTimer);
      joinTimer = null;
      if (failTimer) clearTimeout(failTimer);
      failTimer = null;
    };

    const startJoining = () => {
      stopJoining();
      void postSignal({ session, from: "viewer", viewerId, type: "join" });
      joinTimer = setInterval(() => void postSignal({ session, from: "viewer", viewerId, type: "join" }), JOIN_RETRY_MS);
      failTimer = setTimeout(() => {
        if (!stopped) setStatus("failed");
      }, FAIL_AFTER_MS);
    };

    const teardownPeer = () => {
      if (statsTimer) clearInterval(statsTimer);
      statsTimer = null;
      pc?.close();
      pc = null;
      setStream(null);
      setLatencyS(null);
    };

    const sampleStats = async (conn: RTCPeerConnection) => {
      const report = await conn.getStats().catch(() => null);
      if (!report) return;
      let rtt: number | null = null;
      let jb: number | null = null;
      report.forEach((s) => {
        if (s.type === "candidate-pair" && s.nominated && typeof s.currentRoundTripTime === "number") {
          rtt = s.currentRoundTripTime;
        }
        if (s.type === "inbound-rtp" && s.kind === "video") {
          const delay = Number(s.jitterBufferDelay ?? 0);
          const count = Number(s.jitterBufferEmittedCount ?? 0);
          const dCount = count - lastJb.count;
          if (dCount > 0) jb = (delay - lastJb.delay) / dCount;
          lastJb = { delay, count };
        }
      });
      if (rtt !== null || jb !== null) setLatencyS((rtt ?? 0) / 2 + (jb ?? 0));
    };

    const onOffer = async (offer: RTCSessionDescriptionInit) => {
      teardownPeer();
      const conn = new RTCPeerConnection({ iceServers: ICE_SERVERS });
      pc = conn;
      lastJb = { delay: 0, count: 0 };
      conn.ontrack = (e) => {
        const s = e.streams[0] ?? new MediaStream([e.track]);
        setStream(s);
      };
      conn.onicecandidate = (e) => {
        if (e.candidate) void postSignal({ session, from: "viewer", viewerId, type: "ice", payload: e.candidate.toJSON() });
      };
      conn.onconnectionstatechange = () => {
        if (stopped || pc !== conn) return;
        const rejoin = () => {
          if (stopped || pc !== conn) return;
          teardownPeer();
          setStatus("connecting");
          startJoining();
        };
        if (conn.connectionState === "connected") {
          stopJoining();
          setStatus("connected");
          statsTimer = setInterval(() => void sampleStats(conn), STATS_MS);
        } else if (conn.connectionState === "failed" || conn.connectionState === "closed") {
          rejoin();
        } else if (conn.connectionState === "disconnected") {
          // Why: "disconnected" is often a blip on mobile networks; give ICE a moment to recover.
          setTimeout(() => {
            if (conn.connectionState === "disconnected") rejoin();
          }, DISCONNECT_GRACE_MS);
        }
      };
      try {
        await conn.setRemoteDescription(offer);
        const answer = await conn.createAnswer();
        await conn.setLocalDescription(answer);
        void postSignal({ session, from: "viewer", viewerId, type: "answer", payload: conn.localDescription });
      } catch {
        if (pc === conn) {
          teardownPeer();
          startJoining();
        }
      }
    };

    const onMessage = (m: SignalMessage) => {
      if (stopped) return;
      if (m.type === "offer") {
        void onOffer(m.payload as RTCSessionDescriptionInit);
      } else if (m.type === "ice" && pc) {
        void pc.addIceCandidate(m.payload as RTCIceCandidateInit).catch(() => {});
      } else if (m.type === "bye") {
        teardownPeer();
        setStatus("connecting");
        startJoining();
      }
    };

    const close = openSignalStream(session, "viewer", viewerId, {
      onMessage,
      onOpen: () => {
        if (!pc) startJoining();
      },
      onError: () => {
        if (!stopped && !pc) setStatus("failed");
      },
    });

    return () => {
      stopped = true;
      stopJoining();
      close();
      void postSignal({ session, from: "viewer", viewerId, type: "bye" });
      teardownPeer();
      setStatus("connecting");
    };
  }, [enabled, session]);

  return { stream: enabled ? stream : null, status: enabled ? status : "off", latencyS: enabled ? latencyS : null };
}
