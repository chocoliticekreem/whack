import { UpstreamError } from "./cache";

const TFL = "https://api.tfl.gov.uk";

function retryAfterMs(header: string | null): number | null {
  if (!header) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds)) return seconds * 1000;
  const at = Date.parse(header);
  return Number.isFinite(at) ? Math.max(0, at - Date.now()) : null;
}

/**
 * GET a TfL Unified API path. Wrap calls in `cached()`: TfL allows ~50 anonymous requests a minute,
 * and a 429 carries `Retry-After`, which the cache uses to back the key off while serving the last good value.
 */
function withAppKey(path: string): string {
  const key = process.env.TFL_APP_KEY?.trim();
  if (!key) return path;
  return `${path}${path.includes("?") ? "&" : "?"}app_key=${encodeURIComponent(key)}`;
}

export async function tfl<T>(path: string, timeoutMs = 8000): Promise<T> {
  const res = await fetch(`${TFL}${withAppKey(path)}`, { signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) {
    throw new UpstreamError(
      `TfL ${path.split("?")[0]} failed: HTTP ${res.status}`,
      res.status,
      retryAfterMs(res.headers.get("retry-after")),
    );
  }
  return (await res.json()) as T;
}
