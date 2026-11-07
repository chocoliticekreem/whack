import type { NextRequest } from "next/server";
import { yoloBlob } from "@/lib/server/yolo";

export async function GET(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("id") ?? "";
  const got = await yoloBlob(`/frame?id=${encodeURIComponent(id)}`, 15_000, "image/jpeg");
  if (!got.blob) return Response.json({ error: got.error }, { status: got.status });
  return new Response(got.blob.body, {
    headers: {
      "Content-Type": got.blob.type,
      "Cache-Control": "no-store",
      "X-Data-At": String(got.blob.at),
      ...(got.stale ? { "X-Stale": "1" } : {}),
    },
  });
}
