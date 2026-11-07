import type { LiveState } from "./types";

type Listener = (s: LiveState) => void;

interface Relay {
  latest: Map<string, LiveState>;
  listeners: Map<string, Set<Listener>>;
}

// Why: lives on globalThis so dev HMR and the two route modules share one relay.
const relay: Relay = ((globalThis as { __liveRelay?: Relay }).__liveRelay ??= {
  latest: new Map(),
  listeners: new Map(),
});

export function getLatest(session: string) {
  return relay.latest.get(session) ?? null;
}

export function setLatest(state: LiveState) {
  relay.latest.set(state.session, state);
  relay.listeners.get(state.session)?.forEach((fn) => fn(state));
}

export function listen(session: string, fn: Listener) {
  const set = relay.listeners.get(session) ?? new Set();
  set.add(fn);
  relay.listeners.set(session, set);
  return () => {
    set.delete(fn);
  };
}
