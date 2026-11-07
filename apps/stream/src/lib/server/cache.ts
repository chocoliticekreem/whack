import { gunzipSync, gzipSync } from "node:zlib";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Tiny stale-while-revalidate cache: memory first, then a gzipped JSON file under `.cache/` so a
 * dev-server restart serves the last good value instantly. Expired entries are returned at once
 * while one background refresh runs; a loader failure keeps the stale value (stale-while-error)
 * and backs the key off, so a rate-limited upstream is not hammered by every request.
 */

// Vercel functions can only write to /tmp; locally keep the cache next to the app.
const DIR = process.env.VERCEL ? join("/tmp", "emfol-cache") : join(process.cwd(), ".cache");
const HOUR = 3_600_000;
export const TTL = { minute: 60_000, hour: HOUR, day: 24 * HOUR, week: 7 * 24 * HOUR } as const;

interface Entry<T> {
  at: number;
  body: T;
}

/** A failed upstream call. `retryAfterMs` comes from a 429/503 `Retry-After` header. */
export class UpstreamError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterMs: number | null = null,
  ) {
    super(message);
  }
}

const BACKOFF_BASE_MS = 5_000;
const BACKOFF_MAX_MS = 5 * 60_000;

interface Backoff {
  until: number;
  failures: number;
}

const memory = new Map<string, Entry<unknown>>();
const inflight = new Map<string, Promise<unknown>>();
const backoff = new Map<string, Backoff>();

function noteFailure(key: string, e: unknown) {
  const failures = (backoff.get(key)?.failures ?? 0) + 1;
  const exp = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** (failures - 1));
  const hinted = e instanceof UpstreamError && e.retryAfterMs ? Math.min(BACKOFF_MAX_MS, e.retryAfterMs) : 0;
  backoff.set(key, { until: Date.now() + Math.max(exp, hinted), failures });
}

const backoffLeftMs = (key: string) => (backoff.get(key)?.until ?? 0) - Date.now();

const fileFor = (key: string) => join(DIR, `${key.replace(/[^a-z0-9._-]/gi, "_")}.json.gz`);

function readFile<T>(key: string): Entry<T> | null {
  try {
    return JSON.parse(gunzipSync(readFileSync(fileFor(key))).toString("utf8")) as Entry<T>;
  } catch {
    return null;
  }
}

function writeFile(key: string, entry: Entry<unknown>) {
  try {
    mkdirSync(DIR, { recursive: true });
    const tmp = `${fileFor(key)}.${process.pid}.tmp`;
    writeFileSync(tmp, gzipSync(JSON.stringify(entry), { level: 4 }));
    renameSync(tmp, fileFor(key));
  } catch (e) {
    console.warn(`[cache] could not persist ${key}: ${(e as Error).message}`);
  }
}

function refresh<T>(key: string, loader: () => Promise<T>, persist: boolean): Promise<T> {
  const running = inflight.get(key) as Promise<T> | undefined;
  if (running) return running;
  const wait = backoffLeftMs(key);
  if (wait > 0) return Promise.reject(new Error(`${key} is backing off for ${Math.ceil(wait / 1000)}s`));
  const p = loader()
    .then((body) => {
      const entry = { at: Date.now(), body };
      memory.set(key, entry);
      backoff.delete(key);
      if (persist) writeFile(key, entry);
      return body;
    })
    .catch((e: unknown) => {
      noteFailure(key, e);
      throw e;
    })
    .finally(() => inflight.delete(key));
  inflight.set(key, p);
  return p;
}

export interface CachedOptions {
  /** How long a value is fresh. After this it is still served, but a refresh starts in the background. */
  ttlMs: number;
  /** Write to `.cache/` so the value survives a server restart. Default true. */
  persist?: boolean;
  /** Serve a stale value no older than this; beyond it the caller waits for the loader (and still gets the stale value if the loader fails). Default: forever. */
  maxStaleMs?: number;
}

export async function cached<T>(key: string, opts: CachedOptions, loader: () => Promise<T>): Promise<T> {
  const persist = opts.persist !== false;
  let entry = memory.get(key) as Entry<T> | undefined;
  if (!entry && persist) {
    const fromDisk = readFile<T>(key);
    if (fromDisk) {
      memory.set(key, fromDisk);
      entry = fromDisk;
    }
  }
  const age = entry ? Date.now() - entry.at : Infinity;
  if (entry && age < opts.ttlMs) return entry.body;
  if (entry && age < (opts.maxStaleMs ?? Infinity)) {
    if (backoffLeftMs(key) > 0) return entry.body;
    void refresh(key, loader, persist).catch((e: unknown) => {
      console.warn(`[cache] refresh of ${key} failed, keeping stale: ${(e as Error).message}`);
    });
    return entry.body;
  }
  if (!entry) return refresh(key, loader, persist);
  if (backoffLeftMs(key) > 0) return entry.body;
  try {
    return await refresh(key, loader, persist);
  } catch (e) {
    console.warn(`[cache] ${key} failed, serving value from ${Math.round(age / 1000)}s ago: ${(e as Error).message}`);
    return entry.body;
  }
}
