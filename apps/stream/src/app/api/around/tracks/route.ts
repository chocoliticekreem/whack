import type { NextRequest } from "next/server";
import { yoloTracks } from "@/lib/server/yolo";

export async function GET(request: NextRequest) {
  const ids = (request.nextUrl.searchParams.get("ids") ?? "").split(",").filter(Boolean);
  // On Vercel the virtual cams' footage is not deployed (too large) and their tracks cannot cross the tunnel in time.
  const reply = await yoloTracks(ids, { skipVirtual: !!process.env.VERCEL });
  return Response.json(reply, { status: reply.yolo === "unavailable" ? 503 : 200, headers: { "Cache-Control": "no-store" } });
}
