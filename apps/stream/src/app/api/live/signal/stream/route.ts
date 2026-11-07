import { subscribe } from "@/platform/live/rtc/signalStore";
import type { SignalMessage, SignalRole } from "@/platform/live/rtc/types";
import { DEFAULT_SESSION } from "@/platform/live/types";

const KEEPALIVE_MS = 15_000;

export async function GET(req: Request) {
  const params = new URL(req.url).searchParams;
  const session = params.get("session") ?? DEFAULT_SESSION;
  const role: SignalRole = params.get("role") === "phone" ? "phone" : "viewer";
  const viewerId = params.get("viewerId") ?? "";
  if (role === "viewer" && !viewerId) {
    return new Response("viewerId is required for role=viewer", { status: 400 });
  }
  const encoder = new TextEncoder();
  let stop: (() => void) | null = null;
  const stream = new ReadableStream({
    start(controller) {
      let open = true;
      const write = (chunk: string) => {
        if (!open) return;
        try {
          controller.enqueue(encoder.encode(chunk));
        } catch {
          open = false;
        }
      };
      const send = (m: SignalMessage) => write(`data: ${JSON.stringify(m)}\n\n`);
      write(": signalling open\n\n");
      const unsubscribe = subscribe(session, role, viewerId, send);
      const keepalive = setInterval(() => write(": ping\n\n"), KEEPALIVE_MS);
      stop = () => {
        open = false;
        unsubscribe();
        clearInterval(keepalive);
      };
      req.signal.addEventListener("abort", () => {
        stop?.();
        try {
          controller.close();
        } catch {
          // already closed
        }
      });
    },
    cancel() {
      stop?.();
    },
  });
  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-store, no-transform",
      connection: "keep-alive",
    },
  });
}
