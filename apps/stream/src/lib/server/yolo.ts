import type { CamClipTracks, CamReading, YoloStatus } from "@/platform/around/types";

export const JAMCAM_YOLO_URL = process.env.JAMCAM_YOLO_URL ?? "http://127.0.0.1:8047";

/** Beyond this a reading no longer describes the street now; the field model drops it at the same age. */
const STALE_MAX_MS = 30 * 60_000;
const BACKOFF_BASE_MS = 2_000;
const BACKOFF_MAX_MS = 15_000;

interface Blob {
  at: number;
  type: string;
  body: ArrayBuffer;
}

interface YoloState {
  readings: Map<string, CamReading>;
  clips: Map<string, CamClipTracks>;
  blobs: Map<string, Blob>;
  failures: number;
  downUntil: number;
}

// On globalThis so every route bundle (and a dev hot reload) shares one memory of what YOLO last said.
const g = globalThis as typeof globalThis & { __yoloState?: YoloState };
const state: YoloState = (g.__yoloState ??= {
  readings: new Map(),
  clips: new Map(),
  blobs: new Map(),
  failures: 0,
  downUntil: 0,
});

const unreachable = () => `YOLO service unreachable at ${JAMCAM_YOLO_URL}. Start it: npm run yolo`;

/** "fetch failed" says nothing; name the service and how to start it. */
function describe(e: unknown): string {
  const msg = (e as Error)?.message ?? String(e);
  return msg.includes(JAMCAM_YOLO_URL) ? msg : `${unreachable()} (${msg})`;
}

function noteDown() {
  state.failures += 1;
  state.downUntil = Date.now() + Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** (state.failures - 1));
}

function noteUp() {
  state.failures = 0;
  state.downUntil = 0;
}

/** One call to the YOLO server. While it is known to be down, fails at once instead of waiting on a timeout. */
async function yoloFetch(path: string, timeoutMs: number): Promise<Response> {
  if (Date.now() < state.downUntil) throw new Error(`${unreachable()} (retrying in ${Math.ceil((state.downUntil - Date.now()) / 1000)}s)`);
  try {
    const res = await fetch(`${JAMCAM_YOLO_URL}${path}`, { signal: AbortSignal.timeout(timeoutMs), cache: "no-store" });
    if (res.status >= 500) throw new Error(`YOLO ${path.split("?")[0]} failed: HTTP ${res.status}`);
    noteUp();
    return res;
  } catch (e) {
    noteDown();
    throw e;
  }
}

const fresh = (at: number) => Date.now() - at <= STALE_MAX_MS;

export interface CountsReply {
  readings: CamReading[];
  pending: string[];
  yolo: YoloStatus;
  error?: string;
}

/**
 * Counts for `ids`, merged with the last good reading per camera: YOLO answers only for cams it has
 * finished, and a failed call keeps the previous numbers instead of turning them into zeroes.
 */
export async function yoloCounts(ids: string[], timeoutMs = 15_000): Promise<CountsReply> {
  if (ids.length === 0) return { readings: [], pending: [], yolo: "live" };
  let pending: string[] = [];
  let error: string | undefined;
  let live = false;
  try {
    const res = await yoloFetch(`/counts?ids=${encodeURIComponent(ids.join(","))}`, timeoutMs);
    const body = (await res.json()) as { readings?: CamReading[]; pending?: string[] };
    for (const r of body.readings ?? []) state.readings.set(r.camId, r);
    pending = body.pending ?? [];
    live = true;
  } catch (e) {
    error = describe(e);
  }
  const readings = ids.flatMap((id) => {
    const r = state.readings.get(id);
    return r && fresh(r.at) ? [r] : [];
  });
  if (live) return { readings, pending: pending.filter((id) => !readings.some((r) => r.camId === id)), yolo: "live" };
  return { readings, pending: [], yolo: readings.length ? "stale" : "unavailable", error };
}

export interface TracksReply {
  clips: CamClipTracks[];
  pending: string[];
  yolo: YoloStatus;
  error?: string;
}

async function fetchClips(ids: string[], timeoutMs: number): Promise<{ clips: CamClipTracks[]; pending?: string[] }> {
  const res = await yoloFetch(`/tracks?ids=${encodeURIComponent(ids.join(","))}`, timeoutMs);
  if (!res.ok) throw new Error(`YOLO /tracks failed: HTTP ${res.status}`);
  return (await res.json()) as { clips: CamClipTracks[]; pending?: string[] };
}

/**
 * Tracks for `ids`. TfL cams go in one small request and each virtual cam in its own (its 15 min of
 * pre-tracked footage never changes, so it is fetched once), so one slow part cannot sink the rest.
 * Any cam YOLO did not answer for keeps its last good clip.
 */
export async function yoloTracks(ids: string[], opts: { skipVirtual: boolean }): Promise<TracksReply> {
  const virtual = opts.skipVirtual ? [] : ids.filter((id) => id.startsWith("VCAM_"));
  const tfl = ids.filter((id) => !id.startsWith("VCAM_"));
  const errors: string[] = [];
  let answered = 0;
  const pending: string[] = [];
  const parts = [
    tfl.length ? { ids: tfl, timeoutMs: 15_000 } : null,
    ...virtual.filter((id) => !state.clips.has(id)).map((id) => ({ ids: [id], timeoutMs: 40_000 })),
  ].filter((p) => p !== null);
  await Promise.all(
    parts.map(async (p) => {
      try {
        const got = await fetchClips(p.ids, p.timeoutMs);
        answered++;
        for (const c of got.clips ?? []) if (c.tracks?.length) state.clips.set(c.camId, c);
        pending.push(...(got.pending ?? []));
      } catch (e) {
        errors.push(describe(e));
      }
    }),
  );
  const wanted = [...tfl, ...virtual];
  const clips = wanted.flatMap((id) => {
    const c = state.clips.get(id);
    return c && (id.startsWith("VCAM_") || fresh(c.at)) ? [c] : [];
  });
  if (errors.length === 0) return { clips, pending, yolo: "live" };
  return { clips, pending, yolo: clips.length || answered > 0 ? "stale" : "unavailable", error: errors[0] };
}

/**
 * A binary YOLO asset (annotated frame, source clip), buffered and remembered per path, so a hiccup
 * serves the previous one (marked stale) instead of a broken image. Returns null when nothing is known.
 */
export async function yoloBlob(
  path: string,
  timeoutMs: number,
  fallbackType: string,
): Promise<{ blob: Blob; stale: boolean; status: number } | { blob: null; status: number; error: string }> {
  try {
    const res = await yoloFetch(path, timeoutMs);
    if (res.ok) {
      const blob = { at: Date.now(), type: res.headers.get("Content-Type") ?? fallbackType, body: await res.arrayBuffer() };
      state.blobs.set(path, blob);
      return { blob, stale: false, status: 200 };
    }
    const prev = state.blobs.get(path);
    if (prev) return { blob: prev, stale: true, status: 200 };
    return { blob: null, status: res.status, error: await res.text().catch(() => `HTTP ${res.status}`) };
  } catch (e) {
    const prev = state.blobs.get(path);
    if (prev && fresh(prev.at)) return { blob: prev, stale: true, status: 200 };
    return { blob: null, status: 503, error: describe(e) };
  }
}
