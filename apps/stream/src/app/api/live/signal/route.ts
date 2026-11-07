import { NextResponse } from "next/server";
import { deliver } from "@/platform/live/rtc/signalStore";
import type { SignalMessage, SignalRole, SignalType } from "@/platform/live/rtc/types";
import { DEFAULT_SESSION } from "@/platform/live/types";

const ROLES: SignalRole[] = ["phone", "viewer"];
const TYPES: SignalType[] = ["join", "offer", "answer", "ice", "bye"];

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Partial<SignalMessage> | null;
  const from = body?.from;
  const type = body?.type;
  const viewerId = body?.viewerId;
  if (!from || !ROLES.includes(from) || !type || !TYPES.includes(type) || typeof viewerId !== "string" || !viewerId) {
    return NextResponse.json({ error: "from, type and viewerId are required" }, { status: 400 });
  }
  deliver({
    session: typeof body?.session === "string" && body.session ? body.session : DEFAULT_SESSION,
    from,
    viewerId,
    type,
    payload: body?.payload,
    ts: Date.now(),
  });
  return NextResponse.json({ ok: true });
}
