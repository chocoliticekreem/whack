const VENUE_URL = process.env.VENUE_URL ?? "http://127.0.0.1:8062";
// Why: exact paths only, so nothing a caller puts in [...path] can reach any other upstream path.
const GET_ROUTES = new Set(["health", "contracts", "book", "stats", "leaderboard", "positions"]);
const POST_ROUTES = new Set(["account/new", "order", "cancel"]);
const NO_STORE = { "cache-control": "no-store" };

async function forward(request: Request, path: string[], allowed: Set<string>): Promise<Response> {
  const route = path.join("/");
  if (!allowed.has(route)) return Response.json({ error: "unknown venue route" }, { status: 404, headers: NO_STORE });
  const url = new URL(request.url);
  const body = request.method === "POST" ? (await request.text()) || "{}" : undefined;
  const headers: Record<string, string> = { "content-type": "application/json" };
  const token = request.headers.get("x-player-token");
  if (token) headers["x-player-token"] = token;
  try {
    const upstream = await fetch(`${VENUE_URL}/${route}${url.search}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", ...NO_STORE },
    });
  } catch {
    return Response.json(
      { error: `Exchange venue offline at ${VENUE_URL}. Start services/city/venue_server.py` },
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
