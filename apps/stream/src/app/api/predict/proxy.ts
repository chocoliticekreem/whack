const PREDICT_URL = process.env.PREDICT_URL ?? "http://127.0.0.1:8048";
const OFFLINE = "Predict service offline — npm run predict";

/** Forwards a JSON POST to the Predict service and streams the (often ~2 MB) reply back. */
export async function proxyPost(path: string, request: Request): Promise<Response> {
  const body = await request.text();
  try {
    const upstream = await fetch(`${PREDICT_URL}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: body || "{}",
      cache: "no-store",
      signal: AbortSignal.timeout(300_000),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json({ error: OFFLINE }, { status: 503 });
  }
}
