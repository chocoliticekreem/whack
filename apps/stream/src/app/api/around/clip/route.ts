import type { NextRequest } from "next/server";
import { yoloBlob } from "@/lib/server/yolo";

/**
 * The exact MP4 the YOLO tracks came from. TfL overwrites each JamCam clip in place every few minutes,
 * so the compare view plays this instead of TfL's URL to stay frame-for-frame with the tracks.
 */
export async function GET(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("id") ?? "";
  // Buffered (clips are ~100 KB) so the <video> gets a Content-Length and can loop cleanly.
  const got = await yoloBlob(`/clip?id=${encodeURIComponent(id)}`, 25_000, "video/mp4");
  if (!got.blob) return Response.json({ error: got.error }, { status: got.status });
  return new Response(got.blob.body, {
    headers: {
      "Content-Type": got.blob.type,
      "Content-Length": String(got.blob.body.byteLength),
      "Cache-Control": "no-store",
      "X-Data-At": String(got.blob.at),
      ...(got.stale ? { "X-Stale": "1" } : {}),
    },
  });
}
