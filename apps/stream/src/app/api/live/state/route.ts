import { NextResponse } from "next/server";
import { getLatest, setLatest } from "@/platform/live/relay";
import { DEFAULT_SESSION, type LiveState } from "@/platform/live/types";

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

export async function GET(req: Request) {
  const session = new URL(req.url).searchParams.get("session") ?? DEFAULT_SESSION;
  return NextResponse.json({ state: getLatest(session) }, { headers: { "cache-control": "no-store" } });
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Partial<LiveState> | null;
  const lat = num(body?.lat);
  const lon = num(body?.lon);
  if (!body || lat === null || lon === null) {
    return NextResponse.json({ error: "lat and lon are required numbers" }, { status: 400 });
  }
  const state: LiveState = {
    streamer: typeof body.streamer === "string" ? body.streamer : "streamer",
    session: typeof body.session === "string" && body.session ? body.session : DEFAULT_SESSION,
    lat,
    lon,
    accuracyM: num(body.accuracyM) ?? 0,
    heading: num(body.heading),
    speed: num(body.speed),
    viewers: num(body.viewers) ?? 0,
    live: body.live === true,
    label: typeof body.label === "string" ? body.label : "",
    ts: num(body.ts) ?? Date.now(),
  };
  setLatest(state);
  return NextResponse.json({ ok: true, ts: state.ts });
}
