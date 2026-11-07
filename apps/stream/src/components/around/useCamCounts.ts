"use client";

import { useEffect, useState } from "react";
import type { CamReading } from "@/platform/around/types";

const PENDING_POLL_MS = 15_000;
const SETTLED_POLL_MS = 60_000;

/** YOLO people/vehicle counts per camera. Polls fast while the service is still analysing. */
export function useCamCounts(camIds: string[]) {
  const [readings, setReadings] = useState<Map<string, CamReading>>(() => new Map());
  const key = camIds.join(",");

  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      let delay = SETTLED_POLL_MS;
      try {
        const res = await fetch(`/api/around/counts?ids=${encodeURIComponent(key)}`, {
          signal: controller.signal,
        });
        if (res.ok) {
          const json = (await res.json()) as { readings?: CamReading[]; pending?: string[] };
          if (controller.signal.aborted) return;
          setReadings((prev) => {
            const next = new Map(prev);
            for (const r of json.readings ?? []) next.set(r.camId, r);
            return next;
          });
          if ((json.pending ?? []).length > 0) delay = PENDING_POLL_MS;
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        console.error("[around] counts failed", err);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, delay);
    };
    poll();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [key]);

  return readings;
}
