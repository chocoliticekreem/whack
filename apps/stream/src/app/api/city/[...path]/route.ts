import { mockHandle } from "../mock";

const CITY_URL = process.env.CITY_URL ?? "http://127.0.0.1:8051";
const ROUTES = new Set(["hotspots", "traders", "markets", "settle", "bet", "score", "leaderboard",
  "wallet", "ledger", "positions", "history", "limits", "break"]);

async function forward(request: Request, path: string[]): Promise<Response> {
  const route = path.join("/");
  if (!ROUTES.has(route)) return Response.json({ error: "unknown city route" }, { status: 404 });
  const url = new URL(request.url);
  const body = request.method === "POST" ? (await request.text()) || "{}" : undefined;
  if (url.searchParams.get("mock") === "1") {
    const { status, data } = mockHandle(route, request.method, body ? JSON.parse(body) : {});
    return Response.json(data, { status, headers: { "cache-control": "no-store" } });
  }
  try {
    const upstream = await fetch(`${CITY_URL}/${route}${url.search}`, {
      method: request.method,
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json(
      { error: `City markets engine offline at ${CITY_URL}. Start the city server, or open the game with ?mock=1` },
      { status: 503 },
    );
  }
}

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(request, (await ctx.params).path);
}

export async function POST(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(request, (await ctx.params).path);
}
