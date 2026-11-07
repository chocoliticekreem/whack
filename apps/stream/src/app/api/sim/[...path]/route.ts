import type { Hotspot } from "@/app/multiverse/city-types";
import { fromSim, mockScenario, parseScenario, type SimScenario, type SimState } from "@/app/multiverse/scenario-model";

const SIM_URL = process.env.SIM_URL ?? "http://127.0.0.1:8449";
const CITY_URL = process.env.CITY_URL ?? "http://127.0.0.1:8051";
// Why: exact paths only. /intervene is deliberately absent: it changes the live market's worlds.
const GET_ROUTES = new Set(["state", "forecast"]);
const POST_ROUTES = new Set(["scenario"]);
const NO_STORE = { "cache-control": "no-store" };
const MAX_BODY = 8_192;
const TIMEOUT_MS = 20_000;

const fail = (error: string, status: number) => Response.json({ error }, { status, headers: NO_STORE });

async function upstream(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${SIM_URL}/${path}`, { ...init, cache: "no-store", signal: AbortSignal.timeout(TIMEOUT_MS) });
}

/** Hub lat/lon from the city engine, used to place the sim's stations; empty falls back to the graph's box. */
async function cityHubs(): Promise<Hotspot[]> {
  try {
    const h = await fetch(`${CITY_URL}/hotspots`, { cache: "no-store", signal: AbortSignal.timeout(5_000) });
    return h.ok ? (((await h.json()) as { hotspots?: Hotspot[] }).hotspots ?? []) : [];
  } catch {
    return [];
  }
}

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const route = (await ctx.params).path.join("/");
  if (!GET_ROUTES.has(route)) return fail("unknown sim route", 404);
  const url = new URL(request.url);
  try {
    const res = await upstream(`${route}${url.search}`);
    return new Response(res.body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json", ...NO_STORE },
    });
  } catch {
    return fail(`Central sim offline at ${SIM_URL}`, 503);
  }
}

export async function POST(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const route = (await ctx.params).path.join("/");
  if (!POST_ROUTES.has(route)) return fail("unknown sim route", 404);
  const text = await request.text();
  if (text.length > MAX_BODY) return fail("body too large", 413);
  let raw: unknown;
  try {
    raw = JSON.parse(text || "{}");
  } catch {
    return fail("body must be JSON", 400);
  }
  const req = parseScenario(raw);
  if (typeof req === "string") return fail(req, 400);

  try {
    // The sim's own dry run on copies of the live worlds; a 404 means an older sim, so the stand-in answers.
    const real = await upstream("scenario", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req),
    });
    if (real.status !== 404 && !real.ok) {
      const err = (await real.json().catch(() => ({}))) as { error?: string };
      return fail(err.error ?? `Central sim answered ${real.status}`, real.status);
    }
    const state = (await (await upstream("state")).json()) as SimState;
    if (!state.forecast?.stations?.length) return fail("Central sim has no forecast yet", 409);
    const hubs = await cityHubs();
    const body = real.ok ? fromSim((await real.json()) as SimScenario, state, hubs) : mockScenario(req, state, hubs);
    return Response.json(body, { headers: NO_STORE });
  } catch {
    return fail(`Central sim offline at ${SIM_URL}`, 503);
  }
}
