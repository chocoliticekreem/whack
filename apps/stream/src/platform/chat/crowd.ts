"use client";

import { useEffect, useRef, useSyncExternalStore, type RefObject } from "react";
import type { SceneRequest, SceneResponse } from "@/agents/chat-crowd/scene";
import { subscribe } from "@/platform/bus";

/** Who is writing chat right now: the W&B model, or the local template crowd. */
export type ChatBackend = "wandb" | "local";

const NOT_CONFIGURED_RETRY_MS = 300_000;
const BASE_BACKOFF_MS = 10_000;
const MAX_BACKOFF_MS = 120_000;

let backend: ChatBackend = "local";
let retryAt = 0;
let failures = 0;
const listeners = new Set<() => void>();

function setBackend(next: ChatBackend) {
  if (next === backend) return;
  backend = next;
  listeners.forEach((fn) => fn());
}

/** `"wandb"` after the last model call succeeded, `"local"` before the first one or after a failure. */
export function useChatBackend(): ChatBackend {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => backend,
    () => "local",
  );
}

/** False while the server has no W&B key or a recent call failed; callers write local lines instead. */
export function crowdAvailable(now = Date.now()): boolean {
  return now >= retryAt;
}

/**
 * Ask /api/chat/scene for a batch of persona lines. Null on any failure (no key, timeout, bad
 * reply): the caller falls back to local lines, and the route is left alone for a while.
 */
export async function requestCrowd(
  body: SceneRequest,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<SceneResponse | null> {
  if (!crowdAvailable()) return null;
  const timeout = AbortSignal.timeout(timeoutMs);
  try {
    const res = await fetch("/api/chat/scene", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    if (res.status === 501) {
      retryAt = Date.now() + NOT_CONFIGURED_RETRY_MS;
      setBackend("local");
      return null;
    }
    if (!res.ok) throw new Error(`scene route ${res.status}`);
    const data = (await res.json()) as SceneResponse;
    failures = 0;
    setBackend("wandb");
    return data;
  } catch {
    if (signal?.aborted) return null;
    failures += 1;
    retryAt = Date.now() + Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** (failures - 1));
    setBackend("local");
    return null;
  }
}

/** The latest advisor summary the bus has carried (`surroundings.update`), for the model to echo. */
export function useSurroundingsRef(): RefObject<SceneRequest["surroundings"]> {
  const ref = useRef<SceneRequest["surroundings"]>(undefined);
  useEffect(
    () =>
      subscribe((e) => {
        if (e.type === "surroundings.update") ref.current = { summary: e.summary, level: e.level };
      }),
    [],
  );
  return ref;
}
