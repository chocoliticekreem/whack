import type { NextRequest } from "next/server";
import { AGENT_URL } from "@/platform/agent/agentUrl";

export async function GET(request: NextRequest) {
  const limit = request.nextUrl.searchParams.get("limit") ?? "8";
  try {
    const res = await fetch(`${AGENT_URL}/verdicts?limit=${encodeURIComponent(limit)}`, {
      signal: AbortSignal.timeout(15_000),
      cache: "no-store",
    });
    return Response.json(await res.json(), { status: res.status });
  } catch (e) {
    return Response.json({ error: `Cluster agent at ${AGENT_URL} unreachable: ${(e as Error).message}` }, { status: 503 });
  }
}
