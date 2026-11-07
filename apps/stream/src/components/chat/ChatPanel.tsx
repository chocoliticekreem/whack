"use client";

import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowDown,
  BadgeCheck,
  ChevronDown,
  Crown,
  Eye,
  Gem,
  MapPin,
  MessageSquareOff,
  Send,
  Shield,
  Star,
  Zap,
} from "lucide-react";
import type { Badge, ChatMessage } from "@/lib/types";
import { useChatBackend } from "@/platform/chat/crowd";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  viewers: number;
  live: boolean;
  /** Set while chat floats over the content, to fold it away. */
  onCollapse?: () => void;
}

const BADGE_ICON: Record<Badge, { Icon: typeof Shield; className: string; label: string }> = {
  mod: { Icon: Shield, className: "text-ok", label: "Moderator" },
  vip: { Icon: Gem, className: "text-[#ff6bd6]", label: "VIP" },
  sub: { Icon: Star, className: "text-[#c77dff]", label: "Subscriber" },
  founder: { Icon: Crown, className: "text-warn", label: "Founder" },
  verified: { Icon: BadgeCheck, className: "text-[#5aa9ff]", label: "Verified" },
};

const STICK_THRESHOLD_PX = 48;

function formatViewers(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : String(n);
}

function hhmm(ts: number) {
  return new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function Badges({ badges }: { badges: Badge[] }) {
  if (badges.length === 0) return null;
  return (
    <span className="mr-1 inline-flex translate-y-[2px] gap-0.5">
      {badges.map((b) => {
        const { Icon, className, label } = BADGE_ICON[b];
        return <Icon key={b} aria-label={label} className={`size-3 ${className}`} />;
      })}
    </span>
  );
}

function Stamp({ ts }: { ts: number }) {
  return (
    <span className="mr-1.5 hidden font-mono text-[9px] text-dim group-hover:inline">{hhmm(ts)}</span>
  );
}

function MessageRow({ m }: { m: ChatMessage }) {
  if (m.highlight === "donation") {
    return (
      <div className="animate-chat-in my-1 overflow-hidden rounded-[6px] border border-ember/40 bg-ember-soft">
        <div className="flex items-center justify-between border-b border-ember/20 px-2.5 py-1">
          <span className="flex items-center gap-1.5 font-mono text-[10px] font-semibold tracking-normal text-ember">
            <Zap className="size-3 fill-current" />
            Tip
          </span>
          <span className="font-mono text-[12px] font-semibold text-ember">£{m.amount}</span>
        </div>
        <p className="px-2.5 py-1.5 text-[13px] leading-5">
          <span className="font-semibold" style={{ color: m.color }}>
            {m.username}
          </span>
          <span className="text-muted"> · </span>
          <span className="text-text">{m.text}</span>
        </p>
      </div>
    );
  }

  const base = "group animate-chat-in rounded-[4px] px-2 py-[3px] text-[13px] leading-[18px] break-words";
  const tone = m.fromStreamer
    ? "my-0.5 border-l-2 border-ember bg-ember-soft"
    : m.locationSignal
      ? "border-l-2 border-warn/70 bg-warn/[0.06]"
      : m.highlight === "first"
        ? "border-l-2 border-[#5aa9ff] bg-[#5aa9ff10]"
        : "hover:bg-surface-2";

  return (
    <div className={`${base} ${tone}`}>
      {m.highlight === "first" && (
        <div className="mb-0.5 font-mono text-[9px] font-semibold tracking-normal text-[#5aa9ff]">
          First message
        </div>
      )}
      {m.fromStreamer && (
        <div className="mb-0.5 font-mono text-[9px] font-semibold tracking-normal text-ember">
          Streamer
        </div>
      )}
      <Stamp ts={m.timestamp} />
      <Badges badges={m.badges} />
      <span className="font-semibold" style={{ color: m.color }}>
        {m.username}
      </span>
      <span className="text-dim"> </span>
      <span className={m.fromStreamer ? "font-medium text-text" : "text-text/85"}>{m.text}</span>
      {m.scene && (
        <span title={`chat sees: ${m.scene}`} className="ml-1.5 inline-flex translate-y-[-1px] align-middle text-dim">
          <Eye aria-label="Reacting to the stream" className="size-2.5" />
        </span>
      )}
      {m.locationSignal && (
        <span className="ml-1.5 inline-flex translate-y-[-1px] items-center gap-0.5 rounded-[3px] border border-warn/20 px-1 py-px align-middle font-mono text-[9px] tracking-normal text-warn/80">
          <MapPin className="size-2.5" />
          Location
        </span>
      )}
    </div>
  );
}

export function ChatPanel({ messages, onSend, viewers, live, onCollapse }: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(true);
  const [seenId, setSeenId] = useState<string | undefined>();
  const [draft, setDraft] = useState("");

  const lastId = messages.at(-1)?.id;

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (stuck && el) el.scrollTop = el.scrollHeight;
  }, [lastId, stuck]);

  const seenIndex = seenId ? messages.findIndex((m) => m.id === seenId) : -1;
  const unseen = stuck ? 0 : seenIndex < 0 ? messages.length : messages.length - 1 - seenIndex;

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
    if (atBottom === stuck) return;
    if (!atBottom) setSeenId(lastId);
    setStuck(atBottom);
  }

  function resume() {
    setStuck(true);
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }

  function submit() {
    if (!draft.trim()) return;
    onSend(draft);
    setDraft("");
    resume();
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const backend = useChatBackend();
  const signals = messages.reduce((n, m) => n + (m.locationSignal ? 1 : 0), 0);

  return (
    <section className="flex h-full min-h-0 flex-col bg-surface">
      <header className="flex h-11 shrink-0 items-center justify-between border-b border-line px-3.5">
        <h2 className="flex items-center gap-2 font-mono text-[10px] tracking-normal text-text">
          <span className={`size-1.5 rounded-full ${live ? "animate-live bg-live" : "bg-dim"}`} />
          Chat
          <span
            title={backend === "wandb" ? "Lines written by Nemotron on W&B Inference, traced in Weave" : "Local persona crowd (no W&B key)"}
            className={`rounded-full px-1.5 py-0.5 text-[9px] font-bold ${backend === "wandb" ? "bg-candy-orange/20 text-candy-orange" : "bg-white/10 text-muted"}`}
          >
            {backend === "wandb" ? "W&B" : "local"}
          </span>
        </h2>
        <div className="flex items-center gap-3 font-mono text-[10px] tracking-normal">
          {signals > 0 && (
            <span className="flex items-center gap-1 text-warn">
              <MapPin className="size-3" />
              {signals}
            </span>
          )}
          <span className="text-muted">
            <span className="text-text">{live ? formatViewers(viewers) : "—"}</span> watching
          </span>
          {onCollapse && (
            <button type="button" onClick={onCollapse} aria-label="Collapse chat" className="pill pill-sm !h-7 !w-7 !px-0">
              <ChevronDown className="size-4" aria-hidden />
            </button>
          )}
        </div>
      </header>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="scrollbar-thin h-full overflow-y-auto px-1.5 py-2"
        >
          {!live && messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-muted">
              <MessageSquareOff className="size-5 text-dim" />
              <p className="text-[13px]">Chat opens when you go live</p>
            </div>
          ) : (
            <div className="flex flex-col gap-px">
              {messages.map((m) => (
                <MessageRow key={m.id} m={m} />
              ))}
            </div>
          )}
        </div>

        {!stuck && (
          <button
            type="button"
            onClick={resume}
            className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-md border border-line-strong bg-surface-2/95 px-3 py-1.5 font-mono text-[10px] tracking-normal text-text shadow-lg backdrop-blur hover:border-ember/60"
          >
            <ArrowDown className="size-3" />
            Paused{unseen > 0 ? ` · ${unseen} new` : ""}
          </button>
        )}
      </div>

      <div className="shrink-0 border-t border-line p-2.5">
        <div className="flex items-center gap-2 rounded-full bg-bg pl-4 pr-1.5 shadow-[inset_0_2px_4px_rgb(0_0_0/0.5),0_0_0_1px_rgb(255_255_255/0.07)] transition-shadow focus-within:shadow-[inset_0_2px_4px_rgb(0_0_0/0.5),0_0_0_2px_rgb(255_141_16/0.7)]">
          <span className="font-mono text-[10px] tracking-normal text-ember">You</span>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Say something to chat"
            className="h-10 min-w-0 flex-1 bg-transparent text-[13px] text-text outline-none placeholder:text-dim"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!draft.trim()}
            aria-label="Send"
            className="pill-candy grid size-8 place-items-center rounded-full text-white transition-transform active:scale-90 disabled:bg-transparent disabled:bg-none disabled:text-dim disabled:shadow-none"
          >
            <Send className="size-3.5" />
          </button>
        </div>
      </div>
    </section>
  );
}
