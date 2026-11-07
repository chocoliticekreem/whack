/** WebRTC signalling between the phone (publisher) and laptop viewers, relayed by /api/live/signal. */
export type SignalRole = "phone" | "viewer";

export type SignalType = "join" | "offer" | "answer" | "ice" | "bye";

export interface SignalMessage {
  session: string;
  from: SignalRole;
  viewerId: string;
  type: SignalType;
  payload?: unknown;
  ts: number;
}

export const ICE_SERVERS: RTCIceServer[] = [{ urls: "stun:stun.l.google.com:19302" }];
