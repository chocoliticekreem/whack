import { getLatest, listen } from "@/platform/live/relay";
import { DEFAULT_SESSION, type LiveState } from "@/platform/live/types";

const KEEPALIVE_MS = 15_000;

export async function GET(req: Request) {
  const session = new URL(req.url).searchParams.get("session") ?? DEFAULT_SESSION;
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
      const send = (s: LiveState) => write(`data: ${JSON.stringify(s)}\n\n`);
      const latest = getLatest(session);
      if (latest) send(latest);
      else write(": waiting for the phone\n\n");
      const unlisten = listen(session, send);
      const keepalive = setInterval(() => write(": ping\n\n"), KEEPALIVE_MS);
      stop = () => {
        open = false;
        unlisten();
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
