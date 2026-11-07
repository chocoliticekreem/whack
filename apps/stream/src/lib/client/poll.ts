"use client";

/**
 * One polling loop for every live feed on the client:
 * - a tick never starts while the previous one is still in flight (no pile-up behind a slow route);
 * - every request has a timeout;
 * - a failure (network, 5xx, 429) never reaches `apply`, so the last good value stays on screen,
 *   and the next try backs off (honouring Retry-After) instead of hammering the route;
 * - nothing runs while the tab is hidden, and showing it again catches up at once if a tick is due.
 */

export type FetchResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; data: unknown; retryAfterMs: number | null; error: string };

function retryAfter(header: string | null): number | null {
  if (!header) return null;
  const s = Number(header);
  if (Number.isFinite(s)) return s * 1000;
  const at = Date.parse(header);
  return Number.isFinite(at) ? Math.max(0, at - Date.now()) : null;
}

/** `fetch` + JSON with a timeout; never throws except when `signal` aborts. The body of an error reply is kept (it may say why). */
export async function fetchJson<T>(url: string, signal: AbortSignal, timeoutMs = 20_000): Promise<FetchResult<T>> {
  const timeout = AbortSignal.timeout(timeoutMs);
  const both = AbortSignal.any ? AbortSignal.any([signal, timeout]) : signal;
  try {
    const res = await fetch(url, { signal: both, cache: "no-store" });
    const data = (await res.json().catch(() => null)) as unknown;
    if (res.ok && data !== null) return { ok: true, status: res.status, data: data as T };
    const error = (data as { error?: string } | null)?.error ?? `HTTP ${res.status}`;
    return { ok: false, status: res.status, data, retryAfterMs: retryAfter(res.headers.get("retry-after")), error };
  } catch (e) {
    if (signal.aborted) throw e;
    return { ok: false, status: 0, data: null, retryAfterMs: null, error: timeout.aborted ? "timed out" : (e as Error).message };
  }
}

export interface Poller {
  stop(): void;
  /** Run now, cancelling a tick in flight (the inputs changed, e.g. the origin moved). */
  tick(): void;
}

/** What a run reports back: true/false for success, or a failed fetch so its status and Retry-After drive the backoff. */
export type RunResult = boolean | FetchResult<unknown> | void;

const MAX_BACKOFF_FACTOR = 8;

export function startPoller(run: (signal: AbortSignal) => Promise<RunResult>, everyMs: number): Poller {
  let ctrl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let failures = 0;
  let nextAt = 0;
  const hidden = () => typeof document !== "undefined" && document.hidden;

  const schedule = (ms: number) => {
    if (timer) clearTimeout(timer);
    nextAt = Date.now() + ms;
    timer = setTimeout(() => void go(false), ms);
  };

  const go = async (force: boolean) => {
    if (stopped) return;
    if (ctrl && !force) return;
    if (hidden() && !force) {
      nextAt = 0;
      return;
    }
    ctrl?.abort();
    const mine = new AbortController();
    ctrl = mine;
    let ok = false;
    let hintMs: number | null = null;
    try {
      const r = await run(mine.signal);
      if (typeof r === "object" && r !== null) {
        ok = r.ok;
        if (!r.ok) hintMs = r.retryAfterMs;
      } else ok = r !== false;
    } catch {
      if (mine.signal.aborted) return;
    }
    if (ctrl !== mine || stopped) return;
    ctrl = null;
    failures = ok ? 0 : failures + 1;
    const backoff = everyMs * Math.min(MAX_BACKOFF_FACTOR, 2 ** Math.max(0, failures - 1));
    schedule(ok ? everyMs : Math.max(backoff, hintMs ?? 0));
  };

  const onVisible = () => {
    if (!hidden() && !ctrl && Date.now() >= nextAt) void go(false);
  };
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisible);
  void go(false);

  return {
    stop: () => {
      stopped = true;
      ctrl?.abort();
      if (timer) clearTimeout(timer);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisible);
    },
    tick: () => void go(true),
  };
}
