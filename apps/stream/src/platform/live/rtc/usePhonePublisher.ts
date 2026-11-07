"use client";

import { useEffect, useState } from "react";
import { openSignalStream, postSignal } from "./signalClient";
import { ICE_SERVERS, type SignalMessage } from "./types";

const MAX_FRAMERATE = 24;
const MAX_HEIGHT = 720;
const MAX_BITRATE = 1_500_000;

async function capSender(sender: RTCRtpSender, track: MediaStreamTrack) {
  const params = sender.getParameters();
  if (!params.encodings?.length) params.encodings = [{}];
  const height = track.getSettings().height ?? MAX_HEIGHT;
  params.encodings[0].maxFramerate = MAX_FRAMERATE;
  params.encodings[0].maxBitrate = MAX_BITRATE;
  if (height > MAX_HEIGHT) params.encodings[0].scaleResolutionDownBy = height / MAX_HEIGHT;
  try {
    await sender.setParameters(params);
  } catch {
    // Some browsers reject parameter edits before the first negotiation; defaults are still fine.
  }
}

/**
 * Phone side: while the camera stream exists, answer every viewer "join" with a fresh
 * RTCPeerConnection carrying the video track (one connection per viewer, offer from the phone).
 */
export function usePhonePublisher(stream: MediaStream | null, session: string) {
  const [viewers, setViewers] = useState(0);

  useEffect(() => {
    if (!stream) return;
    const peers = new Map<string, RTCPeerConnection>();
    const update = () => setViewers(peers.size);

    const drop = (viewerId: string) => {
      peers.get(viewerId)?.close();
      peers.delete(viewerId);
      update();
    };

    const onJoin = async (viewerId: string) => {
      drop(viewerId);
      const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
      peers.set(viewerId, pc);
      update();
      pc.onicecandidate = (e) => {
        if (e.candidate) void postSignal({ session, from: "phone", viewerId, type: "ice", payload: e.candidate.toJSON() });
      };
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "failed" || pc.connectionState === "closed") drop(viewerId);
      };
      for (const track of stream.getVideoTracks()) {
        const sender = pc.addTrack(track, stream);
        void capSender(sender, track);
      }
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      void postSignal({ session, from: "phone", viewerId, type: "offer", payload: pc.localDescription });
    };

    const onMessage = (m: SignalMessage) => {
      if (m.type === "join") {
        void onJoin(m.viewerId);
        return;
      }
      const pc = peers.get(m.viewerId);
      if (!pc) return;
      if (m.type === "answer") {
        void pc.setRemoteDescription(m.payload as RTCSessionDescriptionInit).catch(() => drop(m.viewerId));
      } else if (m.type === "ice") {
        void pc.addIceCandidate(m.payload as RTCIceCandidateInit).catch(() => {});
      } else if (m.type === "bye") {
        drop(m.viewerId);
      }
    };

    const close = openSignalStream(session, "phone", "", { onMessage });
    return () => {
      close();
      for (const id of [...peers.keys()]) {
        void postSignal({ session, from: "phone", viewerId: id, type: "bye" });
        drop(id);
      }
    };
  }, [stream, session]);

  return viewers;
}
