import type { NextRequest } from "next/server";
import { yoloCounts } from "@/lib/server/yolo";

export async function GET(request: NextRequest) {
  const ids = (request.nextUrl.searchParams.get("ids") ?? "").split(",").filter(Boolean);
  const reply = await yoloCounts(ids);
  // 503 only when there is nothing to show; stale readings are a 200 that says so in `yolo`.
  return Response.json(reply, { status: reply.yolo === "unavailable" ? 503 : 200, headers: { "Cache-Control": "no-store" } });
}
