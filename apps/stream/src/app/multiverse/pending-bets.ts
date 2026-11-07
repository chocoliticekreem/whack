"use client";

import { useEffect, useRef } from "react";
import type { MyBet } from "./city-store";
import type { BetResponse } from "./city-types";

const PENDING_POLL_MS = 1500;

export interface Fill {
  odds: number;
  payout: number;
}

/** Plain words for why the exchange cancelled a pending bet. */
export function cancelNote(reason: string | undefined): string {
  const why = reason ?? "";
  const paused = why.match(/^(?:market paused|suspended): ([^;]+)/);
  const text = paused
    ? `the market paused (${paused[1]})`
    : why === "market closed"
      ? "the market closed before it filled"
      : why === "stale feed"
        ? "the market paused while its data feed was late"
        : why || "the exchange could not fill it";
  return `Bet cancelled: ${text}. No points moved.`;
}

/** Polls GET /bet for every pending bet until the exchange fills or cancels it. */
export function usePendingFills(
  bets: Record<string, MyBet>,
  player: string,
  token: string | null,
  mock: boolean,
  onFill: (market: string, fill: Fill) => void,
  onCancel: (market: string, reason: string | undefined) => void,
) {
  const handlers = useRef({ onFill, onCancel });
  useEffect(() => {
    handlers.current = { onFill, onCancel };
  }, [onFill, onCancel]);
  const pending = Object.values(bets).filter((b) => b.pending && !b.done);
  const key = pending.map((b) => b.pending?.betId).join(",");

  useEffect(() => {
    if (!key) return;
    const ids = new Map(pending.map((b) => [b.pending!.betId, b.market]));
    let live = true;
    const poll = async () => {
      for (const [id, market] of ids) {
        const q = new URLSearchParams({ player, id, ...(mock ? { mock: "1" } : {}) });
        try {
          const res = await fetch(`/api/city/bet?${q}`, { headers: token ? { "x-player-token": token } : {}, cache: "no-store" });
          if (!live) return;
          if (res.status === 404) {
            ids.delete(id);
            handlers.current.onCancel(market, "the exchange no longer knows this bet");
            continue;
          }
          const data = (await res.json()) as BetResponse;
          if (data.status === "filled" && data.odds != null && data.payout != null) {
            ids.delete(id);
            handlers.current.onFill(market, { odds: data.odds, payout: data.payout });
          } else if (data.status === "cancelled") {
            ids.delete(id);
            handlers.current.onCancel(market, data.reason);
          }
        } catch {
          // Network blip: the next poll retries.
        }
      }
    };
    const timer = window.setInterval(() => void poll(), PENDING_POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
    // Why: restart only when the set of pending bets changes, not on every render's new array.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, player, token, mock]);
}
