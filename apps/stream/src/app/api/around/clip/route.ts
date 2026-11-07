import type { NextRequest } from "next/server";

const JAMCAM_YOLO_URL = process.env.JAMCAM_YOLO_URL ?? "http://127.0.0.1:8047";

/**
 * The exact MP4 the YOLO tracks came from. TfL overwrites each JamCam clip in place every few minutes,
 * so the compare view plays this instead of TfL's URL to stay frame-for-frame with the tracks.
 */
export async function GET(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("id") ?? "";
  try {
    const res = await fetch(`${JAMCAM_YOLO_URL}/clip?id=${encodeURIComponent(id)}`, {
      signal: AbortSignal.timeout(25_000),
    });
    // Buffered (clips are ~100 KB) so the <video> gets a Content-Length and can loop cleanly.
    const body = await res.arrayBuffer();
    return new Response(body, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("Content-Type") ?? "video/mp4",
        "Content-Length": String(body.byteLength),
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json({ error: `YOLO service unreachable at ${JAMCAM_YOLO_URL}` }, { status: 503 });
  }
}
