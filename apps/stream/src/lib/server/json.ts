import { gzipSync } from "node:zlib";

/** Browser may reuse for a day; a week of stale is fine while it revalidates. For geometry that never changes. */
export const CACHE_STATIC = "public, max-age=86400, stale-while-revalidate=604800";
/** Live data: reuse for a few seconds, then refetch in the background. */
export const cacheShort = (seconds: number) => `public, max-age=${seconds}, stale-while-revalidate=${seconds * 4}`;

const MIN_GZIP_BYTES = 1024;
const gzipped = new WeakMap<object, Buffer>();

/**
 * `Response.json` plus Cache-Control and gzip. `next dev` does not compress, so a 1.2 MB geometry
 * reply would otherwise cross a phone tunnel uncompressed. Compressed bytes are memoised per body
 * object, so a cached body is gzipped once per process.
 */
export function json(body: unknown, request: Request, cacheControl: string, status = 200): Response {
  const text = JSON.stringify(body);
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "cache-control": cacheControl,
    vary: "Accept-Encoding",
  };
  const wantsGzip = /\bgzip\b/.test(request.headers.get("accept-encoding") ?? "");
  if (!wantsGzip || text.length < MIN_GZIP_BYTES) return new Response(text, { status, headers });
  let buf = typeof body === "object" && body ? gzipped.get(body) : undefined;
  if (!buf) {
    buf = gzipSync(text, { level: 6 });
    if (typeof body === "object" && body) gzipped.set(body, buf);
  }
  return new Response(new Uint8Array(buf), { status, headers: { ...headers, "content-encoding": "gzip" } });
}
