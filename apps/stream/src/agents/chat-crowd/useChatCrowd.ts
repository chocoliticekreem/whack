"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, StreamContext } from "@/lib/types";
import { publish, subscribe } from "@/platform/bus";
import { crowdAvailable, requestCrowd, useChatBackend, useSurroundingsRef } from "@/platform/chat/crowd";
import {
  createMemory,
  isFresh,
  linesFromResponse,
  nextMessages,
  pickCast,
  plannedMessage,
  reactionPlan,
  remember,
  streamerMessage,
  type PlannedLine,
} from "./engine";
import type { CrowdEvent, SceneRequest } from "./scene";

const TICK_MS = 250;
const MAX_MESSAGES = 150;
const RECENT_LINES = 12;
const LOCAL_HEAD_LINES = 2;
const LOCAL_SCALE_WHEN_MODEL_WRITES = 0.35;
const EVENT_LLM_LINES = 8;
const EVENT_TIMEOUT_MS = 8_000;
/** Lets the streamer's own message land before the crowd reacts to a location leak, so both get one reaction. */
const LEAK_SETTLE_MS = 600;
const MIRROR_COOLDOWN_MS = 8_000;

export function useChatCrowd(ctx: StreamContext): {
  messages: ChatMessage[];
  send: (text: string) => void;
  inject: (batch: ChatMessage[]) => void;
} {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const backend = useChatBackend();
  const surroundings = useSurroundingsRef();
  const ctxRef = useRef(ctx);
  const messagesRef = useRef(messages);
  const backendRef = useRef(backend);
  const memory = useRef(createMemory());
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const leakPending = useRef<{ timer: ReturnType<typeof setTimeout>; streamerText?: string } | null>(null);
  const lastMirrorAt = useRef(0);
  const abort = useRef(new AbortController());

  useEffect(() => {
    ctxRef.current = ctx;
    messagesRef.current = messages;
    backendRef.current = backend;
  }, [ctx, messages, backend]);

  const append = useCallback((batch: ChatMessage[]) => {
    const now = Date.now();
    const accepted = batch.filter((m) => m.fromStreamer || isFresh(memory.current, m.text));
    if (accepted.length === 0) return;
    accepted.forEach((message) => {
      remember(memory.current, message.username, message.text, now);
      publish({ type: "chat.message", message });
    });
    setMessages((prev) => [...prev, ...accepted].slice(-MAX_MESSAGES));
  }, []);

  const later = useCallback((delayMs: number, run: () => void) => {
    const t = setTimeout(() => {
      timers.current.delete(t);
      run();
    }, delayMs);
    timers.current.add(t);
  }, []);

  const play = useCallback(
    (lines: PlannedLine[]) => {
      lines.forEach((line) =>
        later(line.delayMs, () => {
          if (ctxRef.current.live) append([plannedMessage(line, Date.now())]);
        }),
      );
    },
    [append, later],
  );

  /** The crowd reacts to something the streamer did. Two local lines land at once; the model writes the rest, or the local crowd does if it can't. */
  const react = useCallback(
    (event: Exclude<CrowdEvent, "ambient">, streamerText?: string) => {
      const current = ctxRef.current;
      if (!current.live) return;
      const now = Date.now();
      const plan = reactionPlan(event, current, memory.current, now);
      if (!crowdAvailable(now)) return play(plan);
      play(plan.slice(0, LOCAL_HEAD_LINES));

      const cast = pickCast(
        event,
        EVENT_LLM_LINES,
        new Set(messagesRef.current.slice(-RECENT_LINES).map((m) => m.username)),
        Math.random,
      );
      const body: SceneRequest = {
        location: { name: current.location.name, area: current.location.area },
        viewers: current.viewers,
        event,
        leaked: current.locationRevealedAt != null || event === "location_leaked",
        streamerMessage: streamerText,
        surroundings: surroundings.current,
        recent: messagesRef.current.slice(-RECENT_LINES).map((m) => ({ username: m.username, text: m.text })),
        personas: cast.map((p) => ({ username: p.username, archetype: p.archetype })),
        count: EVENT_LLM_LINES,
      };
      void requestCrowd(body, EVENT_TIMEOUT_MS, abort.current.signal).then((res) => {
        if (abort.current.signal.aborted) return;
        const head = plan.length > 0 ? plan[Math.min(LOCAL_HEAD_LINES, plan.length) - 1].delayMs : 0;
        const lines = res ? linesFromResponse(res, cast, Math.max(head + 500, 900)) : [];
        if (lines.length > 0) {
          memory.current.quietUntil = Math.max(memory.current.quietUntil, Date.now() + lines.at(-1)!.delayMs + 2000);
          play(lines);
        } else {
          play(plan.slice(LOCAL_HEAD_LINES));
        }
      });
    },
    [play, surroundings],
  );

  useEffect(
    () =>
      subscribe((e) => {
        if (e.type === "streamer:location_leaked" && !leakPending.current) {
          const pending: { timer: ReturnType<typeof setTimeout>; streamerText?: string } = {
            timer: setTimeout(() => {
              leakPending.current = null;
              react("location_leaked", pending.streamerText);
            }, LEAK_SETTLE_MS),
          };
          leakPending.current = pending;
        }
        if (e.type === "reality:fork" && Date.now() - lastMirrorAt.current > MIRROR_COOLDOWN_MS) {
          lastMirrorAt.current = Date.now();
          react("mirror");
        }
      }),
    [react],
  );

  useEffect(() => {
    if (!ctx.live) return;
    const id = setInterval(() => {
      const scale = backendRef.current === "wandb" ? LOCAL_SCALE_WHEN_MODEL_WRITES : 1;
      append(nextMessages(ctxRef.current, Date.now(), memory.current, Math.random, TICK_MS, scale));
    }, TICK_MS);
    return () => clearInterval(id);
  }, [ctx.live, append]);

  useEffect(() => {
    const pending = timers.current;
    const controller = new AbortController();
    abort.current = controller;
    return () => {
      controller.abort();
      pending.forEach(clearTimeout);
      pending.clear();
      if (leakPending.current) clearTimeout(leakPending.current.timer);
      leakPending.current = null;
    };
  }, []);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const current = ctxRef.current;
      append([streamerMessage(trimmed, current, Date.now())]);
      if (!current.live) return;
      if (leakPending.current) {
        leakPending.current.streamerText = trimmed;
        return;
      }
      react("streamer_message", trimmed);
    },
    [append, react],
  );

  return { messages, send, inject: append };
}
