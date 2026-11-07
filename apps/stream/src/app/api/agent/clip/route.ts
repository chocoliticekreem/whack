import type { NextRequest } from "next/server";
import { AGENT_URL } from "@/platform/agent/agentUrl";

/** Streams one VSS segment's bytes through the cluster agent; the browser cannot reach VSS directly. */
export async function GET(request: NextRequest) {
  const source = request.nextUrl.searchParams.get("source") ?? "";
  if (!source.startsWith("s3://")) return Response.json({ error: "source must be an s3:// URI" }, { status: 400 });
  const range = request.headers.get("range");
  try {
    const upstream = await fetch(`${AGENT_URL}/clip?source=${encodeURIComponent(source)}`, {
      headers: range ? { range } : {},
      signal: AbortSignal.timeout(60_000),
      cache: "no-store",
    });
    const headers = new Headers({ "content-type": "video/mp4" });
    for (const name of ["content-length", "content-range", "accept-ranges"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (e) {
    return Response.json({ error: `Cluster agent at ${AGENT_URL} unreachable: ${(e as Error).message}` }, { status: 503 });
  }
}
