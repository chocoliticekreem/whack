import type { NextRequest } from "next/server";

const JAMCAM_YOLO_URL = process.env.JAMCAM_YOLO_URL ?? "http://127.0.0.1:8047";

export async function GET(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("id") ?? "";
  try {
    const res = await fetch(`${JAMCAM_YOLO_URL}/frame?id=${encodeURIComponent(id)}`, {
      signal: AbortSignal.timeout(15_000),
    });
    return new Response(res.body, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("Content-Type") ?? "image/jpeg",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json({ error: `YOLO service unreachable at ${JAMCAM_YOLO_URL}` }, { status: 503 });
  }
}
