const MULTIVERSE_URL = process.env.MULTIVERSE_URL ?? "http://127.0.0.1:8049";
const ROUTES = new Set(["spawn", "observe", "state", "intervene", "replay", "geometry", "health", "brokers", "vix"]);

async function forward(request: Request, path: string[]): Promise<Response> {
  const route = path.join("/");
  if (!ROUTES.has(route)) return Response.json({ error: "unknown multiverse route" }, { status: 404 });
  const search = new URL(request.url).search;
  try {
    const upstream = await fetch(`${MULTIVERSE_URL}/${route}${search}`, {
      method: request.method,
      headers: { "content-type": "application/json" },
      body: request.method === "POST" ? (await request.text()) || "{}" : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(120_000),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json(
      { error: `Multiverse engine offline at ${MULTIVERSE_URL}. Start it: python3 services/multiverse/server.py` },
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
