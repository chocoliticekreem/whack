import type { SignalMessage, SignalRole, SignalType } from "./types";

export function postSignal(msg: {
  session: string;
  from: SignalRole;
  viewerId: string;
  type: SignalType;
  payload?: unknown;
}) {
  return fetch("/api/live/signal", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(msg),
    keepalive: true,
  }).then(
    (r) => r.ok,
    () => false,
  );
}

export function openSignalStream(
  session: string,
  role: SignalRole,
  viewerId: string,
  handlers: { onMessage: (m: SignalMessage) => void; onOpen?: () => void; onError?: () => void },
) {
  const q = new URLSearchParams({ session, role, viewerId });
  const es = new EventSource(`/api/live/signal/stream?${q}`);
  es.onopen = () => handlers.onOpen?.();
  es.onerror = () => handlers.onError?.();
  es.onmessage = (e) => {
    try {
      handlers.onMessage(JSON.parse(e.data) as SignalMessage);
    } catch {
      // malformed frame; ignore
    }
  };
  return () => es.close();
}

export function newViewerId() {
  return `v-${Math.random().toString(36).slice(2, 10)}`;
}
