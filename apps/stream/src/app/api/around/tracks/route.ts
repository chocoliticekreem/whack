import type { NextRequest } from "next/server";
import type { CamClipTracks } from "@/platform/around/types";

const JAMCAM_YOLO_URL = process.env.JAMCAM_YOLO_URL ?? "http://127.0.0.1:8047";

// Virtual cams' tracks are 15 min of pre-tracked footage (~0.7 MB each) and never change: fetch once per instance.
const virtualCache = new Map<string, CamClipTracks>();

async function fetchClips(ids: string[], timeoutMs: number): Promise<{ clips: CamClipTracks[]; pending?: string[] } | null> {
  if (ids.length === 0) return { clips: [] };
  const res = await fetch(`${JAMCAM_YOLO_URL}/tracks?ids=${encodeURIComponent(ids.join(","))}`, {
    signal: AbortSignal.timeout(timeoutMs),
  }).catch(() => null);
  return res?.ok ? ((await res.json().catch(() => null)) as { clips: CamClipTracks[]; pending?: string[] } | null) : null;
}

/**
 * Splits the poll so one slow part cannot sink the rest: TfL cams in one small request, each virtual cam on
 * its own (and cached), merged from whatever answered.
 */
export async function GET(request: NextRequest) {
  const ids = (request.nextUrl.searchParams.get("ids") ?? "").split(",").filter(Boolean);
  // On Vercel the virtual cams' footage is not deployed (too large) and their tracks cannot cross the tunnel in time.
  const virtual = process.env.VERCEL ? [] : ids.filter((id) => id.startsWith("VCAM_"));
  const tfl = ids.filter((id) => !id.startsWith("VCAM_"));
  const [live, ...vcams] = await Promise.all([
    fetchClips(tfl, 15_000),
    ...virtual.map(async (id) => {
      const hit = virtualCache.get(id);
      if (hit) return { clips: [hit] };
      const got = await fetchClips([id], 40_000);
      for (const c of got?.clips ?? []) virtualCache.set(c.camId, c);
      return got;
    }),
  ]);
  const parts = [live, ...vcams];
  if (parts.every((p) => p === null)) {
    return Response.json({ error: `YOLO service unreachable at ${JAMCAM_YOLO_URL}. Start it: npm run yolo`, clips: [] }, { status: 503 });
  }
  return Response.json({
    clips: parts.flatMap((p) => p?.clips ?? []),
    pending: parts.flatMap((p) => p?.pending ?? []),
  });
}
