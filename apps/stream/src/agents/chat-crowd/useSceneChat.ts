"use client";

import { useEffect, useRef } from "react";
import type { ChatMessage, StreamContext, StreamSource } from "@/lib/types";
import { crowdAvailable, requestCrowd, useChatBackend, useSurroundingsRef, type ChatBackend } from "@/platform/chat/crowd";
import { linesFromResponse, pickCast, plannedMessage, surgeIntensity } from "./engine";
import type { SceneRequest } from "./scene";

const INTERVAL_MS = 12_000;
const FIRST_TICK_MS = 3_000;
const FRAME_WIDTH = 640;
const FRAME_QUALITY = 0.7;
const RECENT_LINES = 12;
const MIN_LINES = 6;
const MAX_LINES = 8;
/** Server work is a text call (8 s), plus a vision call (8 s) and, for a remote clip, an ffmpeg grab (8 s). */
const TEXT_ONLY_TIMEOUT_MS = 11_000;
const FRAME_TIMEOUT_MS = 20_000;
const CLIP_TIMEOUT_MS = 28_000;
/** Grabbing a frame from a remote clip costs an ffmpeg run, so only every few ticks. */
const CLIP_FRAME_EVERY = 3;

/** JPEG data URI from the live <video>, or null when there is no decodable frame or the canvas is tainted. */
function captureFrame(): string | null {
  const video = document.querySelector<HTMLVideoElement>("video");
  if (!video || video.readyState < 2 || !video.videoWidth) return null;
  const scale = Math.min(1, FRAME_WIDTH / video.videoWidth);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  const g = canvas.getContext("2d");
  if (!g) return null;
  try {
    g.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", FRAME_QUALITY);
  } catch {
    return null;
  }
}

/**
 * Every ~12 s while live and visible, ask /api/chat/scene what the persona crowd would say right now:
 * from the video frame when one can be read, from the chat and the place alone otherwise. The lines
 * drip into chat on top of the local crowd. Goes quiet while the server has no W&B key or a call failed.
 * Returns which backend is writing chat, for the UI badge.
 */
export function useSceneChat(
  ctx: StreamContext,
  source: StreamSource,
  messages: ChatMessage[],
  inject: (batch: ChatMessage[]) => void,
): ChatBackend {
  const backend = useChatBackend();
  const surroundings = useSurroundingsRef();
  const ctxRef = useRef(ctx);
  const messagesRef = useRef(messages);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    ctxRef.current = ctx;
    messagesRef.current = messages;
  }, [ctx, messages]);

  const sourceUrl = source.kind === "url" ? source.url : null;

  useEffect(() => {
    if (!ctx.live) return;
    let cancelled = false;
    let ticks = 0;
    let next: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const pending = timers.current;

    const schedule = (delay: number) => {
      if (!cancelled) next = setTimeout(tick, delay * (0.8 + Math.random() * 0.4));
    };

    const tick = async () => {
      if (cancelled) return;
      if (document.hidden || !crowdAvailable()) return schedule(INTERVAL_MS);

      const current = ctxRef.current;
      const recent = messagesRef.current.slice(-RECENT_LINES);
      const now = Date.now();
      const count = Math.min(MAX_LINES, MIN_LINES + Math.floor(current.viewers / 2000));
      const cast = pickCast(
        "ambient",
        count,
        new Set(recent.map((m) => m.username)),
        Math.random,
        surgeIntensity(current, now) > 0,
      );

      const frame = captureFrame();
      const useClip = !frame && sourceUrl !== null && ticks % CLIP_FRAME_EVERY === 0;
      ticks += 1;
      const body: SceneRequest = {
        frame: frame ?? undefined,
        videoUrl: useClip ? (sourceUrl ?? undefined) : undefined,
        location: { name: current.location.name, area: current.location.area },
        viewers: current.viewers,
        event: "ambient",
        leaked: current.locationRevealedAt != null,
        surroundings: surroundings.current,
        recent: recent.map((m) => ({ username: m.username, text: m.text })),
        personas: cast.map((p) => ({ username: p.username, archetype: p.archetype })),
        count,
      };
      const timeoutMs = frame ? FRAME_TIMEOUT_MS : useClip ? CLIP_TIMEOUT_MS : TEXT_ONLY_TIMEOUT_MS;

      const res = await requestCrowd(body, timeoutMs, controller.signal);
      if (cancelled) return;
      if (res) {
        linesFromResponse(res, cast, 400).forEach((line) => {
          const t = setTimeout(() => {
            pending.delete(t);
            if (ctxRef.current.live) inject([plannedMessage(line, Date.now())]);
          }, line.delayMs);
          pending.add(t);
        });
      }
      schedule(INTERVAL_MS);
    };

    next = setTimeout(tick, FIRST_TICK_MS);
    return () => {
      cancelled = true;
      controller.abort();
      if (next) clearTimeout(next);
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, [ctx.live, sourceUrl, inject, surroundings]);

  return backend;
}
