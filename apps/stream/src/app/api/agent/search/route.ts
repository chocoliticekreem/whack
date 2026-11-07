import type { NextRequest } from "next/server";
import { AGENT_URL } from "@/platform/agent/agentUrl";

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as { query?: unknown; top_k?: unknown } | null;
  if (!body || typeof body.query !== "string" || !body.query.trim()) {
    return Response.json({ error: "query is required" }, { status: 400 });
  }
  try {
    const res = await fetch(`${AGENT_URL}/search`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: body.query, top_k: typeof body.top_k === "number" ? body.top_k : 6 }),
      // One VSS search plus a detections and a segments call per moment.
      signal: AbortSignal.timeout(45_000),
      cache: "no-store",
    });
    return Response.json(await res.json(), { status: res.status });
  } catch (e) {
    return Response.json({ error: `Cluster agent at ${AGENT_URL} unreachable: ${(e as Error).message}` }, { status: 503 });
  }
}
