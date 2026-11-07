import type { SignalMessage, SignalRole } from "./types";

type Listener = (m: SignalMessage) => void;

interface Store {
  listeners: Map<string, Set<Listener>>;
  // Why: a viewer's "join" often lands before the phone's SSE is open; hold it briefly instead of dropping it.
  pending: Map<string, SignalMessage[]>;
}

const PENDING_TTL_MS = 15_000;

// Why: lives on globalThis so dev HMR and the two route modules share one store.
const store: Store = ((globalThis as { __liveSignals?: Store }).__liveSignals ??= {
  listeners: new Map(),
  pending: new Map(),
});

function targetKey(session: string, role: SignalRole, viewerId: string) {
  return role === "phone" ? `${session}|phone` : `${session}|viewer|${viewerId}`;
}

function prune(list: SignalMessage[], now: number) {
  return list.filter((m) => now - m.ts < PENDING_TTL_MS);
}

export function deliver(msg: SignalMessage) {
  const to: SignalRole = msg.from === "viewer" ? "phone" : "viewer";
  const key = targetKey(msg.session, to, msg.viewerId);
  const set = store.listeners.get(key);
  if (set && set.size > 0) {
    set.forEach((fn) => fn(msg));
    return;
  }
  const list = prune(store.pending.get(key) ?? [], msg.ts);
  list.push(msg);
  store.pending.set(key, list);
}

export function subscribe(session: string, role: SignalRole, viewerId: string, fn: Listener) {
  const key = targetKey(session, role, viewerId);
  const set = store.listeners.get(key) ?? new Set();
  set.add(fn);
  store.listeners.set(key, set);
  const held = prune(store.pending.get(key) ?? [], Date.now());
  store.pending.delete(key);
  held.forEach(fn);
  return () => {
    set.delete(fn);
    if (set.size === 0) store.listeners.delete(key);
  };
}
