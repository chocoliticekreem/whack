import { mockHandle } from "../mock";

const CITY_URL = process.env.CITY_URL ?? "http://127.0.0.1:8051";
// Why: exact paths only, so nothing a caller puts in [...path] can reach any other upstream path.
const GET_ROUTES = new Set(["hotspots", "traders", "markets", "round", "signals", "leaderboard", "stats",
  "wallet", "ledger", "positions", "history"]);
const POST_ROUTES = new Set(["settle", "bet", "limits", "break", "player/new", "player/name"]);
const NO_STORE = { "cache-control": "no-store" };

async function forward(request: Request, path: string[], allowed: Set<string>): Promise<Response> {
  const route = path.join("/");
  if (!allowed.has(route)) return Response.json({ error: "unknown city route" }, { status: 404, headers: NO_STORE });
  const url = new URL(request.url);
  const body = request.method === "POST" ? (await request.text()) || "{}" : undefined;
  if (url.searchParams.get("mock") === "1") {
    const { status, data } = mockHandle(route, request.method, body ? JSON.parse(body) : {});
    return Response.json(data, { status, headers: NO_STORE });
  }
  const headers: Record<string, string> = { "content-type": "application/json" };
  const token = request.headers.get("x-player-token");
  if (token) headers["x-player-token"] = token;
  const client = request.headers.get("x-forwarded-for");
  if (client) headers["x-forwarded-for"] = client;
  try {
    const upstream = await fetch(`${CITY_URL}/${route}${url.search}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", ...NO_STORE },
    });
  } catch {
    return Response.json(
      { error: `City markets engine offline at ${CITY_URL}. Start the city server, or open the game with ?mock=1` },
      { status: 503, headers: NO_STORE },
    );
  }
}

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(request, (await ctx.params).path, GET_ROUTES);
}

export async function POST(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(request, (await ctx.params).path, POST_ROUTES);
}
