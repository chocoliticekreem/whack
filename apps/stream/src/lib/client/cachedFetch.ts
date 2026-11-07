"use client";

/**
 * Cache API wrapper for the big static twin payloads: a hit is returned at once, and the entry is
 * refreshed in the background so the next load is both instant and current. Bump VERSION when the
 * payload shape changes. Falls back to a plain fetch where the Cache API is unavailable (http on a
 * phone, private mode).
 */

const VERSION = "twin-v2";

async function openCache(): Promise<Cache | null> {
  try {
    if (typeof caches === "undefined") return null;
    return await caches.open(VERSION);
  } catch {
    return null;
  }
}

const refreshing = new Set<string>();

async function refresh(cache: Cache, url: string) {
  if (refreshing.has(url)) return;
  refreshing.add(url);
  try {
    const res = await fetch(url, { cache: "no-cache" });
    if (res.ok) await cache.put(url, res);
  } catch {
    // offline: the cached copy stays
  } finally {
    refreshing.delete(url);
  }
}

export async function cachedFetch(url: string, signal?: AbortSignal): Promise<Response> {
  const cache = await openCache();
  if (!cache) return fetch(url, { signal });
  const hit = await cache.match(url);
  if (hit) {
    void refresh(cache, url);
    return hit;
  }
  const res = await fetch(url, { signal });
  if (res.ok) void cache.put(url, res.clone());
  return res;
}

/** Fills the cache without reading the body, so a later `cachedFetch` of the same URL is instant. */
export async function prefetch(url: string) {
  const cache = await openCache();
  if (!cache) return;
  if (await cache.match(url)) return;
  await refresh(cache, url);
}
